from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.recommendation import Recommendation
from arcadia_recommendation.domain.shared.ids import GameId, UserId
from arcadia_recommendation.infrastructure.persistence.sql.mapping import (
    game_to_domain,
    preference_to_domain,
    recommendation_row,
    recommendation_to_domain,
    write_game,
    write_preference,
)
from arcadia_recommendation.infrastructure.persistence.sql.models import (
    GameProfileRow,
    OwnershipRow,
    RecommendationRow,
    UserPreferenceRow,
)


class SqlGameProfileRepository:
    """Reads and writes the game read-model. Nothing here flushes: the unit of work owns the transaction
    boundary, and a repository that committed on its own would put the outbox row in a different one."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, game_id: GameId) -> GameProfile | None:
        row = await self._session.get(GameProfileRow, game_id.value)
        return game_to_domain(row) if row is not None else None

    async def upsert(self, game: GameProfile) -> None:
        row = await self._session.get(GameProfileRow, game.game_id.value)
        if row is None:
            row = GameProfileRow(game_id=game.game_id.value)
            self._session.add(row)
        write_game(row, game)

    async def recommendable(self, limit: int) -> Sequence[GameProfile]:
        """The candidate set, most popular first.

        Ordered by popularity rather than arbitrarily because `limit` truncates it: when the catalogue
        outgrows one scan, the games that fall off the end should be the ones nobody bought, not whichever
        the planner happened to return last.
        """
        statement = (
            select(GameProfileRow)
            .where(GameProfileRow.is_published.is_(True))
            .order_by(GameProfileRow.purchase_count.desc(), GameProfileRow.game_id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [game_to_domain(row) for row in rows]

    async def by_ids(self, game_ids: Sequence[GameId]) -> Sequence[GameProfile]:
        if not game_ids:
            return []
        statement = select(GameProfileRow).where(
            GameProfileRow.game_id.in_([game_id.value for game_id in game_ids])
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [game_to_domain(row) for row in rows]

    async def count_recommendable(self) -> int:
        total = await self._session.scalar(
            select(func.count()).select_from(GameProfileRow).where(GameProfileRow.is_published.is_(True))
        )
        return int(total or 0)

    async def needing_embedding(self, limit: int) -> Sequence[GameProfile]:
        """Published games with no semantic vector yet, most popular first.

        Ordered by popularity for the same reason the candidate scan is: `limit` truncates, and when a
        backlog exists the games worth spending a provider call on first are the ones people are buying.
        """
        statement = (
            select(GameProfileRow)
            .where(GameProfileRow.is_published.is_(True), GameProfileRow.dense.is_(None))
            .order_by(GameProfileRow.purchase_count.desc(), GameProfileRow.game_id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [game_to_domain(row) for row in rows]

    async def nearest_to(self, game: GameProfile, limit: int) -> Sequence[GameProfile]:
        """Content neighbours as a distance query, which is what the `vector` column buys.

        The sparse space can only answer this by loading the catalogue and doing the arithmetic in Python.
        Doing the same over dense vectors was measured at 118ms for 500 games at 1024 dimensions, three
        quarters of it spent deserialising rows the answer discards; here Postgres orders by cosine distance
        and returns the ten that survive.
        """
        if game.dense.is_empty:
            return []
        statement = (
            select(GameProfileRow)
            .where(
                GameProfileRow.is_published.is_(True),
                GameProfileRow.dense.is_not(None),
                GameProfileRow.game_id != game.game_id.value,
            )
            .order_by(GameProfileRow.dense.cosine_distance(list(game.dense.values)))
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [game_to_domain(row) for row in rows]


class SqlUserPreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId) -> UserPreference | None:
        row = await self._session.get(UserPreferenceRow, user_id.value)
        return preference_to_domain(row) if row is not None else None

    async def upsert(self, preference: UserPreference) -> None:
        row = await self._session.get(UserPreferenceRow, preference.user_id.value)
        if row is None:
            row = UserPreferenceRow(user_id=preference.user_id.value)
            self._session.add(row)
        write_preference(row, preference)

    async def with_signals(self, limit: int) -> Sequence[UserId]:
        """Who the sweep should regenerate. Most recently active first, so a bounded batch spends its budget
        on the users whose taste has actually moved rather than on dormant accounts."""
        statement = (
            select(UserPreferenceRow.user_id)
            .where(UserPreferenceRow.signal_count > 0)
            .order_by(UserPreferenceRow.updated_at.desc().nullslast(), UserPreferenceRow.user_id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [UserId(value) for value in rows]


class SqlOwnershipRepository:
    """The item-item half of Requirements §3.1, as one self-join."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, user_id: UserId, game_id: GameId, *, counted: bool) -> None:
        """On conflict the `counted` flag is raised but never lowered.

        A redelivered purchase must not un-count a signal already folded into the taste vector — the vector
        has no memory of which purchase contributed what, so it could not be credited a second time.
        """
        statement = insert(OwnershipRow).values(
            user_id=user_id.value,
            game_id=game_id.value,
            acquired_at=datetime.now(UTC),
            counted=counted,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[OwnershipRow.user_id, OwnershipRow.game_id],
                set_={"counted": OwnershipRow.counted.op("OR")(statement.excluded.counted)},
            )
        )

    async def uncounted_owners(self, game_id: GameId, limit: int) -> Sequence[UserId]:
        statement = (
            select(OwnershipRow.user_id)
            .where(OwnershipRow.game_id == game_id.value, OwnershipRow.counted.is_(False))
            .order_by(OwnershipRow.acquired_at)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [UserId(value) for value in rows]

    async def mark_counted(self, game_id: GameId, user_ids: Sequence[UserId]) -> None:
        if not user_ids:
            return
        await self._session.execute(
            update(OwnershipRow)
            .where(
                OwnershipRow.game_id == game_id.value,
                OwnershipRow.user_id.in_([user_id.value for user_id in user_ids]),
            )
            .values(counted=True)
        )

    async def co_purchases(
        self, user_id: UserId, owned: Sequence[GameId], limit: int
    ) -> Mapping[GameId, int]:
        """ "People who own what you own also own these", counted by distinct neighbour.

        Distinct on the *user*, not on the row: without it a neighbour who owns three of the caller's games
        would contribute three votes to every one of their other games, and the ranking would be decided by
        whoever happens to have the largest library rather than by agreement between people.
        """
        if not owned:
            return {}
        owned_ids = [game_id.value for game_id in owned]
        mine = aliased(OwnershipRow)
        theirs = aliased(OwnershipRow)
        statement = (
            select(theirs.game_id, func.count(func.distinct(theirs.user_id)))
            .select_from(mine)
            .join(theirs, mine.user_id == theirs.user_id)
            .where(
                mine.game_id.in_(owned_ids),
                mine.user_id != user_id.value,
                theirs.game_id.notin_(owned_ids),
            )
            .group_by(theirs.game_id)
            .order_by(func.count(func.distinct(theirs.user_id)).desc(), theirs.game_id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return {GameId(game_id): int(count) for game_id, count in rows}


class SqlRecommendationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for(self, user_id: UserId, recommendations: Sequence[Recommendation]) -> None:
        """Delete-then-insert inside the caller's transaction, so a reader never sees a half-replaced list.

        Wholesale replacement rather than a diff because ranks shift: a game moving from third to first
        changes every row between them, and reconciling that costs more than writing ten rows.
        """
        await self._session.execute(
            delete(RecommendationRow).where(RecommendationRow.user_id == user_id.value)
        )
        # Flushed here on purpose: SQLAlchemy would otherwise order the inserts before the delete within the
        # same flush and trip the (user_id, game_id) unique constraint on a game that was already suggested.
        await self._session.flush()
        for recommendation in recommendations:
            self._session.add(recommendation_row(recommendation))

    async def for_user(self, user_id: UserId, limit: int) -> Sequence[Recommendation]:
        statement = (
            select(RecommendationRow)
            .where(RecommendationRow.user_id == user_id.value)
            .order_by(RecommendationRow.rank)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [recommendation_to_domain(row) for row in rows]
