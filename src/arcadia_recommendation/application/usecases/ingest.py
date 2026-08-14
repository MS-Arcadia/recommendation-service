from datetime import datetime
from uuid import UUID

from arcadia_recommendation.application.ports.outbound.repositories import UnitOfWork, UnitOfWorkFactory
from arcadia_recommendation.application.ports.outbound.support import EventStampFactory
from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.preference.signal import SignalKind
from arcadia_recommendation.domain.shared.ids import GameId, UserId
from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class HandleGamePublishedUseCase:
    """`GamePublished` from Catalog is what gives this service something to recommend at all.

    Republication updates the description in place: a developer editing genres must move the game in the
    content space, and treating each publish as a new game would leave the old vector ranking forever.

    It also backfills: anyone who already owns this game but was never credited for it — because their
    purchase arrived before the description did — has their taste vector reinforced now. See
    `OwnershipRepository` for why that ordering is the normal case rather than a rarity.
    """

    def __init__(
        self, uow_factory: UnitOfWorkFactory, stamps: EventStampFactory, backfill_limit: int = 500
    ) -> None:
        self._uow_factory = uow_factory
        self._stamps = stamps
        self._backfill_limit = backfill_limit

    async def execute(
        self,
        event_id: UUID,
        *,
        game_id: GameId,
        developer_id: UserId,
        title: str,
        genres: tuple[str, ...],
        tags: tuple[str, ...],
        published_at: datetime | None,
        description: str = "",
    ) -> None:
        async with self._uow_factory() as uow:
            if await _already_handled(uow, event_id):
                return
            existing = await uow.games.get(game_id)
            game = (
                existing.redescribed(title=title, genres=genres, tags=tags, description=description)
                if existing is not None
                else GameProfile.published(
                    game_id=game_id,
                    developer_id=developer_id,
                    title=title,
                    genres=genres,
                    tags=tags,
                    description=description,
                    published_at=published_at,
                )
            )
            await uow.games.upsert(game)
            await self._backfill(uow, game)
            await uow.processed_events.mark(event_id)
            await uow.commit()

    async def _backfill(self, uow: UnitOfWork, game: GameProfile) -> None:
        owners = await uow.ownerships.uncounted_owners(game.game_id, self._backfill_limit)
        if not owners:
            return
        at = self._stamps.now()
        for user_id in owners:
            preference = await uow.preferences.get(user_id) or UserPreference.blank(user_id)
            await uow.preferences.upsert(preference.observe(game, SignalKind.PURCHASE, at))
        await uow.ownerships.mark_counted(game.game_id, owners)
        _logger.info("backfilled_owners", game_id=str(game.game_id), owners=len(owners))


class HandleGameWithdrawnUseCase:
    """A withdrawn game stops being recommendable but keeps its history — see `GameProfile.withdrawn`."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, event_id: UUID, game_id: GameId) -> None:
        async with self._uow_factory() as uow:
            if await _already_handled(uow, event_id):
                return
            game = await uow.games.get(game_id)
            if game is None:
                await uow.processed_events.mark(event_id)
                await uow.commit()
                return
            await uow.games.upsert(game.withdrawn())
            await uow.processed_events.mark(event_id)
            await uow.commit()


class HandlePurchaseCompletedUseCase:
    """The strongest signal on the platform, and the one that feeds both halves of the hybrid at once: it
    moves the buyer's taste vector and adds the ownership row the item-item query joins on.

    The *recipient* is credited, not the buyer. A gift is evidence about the person who ends up owning the
    game, and attributing it to the payer would teach the platform that a buyer loves whatever their friends
    play — while also leaving the recipient's own library recommendable back to them.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, stamps: EventStampFactory) -> None:
        self._uow_factory = uow_factory
        self._stamps = stamps

    async def execute(
        self, event_id: UUID, *, recipient_id: UserId, game_id: GameId, occurred_at: datetime | None
    ) -> None:
        async with self._uow_factory() as uow:
            if await _already_handled(uow, event_id):
                return
            game = await uow.games.get(game_id)
            if game is None:
                # Catalog's GamePublished has not arrived yet — on a cold start replaying history, the
                # usual case. Ownership is recorded uncounted, so the collaborative half is not left with a
                # hole and HandleGamePublished credits the taste vector once the description turns up.
                _logger.info("purchase_for_unknown_game", game_id=str(game_id))
                await uow.ownerships.record(recipient_id, game_id, counted=False)
                await uow.processed_events.mark(event_id)
                await uow.commit()
                return

            preference = await uow.preferences.get(recipient_id) or UserPreference.blank(recipient_id)
            at = occurred_at or self._stamps.now()
            await uow.preferences.upsert(preference.observe(game, SignalKind.PURCHASE, at))
            await uow.ownerships.record(recipient_id, game_id, counted=True)
            await uow.games.upsert(game.bought())
            await uow.processed_events.mark(event_id)
            await uow.commit()


class HandleReviewPostedUseCase:
    """A review refines a taste vector in a direction a purchase cannot: it is the only signal that can be
    negative. Ownership is untouched — the reviewer already owned the game, by Review's own rule."""

    def __init__(self, uow_factory: UnitOfWorkFactory, stamps: EventStampFactory) -> None:
        self._uow_factory = uow_factory
        self._stamps = stamps

    async def execute(
        self,
        event_id: UUID,
        *,
        author_id: UserId,
        game_id: GameId,
        liked: bool,
        occurred_at: datetime | None,
    ) -> None:
        async with self._uow_factory() as uow:
            if await _already_handled(uow, event_id):
                return
            game = await uow.games.get(game_id)
            if game is None:
                await uow.processed_events.mark(event_id)
                await uow.commit()
                return
            preference = await uow.preferences.get(author_id) or UserPreference.blank(author_id)
            kind = SignalKind.REVIEW_LIKE if liked else SignalKind.REVIEW_DISLIKE
            at = occurred_at or self._stamps.now()
            await uow.preferences.upsert(preference.observe(game, kind, at))
            await uow.processed_events.mark(event_id)
            await uow.commit()


async def _already_handled(uow: UnitOfWork, event_id: UUID) -> bool:
    """Deduplication inside the same transaction as the effect it guards. The consumer wrapper checks too,
    but it commits its mark separately — this is the check that makes a redelivery landing on two replicas
    at once a no-op rather than a doubled purchase count."""
    return await uow.processed_events.seen(event_id)
