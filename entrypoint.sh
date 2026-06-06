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

exec "$@"
