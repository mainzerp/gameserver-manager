# GameServer Manager - Agent Instructions

> **PROJECT DEFINITION: `.github/instructions/project-definition.md` contains project information (tech stack, architecture, layout). Read it for a detailed overview.**

## Project Identity

GameServer Manager is a web-based game-server management panel for Minecraft (Java/Bedrock) and Steam game servers. Built with:

- **Python 3.12** (deployment target, per `Dockerfile`)
- **FastAPI** with async architecture (Uvicorn ASGI server)
- **SQLAlchemy 2.0 async** ORM (PostgreSQL primary, SQLite/MySQL alternatives)
- **Alembic** for database migrations
- **Jinja2** server-side rendered HTML templates with **Tailwind CSS** (local build, dark theme)
- **WebSocket** for real-time console and SteamCMD progress

## Codebase Layout

```
gameserver/
|-- app/
|   |-- config.py              # Pydantic Settings (env-prefixed GSM_)
|   |-- database.py            # Async engine, session factory, Base, get_db, init_db
|   |-- main.py                # FastAPI app: lifespan, middleware, router registration
|   |-- models/                # 17 SQLAlchemy ORM models
|   |-- routers/               # 17 web routers + api_v1/ (REST API v1)
|   |-- services/              # 37 service modules (singletons + module-level)
|   |-- schemas/               # Pydantic request/response schemas
|   |-- templates/             # Jinja2 HTML templates
|   |-- static/                # Compiled CSS, JS, icons, PWA manifest
|   |-- middleware/            # CSRF protection middleware
|   |-- utils/                 # Security utilities (SSRF prevention, etc.)
|-- alembic/versions/          # 32 migration files
|-- tests/                     # unittest-style + pytest, SQLite in-memory
|-- Dockerfile                 # python:3.12-slim + Java 8/17/21/25
|-- requirements.txt           # Runtime dependencies
|-- VERSION.md                 # Version history and changelog
|-- main.py                    # Entry point (uvicorn runner)
|-- package.json               # Tailwind CSS build tooling
```

## Commands

```sh
# Run the application
python main.py

# Run tests
python -m pytest tests/ -q

# Lint
ruff check app main.py

# Build CSS (after modifying Tailwind classes in templates)
npm run css:build
```

Tests use an in-memory SQLite database. Env overrides are set at the top of each test file via `os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")`. New tests may use shared helpers in `tests/conftest.py`.

## Version Tracking

This project uses **Semantic Versioning (SemVer)**: `MAJOR.MINOR.PATCH`.

| Version Part | When to Increment | Examples |
|--------------|-------------------|----------|
| **MAJOR** (X.0.0) | Breaking changes that require user action | Incompatible API changes, migrations that break rollback, UI workflow changes |
| **MINOR** (1.X.0) | New features, backward-compatible | New server types, new services, new UI pages, new integrations |
| **PATCH** (1.0.X) | Bug fixes, small improvements | Bug fixes, performance optimizations, documentation updates, translation fixes |

**Version sources (keep all in sync):**

- `VERSION.md` line 3: `## Current Version: X.Y.Z`
- `app/__init__.py`: `__version__ = "X.Y.Z"`
- `app/config.py`: `version: str = __version__` (reads from `app.__init__`)

When releasing a version:

- [ ] Bump the version in `VERSION.md` (line 3).
- [ ] Update `app/__init__.py` `__version__` to match.
- [ ] Add a clear entry under the version history in `VERSION.md`.
- [ ] Reset "Recent Changes" to track changes since the new tag.
- [ ] Ensure the git tag matches the version in all files.

## GitHub Releases

- When creating a release, always fill in the release title and release notes.
- Release notes must be explicit: list every new feature, changed behavior, or removed capability. Auto-generated notes are a starting point, not a substitute.

## Commit Messages

This project uses **Conventional Commits**: `<type>(<scope>): <short summary>`

| Type | When to use |
| ---- | ----------- |
| `feat` | New feature (triggers MINOR bump) |
| `fix` | Bug fix (triggers PATCH bump) |
| `chore` | Maintenance, dependency updates |
| `docs` | Documentation only |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or updating tests |
| `release` | Version bump commit |

- Keep the summary under 72 characters.
- Use imperative mood: "add X", not "added X".
- Reference issue numbers where applicable: `fix(auth): correct token expiry (#42)`.
- Do not use emojis in commit messages.

## Coding Standards

- **No emojis** anywhere (messages, docs, comments, commit messages, generated output, or source code including string literals and UI text) unless explicitly requested.
- Follow existing code style and patterns in the codebase.
- **Security best practices:** never expose or log secrets and keys. Never commit secrets to the repository. Validate all user input.
- Prefer async I/O for all database and network operations (asyncpg/aiosqlite, httpx, asyncio subprocess).
- Use the PRG (Post-Redirect-Get) pattern for form mutations: POST + 303 redirect.
- Run `ruff check app main.py` and `python -m pytest tests/ -q` before considering a task complete.

## Fact-Based Analysis

Base every analysis, decision, and statement on verifiable facts from the codebase, logs, or documentation. Do not speculate, assume, or invent explanations when information is missing.

- Use search and read tools to verify facts before stating them.
- If something is unclear or ambiguous, state the uncertainty explicitly rather than constructing a plausible explanation.
- When evidence contradicts an assumption, discard the assumption immediately and report only what is confirmed.
