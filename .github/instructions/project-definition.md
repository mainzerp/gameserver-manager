# GameServer Manager - Project Definition

A web-based game server management panel for Minecraft (Java/Bedrock) and Steam game servers. Built with Python 3.12 and FastAPI, providing a fully async architecture with server-side rendered HTML pages and a REST API. Conceptually similar to Crafty Controller.

---

## Tech Stack

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.12 | Runtime |
| FastAPI | 0.115.6 | Async web framework |
| Uvicorn | 0.34.0 | ASGI server |
| SQLAlchemy | 2.0.36 | Async ORM (with asyncio extra) |
| asyncpg | 0.29+ | Async PostgreSQL driver (primary) |
| aiosqlite | 0.20.0 | Async SQLite driver (alternative) |
| Alembic | 1.13+ | Database migrations |
| Jinja2 | 3.1.5 | Server-side HTML template engine |
| Tailwind CSS | local build | Utility-first CSS (dark theme) |
| Pydantic | 2.10.4 | Data validation |
| pydantic-settings | 2.7.1 | Settings management from env vars |
| httpx | 0.28.1 | Async HTTP client (Modrinth, CurseForge, JAR downloads) |
| APScheduler | 3.10.4 | Periodic task scheduling |
| websockets | 14.1 | WebSocket protocol support |
| passlib + bcrypt | latest | Password hashing |
| pyotp + qrcode | latest | TOTP 2FA |
| Babel | 2.14+ | Internationalization (i18n) |
| psutil | 5.9+ | System resource monitoring |
| cryptography | 42+ | Encryption for sensitive data |
| PostgreSQL | 17 | Primary database |
| Docker / docker-compose | - | Containerization |

---

## Architecture

Monolithic MVC-like async web application with server-side rendering and a REST API layer.

```
User Browser
    |
    v
FastAPI (app/main.py)
    |
    +-- Middleware (SessionMiddleware, CSRFMiddleware)
    |
    +-- Auth Router (/login, /setup, /logout)
    |
    +-- Web Routers (servers, mods, files, backups, scheduler, users, etc.)
    |       |
    |       +-- Templates (Jinja2 HTML) -- Server-side rendered
    |       |
    |       +-- Services (30 service modules)
    |               |
    |               +-- Models (13 SQLAlchemy models)
    |                       |
    |                       +-- PostgreSQL / SQLite (via Alembic migrations)
    |
    +-- REST API v1 (/api/v1/) -- JSON endpoints with API key auth
    |
    +-- WebSocket (/ws/console/{id}) -- Real-time server console
    |
    +-- Static Files (/static/) -- CSS, JS, icons, PWA manifest
    |
    +-- Metrics (/metrics) -- Prometheus/OpenMetrics
```

**Key Design Decisions:**

- Fully async I/O (asyncpg/aiosqlite, httpx, asyncio subprocess)
- Server-side rendering with Jinja2 + Tailwind CSS (no SPA, no frontend build step required)
- Singleton services for shared state (ServerManager, ModUpdater, etc.)
- Form-based mutations with POST + redirect (PRG pattern, 303 status)
- Session-based authentication with CSRF protection
- Role-based access control (admin/user roles with per-server permissions)
- Game servers run as child processes (or Docker containers when isolation is enabled)
- Circular log buffer (500 lines per server)
- Alembic for database migrations (no auto-create)
- Multi-database support (PostgreSQL, SQLite, MySQL)

---

## Directory Structure

