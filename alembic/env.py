import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import Base

import app.models.server
import app.models.mod
import app.models.user
import app.models.backup
import app.models.scheduled_task
import app.models.api_key
import app.models.metric
import app.models.audit_log
import app.models.server_access
import app.models.webhook
import app.models.webauthn_credential
import app.models.node
import app.models.site_settings

config = context.config
if config.config_file_name is not None and not logging.root.handlers:
    # Only configure logging when running from the alembic CLI.
    # If handlers are already set up (e.g. running inside the app), skip this
    # so the app's logging configuration is not overwritten.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    connectable = create_async_engine(settings.database_url)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
