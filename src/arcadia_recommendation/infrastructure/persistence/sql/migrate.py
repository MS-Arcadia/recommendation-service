import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from arcadia_recommendation.infrastructure.config.settings import Settings
from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class SchemaMigrator:
    """Runs `alembic upgrade head` at boot, behind RUN_MIGRATIONS like the rest of the platform. With the flag
    off the service still starts, serves /livez and answers /readyz 503 — which is what makes a bad rollout
    visible instead of a crash loop, and what lets a deployment run migrations as a separate job.

    Alembic is synchronous, so the upgrade runs in a worker thread: driving it on the event loop would block
    every other startup task behind a DDL lock. A failed upgrade is logged and the service still starts, so
    the failure shows up as /readyz 503 on one pod rather than as a crash loop that rolls the whole
    deployment."""

    def __init__(self, settings: Settings, migrations_dir: Path | None = None) -> None:
        self._settings = settings
        self._directory = migrations_dir or _default_directory()

    async def start(self) -> None:
        if not self._directory.exists():
            _logger.warning("migrations_directory_missing", path=str(self._directory))
            return
        try:
            await asyncio.to_thread(self._upgrade)
        except Exception as exc:
            _logger.error("migrations_failed", error=str(exc))
            return
        _logger.info("migrations_applied")

    async def stop(self) -> None:
        return None

    def _upgrade(self) -> None:
        config = Config()
        config.set_main_option("script_location", str(self._directory))
        config.set_main_option("sqlalchemy.url", self._settings.database_url)
        command.upgrade(config, "head")


def _default_directory() -> Path:
    """Inside the package rather than at the repo root: the image installs the wheel, and a migration
    directory left outside it would be missing in exactly the environment that needs to run it."""
    return Path(__file__).resolve().parent / "migrations"
