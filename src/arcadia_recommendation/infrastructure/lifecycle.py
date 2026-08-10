from typing import Protocol


class Lifecycle(Protocol):
    """A component with a connection to open at boot and close at shutdown. Collected by the composition root
    so the application's lifespan starts and stops whatever the backend flags happened to select, without
    naming any of them."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
