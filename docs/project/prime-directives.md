# Prime Directives

Project-specific architectural and correctness rules for GameServer Manager.

These rules define what the codebase must enforce at runtime. They are non-negotiable and override all other guidance.

## Architecture

- **Runtime:** Python 3.12 async application using FastAPI + Uvicorn.
- **Database:** SQLAlchemy 2.0 async ORM with PostgreSQL as primary backend. SQLite is supported for tests and development only.
- **Migrations:** Alembic manages all schema migrations. The migration chain must always have exactly one head.
- **Templating:** Server-side rendered HTML via Jinja2 with Tailwind CSS. No SPA framework is used.
- **State:** Runtime state (server processes, background tasks) is kept in memory-managed singletons; persistent state belongs in the database.

## Coding Standards

- Use **Conventional Commits** for all commit messages.
- Do **not** use emojis in messages, docs, comments, commit messages, or UI text unless explicitly requested.
- Run `ruff check gsm/app gsm/main.py` and `python -m pytest gsm/tests/ -q` before considering any task complete.
- Follow the existing project structure and naming conventions.
- Prefer async I/O for all database and network operations.

## Security

- Never commit secrets, API keys, or credentials to the repository.
- Validate all user input on both client and server side.
- Use CSRF protection for all state-changing forms.
- Store sensitive data encrypted when persistence is required.

## Web & UI

- Use the PRG (Post-Redirect-Get) pattern for form mutations: POST + 303 redirect.
- Keep UI text and labels in English or German i18n dictionaries; avoid hardcoded UI strings in Python logic.
- CSS changes that need new Tailwind utilities require rebuilding `npm run css:build`.

## Database & Migrations

- Every schema change requires an Alembic migration.
- Migrations must remain compatible with PostgreSQL; SQLite-only statements are acceptable only as best-effort checks.
- After creating or reordering migrations, run `alembic heads` and verify there is exactly one head.

## Container & Deployment

- The Docker image is built from `Dockerfile` and orchestrated via `docker-compose.yml`.
- The host-facing port is controlled by `GSM_HOST_PORT` (default 8443).
- HTTPS/SSL is auto-enabled in the container via self-signed certificates.
- Docker container isolation per server is optional and controlled by `GSM_DOCKER_ISOLATION_ENABLED`.

## Versioning

- Use Semantic Versioning (SemVer): `MAJOR.MINOR.PATCH`.
- Keep version sources in sync:
  - `VERSION.md` line 3: `## Current Version: X.Y.Z`
  - `app/__init__.py`: `__version__ = "X.Y.Z"`
- When releasing, ensure the git tag matches the version string in both files.
- Update `VERSION.md` history when implementing new features or notable fixes.

## GitHub Releases

- Always fill release title and release notes explicitly.
- Auto-generated release notes are a starting point, not a substitute for a clear changelog.
- The Docker image release workflow is triggered by `v*` tags and publishes to `ghcr.io`.

## Documentation

- `AGENTS.md` contains agent behavior rules and is not part of the application.
- `docs/project/project-definition.md` describes the tech stack and architecture.
- `docs/project/lessons.md` records recurring patterns and gotchas discovered during development.
- Keep `README.md` screenshots and setup instructions up to date when the UI or deployment changes.
