from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from arcadia_recommendation.application.ports.outbound.enrichment import (
    EmbeddingPort,
    ExplanationPort,
)
from arcadia_recommendation.application.ports.outbound.messaging import ProcessedEventStore
from arcadia_recommendation.application.ports.outbound.repositories import UnitOfWork
from arcadia_recommendation.application.ports.outbound.support import EventStampFactory
from arcadia_recommendation.application.usecases.enrich import EmbedPendingGamesUseCase
from arcadia_recommendation.application.usecases.generate import (
    GenerateRecommendationsUseCase,
    RefreshAllRecommendationsUseCase,
)
from arcadia_recommendation.application.usecases.ingest import (
    HandleGamePublishedUseCase,
    HandleGameWithdrawnUseCase,
    HandlePurchaseCompletedUseCase,
    HandleReviewPostedUseCase,
)
from arcadia_recommendation.application.usecases.serve import (
    ListSimilarGamesUseCase,
    ServeRecommendationsUseCase,
)
from arcadia_recommendation.domain.policy.scoring import (
    CollaborativeScorer,
    ContentBasedScorer,
    ContentScorer,
    DenseContentScorer,
    HybridRanker,
    TopSellersProvider,
)
from arcadia_recommendation.infrastructure.adapters.embedding import (
    HashingEmbedder,
    HuggingFaceEmbedder,
)
from arcadia_recommendation.infrastructure.adapters.explanation import (
    NoExplanations,
    OpenAiCompatibleExplainer,
)
from arcadia_recommendation.infrastructure.adapters.support import SystemClock, UuidGenerator
from arcadia_recommendation.infrastructure.config.settings import Settings
from arcadia_recommendation.infrastructure.health import AlwaysReachable, Probe
from arcadia_recommendation.infrastructure.lifecycle import Lifecycle
from arcadia_recommendation.infrastructure.messaging.bus.in_process import InProcessEventBus
from arcadia_recommendation.infrastructure.messaging.bus.publisher import EventPublisher
from arcadia_recommendation.infrastructure.messaging.bus.subscriber import EventSubscriber
from arcadia_recommendation.infrastructure.messaging.kafka.producer import KafkaEventPublisher
from arcadia_recommendation.infrastructure.messaging.kafka.subscriber import KafkaSubscriber
from arcadia_recommendation.infrastructure.messaging.outbox.dispatcher import OutboxDispatcher
from arcadia_recommendation.infrastructure.messaging.outbox.store import OutboxStore
from arcadia_recommendation.infrastructure.observability.correlation import current_correlation_id
from arcadia_recommendation.infrastructure.observability.gauge import PolledGauge
from arcadia_recommendation.infrastructure.persistence.memory.outbox_store import (
    MemoryOutboxStore,
    MemoryProcessedEventLog,
)
from arcadia_recommendation.infrastructure.persistence.memory.store import MemoryStore
from arcadia_recommendation.infrastructure.persistence.memory.unit_of_work import MemoryUnitOfWork
from arcadia_recommendation.infrastructure.persistence.sql.engine import (
    PostgresProbe,
    create_engine,
    create_session_factory,
)
from arcadia_recommendation.infrastructure.persistence.sql.migrate import SchemaMigrator
from arcadia_recommendation.infrastructure.persistence.sql.outbox_store import SqlOutboxStore
from arcadia_recommendation.infrastructure.persistence.sql.processed_events import SqlProcessedEventLog
from arcadia_recommendation.infrastructure.persistence.sql.unit_of_work import SqlUnitOfWork
from arcadia_recommendation.infrastructure.response_cache import ResponseCache
from arcadia_recommendation.infrastructure.scheduler import GenerationScheduler
from arcadia_recommendation.infrastructure.security.jwt_verifier import JwtAccessTokenVerifier


@dataclass(frozen=True, slots=True)
class UseCases:
    """Every use case the presentation layer may call, constructed once at startup."""

    serve: ServeRecommendationsUseCase
    similar_games: ListSimilarGamesUseCase
    generate: GenerateRecommendationsUseCase
    refresh_all: RefreshAllRecommendationsUseCase
    handle_game_published: HandleGamePublishedUseCase
    handle_game_withdrawn: HandleGameWithdrawnUseCase
    handle_purchase_completed: HandlePurchaseCompletedUseCase
    handle_review_posted: HandleReviewPostedUseCase


