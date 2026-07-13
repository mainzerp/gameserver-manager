# Version History

## Current Version: 2.7.6

### v2.7.6 -- Header Badge Visibility Fix

**Fixed:**
- The `Steam Operation...` header badge was always visible because the `.badge` CSS rule (`display: inline-flex`) was overriding Tailwind's `.hidden` rule. Switched to the HTML `hidden` attribute for the badge so it is correctly hidden when no SteamCMD operation is active.

### v2.7.5 -- Tab Switching Fix

**Fixed:**
- The `Steam` tab was missing from the client-side tab list in `server_detail.html`, causing the Steam panel to remain visible when switching to other tabs (e.g. Settings, Scheduler, Workshop, Backups, Files). Added `steam` to `allTabs` so the Steam tab now switches correctly.

### v2.7.4 -- Steam Operation UI Improvements

**Changed:**
- The server detail page now shows active SteamCMD operations (install, update, validate, update & start, workshop install/update) in the existing SteamCMD panel and via a new header badge.
- The **Start** button is disabled while any SteamCMD operation is active, so users cannot start a server while it is still installing.
- Added `steam_operation_active` to the server detail template context and generalized the Steam operation JavaScript to handle all operation types, not only `update_start`.

**Fixed:**
- Pre-flight executable check from v2.7.3 is still in place.

### v2.7.3 -- Pre-flight Executable Check

**Fixes:**
- Added a pre-flight check in `server_manager.start_server` that verifies the server executable exists before launching the subprocess. This prevents a confusing `[Errno 2] No such file or directory` error when a Steam server is started while SteamCMD installation is still in progress, and returns a clear message instead.

### v2.7.2 -- PostgreSQL Enum Migration Fix

**Fixes:**
- Fixed `b13_01_query_port` migration to use uppercase enum literal `'STEAM'` instead of lowercase `'steam'` for PostgreSQL, matching how SQLAlchemy stores enum member names.
- Made the container HTTPS host port configurable via `GSM_HOST_PORT` in `.env` (defaults to 8443) so deployments can avoid Windows reserved port ranges.
- Added `host_port` to `app.config` so `GSM_HOST_PORT` in `.env` does not trigger Pydantic validation errors.

### v2.7.1 -- TemplateResponse Deprecation Fix

**Fixes:**
- Updated all 37 `templates.TemplateResponse(...)` calls across `app/routers/` to the current Starlette signature (`request` as first positional argument, removed from context dict). This eliminates the deprecation warnings previously emitted on every test run and template render.

### v2.7.0 -- SteamCMD Backend & UX Improvements

**Features and Improvements:**
- Added dedicated Steam tab in server detail for Steam servers, consolidating Steam Information, SteamCMD settings, A2S_INFO status, and SteamCMD activity log
- SteamCMD operations now show a persistent activity log with timestamped events in the new Steam tab
- Added A2S_INFO server query integration to display live Steam server status (name, map, players, VAC) via the API v1 Steam status endpoint
- Added Steam Guard TOTP auto-generation: encrypted TOTP shared secret storage and automatic 5-digit Steam Guard code generation for SteamCMD operations
- Added API v1 Steam endpoints for update, validate, Steam Guard submission, and status
- Added basic Steam RCON support so server commands are sent via RCON when enabled for Steam servers
- Added query port tracking and conflict detection for Steam servers, with a configurable query port per server
- Workshop items can now be previewed before adding via the Steam Web API on both the server detail Workshop tab and the full Workshop page
- Updated SteamCMD to use the server's configured query port in generated launch commands
- Added API v1 workshop preview endpoint and expanded test coverage for SteamCMD, A2S_INFO, API v1 Steam, and Steam RCON

**Fixes:**
- Fixed SteamCMD launch command for Enshrouded (app id 1604030) to include the correct server executable name
- Steam account credentials are now encrypted at rest and decrypted only when needed for SteamCMD operations
- Alembic migration path updated to include the steam_account model for future migration generation

### v2.6.2 -- Steam RAM Settings Edit

