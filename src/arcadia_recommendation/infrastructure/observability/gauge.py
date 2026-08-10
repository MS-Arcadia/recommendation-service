import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import timedelta

from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class PolledGauge:
    """A value refreshed on a timer and read synchronously. OpenTelemetry's observable gauges call back on the
    scrape thread and cannot await, so a metric whose source is a query has to be sampled somewhere else —
    and sampling it on a schedule also stops a scrape storm from turning into a query storm."""

    def __init__(self, provider: Callable[[], Awaitable[int]], interval: timedelta, initial: int = 0) -> None:
        self._provider = provider
        self._interval = interval
        self._value = initial
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def value(self) -> int:
        return self._value

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        await self._refresh()
        self._task = asyncio.create_task(self._run())

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
            await self._refresh()

    async def _refresh(self) -> None:
        try:
            self._value = await self._provider()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("gauge_refresh_failed", error=str(exc))
