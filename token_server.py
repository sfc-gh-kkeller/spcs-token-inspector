#!/usr/bin/env python3
"""
Token Inspector for Snowflake Container Services (SPCS)
========================================================
Lightweight HTTP server that exposes Snowflake OAuth tokens and environment
info as a web UI. Useful as a sidecar or standalone debug container in any
SPCS service.

Headers captured from Snowflake ingress:
  - Sf-Context-Current-User       -> /tmp/sf_current_user.txt
  - Sf-Context-Current-User-Token -> /tmp/sf_user_token.txt

Token files:
  - /snowflake/session/token      - Container service token (auto-refreshed by Snowflake)
  - /tmp/sf_user_token.txt        - User token (captured from ingress headers)
  - /tmp/sf_combined_token.txt    - Combined token for caller's rights
"""

import os
import json
import base64
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone


def url_encode_token(token):
    if not token:
        return ""
    return urllib.parse.quote(token, safe='')


TOKEN_FILE = "/tmp/sf_user_token.txt"
USER_FILE = "/tmp/sf_current_user.txt"
JDBC_FILE = "/tmp/sf_user_jdbc_url.txt"
STATUS_FILE = "/tmp/sf_token_status.json"
CONTAINER_TOKEN_FILE = "/snowflake/session/token"
COMBINED_TOKEN_FILE = "/tmp/sf_combined_token.txt"
SERVICE_JDBC_FILE = "/tmp/snowflake_jdbc_url.txt"
COMBINED_JDBC_FILE = "/tmp/sf_combined_jdbc_url.txt"