**Fixes:**
- Existing SteamCMD servers can now update their stored min/max RAM values directly from the Settings tab without going through Minecraft-only startup forms
- The Steam Settings view keeps RAM editing isolated from unrelated JVM and Minecraft startup fields

### v2.6.1 -- Steam Detail Metrics UI Fix

**Fixes:**
- Steam server detail pages now read the nested `/servers/{id}/stats` payload correctly, so live CPU and RAM usage render again in Resource Monitoring
- Steam server Settings no longer show the irrelevant Java section; Java-specific details remain limited to Minecraft Java servers

### v2.6.0 -- Per-Server GMod GSLT Support

**Features and Improvements:**
- Steam server settings now support an encrypted per-server Garry's Mod GSLT without storing the token in the persisted start command
- Garry's Mod app `4020` now receives `+sv_setsteamaccount <token>` only at launch time when a server-scoped token is configured
- Existing SteamCMD account selection, authenticated login, branch handling, and update-on-start behavior remain unchanged
- Added focused regression coverage for launch-time injection, token-safe settings persistence, and the narrow server detail UI

### v2.5.8 -- Steam Runtime libstdc++ Fix

**Fixes:**
- Docker images now install `lib32stdc++6`, the missing 32-bit GNU C++ runtime required by Source-based Steam servers
- Live container verification confirmed the real `srcds_run` path no longer fails on missing `libstdc++.so.6`

### v2.5.7 -- Steam Completion Snapshot Reload Fix

**Fixes:**
- Steam server detail pages now keep the intended one-time reload for live `update_start` completion events while ignoring replayed completed `snapshot` payloads after reconnect
- Steam panel completed-state rendering and Steam WebSocket payload contracts remain unchanged; only the client-side reload gate was narrowed

### v2.5.6 -- Steam Idle Snapshot Hardening

**Fixes:**
- Steam server detail pages now keep the Steam progress panel visible for the current page session when a later reconnect delivers an unexpected `snapshot` with `status: idle` after meaningful Steam work has already been shown
- First-load idle pages still stay hidden until meaningful Steam activity is observed, while existing live WebSocket updates and update-on-start completion reload behavior remain unchanged

### v2.5.5 -- Steam Start Progress First-Paint Fix

**Fixes:**
- Steam server detail pages now seed an active `update_start` snapshot into the initial render so progress is visible on first paint without waiting for the WebSocket snapshot
- The generic 5-second `starting` reload is no longer armed when the seeded snapshot already proves a Steam update-before-start is active, while live WebSocket updates and completion reload behavior remain unchanged

### v2.5.4 -- Steam Start Progress Reload Fix

**Fixes:**
- Steam server detail pages now keep the existing 5-second startup auto-reload cancelable so active `update_start` progress can continue without page flicker
- Auto-reload is only suppressed after the browser confirms an active Steam `update_start` state, preserving normal startup reload behavior for other flows

### v2.5.3 -- Steam Create/Start Handoff Fix

**Fixes:**
- Steam servers with update-on-start now queue the update-and-start work in the existing Steam background operation flow instead of blocking the HTTP start request
- Steam detail pages surface the Steam progress panel near the top of the page so queued create/install and start/update work is visible immediately after redirect
- Added regression coverage for queued Steam start behavior, background update-before-start ordering, unattended failure handling, and WebSocket snapshot compatibility for the reused Steam progress flow

### v2.5.2 -- Steam Detail Load Path Fix

**Fixes:**
- Steam server detail HTML no longer performs a blocking render-time update/build lookup
- Steam update checks continue to run through the existing async `/servers/{id}/update-check` route
- Added regression coverage to prove detail-page GET avoids `server_updater.check_update` while the async update-check endpoint still invokes it

### v2.5.1 -- Server Tab Visibility Fix

**Fixes:**
- Steam server detail pages no longer render the Mods tab or panel
- Minecraft server detail pages continue to render Mods but keep Workshop hidden
- Server detail tab initialization now validates requested and remembered tabs against the tabs actually rendered for that server type

### v2.5.0 -- Steam Operations Completion

