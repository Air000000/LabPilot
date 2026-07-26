import os

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

MIGRATION_LOCK_KEY = 0x4C414250


def main() -> None:
    async_url = os.environ["LABPILOT_DATABASE_URL"]
    sync_url = make_url(async_url.replace("+asyncpg", "+psycopg"))
    dsn = sync_url.render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", dsn)
            command.upgrade(config, "head")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


if __name__ == "__main__":
    main()
