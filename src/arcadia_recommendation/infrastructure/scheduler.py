import asyncio
import contextlib
from datetime import timedelta

from arcadia_recommendation.application.usecases.generate import RefreshAllRecommendationsUseCase
from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class GenerationScheduler:
    """The `@Scheduled` batch of C4 الف-۱۲, as a task on this process's event loop.

    One task per replica, not one job for the cluster. That is a deliberate simplification and it means N
    replicas do the same work N times: the sweep is idempotent — it replaces each user's list with the same
    ranking computed from the same rows — so the cost is duplicated CPU rather than a wrong answer. Making it
    a genuine singleton needs a distributed lock, which is real machinery to add when there is more than one
    replica to coordinate.

    The first run is delayed by one interval rather than fired at boot: a replica that starts during a
    rollout would otherwise begin a full sweep while it is still the least warm process in the cluster.
    """

    def __init__(self, refresh: RefreshAllRecommendationsUseCase, interval: timedelta, enabled: bool) -> None:
        self._refresh = refresh
        self._interval = interval
        self._enabled = enabled
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self._enabled:
            _logger.info("generation_scheduler_disabled")
            return
        if self.is_running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run())
        _logger.info("generation_scheduler_started", interval_seconds=self._interval.total_seconds())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(self._interval.total_seconds())
            if self._stopping.is_set():
                return
            try:
                report = await self._refresh.execute()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Swallowed so one bad sweep does not end the loop. A scheduler that dies on an exception
                # leaves a service that looks healthy and silently stops recommending anything ever again.
                _logger.error("generation_sweep_failed", error=str(exc))
                continue
            _logger.info(
                "generation_sweep_completed",
                users=report.users_processed,
                recommendations=report.recommendations_written,
            )
