import hashlib
import math
from collections.abc import Sequence

import httpx

from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)


class ProviderUnavailableError(RuntimeError):
    """Raised when a third party could not answer. Named rather than leaking `httpx` upwards, so the use
    case that catches it does not import the transport."""


class HashingEmbedder:
    """The embedding provider for a machine with no network — the default, so `make run` works unconfigured.

    Each word of the game's text is hashed into one bucket and signed by a second hash bit, then the vector
    is normalised. This is the hashing trick, and it is honest about what it is: it recovers exact word
    overlap and nothing else, so it behaves roughly like the sparse scorer with the labels thrown away. It
    exists to keep the dense code path exercised without a key, not to be good at recommending.

    Deterministic across processes because it uses `blake2b` rather than `hash`, whose seed changes on every
    interpreter start — a vector that differed per replica would make the stored column meaningless.
    """

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> tuple[float, ...]:
        buckets = [0.0] * self._dimensions
        for word in text.lower().split():
            token = word.strip(".,:;!?()[]\"'").strip()
            if not token:
                continue
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            buckets[index] += 1.0 if digest[4] & 1 else -1.0
        magnitude = math.sqrt(sum(value * value for value in buckets))
        if magnitude == 0.0:
            return tuple(buckets)
        return tuple(value / magnitude for value in buckets)


class HuggingFaceEmbedder:
    """Anti-corruption boundary for a HuggingFace inference endpoint — §2.8's translation layer, applied to
    an embedding provider rather than a bank.

    The response shape is the whole reason this class is more than a POST. Feature-extraction endpoints
    answer with one vector per input on some models and a token-by-token matrix on others; the second is
    mean-pooled here so the rest of the service only ever sees the first. A provider changing its mind about
    which it returns is then a change to this file and nothing else.

    `prefix` is prepended to every input for models trained to expect one, such as e5's `query: `.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
        prefix: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._dimensions = dimensions
        self._timeout = timeout_seconds
        self._prefix = prefix

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        if not texts:
            return []
        payload = {
            "inputs": [self._prefix + text for text in texts],
            "options": {"wait_for_model": True},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._endpoint, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailableError(f"embedding provider failed: {exc}") from exc

        vectors = [_pooled(item) for item in _as_list(body)]
        if len(vectors) != len(texts):
            raise ProviderUnavailableError(
                f"embedding provider returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        for vector in vectors:
            if len(vector) != self._dimensions:
                raise ProviderUnavailableError(
                    f"embedding provider returned {len(vector)} dimensions, "
                    f"but this deployment stores {self._dimensions}"
                )
        return vectors


def _as_list(body: object) -> list[object]:
    if not isinstance(body, list):
        raise ProviderUnavailableError(f"embedding provider returned {type(body).__name__}, expected a list")
    return list(body)


def _pooled(item: object) -> tuple[float, ...]:
    """One input's answer, as a single vector.

    A list of floats is already one. A list of lists is a per-token matrix, which is averaged — the standard
    mean-pooling that sentence-transformers models apply internally and raw feature-extraction endpoints do
    not.
    """
    if not isinstance(item, list) or not item:
        raise ProviderUnavailableError("embedding provider returned an empty or non-list vector")
    if all(isinstance(value, int | float) for value in item):
        return tuple(float(value) for value in item)
    rows = [row for row in item if isinstance(row, list) and row]
    if not rows:
        raise ProviderUnavailableError("embedding provider returned an unrecognised vector shape")
    width = len(rows[0])
    return tuple(sum(float(row[index]) for row in rows) / len(rows) for index in range(width))
