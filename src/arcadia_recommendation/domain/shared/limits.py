from typing import Final

DEFAULT_RECOMMENDATION_COUNT: Final = 10
MAX_RECOMMENDATION_COUNT: Final = 50

# How many co-purchase neighbours the collaborative scorer considers before ranking. Bounded because the
# query behind it is a self-join over ownerships and its cost grows with the candidate set, not the answer.
MAX_COLLABORATIVE_CANDIDATES: Final = 200

# A feature token is `genre:racing` or `tag:co-op`, lowercased. Anything longer than this is a catalog
# mistake rather than a genre, and letting it through would put it in a JSONB key and an index.
MAX_FEATURE_CHARS: Final = 64

MAX_TITLE_CHARS: Final = 200

# Catalog's description is the richest input the semantic space has — a paragraph says more about a game
# than five tags do. Truncated because it is prose from another service and the embedding provider charges
# by input length, so an unbounded field is somebody else's cost decision.
MAX_DESCRIPTION_CHARS: Final = 2000

# How many signals a preference profile remembers individually. The vector is a running sum and cannot be
# rebuilt from itself, so the history is what lets a taste vector be recomputed in a different space when a
# game's embedding arrives late. Capped because it lives in one JSONB column, and the newest signals are the
# ones worth keeping when the cap bites.
MAX_SIGNAL_HISTORY: Final = 200

# How many candidates the explanation model is shown at once. One call per user per sweep, so this is the
# whole prompt size — and a model asked to justify fifty games at once justifies none of them well.
MAX_EXPLAINED_CANDIDATES: Final = 10
