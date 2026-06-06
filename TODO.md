# GameServer Manager - TODO

This is the prioritized roadmap for the GameServer Manager project. Items are grouped by category and sorted by priority within each group. Use checkboxes to track progress. Items marked **(PARTIAL)** have placeholder code or incomplete implementations already in the codebase.

## Priority Legend

| Tag | Meaning |
|-----|---------|
| `[CRITICAL]` | Security and stability essentials; must be done before any public or shared use |
| `[HIGH]` | Core functionality gaps that users expect from any game server panel |
| `[MEDIUM]` | Competitive parity features with Crafty Controller and similar tools |
| `[LOW]` | Polish and advanced features for long-term improvement |
| `[NICE]` | Extra polish; implement only when all higher priorities are addressed |

---

## Security & Authentication

- [x] **[CRITICAL]** User authentication (login/sessions) -- Implement login page, password hashing (Argon2/bcrypt), and session management. Currently zero access control.
- [x] **[CRITICAL]** HTTPS/SSL support -- Add TLS configuration to Uvicorn or document reverse proxy setup. All traffic is currently plaintext.
- [x] **[CRITICAL]** CSRF protection -- Add CSRF tokens to all HTML forms. Currently vulnerable to cross-site request forgery.
- [x] **[CRITICAL]** Input validation and error handling -- Add robust validation on server creation, file operations, and all form inputs. Minimal validation exists but is not comprehensive.
- [x] **[HIGH]** Role-based access control (RBAC) -- Multi-user permissions system with per-server access control. Depends on authentication.
- [x] **[HIGH]** Two-factor authentication (TOTP) -- Add TOTP-based 2FA for user accounts. Depends on authentication.
- [x] **[LOW]** Audit log -- Track user actions (who did what and when). Depends on authentication and RBAC.
- [x] **[NICE]** Passkey/WebAuthn authentication -- Modern passwordless authentication as an alternative to password login.

## Database & Migrations

- [x] **[CRITICAL]** Database migration system (Alembic) -- Schema changes currently require manual DB recreation with data loss risk. Add Alembic for versioned migrations.
- [x] **[LOW]** PostgreSQL/MySQL backend support -- Alternative to SQLite for larger multi-server deployments with concurrent write requirements.

## Server Management

- [x] **[CRITICAL]** Graceful process management -- Panel crash kills all game servers. Add process recovery, PID tracking, and graceful shutdown per game type. Minecraft "stop" works but Steam servers need different commands.
- [x] **[HIGH]** Auto-start on panel boot -- The `auto_start` field exists in the DB but is never read at startup. Wire it up to start servers when the panel launches.
- [x] **[HIGH]** Automatic server updates (JAR/Steam) -- Check for and apply game server updates including new JAR versions and SteamCMD updates.
- [x] **[HIGH]** Proper log persistence and search -- Replace the 500-line volatile circular buffer with persistent log files, rotation, and search capability.
- [x] **[MEDIUM]** Server import (existing installations) -- Allow importing a pre-existing game server directory into the panel without recreating from scratch.
- [x] **[MEDIUM]** Server templates/presets -- Quick-setup profiles for common server configurations (e.g., survival Minecraft, modded Fabric).
- [x] **[MEDIUM]** Forge/NeoForge/Quilt auto-download -- Extend the JAR downloader to support these loaders. Currently requires manual JAR placement.
- [x] **[MEDIUM]** Environment variables per server -- Allow setting custom environment variables for each server process.
- [x] **[MEDIUM]** Minecraft whitelist/ban management -- UI for managing whitelist.json and banned-players.json without editing files manually.
- [x] **[MEDIUM]** Online player list -- Query game servers and display currently connected players on the server detail page.
- [x] **[MEDIUM]** RCON client support -- Built-in RCON client for sending remote commands to supported game servers.
- [x] **[LOW]** Port allocation management -- Automatic port assignment and conflict detection across all managed servers.
- [x] **[LOW]** World management (Minecraft) -- Support multiple worlds per server with world reset and regeneration options.
- [x] **[NICE]** Server cloning -- Duplicate a server's configuration and files into a new instance.

## Backup & Restore

- [x] **[HIGH]** Server backup creation and restore -- Create and restore full server backups as zip archives of the server directory.
- [x] **[MEDIUM]** Scheduled backups with rotation -- Automated backup scheduling (cron-like) with configurable retention and cleanup of old backups.

## File Management

- [x] **[HIGH]** File upload and download -- Upload files to and download files from the server file browser.
- [x] **[HIGH]** File and folder deletion -- Delete files and directories from the file browser UI.
- [x] **[HIGH]** File and folder rename -- Rename files and directories in the file browser.
- [x] **[HIGH]** Directory creation -- Create new folders from the file browser UI.
- [x] **[MEDIUM]** Archive operations (zip/unzip) -- Compress and extract archives directly in the file browser.
- [x] **[LOW]** SFTP access per server -- Provide SFTP endpoints for direct file access outside the web UI.
- [x] **[NICE]** Drag-and-drop file upload -- Enhanced upload UX with drag-and-drop support in the file browser.
- [x] **[NICE]** File search -- Search file contents across a server's directory tree.

