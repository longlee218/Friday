import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from dotenv import load_dotenv

from friday import schema
from friday.config import load_config

# config.yaml interpolates secrets from .env, so they have to be present
# before it can be read — even though a migration needs none of them.
load_dotenv()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# The database lives where config.yaml says, not where alembic.ini says. One
# place to change it, and no connection string in a file that gets committed.
# FRIDAY_DB overrides it, so a migration can be generated or verified against
# a throwaway database instead of the live one.
_path = os.environ.get("FRIDAY_DB") or load_config().database_path
config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{_path}")

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# The one source of truth for the schema. Autogenerate diffs against this,
# and a test asserts the diff is empty — otherwise the models and the
# migrations drift apart in silence.
target_metadata = schema.Base.metadata


def render_item(type_, obj, autogen_context):
    """Render a custom column type as the type it actually stores.

    Otherwise autogenerate emits `friday.schema.IsoDateTime()` into a migration
    that never imports it — and worse, ties a migration to a class that may be
    renamed or deleted later. A migration should describe the database, not the
    code that happened to be there when it was written.
    """
    if type_ == "type" and isinstance(obj, schema.IsoDateTime):
        return "sa.String()"
    return False

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        render_item=render_item,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
