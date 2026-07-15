# Testing with Docker

The repository includes an isolated Docker Compose test stack defined in `docker-compose.test.yml`. It uses separate service names, container names, networks, and named volumes so it does not interfere with the production stack.

## Test stack overview

| Resource | Test stack | Production stack (for reference) |
|---|---|---|
| Compose file | `docker-compose.test.yml` | `docker-compose.yml` |
| Environment file | `.env.test` | `.env` |
| App service | `gameserver-manager-test` | `gameserver-manager` |
| App container | `gameserver_manager_test` | `gameserver_manager` |
| Database service | `db-test` | `db` |
| Database container | `gsm_postgres_test` | `gsm_postgres` |
| Network | `gameserver_test` | `gameserver_default` |
| Default host port | `8444` | `8443` |
| App volumes | `gsm_test_data`, `gsm_test_servers`, `gsm_test_certs`, `gsm_test_steamcmd` | `gsm_data`, `gsm_servers`, `gsm_certs`, `gsm_steamcmd` |
| Database volume | `pg_test_data` | `pgdata` |

## Generate the test environment file

Create `.env.test` in the repository root with strong random secrets:

```bash
python -c "import secrets; print('GSM_SECRET_KEY=' + secrets.token_hex(32)); print('POSTGRES_PASSWORD=' + secrets.token_hex(32))" > .env.test
{
  echo "GSM_HOST_PORT_TEST=8444"
  echo "POSTGRES_DB=gameserver"
  echo "POSTGRES_USER=gsm"
} >> .env.test
```

Do not commit `.env.test`.

## Start the test stack

The test app image runs `alembic upgrade head` automatically before starting via the `command` override in `docker-compose.test.yml`. For manual control, start only the database first, then run migrations, then start the app:

```bash
# Start the test database
docker compose --env-file .env.test -f docker-compose.test.yml up -d db-test

# Run migrations explicitly (optional when using the built-in command override)
docker compose --env-file .env.test -f docker-compose.test.yml run --rm gameserver-manager-test alembic upgrade head

# Start the test app
docker compose --env-file .env.test -f docker-compose.test.yml up -d gameserver-manager-test
```

The panel is available at **https://localhost:8444** by default.

## Verify the configuration

Validate the Compose file without starting containers:

```bash
docker compose --env-file .env.test -f docker-compose.test.yml config
```

Check service status:

```bash
docker compose --env-file .env.test -f docker-compose.test.yml ps
```

View logs:

```bash
docker compose --env-file .env.test -f docker-compose.test.yml logs -f
```

Test the health endpoint:

```bash
curl -fk https://localhost:8444/health
```

## Tear down the test stack

Stop and remove containers together with the named test volumes:

```bash
docker compose --env-file .env.test -f docker-compose.test.yml down -v
```

This deletes the test data, test server files, test certificates, and test database. It does not affect the production stack or source directories.

## Running Python tests locally

Outside Docker, run the Python test suite and lint checks from the repository root:

```bash
ruff check gsm/app gsm/main.py
pytest gsm/tests/ -q
```