def read_fresh_token(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return f.read().strip()
        except Exception as e:
            return f"ERROR: {e}"
    return ""


def decode_jwt(token):
    if not token:
        return None, None, None, "No token provided"
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None, None, None, f"Invalid JWT format (expected 3 parts, got {len(parts)})"
        header_b64 = parts[0]
        header_b64 += '=' * (4 - len(header_b64) % 4) if len(header_b64) % 4 else ''
        header = json.loads(base64.urlsafe_b64decode(header_b64).decode('utf-8'))
        payload_b64 = parts[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4) if len(payload_b64) % 4 else ''
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        return header, payload, parts[2], None
    except Exception as e:
        return None, None, None, f"Failed to decode JWT: {str(e)}"


def get_token_validity(payload):
    if not payload:
        return {"error": "No payload"}
    now = datetime.now(timezone.utc)
    result = {"now_utc": now.isoformat()}
    if 'exp' in payload:
        exp_time = datetime.fromtimestamp(payload['exp'], timezone.utc)
        result['expires_at'] = exp_time.isoformat()
        result['expires_at_local'] = exp_time.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
        remaining = exp_time - now
        result['remaining_seconds'] = remaining.total_seconds()
        if remaining.total_seconds() > 0:
            minutes, seconds = divmod(int(remaining.total_seconds()), 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                result['remaining_human'] = f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                result['remaining_human'] = f"{minutes}m {seconds}s"
            else:
                result['remaining_human'] = f"{seconds}s"
            result['is_valid'] = True
            result['status'] = '✅ VALID'
        else:
            result['remaining_human'] = "EXPIRED"
            result['is_valid'] = False
            result['status'] = '❌ EXPIRED'
    else:
        result['status'] = '⚠️ NO EXPIRY'
    if 'iat' in payload:
        iat_time = datetime.fromtimestamp(payload['iat'], timezone.utc)
        result['issued_at'] = iat_time.isoformat()
        result['age_seconds'] = (now - iat_time).total_seconds()
    return result


def format_payload_html(payload):
    if not payload:
        return "<p>No payload to display</p>"
    rows = []
    for key, value in payload.items():
        if key in ('exp', 'iat', 'nbf') and isinstance(value, (int, float)):
            try:
                dt = datetime.fromtimestamp(value, timezone.utc)
                value_display = f"{value} ({dt.strftime('%Y-%m-%d %H:%M:%S UTC')})"
            except:
                value_display = str(value)
        else:
            value_display = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        rows.append(f'<tr><td><strong>{key}</strong></td><td class="value">{value_display}</td></tr>')
    return f'<table><tr><th>Claim</th><th>Value</th></tr>{"".join(rows)}</table>'


class TokenInspectorHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"[{datetime.now().isoformat()}] {format % args}")

    def send_html_response(self, status, html):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(html.encode())

    def send_json_response(self, status, data):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def do_GET(self):
        if self.path in ('/', '/debug'):
            self.handle_debug()
        elif self.path == '/refresh':
            self.handle_refresh()
        elif self.path == '/status':
            self.handle_status()
        elif self.path == '/health':
            self.send_json_response(200, {"status": "healthy"})
        elif self.path == '/token':
            self.handle_token_json()
        else:
            self.send_json_response(404, {"error": "Not found"})

    def handle_token_json(self):
        container_token = read_fresh_token(CONTAINER_TOKEN_FILE)
        user_token = read_fresh_token(TOKEN_FILE)
        combined = read_fresh_token(COMBINED_TOKEN_FILE)
        self.send_json_response(200, {
            "container_token": container_token,
            "user_token": user_token,
            "combined_token": combined,
            "current_user": read_fresh_token(USER_FILE),
            "jdbc_url": read_fresh_token(SERVICE_JDBC_FILE),
        })

    def handle_refresh(self):
        user = self.headers.get('Sf-Context-Current-User', '')
        token = self.headers.get('Sf-Context-Current-User-Token', '')
        sf_headers = {k: v for k, v in self.headers.items() if k.lower().startswith('sf-')}
        print(f"Snowflake headers received: {list(sf_headers.keys())}")

        if not token:
            self.send_json_response(400, {
                "error": "No user token in headers",
                "hint": "Access this endpoint through the Snowflake ingress URL",
                "headers_received": list(self.headers.keys()),
                "sf_headers": sf_headers,
            })
            return

        with open(TOKEN_FILE, 'w') as f:
            f.write(token)
        os.chmod(TOKEN_FILE, 0o644)

        with open(USER_FILE, 'w') as f:
            f.write(user)
        os.chmod(USER_FILE, 0o644)

        sf_host = os.environ.get('SNOWFLAKE_HOST', '')
        sf_database = os.environ.get('SNOWFLAKE_DATABASE', '')
        sf_schema = os.environ.get('SNOWFLAKE_SCHEMA', '')
        sf_warehouse = os.environ.get('SNOWFLAKE_WAREHOUSE', '')
        sf_role = os.environ.get('SNOWFLAKE_ROLE', '')

        service_token = read_fresh_token(CONTAINER_TOKEN_FILE)
        combined_token = f"{service_token}.{token}" if service_token else token
        encoded_token = url_encode_token(combined_token)

        jdbc_url = f"jdbc:snowflake://{sf_host}/?authenticator=oauth&token={encoded_token}&db={sf_database}&schema={sf_schema}&warehouse={sf_warehouse}"
        if sf_role:
            jdbc_url += f"&role={sf_role}"

        with open(JDBC_FILE, 'w') as f:
            f.write(jdbc_url)
        os.chmod(JDBC_FILE, 0o644)

        with open(COMBINED_TOKEN_FILE, 'w') as f:
            f.write(combined_token)
        os.chmod(COMBINED_TOKEN_FILE, 0o644)

        status = {
            "timestamp": datetime.now().isoformat(),
            "user": user,
            "user_token_length": len(token),
            "service_token_length": len(service_token),
            "combined_token_length": len(combined_token),
        }
        with open(STATUS_FILE, 'w') as f:
            json.dump(status, f, indent=2)

        self.send_json_response(200, {
            "success": True,
            "user": user,
            "token_length": len(token),
            "files": {"token": TOKEN_FILE, "user": USER_FILE, "jdbc_url": JDBC_FILE},
        })

    def handle_status(self):
        try:
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
                status["age_seconds"] = (datetime.now() - datetime.fromisoformat(status["timestamp"])).total_seconds()
                self.send_json_response(200, status)
            else:
                self.send_json_response(200, {"status": "No token captured yet", "hint": "Visit /refresh through Snowflake ingress"})
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})

    def handle_debug(self):
        container_token = read_fresh_token(CONTAINER_TOKEN_FILE)
        user_token = read_fresh_token(TOKEN_FILE)
        current_user = read_fresh_token(USER_FILE)
        combined_token_file = read_fresh_token(COMBINED_TOKEN_FILE)
        service_jdbc_url = read_fresh_token(SERVICE_JDBC_FILE)
        combined_jdbc_url = read_fresh_token(COMBINED_JDBC_FILE)
        user_jdbc_url = read_fresh_token(JDBC_FILE)

        container_header, container_payload, container_sig, container_err = decode_jwt(container_token)
        user_header, user_payload, user_sig, user_err = decode_jwt(user_token)
        container_validity = get_token_validity(container_payload) if container_payload else {"status": "⚠️ NO TOKEN"}
        user_validity = get_token_validity(user_payload) if user_payload else {"status": "⚠️ NO TOKEN"}

        combined_token_live = f"{container_token}.{user_token}" if container_token and user_token else container_token
        combined_token = combined_token_file if combined_token_file else combined_token_live

        sf_host = os.environ.get('SNOWFLAKE_HOST', '')
        sf_database = os.environ.get('SNOWFLAKE_DATABASE', '')
        sf_schema = os.environ.get('SNOWFLAKE_SCHEMA', '')
        sf_warehouse = os.environ.get('SNOWFLAKE_WAREHOUSE', '')
        sf_role = os.environ.get('SNOWFLAKE_ROLE', '')

        encoded_service = url_encode_token(container_token) if container_token else "<URL_ENCODED_SERVICE_TOKEN>"
        encoded_combined = url_encode_token(combined_token) if combined_token else "<URL_ENCODED_COMBINED_TOKEN>"
        snowflake_jdbc_service = f"jdbc:snowflake://{sf_host}/?authenticator=oauth&token={encoded_service}&db={sf_database}&schema={sf_schema}&warehouse={sf_warehouse}&role={sf_role}"
        snowflake_jdbc_combined = f"jdbc:snowflake://{sf_host}/?authenticator=oauth&token={encoded_combined}&db={sf_database}&schema={sf_schema}&warehouse={sf_warehouse}&role={sf_role}"

        snowflake_vars = {k: v for k, v in sorted(os.environ.items()) if 'SNOWFLAKE' in k.upper() or 'SF_' in k.upper()}
        other_vars = {k: v for k, v in sorted(os.environ.items()) if 'SNOWFLAKE' not in k.upper() and 'SF_' not in k.upper()}
        headers = dict(self.headers)
        sf_headers = {k: v for k, v in headers.items() if k.lower().startswith('sf-')}

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>SPCS Token Inspector</title>
    <style>
        body {{ font-family: monospace; background: #1e1e1e; color: #d4d4d4; padding: 20px; }}
        h1 {{ color: #569cd6; }}
        h2 {{ color: #4ec9b0; margin-top: 30px; border-bottom: 1px solid #444; padding-bottom: 5px; }}
        h3 {{ color: #dcdcaa; }}
        .section {{ background: #252526; padding: 15px; border-radius: 5px; margin: 10px 0; overflow-x: auto; }}
        .token {{ background: #0d0d0d; padding: 10px; border: 1px solid #444; word-break: break-all; white-space: pre-wrap; font-size: 11px; }}
        .success {{ color: #4ec9b0; }}
        .warning {{ color: #ce9178; }}
        .error {{ color: #f44747; }}
        table {{ border-collapse: collapse; width: 100%; }}
        td, th {{ border: 1px solid #444; padding: 8px; text-align: left; }}
        th {{ background: #333; color: #569cd6; }}
        .value {{ max-width: 600px; word-break: break-all; }}
        a {{ color: #569cd6; }}
        .btn {{ background: #0e639c; color: white; padding: 10px 20px; border: none; cursor: pointer; margin: 5px; border-radius: 3px; text-decoration: none; display: inline-block; }}
        .btn:hover {{ background: #1177bb; }}
        .countdown {{ font-size: 24px; font-weight: bold; }}
        .jwt-header {{ color: #f44747; }}
        .jwt-payload {{ color: #4ec9b0; }}
        .jwt-signature {{ color: #569cd6; }}
    </style>
    <script>setTimeout(function() {{ location.reload(); }}, 30000);</script>
</head>
<body>
    <h1>❄️ SPCS Token Inspector</h1>
    <p>Timestamp: {datetime.now().isoformat()} &nbsp;|&nbsp; auto-refreshes every 30s</p>

    <div>
        <a class="btn" href="javascript:location.reload()">🔄 Refresh</a>
        <a class="btn" href="/refresh">📥 Capture User Token</a>
        <a class="btn" href="/token">📋 JSON API</a>
        <a class="btn" href="/status">📊 Status</a>
    </div>

    <h2>⏱️ Token Validity</h2>
    <div class="section">
        <table>
            <tr><th>Token</th><th>Status</th><th>Expires At</th><th>Time Remaining</th></tr>
            <tr>
                <td><strong>Container Token</strong><br><small>/snowflake/session/token</small></td>
                <td>{container_validity.get('status', '⚠️ UNKNOWN')}</td>
                <td>{container_validity.get('expires_at_local', 'N/A')}</td>
                <td class="countdown">{container_validity.get('remaining_human', 'N/A')}</td>
            </tr>
            <tr>
                <td><strong>User Token</strong><br><small>Sf-Context-Current-User-Token</small></td>
                <td>{user_validity.get('status', '⚠️ UNKNOWN')}</td>
                <td>{user_validity.get('expires_at_local', 'N/A')}</td>
                <td class="countdown">{user_validity.get('remaining_human', 'N/A')}</td>
            </tr>
        </table>
    </div>

    <h2>🎟️ Container Token</h2>
    <div class="section">
        <p>File: <code>{CONTAINER_TOKEN_FILE}</code> — {'<span class="success">EXISTS</span>' if os.path.exists(CONTAINER_TOKEN_FILE) else '<span class="error">NOT FOUND</span>'} &nbsp;|&nbsp; Length: {len(container_token)} chars</p>
        <div class="token">{container_token if container_token else '(empty)'}</div>
    </div>
    <div class="section">
        {f'<p class="error">Decode Error: {container_err}</p>' if container_err else ''}
        {f'''
        <h3>Header</h3><pre class="token jwt-header">{json.dumps(container_header, indent=2)}</pre>
        <h3>Payload</h3>{format_payload_html(container_payload)}
        <h3>Signature</h3><pre class="token jwt-signature" style="font-size:10px;">{container_sig}</pre>
        ''' if container_header else '<p>No token to decode</p>'}
    </div>

    <h2>👤 User Token</h2>
    <div class="section">
        <p>File: <code>{TOKEN_FILE}</code> — {'<span class="success">EXISTS</span>' if os.path.exists(TOKEN_FILE) else '<span class="warning">NOT CAPTURED YET</span>'} &nbsp;|&nbsp; Length: {len(user_token)} chars</p>
        <p>Current User: <strong>{current_user if current_user else '(not captured)'}</strong></p>
        <div class="token">{user_token if user_token else '(empty — visit /refresh to capture)'}</div>
    </div>
    {f'''<div class="section">
        <h3>Header</h3><pre class="token jwt-header">{json.dumps(user_header, indent=2)}</pre>
        <h3>Payload</h3>{format_payload_html(user_payload)}
    </div>''' if user_header else ''}

    <h2>🔗 Combined Token (Caller&apos;s Rights)</h2>
    <div class="section">
        <p>Format: <code>&lt;service-token&gt;.&lt;user-token&gt;</code> &nbsp;|&nbsp; Length: {len(combined_token)} chars</p>
        <div class="token" style="max-height:200px;overflow-y:auto;">{combined_token if combined_token else '(need both tokens — visit /refresh first)'}</div>
    </div>

    <h2>📋 JDBC URLs</h2>
    <div class="section">
        <h3>Service Token JDBC</h3>
        <p>File: <code>{SERVICE_JDBC_FILE}</code></p>
        <div class="token" style="max-height:100px;overflow-y:auto;">{service_jdbc_url if service_jdbc_url else snowflake_jdbc_service}</div>

        <h3>Combined Token JDBC (Caller&apos;s Rights)</h3>
        <p>File: <code>{COMBINED_JDBC_FILE}</code></p>
        <div class="token" style="max-height:100px;overflow-y:auto;">{combined_jdbc_url if combined_jdbc_url else snowflake_jdbc_combined}</div>
    </div>

    <h2>📨 Snowflake Request Headers</h2>
    <div class="section">
        <table>
            <tr><th>Header</th><th>Value</th></tr>
            {''.join(f'<tr><td>{k}</td><td class="value">{v}</td></tr>' for k, v in sf_headers.items()) if sf_headers else '<tr><td colspan="2" class="warning">No Sf-* headers. Make sure executeAsCaller: true is set.</td></tr>'}
        </table>
        <details style="margin-top:10px;"><summary>All request headers</summary>
        <table><tr><th>Header</th><th>Value</th></tr>
        {''.join(f'<tr><td>{k}</td><td class="value">{v}</td></tr>' for k, v in sorted(headers.items()))}
        </table></details>
    </div>

    <h2>❄️ Snowflake Environment</h2>
    <div class="section">
        <table><tr><th>Variable</th><th>Value</th></tr>
        {''.join(f'<tr><td>{k}</td><td class="value">{v}</td></tr>' for k, v in snowflake_vars.items()) if snowflake_vars else '<tr><td colspan="2">No SNOWFLAKE_* variables found</td></tr>'}
        </table>
    </div>

    <details>
        <summary style="color:#4ec9b0;cursor:pointer;margin-top:20px;">📋 All Other Environment Variables</summary>
        <div class="section">
            <table><tr><th>Variable</th><th>Value</th></tr>
            {''.join(f'<tr><td>{k}</td><td class="value">{v}</td></tr>' for k, v in other_vars.items())}
            </table>
        </div>
    </details>

    <h2>📁 Token Files</h2>
    <div class="section">
        <table><tr><th>File</th><th>Exists</th><th>Size</th></tr>
        {''.join(f'<tr><td><code>{f}</code></td><td>{"✅" if os.path.exists(f) else "❌"}</td><td>{os.path.getsize(f) if os.path.exists(f) else "-"} bytes</td></tr>' for f in [CONTAINER_TOKEN_FILE, TOKEN_FILE, USER_FILE, JDBC_FILE, COMBINED_TOKEN_FILE, SERVICE_JDBC_FILE])}
        </table>
    </div>

    <h2>🔗 Endpoints</h2>
    <div class="section">
        <ul>
            <li><a href="/">/</a> — Token inspector UI (this page)</li>
            <li><a href="/refresh">/refresh</a> — Capture user token from Snowflake ingress headers</li>
            <li><a href="/token">/token</a> — JSON: all tokens</li>
            <li><a href="/status">/status</a> — JSON: token capture status</li>
            <li><a href="/health">/health</a> — Health check</li>
        </ul>
    </div>
</body>
</html>"""
        self.send_html_response(200, html)


def main():
    port = int(os.environ.get('TOKEN_SERVER_PORT', '8081'))
    server = HTTPServer(('0.0.0.0', port), TokenInspectorHandler)
    print("=" * 60)
    print(f"❄️  SPCS Token Inspector starting on port {port}")
    print("=" * 60)
    print("Endpoints:")
    print("  GET /        - Token inspector UI")
    print("  GET /refresh - Capture user token from Sf-* headers")
    print("  GET /token   - JSON: all tokens")
    print("  GET /status  - JSON: token capture status")
    print("  GET /health  - Health check")
    print("=" * 60)
    server.serve_forever()


if __name__ == '__main__':
    main()
