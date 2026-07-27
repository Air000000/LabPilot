from labpilot.api.app import create_app
from labpilot.db.readiness import DatabaseReadinessProbe
from labpilot.db.session import Database
from labpilot.settings import get_settings

settings = get_settings()
database = Database(settings.database_url)
app = create_app(
    readiness_probe=DatabaseReadinessProbe(database.engine, "alembic.ini"),
    shutdown_callbacks=(database.dispose,),
)
