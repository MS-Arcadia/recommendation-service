from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.recommendation.recommendation import RecommendationSource
from arcadia_recommendation.domain.shared.ids import GameId


@dataclass(frozen=True, slots=True)
class ScoredGame:
    """A candidate and the two component scores behind it, before the ranker blends them. Kept apart rather
    than pre-summed so `source` can say which half actually carried the suggestion."""

    game: GameProfile
    content: float
    collaborative: float
    reasons: tuple[str, ...] = ()

    @property
    def source(self) -> RecommendationSource:
        if self.content > 0.0 and self.collaborative > 0.0:
            return RecommendationSource.HYBRID
        if self.collaborative > 0.0:
            return RecommendationSource.COLLAB
        return RecommendationSource.CONTENT


class ContentBasedScorer:
    """Requirements §3.1's content half: cosine similarity between the user's taste vector and each game's
    genre/tag vector. Cheap, and the only half that works for a user nobody else resembles yet."""

    def score(self, preference: UserPreference, game: GameProfile) -> float:
        return max(0.0, preference.taste.cosine(game.embedding))

    def reasons(self, preference: UserPreference, game: GameProfile) -> tuple[str, ...]:
        return preference.taste.overlap(game.embedding)


class CollaborativeScorer:
    """Requirements §3.1's item-item half: "users who bought what you bought also bought this".

    Takes co-purchase counts already gathered by a repository rather than the raw ownership table, because
    the aggregation is a database self-join and doing it in Python would mean loading every ownership row on
    the platform to answer one request.

    Counts are shrunk toward zero by `count / (count + PRIOR)` rather than normalised against the strongest
    candidate in the batch. Relative normalisation was the first attempt and it is wrong in the case that
    matters most: with one candidate, that candidate is the strongest, so a *single* co-buyer scored a
    perfect 1.0 — and 0.35 of certainty beat a genuine genre match at 0.65 × 0.5. The platform's own
    end-to-end suite caught it, recommending a strategy game to a racing fan ahead of another racing game
    on the strength of one person having bought both.

    Shrinkage fixes it because the score becomes a statement about how much evidence there is, not about
    how one candidate compares to whoever else happens to be in the batch. One co-buyer earns 0.17, ten
    earn 0.67, and the number means the same thing on a platform with a hundred users and a million.
    """

    # How many co-buyers it takes to be believed halfway. Small, because this platform is small; raising it
    # makes the collaborative half quieter until the evidence is stronger.
    PRIOR = 5.0

    def scores(self, co_purchases: Mapping[GameId, int]) -> dict[GameId, float]:
        return {game_id: count / (count + self.PRIOR) for game_id, count in co_purchases.items() if count > 0}


class HybridRanker:
    """Blends the two scorers into the ordering Requirements §3.1 asks for.

    The content half is weighted higher. Collaborative filtering is the better signal once a platform has
    density, and has nothing to say before that — with few users, co-purchase counts are dominated by
    whichever two games happen to have been bought together twice. Weighting content above it degrades
    gracefully as the platform fills up rather than producing confident nonsense while it is empty.

    Owned games are removed here rather than by the query that produced the candidates, so the rule holds
    however a candidate was found — the collaborative half searches by co-purchase and would otherwise
    happily suggest a game back to one of the very users whose ownership put it in the running.
    """

    CONTENT_WEIGHT = 0.65
    COLLABORATIVE_WEIGHT = 0.35

    def __init__(
        self,
        content: ContentBasedScorer | None = None,
        collaborative: CollaborativeScorer | None = None,
    ) -> None:
        self._content = content or ContentBasedScorer()
        self._collaborative = collaborative or CollaborativeScorer()

    def rank(
        self,
        preference: UserPreference,
        candidates: Sequence[GameProfile],
        co_purchases: Mapping[GameId, int],
        limit: int,
    ) -> list[ScoredGame]:
        collaborative = self._collaborative.scores(co_purchases)
        scored: list[ScoredGame] = []
        for game in candidates:
            if not game.is_recommendable or preference.owns(game.game_id):
                continue
            content = self._content.score(preference, game)
            collab = collaborative.get(game.game_id, 0.0)
            if self.blend(content, collab) <= 0.0:
                continue
            scored.append(
                ScoredGame(
                    game=game,
                    content=content,
                    collaborative=collab,
                    reasons=self._content.reasons(preference, game),
                )
            )
        # Ties broken by popularity and then by game id: a stable total order, so two runs over unchanged
        # data produce the same list and a user's page does not reshuffle on refresh.
        scored.sort(
            key=lambda item: (
                -self.blend(item.content, item.collaborative),
                -item.game.purchase_count,
                str(item.game.game_id),
            )
        )
        return scored[:limit]

    def blend(self, content: float, collaborative: float) -> float:
        return self.CONTENT_WEIGHT * content + self.COLLABORATIVE_WEIGHT * collaborative


class TopSellersProvider:
    """The graceful degradation of ب-۹: what to serve a user with no history, or when generation has not run.

    Popularity is the only ranking available without knowing anything about the caller, and returning it is
    strictly better than returning nothing — an empty storefront section reads as a broken page, and a
    Recommendation outage must not look like one.
    """

    def rank(self, games: Sequence[GameProfile], preference: UserPreference, limit: int) -> list[GameProfile]:
        eligible = [game for game in games if game.is_recommendable and not preference.owns(game.game_id)]
        eligible.sort(key=lambda game: (-game.purchase_count, str(game.game_id)))
        return eligible[:limit]
