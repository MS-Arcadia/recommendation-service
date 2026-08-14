from dataclasses import dataclass, field, replace
from datetime import datetime

from arcadia_recommendation.domain.policy.embedding import DenseEmbedding, Embedding, feature
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

    Two content vectors are held, not one. `embedding` is derived here from genres and tags and is always
    present; `dense` is computed by an embedding provider and is empty until it has been. Carrying both is
    what lets the ranking space be chosen by configuration rather than by a migration.
    """

    game_id: GameId
    developer_id: UserId
    title: str
    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    description: str = ""
    published_at: datetime | None = None
    is_published: bool = True
    purchase_count: int = 0
    embedding: Embedding = field(default_factory=Embedding.empty)
    dense: DenseEmbedding = field(default_factory=DenseEmbedding.empty)

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
        description: str = "",
    ) -> GameProfile:
        return cls(
            game_id=game_id,
            developer_id=developer_id,
            title=title.strip(),
            genres=genres,
            tags=tags,
            description=_trimmed(description),
            published_at=published_at,
            is_published=True,
            embedding=_embed(genres, tags),
        )

    def redescribed(
        self, *, title: str, genres: tuple[str, ...], tags: tuple[str, ...], description: str = ""
    ) -> GameProfile:
        """A republished game keeps its purchase history. Resetting it would make every edit to a game's
        description look like a brand new release to the fallback ranking.

        The dense vector is discarded rather than kept, because it was computed from the words that just
        changed. Clearing it queues the game for re-embedding; keeping it would leave a game ranked forever
        by a description nobody can read any more.
        """
        return replace(
            self,
            title=title.strip(),
            genres=genres,
            tags=tags,
            description=_trimmed(description),
            is_published=True,
            embedding=_embed(genres, tags),
            dense=DenseEmbedding.empty(),
        )

    def embedded(self, dense: DenseEmbedding) -> GameProfile:
        return replace(self, dense=dense)

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

    @property
    def needs_embedding(self) -> bool:
        return self.is_published and self.dense.is_empty

    @property
    def embedding_text(self) -> str:
        """What the embedding provider is shown. The genres and tags are repeated in prose beside the
        description rather than sent as a bare list, because the provider embeds sentences: a curated label
        carries more weight when it appears as a claim about the game than as a token beside it."""
        parts = [self.title]
        if self.genres:
            parts.append(f"Genres: {', '.join(self.genres)}.")
        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}.")
        if self.description:
            parts.append(self.description)
        return " ".join(parts)


def _embed(genres: tuple[str, ...], tags: tuple[str, ...]) -> Embedding:
    return Embedding.of(
        [feature("genre", genre) for genre in genres if genre.strip()]
        + [feature("tag", tag) for tag in tags if tag.strip()]
    )


def _trimmed(description: str) -> str:
    return description.strip()[: limits.MAX_DESCRIPTION_CHARS]
