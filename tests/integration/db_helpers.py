from alembic import command
from alembic.config import Config


def upgrade_database_to_head(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("+asyncpg", "+psycopg"),
    )
    command.upgrade(config, "head")