```
gameserver/
|-- .env.example                  # Template for environment variables
|-- .gitignore                    # Git ignore rules
|-- alembic.ini                   # Alembic migration configuration
|-- babel.cfg                     # Babel i18n extraction config
|-- docker-compose.yml            # Docker Compose (app + PostgreSQL 17)
|-- Dockerfile                    # Multi-Java Docker image (JDK 8, 17, 21, 25)
|-- main.py                       # Application entry point (uvicorn runner)
|-- package.json                  # Tailwind CSS build tooling
|-- README.md                     # Project documentation
|-- requirements.txt              # Python dependencies
|-- tailwind.config.js            # Tailwind CSS configuration
|-- TODO.md                       # Completed roadmap (all 58 items done)
|-- VERSION.md                    # Version history and changelog
|
|-- .github/
|   |-- copilot-instructions.md   # Copilot agent configuration
|   |-- instructions/
|       |-- project-definition.md # This file
|
|-- alembic/
|   |-- env.py                    # Alembic environment configuration
|   |-- script.py.mako            # Migration template
|   |-- versions/                 # 18 migration files (initial through batch 7)
|
|-- app/
|   |-- __init__.py
|   |-- config.py                 # Pydantic Settings (50+ configuration fields)
|   |-- database.py               # SQLAlchemy async engine and session setup
|   |-- i18n.py                   # Internationalization setup (Babel)
|   |-- main.py                   # FastAPI app, lifespan, middleware, router registration
|   |-- template_utils.py         # Jinja2 template helpers
|   |-- validation.py             # Input validation utilities
|   |
|   |-- middleware/
|   |   |-- csrf.py               # CSRF protection middleware
|   |
|   |-- models/                   # 13 SQLAlchemy ORM models
|   |   |-- api_key.py            # API key model
|   |   |-- audit_log.py          # Audit log entries
|   |   |-- backup.py             # Backup records
|   |   |-- metric.py             # Resource metrics (CPU, RAM, disk)
|   |   |-- mod.py                # Installed mods per server
|   |   |-- node.py               # Multi-node panel nodes
|   |   |-- scheduled_task.py     # Cron-like scheduled tasks
|   |   |-- server.py             # Server model + ServerType/ServerStatus enums
|   |   |-- server_access.py      # Per-server user access permissions
|   |   |-- site_settings.py      # Site-wide settings (key-value)
|   |   |-- user.py               # User model (auth, RBAC, TOTP)
|   |   |-- webauthn_credential.py # WebAuthn passkey credentials
|   |   |-- webhook.py            # Custom webhook definitions
|   |
|   |-- routers/                  # 16 web routers + API v1 sub-routers
|   |   |-- api_keys.py           # API key management UI
|   |   |-- audit.py              # Audit log viewer
|   |   |-- auth.py               # Login, logout, setup, 2FA
|   |   |-- backups.py            # Backup management UI
|   |   |-- files.py              # File browser + editor
|   |   |-- metrics.py            # Prometheus metrics endpoint
|   |   |-- mods.py               # Mod management (search, install, update)
|   |   |-- nodes.py              # Multi-node management UI
|   |   |-- scheduler.py          # Scheduled task management UI
|   |   |-- servers.py            # Server CRUD + controls (dashboard)
|   |   |-- site_settings.py      # Admin settings panel
|   |   |-- status.py             # Public status page
|   |   |-- users.py              # User management (admin)
|   |   |-- webhooks.py           # Webhook management UI
|   |   |-- ws.py                 # WebSocket console endpoint
|   |   |-- api_v1/               # REST API v1 (JSON)
|   |       |-- __init__.py       # API router aggregation + API key auth
|   |       |-- servers.py        # Server API endpoints
|   |       |-- backups.py        # Backup API endpoints
|   |       |-- schedules.py      # Schedule API endpoints
|   |       |-- system.py         # System status API endpoints
|   |       |-- versions.py       # MC version API endpoints
|   |
|   |-- schemas/                  # Pydantic request/response schemas
|   |   |-- backup.py
|   |   |-- common.py
|   |   |-- schedule.py
|   |   |-- server.py
|   |   |-- system.py
|   |
|   |-- services/                 # 30 service modules
|   |   |-- api_key_service.py    # API key generation and validation
|   |   |-- audit_service.py      # Audit log recording and cleanup
|   |   |-- auth.py               # Authentication, session management, RBAC
|   |   |-- backup_manager.py     # Backup creation, restoration, rotation
|   |   |-- docker_manager.py     # Docker container lifecycle management
|   |   |-- email_service.py      # SMTP email notifications
|   |   |-- jar_downloader.py     # MC server JAR download (Vanilla/Fabric/Paper/Forge/NeoForge/Quilt)
|   |   |-- java_manager.py       # Java version detection + MC version mapping
|   |   |-- log_manager.py        # Log persistence and search
|   |   |-- mod_updater.py        # Modrinth + CurseForge mod management
|   |   |-- node_manager.py       # Multi-node registration and health checks
|   |   |-- notification_service.py # Discord/webhook notification dispatch
|   |   |-- player_manager.py     # Online player list, whitelist, bans
|   |   |-- port_manager.py       # Port allocation and conflict detection
|   |   |-- query_protocol.py     # Minecraft SLP + Steam A2S query protocols
|   |   |-- rcon_client.py        # RCON remote console client
|   |   |-- resource_monitor.py   # CPU/RAM/disk metric collection
|   |   |-- server_detector.py    # Detect existing server installations for import
|   |   |-- server_manager.py     # Process management (start/stop/command/logs)
|   |   |-- server_templates.py   # Server quick-setup presets
|   |   |-- server_updater.py     # Automatic server version update checking
|   |   |-- settings_service.py   # Site settings persistence
|   |   |-- sftp_server.py        # SFTP file access server
|   |   |-- status_service.py     # Public status page data
|   |   |-- steamcmd.py           # SteamCMD wrapper (install/update Steam servers)
|   |   |-- task_scheduler.py     # Cron-like task scheduling engine
|   |   |-- update_checker.py     # Panel self-update checker
|   |   |-- version_cache.py      # MC version caching
|   |   |-- webauthn_service.py   # WebAuthn passkey registration/verification
|   |   |-- world_manager.py      # Minecraft world management
|   |
|   |-- static/                   # Static assets
|   |   |-- css/                  # Compiled Tailwind CSS
|   |   |-- js/                   # Client-side JavaScript
|   |   |-- icons/                # App icons
|   |   |-- manifest.json         # PWA manifest
|   |   |-- offline.html          # PWA offline fallback
|   |
|   |-- templates/                # 26 Jinja2 HTML templates
|       |-- base.html             # Base layout (dark theme, nav, i18n)
|       |-- dashboard.html        # Server list with status cards
|       |-- login.html            # Login page
|       |-- setup.html            # Initial admin setup
|       |-- server_create.html    # Server creation form
|       |-- server_detail.html    # Server detail + console
|       |-- server_import.html    # Import existing servers
|       |-- mods.html             # Mod management table
|       |-- mod_search.html       # Mod search interface
|       |-- file_browser.html     # Directory listing
|       |-- file_editor.html      # Text file editor
|       |-- backups.html          # Backup management
|       |-- scheduler.html        # Scheduled tasks
|       |-- users.html            # User management
|       |-- user_access.html      # Per-server user access
|       |-- api_keys.html         # API key management
|       |-- audit_log.html        # Audit log viewer
|       |-- webhooks.html         # Webhook management
|       |-- nodes.html            # Multi-node management
|       |-- site_settings.html    # Admin settings panel
|       |-- status.html           # Public status page
|       |-- player_management.html # Player whitelist/bans
|       |-- totp_setup.html       # TOTP 2FA setup
|       |-- totp_verify.html      # TOTP verification
|       |-- webauthn_register.html # WebAuthn passkey registration
|
|-- data/
|   |-- backups/                  # Server backup archives
|
|-- docs/
|   |-- database-backends.md      # PostgreSQL/SQLite/MySQL setup guide
|   |-- reverse-proxy/            # Nginx, Caddy, Traefik config examples
|
|-- servers/                      # Game server installation directories (gitignored)
|
|-- translations/                 # i18n translation files
    |-- de/                       # German translations
    |-- en/                       # English translations
```

