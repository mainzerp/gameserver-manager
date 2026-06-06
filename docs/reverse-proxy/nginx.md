# nginx Reverse Proxy

## Basic HTTP Reverse Proxy

```nginx
server {
    listen 80;
    server_name gsm.example.com;

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8443;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## SSL Termination with Let's Encrypt

```nginx
server {
    listen 443 ssl http2;
    server_name gsm.example.com;

    ssl_certificate /etc/letsencrypt/live/gsm.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gsm.example.com/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8443;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name gsm.example.com;
    return 301 https://$host$request_uri;
}
```

## Recommended Headers

- `X-Real-IP` -- Client's real IP address
- `X-Forwarded-For` -- Full proxy chain
- `X-Forwarded-Proto` -- Original protocol (http/https)
- `Host` -- Original Host header

## WebSocket Support

The `/ws/` location block is required for the live server console. Without `proxy_http_version 1.1` and the `Upgrade`/`Connection` headers, WebSocket connections will fail.
