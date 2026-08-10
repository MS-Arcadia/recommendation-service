from collections.abc import Awaitable, Callable

from arcadia_recommendation.application.ports.outbound.messaging import ProcessedEventStore
from arcadia_recommendation.composition import Container
from arcadia_recommendation.infrastructure.messaging.bus.idempotent import IdempotentHandler
from arcadia_recommendation.infrastructure.messaging.bus.subscriber import EventSubscriber
from arcadia_recommendation.infrastructure.messaging.outbox.record import OutboxRecord
from arcadia_recommendation.presentation.consumers import game_events, purchase_events, review_events

Handler = Callable[[OutboxRecord], Awaitable[None]]


def register_consumers(container: Container, subscriber: EventSubscriber) -> None:
    """Subscribes every inbound handler, on the in-process bus or on a Kafka consumer group depending on
    which the composition root built.

    Each is wrapped in IdempotentHandler even though the use cases guard themselves transactionally too.
    That is belt and braces here for a specific reason: `purchase_count` only ever increments, so a
    duplicate that slipped past both would not raise anything — it would quietly overstate a game's
    popularity for ever, and the fallback ranking is built on that number.
    """
    use_cases = container.use_cases
    store: ProcessedEventStore = container.adapters.processed_events
    topics = container.settings

    subscriptions: tuple[tuple[str, str, Handler], ...] = (
        (
            topics.kafka_topic_game_events,
            game_events.GAME_PUBLISHED,
            game_events.GamePublishedHandler(use_cases.handle_game_published),
        ),
        (
            topics.kafka_topic_game_events,
            game_events.GAME_WITHDRAWN,
            game_events.GameWithdrawnHandler(use_cases.handle_game_withdrawn),
        ),
        (
            topics.kafka_topic_purchase_events,
            purchase_events.PURCHASE_COMPLETED,
            purchase_events.PurchaseCompletedHandler(use_cases.handle_purchase_completed),
        ),
        (
            topics.kafka_topic_review_events,
            review_events.REVIEW_POSTED,
            review_events.ReviewPostedHandler(use_cases.handle_review_posted),
        ),
    )
    for topic, event_type, handler in subscriptions:
        subscriber.subscribe(topic, event_type, IdempotentHandler(store, handler))
