from arcadia_recommendation.application.ports.outbound.enrichment import EmbeddingPort
from arcadia_recommendation.application.ports.outbound.repositories import UnitOfWorkFactory
from arcadia_recommendation.domain.policy.embedding import DenseEmbedding
from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class EmbedPendingGamesUseCase:
    """Gives a semantic vector to every game published without one.

    This runs on the sweep rather than on the `GamePublished` handler, and that placement is the whole
    design. Embedding is an HTTP call to a third party; making a Kafka consumer wait for one would put a
    provider's latency on the ingest path and its outages on the partition — a timeout there stalls every
    event behind it, including purchases, which have nothing to do with embeddings. Deferring it means a
    game is briefly present but unrankable in the dense space, which the sweep resolves on its next pass and
    `UserPreference.rebuilt_in` repairs retroactively for anyone who bought it in the meantime.

    Games are claimed in bounded batches, most popular first, for the same reason the candidate scan is
    bounded: an unbounded pass over a growing catalogue is how a scheduled job becomes an outage.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, embedder: EmbeddingPort, batch_size: int = 32) -> None:
        self._uow_factory = uow_factory
        self._embedder = embedder
        self._batch_size = batch_size

    async def execute(self) -> int:
        async with self._uow_factory() as uow:
            pending = list(await uow.games.needing_embedding(self._batch_size))
        if not pending:
            return 0

        try:
            vectors = await self._embedder.embed([game.embedding_text for game in pending])
        except Exception as exc:
            # The provider is somebody else's uptime. A failed batch leaves the games unembedded and tries
            # again on the next sweep; raising here would take generation down with it for every user,
            # including those whose recommendations do not depend on a new game at all.
            _logger.warning("embedding_batch_failed", games=len(pending), error=str(exc))
            return 0

        async with self._uow_factory() as uow:
            for game, values in zip(pending, vectors, strict=True):
                await uow.games.upsert(game.embedded(DenseEmbedding.of(values)))
            await uow.commit()
        _logger.info("games_embedded", games=len(pending), dimensions=self._embedder.dimensions)
        return len(pending)
