"""Alembic runs as the schema owner. Application roles never run DDL."""
import os

from alembic import context
from sqlalchemy import create_engine


def owner_url():
    url = context.get_x_argument(as_dictionary=True).get('url') or os.getenv('DATABASE_OWNER_URL')
    if not url:
        raise SystemExit('Set DATABASE_OWNER_URL to run migrations.')
    # SQLAlchemy needs the driver named; plain postgresql:// URLs are accepted too.
    return url.replace('postgresql://', 'postgresql+psycopg://', 1)


def run():
    engine = create_engine(owner_url())
    with engine.connect() as connection:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit('Offline SQL generation is not supported; run against a database.')
run()
