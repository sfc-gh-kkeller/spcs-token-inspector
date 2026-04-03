# SPCS Token Inspector

A lightweight debug sidecar for Snowflake Container Services (SPCS) that exposes
Snowflake OAuth tokens via a web UI — useful for any SPCS service that needs to
inspect, copy, or forward tokens and JDBC URLs.

## What It Does

- **Token inspector UI** — shows container token and user token with JWT decode, validity countdown, and copy-ready JDBC URLs
- **User token capture** — captures `Sf-Context-Current-User-Token` from Snowflake ingress headers (requires `executeAsCaller: true`)
- **Token refresh daemon** — keeps `/tmp/snowflake_jdbc_url.txt` up to date as Snowflake rotates the container session token
- **JSON API** — `/token` endpoint returns all tokens as JSON for programmatic access

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Token inspector UI |
| `GET /refresh` | Capture user token from Snowflake ingress headers |
| `GET /token` | JSON: all current tokens |
| `GET /status` | JSON: last token capture status |
| `GET /health` | Health check |

## Token Files

| File | Contents |
|---|---|
| `/snowflake/session/token` | Container service token (auto-refreshed by Snowflake) |
| `/tmp/sf_user_token.txt` | User token (captured via `/refresh`) |
| `/tmp/sf_combined_token.txt` | Combined token for caller's rights |
| `/tmp/snowflake_jdbc_url.txt` | Ready-to-use Snowflake JDBC URL (kept fresh by daemon) |
| `/tmp/sf_combined_jdbc_url.txt` | JDBC URL using combined token |

## Quick Start

### Add as a sidecar to your SPCS service spec

```yaml
spec:
  containers:
    - name: your-app
      image: /your/repo/your-app:latest
      # ... your app config ...

    - name: token-inspector
      image: /your/repo/spcs-token-inspector:latest
      env:
        - name: TOKEN_SERVER_PORT
          value: "8081"

  endpoints:
    - name: your-app
      port: 8080
      public: true
    - name: token-inspector
      port: 8081
      public: true
```

### Build and push

```bash
docker build -t spcs-token-inspector .
docker tag spcs-token-inspector <your-snowflake-repo>/spcs-token-inspector:latest
docker push <your-snowflake-repo>/spcs-token-inspector:latest
```

### Run locally

```bash
docker run -p 8081:8081 spcs-token-inspector
# Open http://localhost:8081
```

## Token Capture (Caller's Rights)

To capture the user's identity token from Snowflake ingress:

1. Set `executeAsCaller: true` in your service spec
2. Open the token inspector UI through the SPCS ingress URL
3. Click **Capture User Token** (or visit `/refresh`)
4. The combined token (`service-token.user-token`) is now available in `/tmp/sf_combined_token.txt`

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TOKEN_SERVER_PORT` | `8081` | HTTP port for the inspector UI |
| `SNOWFLAKE_HOST` | auto-set by SPCS | Used to build JDBC URLs |
| `SNOWFLAKE_DATABASE` | auto-set by SPCS | Default database |
| `SNOWFLAKE_SCHEMA` | auto-set by SPCS | Default schema |
| `SNOWFLAKE_WAREHOUSE` | — | Warehouse for JDBC URL |
| `SNOWFLAKE_ROLE` | — | Role for JDBC URL |
