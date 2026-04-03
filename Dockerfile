FROM python:3.12-slim

WORKDIR /app

COPY token_server.py .
COPY token_refresh_daemon.sh .
COPY start.sh .

RUN chmod +x start.sh token_refresh_daemon.sh \
    && apt-get update \
    && apt-get install -y --no-install-recommends bash procps \
    && rm -rf /var/lib/apt/lists/*

ENV TOKEN_SERVER_PORT=8081

EXPOSE 8081

CMD ["/app/start.sh"]
