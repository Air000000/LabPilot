from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.db.readiness import DatabaseReadinessProbe
from labpilot.db.session import Database
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.settings import get_settings

settings = get_settings()
database = Database(settings.database_url)
services = ApplicationServices(
    projects=ProjectApplicationService(database.session),
)
actor_provider = ServerConfiguredActorProvider(
    installation_identity=settings.installation_identity,
    actor_id=settings.local_actor_id,
)
app = create_app(
    readiness_probe=DatabaseReadinessProbe(database.engine, "alembic.ini"),
    shutdown_callbacks=(database.dispose,),
    services=services,
    actor_provider=actor_provider,
)
