import contextlib
from collections.abc import Sequence

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from arcadia_recommendation.infrastructure.observability.logging import get_logger

RETRY_SUFFIX = ".retry"
DLQ_SUFFIX = ".dlq"

PARTITIONS = 3
REPLICATION = 1

_logger = get_logger(__name__)


async def ensure_topics(bootstrap_servers: str, topics: Sequence[str]) -> None:
    """Creates the topic this service *produces* to, plus its retry and DLQ companions. Broker-side
    auto-creation is off on this platform, and a consumer on a missing topic logs a metadata error on every
    refresh — enough noise to bury anything real. Topics owned by other services are deliberately absent:
    creating one from here would mean choosing somebody else's partition count.

    Failure is logged and swallowed. A broker that is briefly unreachable at boot must not stop this service
    from serving recommendations, and the outbox holds every event until it comes back."""
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        await admin.start()
    except Exception as exc:
        _logger.warning("kafka_topics_unreachable", error=str(exc))
        return
    try:
        wanted = [
            NewTopic(name=name, num_partitions=PARTITIONS, replication_factor=REPLICATION) for name in topics
        ]
        with contextlib.suppress(TopicAlreadyExistsError):
            await admin.create_topics(wanted)
        _logger.info("kafka_topics_ready", topics=list(topics))
    except Exception as exc:
        _logger.warning("kafka_topics_not_created", error=str(exc))
    finally:
        with contextlib.suppress(Exception):
            await admin.close()