@dataclass(frozen=True, slots=True)
class Adapters:
    """The concrete adapters chosen by the *_BACKEND settings, held on the container so local runs and tests
    can reach their control hooks; nothing outside this module may import their classes. `store` and `bus`
    are the in-memory backends and are None whenever a real one is configured."""

    uow_factory: Callable[[], UnitOfWork]
    outbox: OutboxStore
    dispatcher: OutboxDispatcher
    scheduler: GenerationScheduler
    processed_events: ProcessedEventStore
    tokens: JwtAccessTokenVerifier | None
    persistence_probe: Probe
    cache_probe: Probe | None
    response_cache: ResponseCache
    known_games: PolledGauge
    lifecycle: tuple[Lifecycle, ...]
    store: MemoryStore | None
    bus: InProcessEventBus | None
    subscriber: EventSubscriber


@dataclass(frozen=True, slots=True)
class Container:
    """The composition root's output. This is the only module in the codebase permitted to import from
    infrastructure.persistence."""

    settings: Settings
    use_cases: UseCases
    adapters: Adapters


def build_container(settings: Settings) -> Container:
    clock = SystemClock()
    ids = UuidGenerator()
    stamps = EventStampFactory(clock, ids)
    lifecycle: list[Lifecycle] = []

    engine, sessions = _database(settings)
    if sessions is not None and settings.run_migrations:
        lifecycle.append(SchemaMigrator(settings))
    store = None if sessions is not None else MemoryStore()
    publisher, subscriber, bus = _messaging(settings, lifecycle)

    outbox = _outbox_store(settings, store, sessions)
    dispatcher = OutboxDispatcher(
        outbox,
        publisher,
        timedelta(milliseconds=settings.outbox_poll_interval_ms),
        settings.outbox_batch_size,
    )
    lifecycle.append(dispatcher)

    cache = redis.from_url(settings.redis_url, decode_responses=True) if settings.uses_redis else None
    response_cache = ResponseCache(
        cache, prefix=settings.service_name, default_ttl=settings.recommendation_cache_ttl_seconds
    )
    cache_probe: Probe | None = RedisProbe(cache) if cache is not None else None

    tokens = (
        JwtAccessTokenVerifier(
            settings.jwt_secret, settings.jwt_algorithm, settings.jwt_issuer, settings.jwt_audience
        )
        if settings.identity_backend == "jwt"
        else None
    )

    processed_events: ProcessedEventStore = (
        SqlProcessedEventLog(sessions) if sessions is not None else MemoryProcessedEventLog(_require(store))
    )
    persistence_probe: Probe = PostgresProbe(_require(engine)) if engine is not None else AlwaysReachable()

    uow_factory = _unit_of_work_factory(settings, store, sessions)
    embedder = _embedder(settings)
    explanations = _explanations(settings)
    ranker = HybridRanker(_content_scorer(settings), CollaborativeScorer())
    generate = GenerateRecommendationsUseCase(
        uow_factory,
        ranker,
        stamps,
        settings.catalogue_scan_limit,
        explanations,
        settings.ranks_in_dense_space,
    )
    embed_pending = EmbedPendingGamesUseCase(uow_factory, embedder, settings.embedding_batch_size)
    refresh_all = RefreshAllRecommendationsUseCase(
        uow_factory, generate, stamps, settings.generation_batch_size, embed_pending
    )
    scheduler = GenerationScheduler(
        refresh_all,
        timedelta(seconds=settings.generation_interval_seconds),
        settings.generation_enabled,
    )
    lifecycle.append(scheduler)

    known_games = PolledGauge(
        _known_game_provider(uow_factory), timedelta(seconds=settings.gauge_refresh_seconds)
    )
    lifecycle.append(known_games)

    use_cases = UseCases(
        serve=ServeRecommendationsUseCase(uow_factory, TopSellersProvider()),
        similar_games=ListSimilarGamesUseCase(
            uow_factory, settings.catalogue_scan_limit, settings.ranks_in_dense_space
        ),
        generate=generate,
        refresh_all=refresh_all,
        handle_game_published=HandleGamePublishedUseCase(uow_factory, stamps),
        handle_game_withdrawn=HandleGameWithdrawnUseCase(uow_factory),
        handle_purchase_completed=HandlePurchaseCompletedUseCase(uow_factory, stamps),
        handle_review_posted=HandleReviewPostedUseCase(uow_factory, stamps),
    )
    adapters = Adapters(
        uow_factory=uow_factory,
        outbox=outbox,
        dispatcher=dispatcher,
        scheduler=scheduler,
        processed_events=processed_events,
        tokens=tokens,
        persistence_probe=persistence_probe,
        cache_probe=cache_probe,
        response_cache=response_cache,
        known_games=known_games,
        lifecycle=tuple(lifecycle),
        store=store,
        bus=bus,
        subscriber=subscriber,
    )
    return Container(settings=settings, use_cases=use_cases, adapters=adapters)