## Mod & Plugin Management

- [x] **[HIGH]** CurseForge integration -- The `curseforge_api_key` config field exists but no API implementation. Add CurseForge mod search and install.
- [x] **[LOW]** Mod dependency resolution -- Automatically detect and install required mod dependencies when installing a mod.
- [x] **[LOW]** SpigotMC/Hangar plugin sources -- Add SpigotMC and Hangar as additional plugin and mod sources beyond Modrinth.
- [x] **[LOW]** Mod conflict detection -- Warn when installed mods have known incompatibilities.

## Scheduling & Automation

- [x] **[HIGH]** Task scheduler (cron-like) -- Schedule server start/stop/restart, console commands, and backups on a recurring basis.

## Monitoring & Metrics

- [x] **[HIGH]** Resource monitoring (CPU/RAM/Disk) -- Display per-server resource usage on the dashboard and server detail pages.
- [x] **[MEDIUM]** Performance graphs (historical) -- CPU and RAM usage charts over time using stored metrics data.
- [x] **[NICE]** Prometheus/OpenMetrics endpoint -- Expose server metrics for external monitoring and alerting tools.

## Notifications & Integrations

- [x] **[MEDIUM]** Discord webhook notifications -- Send alerts on server start, stop, crash, and update events to Discord channels.
- [x] **[MEDIUM]** Custom webhooks -- Generic webhook support for arbitrary endpoints and third-party integrations.
- [x] **[NICE]** Email notifications -- Server event notifications via SMTP email delivery.

## REST API

- [x] **[HIGH]** REST API (JSON endpoints) -- Add JSON API endpoints parallel to existing HTML routes. Currently all endpoints return HTML.
- [x] **[HIGH]** API key authentication -- Secure API access with API keys. Depends on the authentication system.
- [x] **[NICE]** API documentation (OpenAPI/Swagger) -- Auto-generated interactive API docs via FastAPI's built-in OpenAPI support.

## UI & UX

- [x] **[MEDIUM]** Light/dark theme toggle -- Currently dark theme only. Add a theme switcher with persistent user preference.
- [x] **[MEDIUM]** Console command history -- Up/down arrow key recall for previously sent console commands in the WebSocket terminal.
- [x] **[MEDIUM]** Multi-language support (i18n) -- Internationalization framework for all UI strings to support multiple languages.
- [x] **[LOW]** Tailwind local build (offline CSS) -- Replace CDN dependency with a locally built CSS bundle to remove the internet requirement.
- [x] **[LOW]** Public status page -- Player-facing page showing server status and player counts without requiring login.
- [x] **[NICE]** Progressive Web App (PWA) -- Installable web app with offline indicators and push notification support.

## DevOps & Infrastructure

- [x] **[LOW]** Docker container isolation per server -- Run each game server in its own Docker container for process isolation and resource limits.
- [x] **[LOW]** Automatic panel self-updates -- Check for and apply panel updates from the repository.
- [x] **[LOW]** Reverse proxy documentation -- Provide nginx, Caddy, and Traefik configuration examples for production deployment.
- [x] **[NICE]** Multi-node support -- Manage game servers across multiple machines from a single panel instance.

## SteamCMD Follow-Up

- [x] **[HIGH]** Complete authenticated Steam account execution including Steam Guard handling -- Interactive Steam Guard challenges now pause browser-driven Steam operations and resume after one-time code submission, while unattended guarded flows fail fast.
- [x] **[HIGH]** Honor selected Steam branches in real SteamCMD operations -- Branch selection is now passed through install/update/build-check flows instead of being UI-only.
- [x] **[HIGH]** Fix Steam scheduled tasks integration -- Steam scheduler update/validate tasks now call the current SteamCMD service interface correctly.
- [x] **[HIGH]** Add frontend SteamCMD progress WebSocket consumption -- Server detail and workshop views now consume the SteamCMD progress socket, replay current state, reconnect automatically, and surface Steam Guard prompts.
- [x] **[HIGH]** Fix workshop page context handling -- Workshop routes and templates now agree on the item context used for rendering.
- [x] **[HIGH]** Improve workshop metadata enrichment -- Workshop items now refresh title, description, size, and update timestamps from the Steam Web API when metadata is available.
- [x] **[MEDIUM]** Add Steam settings editing for existing servers -- Existing Steam servers can now be updated from the server detail page.
- [x] **[MEDIUM]** Add regression coverage for Steam create/update/validate/workshop paths -- Steam service state transitions, route queuing, workshop metadata normalization, and WebSocket delivery now have automated regression coverage.
