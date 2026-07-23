# Lessons Learned

Accumulated context, lessons learned, and recurring patterns for working on GameServer Manager.

Append new learnings at the end of each session so they persist across conversations.

## Session 2026-07-15

### Container & Deployment

- The local container runs on **port 9000** via the `GSM_HOST_PORT` environment variable (default is 8443).
- Health checks use `https://localhost:8443/health` inside the container; the host port is mapped separately.

### Git & Ignore Rules

- `.gitignore` contains `/servers/` to ignore the root `servers/` data directory, but this must **not** match `app/routers/servers/`. Use root-scoped patterns (`/servers/`) carefully.
- The `app/routers/servers/` package was once accidentally untracked because of broad `.gitignore` rules; keep the rule scoped.

### Server Query & Telemetry

- Minecraft Java player telemetry uses **Server List Ping (SLP)** on the **game port**; it does not require `enable-query`.
- `enable-query` in Minecraft controls the older GameSpy4 UDP query protocol. For out-of-the-box compatibility, set `enable-query=true` and `query.port=<game-port>` in `server.properties`.
- Steam player telemetry uses the **A2S_INFO** query on the configured `query_port`. The default is `game_port + 1` (e.g., 27016 for game port 27015).
- Palworld (`steam_app_id=2394010`) requires an authenticated Steam account and does not work with anonymous SteamCMD login.

### UI / README

- GitHub README badge URLs must use the exact repository name. `gameserver` is not the same as `gameserver-manager`.
- Screenshots belong under `docs/screenshots/`; this directory needs a `.gitignore` exception because generic `screenshots/` patterns are ignored.
- Using `<h1 align="center"><font color="...">` for colored README titles may be stripped by GitHub's sanitizer; an image-based wordmark is more reliable if exact color is required.

### Project Documentation

- Agent behavior rules live in `AGENTS.md`.
- Project-specific architectural docs, learnings, and definitions belong under `docs/project/`:
  - `docs/project/prime-directives.md`
  - `docs/project/lessons.md`
  - `docs/project/project-definition.md`

## Session 2026-07-23

### UI / Templates

- Standalone (pre-auth) pages do NOT share a CSS file: `login.html`, `setup.html`, `status.html` each carry their own full inline `<style>` block with the design tokens. Restyling one means copying the block; extracting shared static CSS would deviate from the established pattern.
- `base.html`'s global `data-action` click delegation only exists inside the app shell; standalone pages must attach their own `addEventListener` handlers inside a `nonce="{{ request.state.csp_nonce }}"` script.
- `base.html` defines only `title`, `head`, `content`, `scripts` Jinja blocks; templates that declare `{% block breadcrumb %}` render it nowhere (dead markup).
- The sidebar in `base.html` renders unconditionally, so any template extending `base.html` during a pre-auth or pending-2FA session shows a broken shell (use a standalone template instead).

### 2FA

- `POST /settings/2fa/disable` existed and was PRG-compliant long before any UI linked to it; the security page gained the enrolled-state deactivate form only in v2.12.2.
- `GET /settings/2fa/setup` previously generated a fresh unpersisted TOTP secret/QR on every visit regardless of enrollment state; it now branches on `user.totp_enabled` and the template receives `totp_enabled` in every render context.

### i18n

- After editing `gsm/translations/*/LC_MESSAGES/messages.po`, the `.mo` files must be regenerated with `pybabel compile -d gsm/translations` (run from repo root) or new msgids silently render as English.
- `docs/SubAgent/` artifacts are ephemeral (gitignored); verification commands: `ruff check gsm/app gsm/main.py` and `python -m pytest gsm/tests/ -q`.

## Session 2026-07-23 (STEAM_AUTH_LOGIN)

### Steam

- `login_required` in `STEAM_APPS` was dead metadata since introduction (declared in all 12 entries, never consumed); it is now consumed by the create/edit form warnings via `data-login-required` option attributes.
- `_run_process` failure messages keep their tail (`message[-400:]`), so a hint appended to the end of the message survives truncation; prepended hints would be cut off.
- `_()` (gettext) is wired only for Jinja templates; Python service modules have no i18n infrastructure, so SteamCMD service strings (e.g. the install-failure hint) stay hardcoded English.
- `get_remote_build_id_for_branch` was hard-coded to anonymous login, so update checks for auth-only apps (e.g. Palworld) failed silently; it now accepts optional credential kwargs (keyword-only) with anonymous defaults for backward compatibility.
- `run_workshop_install` forwarded `login_anonymous`/`username`/`password` from `get_server_install_kwargs` but dropped `steam_guard_code`, losing TOTP pre-seeding on the workshop path.
- Page-load-safe form defaults: `toggleFields()` on the create page calls `onSteamAppChange()` during `DOMContentLoaded`, so any auto-mutation of user fields (e.g. unchecking anonymous login) must live in a `change` event listener, not in the shared handler, or error re-renders lose repopulated `form_values`.
