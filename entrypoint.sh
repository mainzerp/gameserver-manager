#!/bin/sh
set -e

if [ ! -f /app/certs/cert.pem ] || [ ! -f /app/certs/key.pem ]; then
    echo "Generating self-signed TLS certificate..."
    mkdir -p /app/certs
    openssl req -x509 -newkey rsa:4096 -keyout /app/certs/key.pem -out /app/certs/cert.pem \
        -days 3650 -nodes -subj "/CN=gameserver-manager" \
        -addext "subjectAltName=DNS:localhost,DNS:gameserver-manager,IP:127.0.0.1"
    echo "Certificate generated."
else
    echo "Using existing TLS certificate."
fi

# Enable SSL by default when the generated certificate is present and SSL is not explicitly disabled.
if [ -f /app/certs/cert.pem ] && [ -f /app/certs/key.pem ] && [ "${GSM_SSL_ENABLED:-}" != "false" ]; then
    export GSM_SSL_ENABLED=${GSM_SSL_ENABLED:-1}
    export GSM_SSL_CERTFILE=${GSM_SSL_CERTFILE:-/app/certs/cert.pem}
    export GSM_SSL_KEYFILE=${GSM_SSL_KEYFILE:-/app/certs/key.pem}
fi

exec "$@"