---

## Database

### Setup

- **Primary**: PostgreSQL 17 via asyncpg (default in docker-compose)
- **Alternative**: SQLite via aiosqlite, MySQL via aiomysql
- **Migrations**: Alembic (18 migration files, from initial schema through batch 7)
- **Session factory**: `async_sessionmaker` with `expire_on_commit=False`
- **Base class**: `DeclarativeBase` (SQLAlchemy 2.0 style)

### Models (13 total)

| Model | Table | Key Fields |
|---|---|---|
| Server | `servers` | name, server_type, status, path, executable, port, mc_version, loader, docker fields |
| Mod | `mods` | server_id (FK), name, source, project_id, version tracking, auto_update |
| User | `users` | username, email, hashed_password, role (admin/user), totp_secret, is_active |
| Backup | `backups` | server_id (FK), filename, size, type (manual/scheduled), status |
| ScheduledTask | `scheduled_tasks` | server_id (FK), task_type, cron expression, enabled |
| ApiKey | `api_keys` | user_id (FK), key_hash, name, permissions, expires_at |
| AuditLog | `audit_logs` | user_id (FK), action, target_type, target_id, details, ip_address |
| Metric | `metrics` | server_id (FK), cpu_percent, memory_mb, disk_mb, player_count |
| Webhook | `webhooks` | name, url, secret, events, active |
| WebAuthnCredential | `webauthn_credentials` | user_id (FK), credential_id, public_key, sign_count |
| Node | `nodes` | name, url, api_key, status, is_local |
| ServerAccess | `server_access` | user_id (FK), server_id (FK), permission level |
| SiteSetting | `site_settings` | key, value (key-value store for runtime config) |

---

## Routers (16 + API v1)

### Web Routers

