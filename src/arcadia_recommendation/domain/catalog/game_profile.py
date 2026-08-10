from dataclasses import dataclass, field, replace
from datetime import datetime

from arcadia_recommendation.domain.policy.embedding import Embedding, feature
from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.domain.shared.errors import InvariantViolation
from arcadia_recommendation.domain.shared.ids import GameId, UserId


@dataclass(frozen=True, slots=True)
class GameProfile:
    """This service's read-model of a game, built from `game-events` and never from a call into Catalog.

    Catalog owns the game; what is kept here is only what ranking needs — the features a game is described
    by, and how often it has been bought. `purchase_count` is the popularity signal the fallback ranks on,
    and it is maintained by consuming purchases rather than asked for, so a recommendation can still be
    served when Store and Catalog are both down.
    """

    game_id: GameId
    developer_id: UserId
    title: str
    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    published_at: datetime | None = None
    is_published: bool = True
    purchase_count: int = 0
    embedding: Embedding = field(default_factory=Embedding.empty)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvariantViolation("game title must not be blank")
        if len(self.title) > limits.MAX_TITLE_CHARS:
            raise InvariantViolation(f"game title must be at most {limits.MAX_TITLE_CHARS} characters")
        if self.purchase_count < 0:
            raise InvariantViolation("purchase count must not be negative")
        if self.embedding.is_empty and (self.genres or self.tags):
            object.__setattr__(self, "embedding", _embed(self.genres, self.tags))

    @classmethod
    def published(
        cls,
        *,
        game_id: GameId,
        developer_id: UserId,
        title: str,
        genres: tuple[str, ...],
        tags: tuple[str, ...],
        published_at: datetime | None,
    ) -> GameProfile:
        return cls(
            game_id=game_id,
            developer_id=developer_id,
            title=title.strip(),
            genres=genres,
            tags=tags,
            published_at=published_at,
            is_published=True,
            embedding=_embed(genres, tags),
        )

    def redescribed(self, *, title: str, genres: tuple[str, ...], tags: tuple[str, ...]) -> GameProfile:
        """A republished game keeps its purchase history. Resetting it would make every edit to a game's
        description look like a brand new release to the fallback ranking."""
        return replace(
            self,
            title=title.strip(),
            genres=genres,
            tags=tags,
            is_published=True,
            embedding=_embed(genres, tags),
        )

    def bought(self) -> GameProfile:
        return replace(self, purchase_count=self.purchase_count + 1)

    def withdrawn(self) -> GameProfile:
        """Delisted games stay in the read-model but stop being recommendable. Deleting the row instead would
        also delete the co-purchase history of everyone who owns it, and that history is still evidence about
        the games that remain."""
        return replace(self, is_published=False)

    @property
    def is_recommendable(self) -> bool:
        return self.is_published


def _embed(genres: tuple[str, ...], tags: tuple[str, ...]) -> Embedding:
    return Embedding.of(
        [feature("genre", genre) for genre in genres if genre.strip()]
        + [feature("tag", tag) for tag in tags if tag.strip()]
    )
