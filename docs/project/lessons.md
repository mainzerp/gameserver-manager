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
