from dataclasses import dataclass
from enum import StrEnum

from arcadia_recommendation.domain.shared.ids import GameId


class SignalKind(StrEnum):
    """What a user did. Requirements §3.1 names purchases, reviews and published games as the inputs; these
    are the two that say something about *this* user's taste."""

    PURCHASE = "PURCHASE"
    REVIEW_LIKE = "REVIEW_LIKE"
    REVIEW_DISLIKE = "REVIEW_DISLIKE"


# How much each action moves a preference profile.
#
# A purchase is the strongest statement a user makes and is worth 1.0. A positive review is worth less
# despite being more explicit, because it is only ever left on something already bought — counting it as
# highly as the purchase would double-weight one game. A dislike is negative and deliberately smaller in
# magnitude than a like: it is evidence about one game, and taken at full strength it would push a user away
# from an entire genre over a single disappointment.
_WEIGHTS = {
    SignalKind.PURCHASE: 1.0,
    SignalKind.REVIEW_LIKE: 0.5,
    SignalKind.REVIEW_DISLIKE: -0.3,
}


def weight_of(kind: SignalKind) -> float:
    return _WEIGHTS[kind]


@dataclass(frozen=True, slots=True)
class SignalRecord:
    """One remembered action — `interaction_history` of ER د-۱۲.

    A taste vector is a running sum, and a sum cannot be taken apart again: once a game has been folded in,
    nothing recovers which game contributed what. That is fine while there is only one space to fold into,
    and fatal the moment there are two — a game's semantic embedding is computed by a third party and can
    arrive minutes after the purchase that should have counted it. Keeping the actions themselves means a
    taste vector can be rebuilt in any space at any time, so a late embedding costs a recomputation rather
    than a signal lost for good.
    """

    game_id: GameId
    weight: float
