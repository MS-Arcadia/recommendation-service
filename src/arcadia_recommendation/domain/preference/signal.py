from enum import StrEnum


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