| Router | Prefix | Purpose |
|---|---|---|
| auth | `/` | Login, logout, setup, TOTP 2FA, locale switching |
| servers | `/` | Dashboard, server CRUD, start/stop/restart, console commands |
| mods | `/servers/{id}/mods` | Mod search, install, update, delete (Modrinth + CurseForge) |
| files | `/servers/{id}/files` | File browser, editor, upload, download, rename, delete, archive ops |
| ws | `/ws/` | WebSocket console endpoint |
| backups | `/servers/{id}/backups` | Backup create, restore, delete, download |
| scheduler | `/scheduler` | Scheduled task CRUD |
| api_keys | `/api-keys` | API key management |
| status | `/status` | Public status page |
| audit | `/audit` | Audit log viewer |
| users | `/users` | User management (admin) + access control |
| webhooks | `/webhooks` | Webhook management |
| metrics | `/metrics` | Prometheus/OpenMetrics endpoint |
| nodes | `/nodes` | Multi-node management |
| site_settings | `/settings` | Admin settings panel (runtime config) |

### REST API v1 (JSON)

| Module | Prefix | Purpose |
|---|---|---|
| servers | `/api/v1/servers` | Server CRUD and control |
| backups | `/api/v1/backups` | Backup operations |
| schedules | `/api/v1/schedules` | Schedule management |
| system | `/api/v1/system` | System status and info |
| versions | `/api/v1/versions` | MC version listing |

Authentication: API key via `X-API-Key` header.

---

## Services (30 modules)

| Service | Pattern | Purpose |
|---|---|---|
| server_manager | Singleton | Process lifecycle (start/stop/restart/command), log buffering |
| mod_updater | Singleton | Modrinth + CurseForge API, mod install/update/remove |
| resource_monitor | Singleton | CPU/RAM/disk metric collection per server |
| task_scheduler | Singleton | Cron-like scheduled task execution |
| audit_service | Singleton | Audit log recording with 90-day cleanup |
| update_checker | Singleton | Panel version update checking |
| sftp_manager | Singleton | Optional SFTP server for file access |
| docker_manager | Singleton | Docker container lifecycle for server isolation |
| server_updater | Singleton | Automatic server version update checking |
| node_manager | Singleton | Multi-node registration and health monitoring |
| settings_service | Module | Site settings load/save from database |
| backup_manager | Module | Backup creation, restoration, rotation |
| notification_service | Module | Discord/webhook/email notification dispatch |
| email_service | Module | SMTP email sending |
| auth | Module | Authentication, session management, RBAC enforcement |
| api_key_service | Module | API key generation, hashing, validation |
| log_manager | Module | Log persistence and search |
| server_detector | Module | Detect existing server installations for import |
| server_templates | Module | Server quick-setup presets |
| port_manager | Module | Port allocation and conflict detection |
| world_manager | Module | Minecraft world management |
| player_manager | Module | Online player list, whitelist, bans |
| query_protocol | Module | Minecraft SLP + Steam A2S query protocols |
| rcon_client | Module | RCON remote console |
| status_service | Module | Public status page data aggregation |
| version_cache | Module | MC version list caching |
| webauthn_service | Module | WebAuthn passkey registration/verification |
| jar_downloader | Module | MC server JAR download (all loaders) |
| java_manager | Module | Java version detection + MC version mapping |
| steamcmd | Module | SteamCMD wrapper for Steam game servers |

---

## Configuration

Uses `pydantic-settings` with `.env` file support. All environment variables prefixed with `GSM_`. Over 50 configuration fields covering: core settings, database, SSL, monitoring, backups, notifications, Docker isolation, SFTP, multi-node, TOTP, WebAuthn, and email.

See `app/config.py` for the complete `Settings` class and `.env.example` for all available options.

Many settings can also be configured at runtime through the admin settings panel at `/settings/`.

---

## Scheduled Jobs (Lifespan)

| Job | Interval | Purpose |
|---|---|---|
| mod_update_check | Configurable (default 60min) | Check Modrinth/CurseForge for mod updates |
| metric_collection | Configurable (default 30s) | Collect CPU/RAM/disk metrics per server |
| metric_cleanup | 24 hours | Remove metrics older than retention period |
| audit_cleanup | 24 hours | Remove audit logs older than 90 days |
| update_check | Configurable (default 24h) | Check for panel updates (if enabled) |
| server_update_check | 6 hours | Check for Minecraft/Steam server updates |
| node_health_check | 1 minute | Check multi-node health (if enabled) |

---

## Docker

`docker-compose.yml` defines two services:
- `gameserver-manager`: The application (Python 3.12 + Java 8/17/21/25)
- `db`: PostgreSQL 17 Alpine

The Dockerfile generates a self-signed TLS certificate for HTTPS out of the box.

Volumes: `./data` (database, backups), `./servers` (game server files), `pgdata` (PostgreSQL data).
