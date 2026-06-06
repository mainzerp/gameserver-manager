# Traefik Reverse Proxy

Traefik is a Docker-native reverse proxy that configures itself via container labels.

## Docker Compose with Traefik Labels

```yaml
services:
  traefik:
    image: traefik:v3
    command:
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.httpchallenge=true"
      - "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web"
      - "--certificatesresolvers.letsencrypt.acme.email=admin@example.com"
      - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik_letsencrypt:/letsencrypt

  gameserver-manager:
    build: .
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.gsm.rule=Host(`gsm.example.com`)"
      - "traefik.http.routers.gsm.entrypoints=websecure"
      - "traefik.http.routers.gsm.tls.certresolver=letsencrypt"
      - "traefik.http.services.gsm.loadbalancer.server.port=8443"
      # HTTP to HTTPS redirect
      - "traefik.http.routers.gsm-http.rule=Host(`gsm.example.com`)"
      - "traefik.http.routers.gsm-http.entrypoints=web"
      - "traefik.http.routers.gsm-http.middlewares=https-redirect"
      - "traefik.http.middlewares.https-redirect.redirectscheme.scheme=https"

volumes:
  traefik_letsencrypt:
```

## Notes

- Traefik v2+ supports WebSocket proxying by default (no special configuration needed).
- TLS certificates are managed automatically via the Let's Encrypt ACME resolver.
- The Docker socket mount (`/var/run/docker.sock`) allows Traefik to discover containers automatically.
- Game server ports (25565, 27015, etc.) should be exposed directly on the host, not through Traefik.
