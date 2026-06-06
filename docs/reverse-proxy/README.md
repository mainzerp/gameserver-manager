# Reverse Proxy Configuration

When deploying GameServer Manager in production, it is recommended to place it behind a reverse proxy for SSL termination, caching, and security hardening.

## Overview

| Proxy | Complexity | Auto-HTTPS | Docker-Native | Best For |
|-------|-----------|------------|---------------|----------|
| **nginx** | Medium | No (manual cert) | No | Traditional deployments |
| **Caddy** | Low | Yes (built-in) | No | Quick setup, auto-HTTPS |
| **Traefik** | Medium | Yes (ACME) | Yes (labels) | Docker/Kubernetes |

## Important Notes

- **Only the web panel port** (default `8443`) needs to be proxied.
- **Game server ports** (e.g., 25565 for Minecraft, 27015 for Steam) are accessed directly by game clients and should NOT be proxied.
- Run Uvicorn with `--proxy-headers` or ensure `ProxyHeadersMiddleware` is configured so that `X-Forwarded-For` and `X-Forwarded-Proto` headers are respected.
- Set `GSM_SSL_ENABLED=false` when using a reverse proxy for SSL termination (the proxy handles TLS, not Uvicorn).

## Guides

- [nginx](nginx.md) -- Traditional reverse proxy with manual SSL configuration
- [Caddy](caddy.md) -- Simplest setup with automatic HTTPS
- [Traefik](traefik.md) -- Docker-native reverse proxy with label-based configuration
