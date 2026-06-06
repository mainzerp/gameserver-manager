# Database Backend Configuration

GameServer Manager supports multiple database backends via SQLAlchemy.

## PostgreSQL (Default)

PostgreSQL is the default backend. The Docker Compose setup includes a `db` service running `postgres:16-alpine` with automatic healthchecks. No additional configuration is needed when using `docker-compose up`.

```
GSM_DATABASE_URL=postgresql+asyncpg://gsm:gsm@db:5432/gameserver
```

### Connection String Format
```
postgresql+asyncpg://username:password@hostname:5432/database_name
```

For local development outside Docker, use `localhost` instead of `db`:
```
GSM_DATABASE_URL=postgresql+asyncpg://gsm:gsm@localhost:5432/gameserver
```

## SQLite (Alternative)

Zero configuration required. Database file is stored at `data/gameserver.db`. Suitable for single-server or simple deployments.

To use SQLite instead of PostgreSQL:

1. Update `.env`:
   ```
   GSM_DATABASE_URL=sqlite+aiosqlite:///./data/gameserver.db
   ```

2. Ensure `aiosqlite` is in `requirements.txt` (included by default).

3. Remove or comment out the `db` service and `depends_on` block in `docker-compose.yml`.

```
GSM_DATABASE_URL=sqlite+aiosqlite:///data/gameserver.db
```

## MySQL

Alternative to PostgreSQL for environments already running MySQL/MariaDB.

### Setup

1. Uncomment `aiomysql>=0.2.0` in `requirements.txt` and install:
   ```bash
   pip install aiomysql
   ```

2. Set the database URL:
   ```
   GSM_DATABASE_URL=mysql+aiomysql://user:password@host:3306/gameserver
   ```

### Connection String Format
```
mysql+aiomysql://username:password@hostname:3306/database_name
```

## Migration Notes

- Alembic migrations work across all three backends
- SQLite uses `render_as_batch=True` for ALTER TABLE operations
- PostgreSQL and MySQL handle schema changes natively
- When switching backends, data must be migrated manually (export/import)

## Connection Pooling

SQLite uses no connection pool (single-writer model). PostgreSQL and MySQL automatically use connection pooling:

- `pool_size`: 5 (concurrent connections)
- `max_overflow`: 10 (additional connections under load)
- `pool_recycle`: 3600 seconds (reconnect stale connections)
- `pool_pre_ping`: enabled (verify connections before use)
