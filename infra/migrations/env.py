from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from services.api.integrations.config import env

config = context.config
url = env("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL is not set (see .env.example)")
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))


def run_offline() -> None:
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
