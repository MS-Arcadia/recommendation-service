import json
from collections.abc import Mapping

import httpx

from arcadia_recommendation.application.ports.outbound.enrichment import (
    ExplainedGame,
    ExplanationRequest,
)
from arcadia_recommendation.domain.shared.ids import GameId
from arcadia_recommendation.infrastructure.observability.logging import get_logger

_logger = get_logger(__name__)

_SYSTEM = (
    "You explain game recommendations for a digital game store. "
    "For each candidate game, give one short reason it suits this player, based only on the games they "
    "already play and the candidate's own genres and tags. "
    "Write plainly, at most twelve words, no marketing language, no invented facts about the game. "
    'Answer with JSON only: {"reasons": [{"id": "<game id>", "reason": "<text>"}]}. '
    "Omit any game you cannot justify from what you were given."
)


class NoExplanations:
    """The explanation provider for a deployment that has not configured one — the default.

    Returning nothing rather than a placeholder is the point: `reasons` is already optional in the response
    envelope, and a client that renders "recommended for you" when there is no reason to give is telling the
    truth, where one rendering a generated-sounding sentence would not.
    """

    async def explain(self, request: ExplanationRequest) -> Mapping[GameId, tuple[str, ...]]:
        return {}


class OpenAiCompatibleExplainer:
    """Anti-corruption boundary for a chat-completions endpoint — §2.8 again, applied to the model provider.

    Every failure returns an empty mapping instead of raising. The ranking is complete before this class is
    called and is correct without it, so a provider outage costs a list with no reasons on it rather than a
    sweep that stops. That is the same bulkhead argument §2.5 makes for Catalog, applied to a dependency
    this service chose rather than inherited.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_tokens: int = 800,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens

    async def explain(self, request: ExplanationRequest) -> Mapping[GameId, tuple[str, ...]]:
        if not request.candidates:
            return {}
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _prompt(request)},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload, headers=headers)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, LookupError, TypeError, ValueError) as exc:
            _logger.warning("explanation_provider_failed", error=str(exc))
            return {}
        return _parse(content, request.candidates)


def _prompt(request: ExplanationRequest) -> str:
    played = ", ".join(_describe(game) for game in request.liked) or "nothing yet"
    candidates = "\n".join(f"- id={game.game_id} {_describe(game)}" for game in request.candidates)
    return f"The player already plays: {played}.\n\nCandidates:\n{candidates}"


def _describe(game: ExplainedGame) -> str:
    labels = ", ".join((*game.genres, *game.tags))
    return f"{game.title} ({labels})" if labels else game.title


def _parse(content: str, candidates: tuple[ExplainedGame, ...]) -> Mapping[GameId, tuple[str, ...]]:
    """Reads the model's answer, keeping only reasons that name a game actually asked about.

    The id filter is not defensive tidiness — it is what stops a model that invented or transposed an id
    from attaching one game's justification to another, which would be a wrong statement shown to a user
    rather than a missing one.
    """
    try:
        body = json.loads(content)
    except ValueError:
        _logger.warning("explanation_not_json")
        return {}
    entries = body.get("reasons") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        return {}

    wanted = {str(game.game_id): game.game_id for game in candidates}
    reasons: dict[GameId, tuple[str, ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        game_id = wanted.get(str(entry.get("id", "")))
        reason = str(entry.get("reason", "")).strip()
        if game_id is not None and reason:
            reasons[game_id] = (reason,)
    return reasons
