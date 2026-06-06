# Caddy Reverse Proxy

Caddy is the simplest option -- it provides automatic HTTPS via Let's Encrypt with zero configuration.

## Basic Caddyfile

```
gsm.example.com {
    reverse_proxy localhost:8443
}
```

That is the entire configuration. Caddy will:

- Obtain and renew TLS certificates automatically
- Proxy all HTTP and WebSocket traffic
- Redirect HTTP to HTTPS

## With Custom Options

```
gsm.example.com {
    reverse_proxy localhost:8443 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }

    log {
        output file /var/log/caddy/gsm.log
    }
}
```

## Docker Compose Integration

```yaml
services:
  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config

volumes:
  caddy_data:
  caddy_config:
```

## Notes

- Caddy handles WebSocket proxying automatically (no special configuration needed).
- Ensure your domain's DNS A record points to the server's public IP.
- Caddy stores certificates in its data volume -- persist it to avoid re-issuance on restart.
