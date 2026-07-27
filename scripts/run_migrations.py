import os

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

MIGRATION_LOCK_KEY = 0x4C414250


def migration_urls(database_url: str) -> tuple[str, str]:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("LABPILOT_DATABASE_URL must use PostgreSQL")
    alembic_url = parsed.set(drivername="postgresql+psycopg")
    direct_url = parsed.set(drivername="postgresql")
    return (
        alembic_url.render_as_string(hide_password=False),
        direct_url.render_as_string(hide_password=False),
    )


def main() -> None:
    alembic_dsn, direct_dsn = migration_urls(os.environ["LABPILOT_DATABASE_URL"])
    with psycopg.connect(direct_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            config = Config("alembic.ini")
            config.set_main_option(
                "sqlalchemy.url",
                alembic_dsn.replace("%", "%%"),
            )
            command.upgrade(config, "head")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


if __name__ == "__main__":
    main()
