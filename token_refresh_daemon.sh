#!/bin/bash
# =====================================================
# Token Refresh Daemon
# =====================================================
# Runs in background to keep token files updated every 5 minutes
# Snowflake refreshes /snowflake/session/token automatically
# Each token is valid for up to 1 hour
#
# FILES UPDATED (use in NiFi Expression Language):
#   /tmp/snowflake_token.txt       - Raw token
#   /tmp/snowflake_token_encoded.txt - URL-encoded token for JDBC
#   /tmp/snowflake_jdbc_url.txt    - Complete JDBC URL
#   /tmp/snowflake_env.sh          - Source this in shell for env vars
#
# NiFi Expression Language examples:
#   ${file('/tmp/snowflake_token.txt'):trim()}
#   ${file('/tmp/snowflake_jdbc_url.txt'):trim()}

REFRESH_INTERVAL=${TOKEN_REFRESH_INTERVAL:-300}  # Every 5 minutes (300 seconds)

# URL-encode a string (handles +, /, =, :)
url_encode() {
    python3 -c "import urllib.parse; print(urllib.parse.quote('''$1''', safe=''))"
}

echo "🔄 Token Refresh Daemon starting..."
echo "   Refresh interval: ${REFRESH_INTERVAL}s (5 min)"
echo "   Token source: /snowflake/session/token"
echo ""
echo "   Files updated:"
echo "     /tmp/snowflake_token.txt         - Raw token"
echo "     /tmp/snowflake_token_encoded.txt - URL-encoded token"
echo "     /tmp/snowflake_jdbc_url.txt      - Complete JDBC URL"
echo "     /tmp/snowflake_env.sh            - Source for shell env vars"
echo ""

last_token_hash=""
update_count=0

while true; do
    if [ -f "/snowflake/session/token" ]; then
        # Read current token
        current_token=$(cat /snowflake/session/token | tr -d '\n\r')
        current_hash=$(echo -n "$current_token" | md5sum | cut -d' ' -f1)
        
        # Check if token changed OR first run
        if [ "$current_hash" != "$last_token_hash" ]; then
            update_count=$((update_count + 1))
            echo "[$(date -Iseconds)] Token update #${update_count}..."
            
            # 1. Raw token file
            echo "${current_token}" > /tmp/snowflake_token.txt
            chmod 644 /tmp/snowflake_token.txt
            
            # 2. URL-encoded token (for building custom JDBC URLs)
            encoded_token=$(url_encode "$current_token")
            echo "${encoded_token}" > /tmp/snowflake_token_encoded.txt
            chmod 644 /tmp/snowflake_token_encoded.txt
            
            # 3. Complete JDBC URL
            if [ -n "$SNOWFLAKE_HOST" ]; then
                jdbc_url="jdbc:snowflake://${SNOWFLAKE_HOST}/?authenticator=oauth&token=${encoded_token}&db=${SNOWFLAKE_DATABASE}&schema=${SNOWFLAKE_SCHEMA}&warehouse=${SNOWFLAKE_WAREHOUSE}"
                if [ -n "$SNOWFLAKE_ROLE" ]; then
                    jdbc_url="${jdbc_url}&role=${SNOWFLAKE_ROLE}"
                fi
                echo "${jdbc_url}" > /tmp/snowflake_jdbc_url.txt
                chmod 644 /tmp/snowflake_jdbc_url.txt
            fi
            
            # 4. Shell-sourceable env file (for new shell sessions)
            cat > /tmp/snowflake_env.sh << EOF
# Snowflake Token Environment - Updated $(date -Iseconds)
# Source this file: source /tmp/snowflake_env.sh
export SNOWFLAKE_TOKEN='${current_token}'
export SNOWFLAKE_TOKEN_ENCODED='${encoded_token}'
export SNOWFLAKE_JDBC_URL='${jdbc_url}'
EOF
            chmod 644 /tmp/snowflake_env.sh
            
            # 5. Update combined token if user token exists
            if [ -f "/tmp/sf_user_token.txt" ]; then
                user_token=$(cat /tmp/sf_user_token.txt | tr -d '\n\r')
                combined="${current_token}.${user_token}"
                echo "${combined}" > /tmp/sf_combined_token.txt
                
                encoded_combined=$(url_encode "$combined")
                echo "${encoded_combined}" > /tmp/sf_combined_token_encoded.txt
                
                if [ -n "$SNOWFLAKE_HOST" ]; then
                    combined_jdbc="jdbc:snowflake://${SNOWFLAKE_HOST}/?authenticator=oauth&token=${encoded_combined}&db=${SNOWFLAKE_DATABASE}&schema=${SNOWFLAKE_SCHEMA}&warehouse=${SNOWFLAKE_WAREHOUSE}"
                    if [ -n "$SNOWFLAKE_ROLE" ]; then
                        combined_jdbc="${combined_jdbc}&role=${SNOWFLAKE_ROLE}"
                    fi
                    echo "${combined_jdbc}" > /tmp/sf_combined_jdbc_url.txt
                fi
                chmod 644 /tmp/sf_combined_token.txt /tmp/sf_combined_token_encoded.txt /tmp/sf_combined_jdbc_url.txt 2>/dev/null
                echo "   ✓ Combined token updated"
            fi
            
            last_token_hash="$current_hash"
            echo "   ✓ All files updated (hash: ${current_hash:0:8}...)"
            echo "   ✓ Next check in ${REFRESH_INTERVAL}s"
        fi
    fi
    
    sleep "$REFRESH_INTERVAL"
done