**Features and Improvements:**
- SteamCMD operations now expose structured install, update, validate, and workshop progress over WebSocket with reconnect-safe snapshots
- Interactive Steam Guard handling is available for browser-driven Steam operations via one-time code submission without persisting guard codes
- Manual Steam update, validate, and workshop actions now run asynchronously so the UI can follow live progress instead of blocking on POST requests
- Workshop items now enrich name, description, size, and update metadata from the Steam Web API when a Steam API key is configured
- Guarded Steam accounts now fail fast in unattended contexts such as scheduler jobs and update-on-start instead of hanging on Steam Guard prompts
- Added automated regression coverage for Steam service state transitions, metadata normalization, route queuing, and WebSocket event delivery

**Database:**
- Workshop metadata `file_size` now uses a 64-bit integer to safely store large workshop item sizes
- Alembic migration: `b11_03_workshop_file_size_bigint`

### v2.4.2 -- Backup Size Overflow Fix

**Fixes:**
- Backup metadata now stores `size_bytes` as a 64-bit integer so large Steam server backups no longer overflow during update workflows
- Steam end-to-end validation completed with a real Steam test server after resetting the admin login

---

### v2.4.1 -- SteamCMD Admin Completion Patch

**Fixes and Improvements:**
- Steam account selection is now persisted on Steam servers and can be used for authenticated SteamCMD operations
- Steam branch selection is honored during install, update, validate, and remote build checks
- Scheduled Steam update/validate tasks now call the SteamCMD service with the correct arguments
- SteamCMD progress WebSocket subscriptions use the correct queue-based interface
- Workshop page route/template context mismatch fixed
- Steam server detail view prepared for editing Steam-specific settings after creation
- Steam update status fields aligned between backend and template rendering

---

### v2.4.0 -- SteamCMD Integration

**Features:**
- Full SteamCMD integration for installing and managing Steam dedicated game servers
- Auto-download and bootstrap of SteamCMD on application startup (configurable)
- Steam game server installation with progress tracking via WebSocket
- Server update and file validation via SteamCMD
- Steam Workshop item management (add, install, update, remove)
- Steam account management with encrypted credential storage (Fernet encryption)
- Pre-built server templates for CS2, Valheim, Rust, Palworld, 7 Days to Die, Satisfactory
- Update-on-start option: automatically run SteamCMD update before each server start
- Build ID tracking with remote build ID comparison for update detection
- Branch selection support (public, beta, etc.)
- Scheduled task types: STEAM_UPDATE and STEAM_VALIDATE
- WebSocket endpoint for real-time SteamCMD operation progress
- SteamCMD configuration tab in Site Settings with status indicator and install button
- Enhanced dashboard with SteamCMD status banner and install action
- Workshop tab in server detail view with inline item management
- Standalone workshop management page
- Docker support: SteamCMD auto-installed in container with dedicated volume

**Database:**
- New tables: steam_accounts, workshop_items
- New server columns: steam_build_id, steam_branch, steam_login_anonymous, steam_account_id, steam_update_on_start, steam_last_update
- New scheduled task types: steam_update, steam_validate
- Alembic migration: b11_01_steamcmd_integration

**Configuration:**
- GSM_STEAMCMD_PATH: Path to SteamCMD executable
- GSM_STEAMCMD_AUTO_INSTALL: Auto-download SteamCMD on startup (default: true)
- GSM_STEAMCMD_INSTALL_DIR: Directory for SteamCMD installation
- GSM_STEAM_API_KEY: Steam Web API key for metadata lookups

**Translations:**
- English and German translations for all SteamCMD UI strings

---

### v2.3.1 -- Security Hardening (Code Review)

**HIGH Severity Fixes:**
- Always refuse insecure secret key, even in debug mode (removed debug-mode bypass)
- Restrict `forwarded_allow_ips` to trusted proxy via `GSM_TRUSTED_PROXY` env var (default `127.0.0.1`)
- `POST /servers/import`: require admin role; restrict import path to configured servers directory
- `POST /servers/detect`: require admin role; tighten path restriction to `servers_dir` (not parent)
- SFTP access restricted to admin users only
- `start_server` protected by per-server async lock to prevent duplicate-start race condition
- Shell injection prevention: `_build_command` uses `shlex.split` instead of `bash -c`

