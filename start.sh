#!/bin/bash
set -e

echo "=================================================="
echo "SPCS Token Inspector"
echo "=================================================="

echo "Snowflake Environment:"
echo "  SNOWFLAKE_HOST      = ${SNOWFLAKE_HOST:-<not set>}"
echo "  SNOWFLAKE_ACCOUNT   = ${SNOWFLAKE_ACCOUNT:-<not set>}"
echo "  SNOWFLAKE_DATABASE  = ${SNOWFLAKE_DATABASE:-<not set>}"
echo "  SNOWFLAKE_SCHEMA    = ${SNOWFLAKE_SCHEMA:-<not set>}"
echo "  SNOWFLAKE_WAREHOUSE = ${SNOWFLAKE_WAREHOUSE:-<not set>}"
echo "  SNOWFLAKE_ROLE      = ${SNOWFLAKE_ROLE:-<not set>}"
echo ""

if [ -f "/snowflake/session/token" ]; then
    echo "✅ Container token found at /snowflake/session/token"
else
    echo "⚠️  No container token found (expected when running outside SPCS)"
fi
echo ""

echo "Starting token refresh daemon..."
chmod +x /app/token_refresh_daemon.sh
/app/token_refresh_daemon.sh &

echo "Starting token inspector on port ${TOKEN_SERVER_PORT:-8081}..."
exec python3 /app/token_server.py
