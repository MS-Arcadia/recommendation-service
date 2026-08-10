from typing import Protocol


class Probe(Protocol):
    """A dependency that can be asked whether it is reachable. Raising is the failure signal; returning
    anything is success."""

    async def ping(self) -> None: ...


class AlwaysReachable:
    """The probe for a backend that is in this process. It cannot be unreachable, and pretending it might be
    would make readiness report on nothing."""

    async def ping(self) -> None:
        return None


async def check(probe: Probe, *, critical: bool = True) -> dict[str, str]:
    """One check as it appears in the readiness body. A non-critical dependency reports DEGRADED rather than
    DOWN: losing the shared cache costs correctness under scale-out, but a service that still answers reads
    should not be pulled out of the load balancer for it."""
    try:
        await probe.ping()
    except Exception as exc:
        return {"status": "DOWN" if critical else "DEGRADED", "error": str(exc)[:200]}
    return {"status": "UP"}
