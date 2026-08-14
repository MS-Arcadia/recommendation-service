from collections.abc import Sequence
from dataclasses import replace

from arcadia_recommendation.application.dto.recommendation_dto import RefreshReport
from arcadia_recommendation.application.ports.outbound.enrichment import (
    ExplainedGame,
    ExplanationPort,
    ExplanationRequest,
)
from arcadia_recommendation.application.ports.outbound.repositories import UnitOfWork, UnitOfWorkFactory
from arcadia_recommendation.application.ports.outbound.support import EventStampFactory
from arcadia_recommendation.application.usecases.enrich import EmbedPendingGamesUseCase
from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.policy.scoring import HybridRanker, ScoredGame
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.events import RecommendationGenerated
from arcadia_recommendation.domain.recommendation.recommendation import Recommendation
from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.domain.shared.ids import RecommendationId, UserId
from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class GenerateRecommendationsUseCase:
    """The batch half of ب-۹, for one user: read the signals, rank, explain, store, publish.

    Generation is deliberately not on the read path. Ranking touches every recommendable game and runs an
    item-item join; doing that per request would put a self-join between a user and their storefront, and
    §2's p95 budget is 300ms. Running it on a schedule instead means a read is one indexed lookup, at the
    cost of a list that is minutes stale — which for a suggestion is not a cost at all. Everything added for
    the semantic space — recomputing the dense taste vector, calling an explanation model — sits inside that
    same batch and inherits the same argument.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ranker: HybridRanker,
        stamps: EventStampFactory,
        catalogue_limit: int = 500,
        explanations: ExplanationPort | None = None,
        dense_space: bool = False,
    ) -> None:
        self._uow_factory = uow_factory
        self._ranker = ranker
        self._stamps = stamps
        self._catalogue_limit = catalogue_limit
        self._explanations = explanations
        self._dense_space = dense_space

    async def execute(self, user_id: UserId, limit: int = limits.DEFAULT_RECOMMENDATION_COUNT) -> int:
        async with self._uow_factory() as uow:
            written = await self._generate(uow, user_id, limit)
            await uow.commit()
        return written

    async def _generate(self, uow: UnitOfWork, user_id: UserId, limit: int) -> int:
        preference = await uow.preferences.get(user_id) or UserPreference.blank(user_id)
        if preference.is_cold:
            # Nothing to personalise on. The stored set is cleared rather than left alone, so a user whose
            # history was erased stops being served recommendations derived from it.
            await uow.recommendations.replace_for(user_id, [])
            return 0

        played = await uow.games.by_ids(preference.remembered_games)
        if self._dense_space:
            preference = preference.rebuilt_in({game.game_id: game.dense for game in played})
            await uow.preferences.upsert(preference)

        candidates = await uow.games.recommendable(self._catalogue_limit)
        co_purchases = await uow.ownerships.co_purchases(
            user_id, sorted(preference.owned, key=str), limits.MAX_COLLABORATIVE_CANDIDATES
        )
        scored = self._ranker.rank(preference, candidates, co_purchases, limit)
        scored = await self._explained(scored, played)

        at = self._stamps.now()
        recommendations = [
            Recommendation(
                id=RecommendationId(self._stamps.new_uuid()),
                user_id=user_id,
                game_id=item.game.game_id,
                score=self._ranker.blend(item.content, item.collaborative),
                source=item.source,
                rank=rank,
                generated_at=at,
                reasons=item.reasons,
            )
            for rank, item in enumerate(scored, start=1)
        ]
        await uow.recommendations.replace_for(user_id, recommendations)
        if recommendations:
            await uow.outbox.enqueue(
                [RecommendationGenerated.of(user_id, recommendations, self._stamps.next())]
            )
        return len(recommendations)

    async def _explained(self, scored: list[ScoredGame], played: Sequence[GameProfile]) -> list[ScoredGame]:
        """Asks a model why each suggestion suits this user, and keeps whatever it manages to answer.

        Called once per user with the whole shortlist rather than once per game: the cost of this feature is
        entirely a question of how many calls a sweep makes, and a per-item loop would multiply it by ten
        for an answer no better. Anything the model declines to justify keeps the reasons the scorer gave
        it, which in the sparse space is the shared genres and in the dense space is nothing.
        """
        if self._explanations is None or not scored:
            return scored
        request = ExplanationRequest(
            liked=tuple(_as_explained(game) for game in played[: limits.MAX_EXPLAINED_CANDIDATES]),
            candidates=tuple(_as_explained(item.game) for item in scored[: limits.MAX_EXPLAINED_CANDIDATES]),
        )
        reasons = await self._explanations.explain(request)
        if not reasons:
            return scored
        return [replace(item, reasons=reasons.get(item.game.game_id, item.reasons)) for item in scored]


class RefreshAllRecommendationsUseCase:
    """The scheduled sweep, and the endpoint an operator can force.

    Each user is generated in its own transaction rather than one transaction for the batch. A sweep over
    every user on the platform holding a single transaction open would keep row locks for its whole
    duration, and one bad user's failure would discard the work done for everyone before them.

    Embedding runs once at the start of the sweep rather than per user, because a vector belongs to a game
    and not to whoever is being ranked — computing it inside the per-user loop would re-embed the same
    catalogue for every account on the platform.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        generate: GenerateRecommendationsUseCase,
        stamps: EventStampFactory,
        batch_size: int = 500,
        embed_pending: EmbedPendingGamesUseCase | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._generate = generate
        self._stamps = stamps
        self._batch_size = batch_size
        self._embed_pending = embed_pending

    async def execute(self, limit: int = limits.DEFAULT_RECOMMENDATION_COUNT) -> RefreshReport:
        if self._embed_pending is not None:
            await self._embed_pending.execute()

        async with self._uow_factory() as uow:
            users = list(await uow.preferences.with_signals(self._batch_size))

        written = 0
        processed = 0
        for user_id in users:
            try:
                written += await self._generate.execute(user_id, limit)
            except Exception as exc:
                # One user's failure must not end the sweep: the alternative is that a single malformed
                # profile freezes recommendations for the whole platform until someone notices.
                _logger.warning("generation_failed_for_user", user_id=str(user_id), error=str(exc))
                continue
            processed += 1
        return RefreshReport(
            users_processed=processed,
            recommendations_written=written,
            generated_at=self._stamps.now(),
        )

    async def execute_for(
        self, user_id: UserId, limit: int = limits.DEFAULT_RECOMMENDATION_COUNT
    ) -> RefreshReport:
        """One user, reported in the same shape as a sweep, so an operator investigating a single complaint
        reads the same response as one who refreshed everything."""
        if self._embed_pending is not None:
            await self._embed_pending.execute()
        written = await self._generate.execute(user_id, limit)
        return RefreshReport(
            users_processed=1, recommendations_written=written, generated_at=self._stamps.now()
        )


def _as_explained(game: GameProfile) -> ExplainedGame:
    return ExplainedGame(game_id=game.game_id, title=game.title, genres=game.genres, tags=game.tags)