class RedisProbe:
    """Readiness for the response cache. Never fatal — see the note in `health.readyz`."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def ping(self) -> None:
        await self._client.ping()


def _database(settings: Settings) -> tuple[AsyncEngine | None, async_sessionmaker[AsyncSession] | None]:
    if not settings.uses_postgres:
        return None, None
    engine = create_engine(settings)
    return engine, create_session_factory(engine)


def _messaging(
    settings: Settings, lifecycle: list[Lifecycle]
) -> tuple[EventPublisher, EventSubscriber, InProcessEventBus | None]:
    if not settings.uses_kafka:
        bus = InProcessEventBus()
        return bus, bus, bus
    publisher = KafkaEventPublisher(settings.kafka_bootstrap_servers, settings.kafka_topic_reco_events)
    subscriber = KafkaSubscriber(
        settings.kafka_bootstrap_servers,
        settings.kafka_consumer_group,
        settings.kafka_consumer_max_retries,
    )
    lifecycle.extend((publisher, subscriber))
    return publisher, subscriber, None


def _outbox_store(
    settings: Settings, store: MemoryStore | None, sessions: async_sessionmaker[AsyncSession] | None
) -> OutboxStore:
    if sessions is not None:
        return SqlOutboxStore(sessions, settings.outbox_batch_size)
    return MemoryOutboxStore(_require(store))


def _unit_of_work_factory(
    settings: Settings, store: MemoryStore | None, sessions: async_sessionmaker[AsyncSession] | None
) -> Callable[[], UnitOfWork]:
    topic = settings.kafka_topic_reco_events
    if sessions is not None:

        def sql_factory() -> UnitOfWork:
            return SqlUnitOfWork(sessions, topic, current_correlation_id())

        return sql_factory

    memory = _require(store)

    def memory_factory() -> UnitOfWork:
        return MemoryUnitOfWork(memory, topic, current_correlation_id())

    return memory_factory


def _content_scorer(settings: Settings) -> ContentScorer:
    if settings.ranks_in_dense_space:
        return DenseContentScorer()
    return ContentBasedScorer()


def _embedder(settings: Settings) -> EmbeddingPort:
    if settings.embedding_backend == "huggingface":
        return HuggingFaceEmbedder(
            settings.embedding_endpoint,
            settings.embedding_api_key,
            settings.embedding_dimensions,
            settings.embedding_timeout_seconds,
            settings.embedding_prefix,
        )
    return HashingEmbedder(settings.embedding_dimensions)


def _explanations(settings: Settings) -> ExplanationPort:
    if settings.explanation_backend == "openai":
        return OpenAiCompatibleExplainer(
            settings.explanation_base_url,
            settings.explanation_api_key,
            settings.explanation_model,
            settings.explanation_timeout_seconds,
            settings.explanation_max_tokens,
        )
    return NoExplanations()


def _known_game_provider(uow_factory: Callable[[], UnitOfWork]) -> Callable[[], Awaitable[int]]:
    async def count() -> int:
        async with uow_factory() as uow:
            return await uow.games.count_recommendable()

    return count


def _require[T](value: T | None) -> T:
    if value is None:
        raise RuntimeError("the selected backend was not constructed")
    return value
