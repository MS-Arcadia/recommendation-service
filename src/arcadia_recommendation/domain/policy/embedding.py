import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.domain.shared.errors import InvariantViolation


def feature(kind: str, raw: str) -> str:
    """One dimension of the content space, as `genre:racing` or `tag:co-op`.

    Namespaced by kind on purpose: a genre called "indie" and a tag called "indie" are different claims about
    a game, and collapsing them would let a tag nobody curates outvote the genre Catalog validates.
    """
    normalised = "-".join(raw.strip().lower().split())
    if not normalised:
        raise InvariantViolation(f"{kind} must not be blank")
    token = f"{kind}:{normalised}"
    if len(token) > limits.MAX_FEATURE_CHARS:
        raise InvariantViolation(f"feature {token!r} must be at most {limits.MAX_FEATURE_CHARS} characters")
    return token


@dataclass(frozen=True, slots=True)
class Embedding:
    """A sparse content vector over named features, held as a read-only mapping.

    Sparse and named rather than a dense float array, because the space is genres and tags — a few dozen
    dimensions of which any one game occupies five. A dense vector would be mostly zeroes, and the names are
    what make a recommendation explainable: `overlap` is why a game was suggested, and it is a set of words
    rather than a coordinate nobody can read.
    """

    weights: Mapping[str, float]

    def __post_init__(self) -> None:
        cleaned = {name: value for name, value in self.weights.items() if value != 0.0}
        object.__setattr__(self, "weights", MappingProxyType(cleaned))

    @classmethod
    def empty(cls) -> Embedding:
        return cls({})

    @classmethod
    def of(cls, features: Iterable[str]) -> Embedding:
        """An unweighted bag of features — every genre and tag counts once, however often it is repeated."""
        return cls(dict.fromkeys(features, 1.0))

    @property
    def is_empty(self) -> bool:
        return not self.weights

    @property
    def magnitude(self) -> float:
        return math.sqrt(sum(value * value for value in self.weights.values()))

    def plus(self, other: Embedding, scale: float = 1.0) -> Embedding:
        """Accumulates another vector into this one. This is the whole of "learning" here: a preference
        profile is the scaled sum of the games a user acted on."""
        combined = dict(self.weights)
        for name, value in other.weights.items():
            combined[name] = combined.get(name, 0.0) + value * scale
        return Embedding(combined)

    def cosine(self, other: Embedding) -> float:
        """Similarity in [-1, 1], and in practice in [0, 1] since a dislike is the only negative weight.

        Cosine rather than a dot product because magnitudes here mean nothing comparable: a user who has
        bought forty games has a longer vector than one who bought two, and without normalising, every
        candidate would score higher for the first user purely from volume.
        """
        left, right = self.magnitude, other.magnitude
        if left == 0.0 or right == 0.0:
            return 0.0
        shared = self.weights.keys() & other.weights.keys()
        if not shared:
            return 0.0
        dot = sum(self.weights[name] * other.weights[name] for name in shared)
        return dot / (left * right)

    def overlap(self, other: Embedding) -> tuple[str, ...]:
        """The features both vectors carry a positive weight for, strongest first — the human-readable
        reason a game was recommended."""
        shared = [
            name
            for name in self.weights.keys() & other.weights.keys()
            if self.weights[name] > 0.0 and other.weights[name] > 0.0
        ]
        return tuple(sorted(shared, key=lambda name: (-self.weights[name], name)))


@dataclass(frozen=True, slots=True)
class DenseEmbedding:
    """A semantic vector over an anonymous space, as ER د-۱۲'s `vector embedding` column holds it.

    The dimensions have no names, which is the whole difference from `Embedding` and the reason the two
    coexist rather than one replacing the other. Named features can explain themselves — `overlap` returns
    the words a suggestion rests on. These cannot: a coordinate is not a reason, so a service ranking in
    this space has to source its explanations somewhere else. What it buys in exchange is the thing the
    sparse space cannot do at all — two racing games that share no tag are still neighbours here.
    """

    values: tuple[float, ...]

    @classmethod
    def empty(cls) -> DenseEmbedding:
        return cls(())

    @classmethod
    def of(cls, values: Iterable[float]) -> DenseEmbedding:
        return cls(tuple(float(value) for value in values))

    @property
    def is_empty(self) -> bool:
        return not self.values

    @property
    def dimensions(self) -> int:
        return len(self.values)

    @property
    def magnitude(self) -> float:
        return math.sqrt(sum(value * value for value in self.values))

    def plus(self, other: DenseEmbedding, scale: float = 1.0) -> DenseEmbedding:
        """Accumulates another vector into this one, as the sparse space does.

        An empty vector is the additive identity in either direction rather than an error: a game whose
        embedding has not been computed yet must not stop a taste vector being folded, and a profile with
        no signals must accept its first.
        """
        if other.is_empty:
            return self
        if self.is_empty:
            return DenseEmbedding(tuple(value * scale for value in other.values))
        if len(self.values) != len(other.values):
            raise InvariantViolation(
                f"cannot add a {other.dimensions}-dimensional vector to a {self.dimensions}-dimensional one"
            )
        return DenseEmbedding(tuple(a + b * scale for a, b in zip(self.values, other.values, strict=True)))

    def cosine(self, other: DenseEmbedding) -> float:
        """Similarity in [-1, 1]. Mismatched dimensions score zero rather than raising: the only way to hold
        two widths at once is a model change mid-flight, and a ranking that skips the stale half of the
        catalogue is a better outcome than a sweep that dies on it."""
        if self.is_empty or other.is_empty or len(self.values) != len(other.values):
            return 0.0
        left, right = self.magnitude, other.magnitude
        if left == 0.0 or right == 0.0:
            return 0.0
        dot = sum(a * b for a, b in zip(self.values, other.values, strict=True))
        return dot / (left * right)