**MEDIUM Severity Fixes:**
- ZIP extraction: real decompressed byte counting (ZIP bomb mitigation) and resolved-path traversal check (M1+M2)
- Login rate limiter prunes stale IPs to prevent memory leak under distributed attack
- TOTP rate limiter prunes stale per-user entries to prevent accumulation
- `ResourceMonitor.clear_process_cache` added; called from `_watch_process` on server exit
- CSP `script-src` removes `unsafe-inline`; per-request nonce generated and stored in `request.state.csp_nonce`
- Property injection prevention in `generate_default_properties` (strips `\r`/`\n` from values)
- Modrinth conflict detection uses single bulk `GET /versions` instead of N+1 serial requests

**LOW Severity Fixes:**
- RCON errors no longer leak internal host/port details to API response
- WebSocket auth checked before `accept()` to avoid consuming connection slots
- Telegram notification raises on HTTP errors so failures surface in logs
- Node `proxy_command` validates `api_url` scheme to mitigate SSRF
- `asyncio.get_event_loop()` replaced with `asyncio.get_running_loop()` in `init_db`
- SFTP module uses shared `pwd_context` from `app.services.auth` for password verification
- File uploads streamed in 64 KB chunks instead of reading entire file into memory
- Backup manifest logs a warning when a file cannot be read

---

### v2.3.0 -- ZIP Server Upload

**Features:**
- New `GET /servers/upload-zip` and `POST /servers/upload-zip` endpoints
- Upload a `.zip` archive via the UI; it is extracted and registered as a new server
- Magic-byte validation + 10 GB size cap on compressed upload
- Path traversal protection via existing `_extract_archive` helper
- Auto-detection of server type, loader, and executable from extracted content
- Audit log entry on successful upload (`server.upload_zip`)
- "Upload ZIP" card added to the server creation page

---

### v2.2.0 -- Production Readiness

**Security:**
- Extended secret key validation to reject all known placeholder values (C-2)
- Added security headers middleware: HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy (H-5)
- Removed PostgreSQL port 5432 exposure from docker-compose.yml (M-2)
- Removed test admin credentials from repository; added to .gitignore (C-3)

**Infrastructure:**
- Built Tailwind CSS locally via standalone CLI; removed CDN dependency from all templates (H-1)
- Vendored Chart.js, CodeMirror (all modes), Google Fonts (Inter + JetBrains Mono) for offline use (H-2)
- Docker container now runs as non-root user 'gsm' (H-3)
- Added /health endpoint for lightweight Docker healthchecks (H-4)
- Healthcheck updated in Dockerfile and docker-compose.yml to use /health
- Added trusted proxy support (proxy_headers) for rate limiting behind reverse proxies (M-3)
- Docker-compose PostgreSQL and secret key now use required env variables (C-1, C-2)

---

### v2.0.1 -- Security & Bug Fix Patch

Deep code review: 29 fixes across 14 files.

**Critical Fixes:**
- Startup validation refusing default secret key in non-debug mode
- Shell injection prevention in Docker command execution (shlex.quote)
- WebSocket console RBAC authorization check added
- proxy_command argument order fix for multi-node support
- API key revocation ownership validation

**High Fixes:**
- Authorization checks added to world reset/switch endpoints
- Backup restore/delete IDOR fix (backup-to-server ownership validation)
- Open redirect prevention in set_locale
- Session fixation prevention (session cleared on login)
- Prometheus /metrics endpoint authentication
- detect_server path restricted to servers directory
- Streaming chunked upload with incremental size check
- Duplicate DB query eliminated in user deletion
- get_current_user accepts optional DB session to avoid pool exhaustion
- Login rate limiting (10 attempts / 5 min per IP)

**Medium Fixes:**
- Path traversal check upgraded to Path.is_relative_to()
- Scheduler task creation validates server existence
- LogManager write_line made async via asyncio.to_thread
- Username validation (alphanumeric + underscore/hyphen/dot only)
- TOTP rate limiting (5 attempts / 5 min)
- SSRF protection on webhook test endpoint
- Version string centralized in Settings
- Player name sanitization in ban command
- Fresh DB session per server in auto-update checker
- CSRF middleware body read limited to 10MB

