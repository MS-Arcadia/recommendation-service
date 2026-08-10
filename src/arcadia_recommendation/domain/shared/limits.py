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
