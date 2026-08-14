from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from arcadia_recommendation.domain.shared.ids import GameId


class EmbeddingPort(Protocol):
    """Turns game text into vectors. The only reason this is a port and not a function is that the
    production implementation is somebody else's HTTP endpoint, and §2.8 says those get a translation layer
    rather than a direct dependency.

    `dimensions` is declared rather than discovered because the column that stores the result has a fixed
    width: a provider quietly returning a different size must be caught at the boundary, not by Postgres
    halfway through a sweep.
    """

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]: ...


@dataclass(frozen=True, slots=True)
class ExplainedGame:
    """One candidate as the explanation model sees it. Ids are carried so the answer can be matched back;
    everything else is what a model needs to say something true about the game."""

    game_id: GameId
    title: str
    genres: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExplanationRequest:
    """Everything one explanation call is allowed to know.

    `liked` is what the user actually played rather than their vector, because a coordinate cannot be put in
    a prompt and a list of titles can. This is deliberately a value object built in the application layer:
    the adapter that talks to the model never touches an aggregate, so no provider payload shape can reach
    inwards.
    """

    liked: tuple[ExplainedGame, ...]
    candidates: tuple[ExplainedGame, ...]


class ExplanationPort(Protocol):
    """Says why each candidate suits the user, in words a person can check.

    Returns a mapping rather than a list so a model that answers about six of ten games degrades to six
    explanations instead of a misalignment nobody notices. An implementation that fails must return nothing
    rather than raise — the ranking is already correct without it, and an explanation is not worth failing a
    sweep over.
    """

    async def explain(self, request: ExplanationRequest) -> Mapping[GameId, tuple[str, ...]]: ...