### v2.0.0 -- Complete Feature Set

All 58 roadmap items implemented across 7 development batches.

**Server Management:**
Server CRUD, start/stop/restart, auto-start on boot, server import, server cloning, server templates, auto server updates, per-server environment variables, Docker container isolation.

**Mod Management:**
Modrinth search/install/auto-update, CurseForge integration, SpigotMC/Hangar plugin sources, mod conflict detection, mod dependency resolution, loader version tracking.

**Player Management:**
Online player list (Minecraft SLP + Steam A2S), whitelist management, ban management.

**File Management:**
Web file browser and editor, drag-and-drop upload, download, rename, delete, directory creation, archive operations (zip/unzip), file search, SFTP access.

**Monitoring and Logging:**
Real-time resource monitoring (CPU, RAM, disk), performance graphs, Prometheus/OpenMetrics endpoint, audit log with 90-day retention.

**User System:**
User authentication with sessions, TOTP two-factor authentication, WebAuthn passkey authentication, role-based access control (RBAC) with per-server permissions.

**Notifications:**
Discord webhook notifications, custom webhooks with HMAC signing, email notifications via SMTP.

**Infrastructure:**
REST API v1 with API key authentication, OpenAPI/Swagger documentation, scheduled task system, public status page, panel update checker, PWA support, multi-language (English, German), light/dark theme toggle, HTTPS/SSL, CSRF protection, reverse proxy documentation, multi-node panel support, PostgreSQL/SQLite/MySQL backends, Alembic database migrations.

### v1.0.0 -- Initial Release

FastAPI application scaffold, async SQLAlchemy + aiosqlite, Jinja2 + Tailwind CSS dark theme, WebSocket real-time console, Modrinth mod management, SteamCMD integration, Java auto-detection (JDK 8/17/21), file browser and editor.

### v2.1.0 -- Feature Expansion

22 new features across 6 batches.

**Batch 1 - Quick Wins:**
- A3 JVM Flags Editor: Custom JVM flags and server args with Aikar preset
- A9 Startup Readiness Detection: STARTING status with log pattern matching
- B4 Scheduler Improvements: Conditions (only_running/only_stopped/always), run-now, result tracking

**Batch 2 - Server Management:**
- A2 Config Editor: Typed server.properties editor with raw mode
- A4 Bedrock Download: Automatic BDS download and setup for Minecraft Bedrock
- A5 Uptime Schedule: Scheduled start/stop times with day selection and shutdown warnings

**Batch 3 - UI/UX:**
- C1 Dashboard: Status summary bar, filter/sort, quick-action buttons
- C3 Server Detail: Info bar with uptime, copy address, recent events
- C5 File Browser: CodeMirror syntax highlighting, bulk operations, file download
- A7 Console Colors: ANSI/log-level color parsing, level filters

**Batch 4 - Backup/Status/Notifications:**
- B1 Backup: Incremental/config-only types, retention policy, compression toggle
- B5 Status Page: Uptime display, grouped by status, JSON API, auto-refresh
- C4 Notifications: Telegram bot integration, per-server notification preferences

**Batch 5 - Advanced:**
- A10 Modpack Import: .mrpack file upload and Modrinth URL import
- C9 Java Auto-Download: Adoptium Temurin JDK auto-download and management
- C7 Mod Improvements: Compatibility checks, mod profiles (Modrinth only)

**Batch 6 - Extras:**
- A6 Invite Links: Shareable codes with role, max uses, expiry
- A8 Port Reachability Check: TCP connectivity test from server detail
- C2 Bulk Operations: Multi-select dashboard for bulk start/stop/restart/backup
- C6 Console Improvements: Clear, download logs, saved commands, tab completion
- C8 Backup to External Storage: Copy to external path (NAS/mounted drive)
- C10 Config Import/Export: JSON export/import of server configuration

**Other:**
- Crash Auto-Restart with configurable retry, delay, and stability window
- CurseForge support removed (no free API keys)

## Recent Changes (since v2.7.6)

None yet.
