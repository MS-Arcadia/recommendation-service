# Arcadia — Recommendation Service

Personalised game recommendations for the [Arcadia](../PHASE01/README.md) platform.
Requirements **§3.1**, the competitive differentiator.

Python 3.14 · FastAPI · PostgreSQL · Kafka · Redis · port **8093**

```bash
make install
make run                 # http://localhost:8093/v1/docs
make test                # ruff, then mypy --strict
```

---

## What it does

Two questions, answered two different ways:

| | |
|---|---|
| `GET /v1/recommendations` | *What should this user play next?* — hybrid, personalised |
| `GET /v1/games/{id}/similar` | *What else is like this game?* — content only, public |

**This service has no API that changes anything.** Everything it knows arrives on three topics
owned by three other services, and it calls none of them. That is the point: it keeps serving
when Catalog and Store are both down.

```
game-events  ──┐
purchase-events├──▶  ingest  ──▶  taste vectors + ownership
review-events ──┘                         │
                                          ▼
                              batch sweep (every 5 min)
                                          │
                                          ▼
                        recommendations table ──▶ GET /v1/recommendations
                                          │
                                          └──▶ reco-events ──▶ Profile
```

---

## How the ranking works

### Games and users live in the same space

A game becomes a sparse vector over named features built from Catalog's genres and tags —
`genre:racing`, `tag:co-op`. Namespaced by kind, so a genre Catalog validates is not outvoted by
a tag nobody curates.

A user's **taste** is the scaled sum of the games they acted on. That is the whole of "learning"
here:

| Signal | Weight | |
|---|---|---|
| `PurchaseCompleted` | `+1.0` | the strongest statement a user makes |
| `ReviewPosted` — LIKE | `+0.5` | only ever left on something already bought; full weight would double-count the game |
| `ReviewPosted` — DISLIKE | `−0.3` | smaller on purpose: one disappointment should not evict a whole genre |

A **gift credits the recipient, not the payer.** Otherwise the platform learns that you love
whatever your friends play, and the recipient's own library stays recommendable back to them.

### Two scorers, blended 65 / 35

**Content** — cosine between the user's taste and each game's vector. Cosine rather than a dot
product because magnitudes mean nothing comparable: someone with forty games has a longer vector
than someone with two, and without normalising, every candidate scores higher for the first user
purely on volume.

**Collaborative** — item-item co-purchase, as one Postgres self-join: *everyone who owns what you
own, and what else they own*. Counted by **distinct neighbour**, not by row — otherwise the
ranking is decided by whoever has the largest library rather than by agreement between people.

Counts are shrunk by `count / (count + 5)` rather than normalised against the batch. The first
version normalised against the strongest candidate, which meant a *single* co-buyer scored a
perfect 1.0 — and 0.35 of certainty beat a real genre match at 0.65 × 0.5. The platform's
end-to-end suite caught it recommending a strategy game to a racing fan ahead of another racing
game, on the strength of one person having bought both.

Content is weighted higher deliberately. Collaborative filtering is the better signal once a
platform has density and has nothing to say before that, so this degrades gracefully as the
platform fills up instead of being confidently wrong while it is empty.

### Nothing is recommended that the user owns

Enforced at ranking rather than by the query that produced the candidates, so it holds however a
candidate got into the running — the collaborative half searches *by* co-purchase and would
otherwise happily suggest a game back to one of the very users whose ownership put it there.

### There is no empty answer

A user with no history, or one whose list has not been generated yet, gets **top sellers**,
labelled `source: FALLBACK` in the envelope. The read path has no failure mode that returns
nothing, so a storefront can render the section unconditionally rather than branching on whether
personalisation happened to be available. `purchase_count` is maintained by consuming purchases
rather than asked of Store, so this works during an outage of everything else.

---

## Why generation is a batch

Ranking touches every recommendable game and runs a self-join. Doing that per request would put
it between a user and their storefront against §2's 300ms p95 budget.

So it runs on a schedule (five minutes, matching Marketplace's matching engine so one machine is
not running two unrelated cadences), and a read becomes one indexed lookup. The cost is a list
that is minutes stale, which for a suggestion is not a cost.

`POST /v1/admin/recommendations/refresh` forces a sweep — for an operator investigating a
complaint, and for the end-to-end suite, which would otherwise have to wait five minutes to
assert anything.

The scheduler is **one task per replica, not a singleton.** N replicas do the same work N times;
the sweep is idempotent, so the cost is duplicated CPU rather than a wrong answer. Making it a
genuine singleton needs a distributed lock, which is machinery to add when there is more than one
replica to coordinate.

---

## The ordering problem worth knowing about

`PurchaseCompleted` and `GamePublished` arrive on independent topics, and **on a cold start
replaying history the purchase usually lands first.** A purchase for a game this service cannot
yet describe has nothing to contribute to a taste vector.

Those purchases are recorded as ownership with `counted = false` — so the collaborative half is
never left with a hole — and `GamePublished` credits them when the description turns up.

This was not theoretical. Booting against the platform's real 18 hours of event history produced
19 ownerships and **2** preference profiles. With backfill: 16 ownerships, **14** profiles, zero
uncounted. Without it the content half would have silently done almost nothing on every fresh
deploy.

`test_15_recommendations.py::test_no_signal_was_dropped_for_want_of_a_game` is the guard.

---

## API

| | |
|---|---|
| `GET /v1/recommendations` | the caller's own list · authenticated |
| `GET /v1/users/{id}/recommendations` | another user's · self or Support |
| `GET /v1/games/{id}/similar` | content neighbours · **public** |
| `POST /v1/admin/recommendations/refresh` | force a sweep · Support/Admin |
| `POST /v1/admin/recommendations/users/{id}/refresh` | force one user · Support/Admin |
| `GET /livez` `/readyz` `/metrics` | probes and Prometheus |

`/similar` is public because the answer does not depend on who is asking — it is a property of
the catalogue, which is itself public. Requiring a token would buy nothing and hide the rail from
a logged-out visitor.

Personalised reads are authenticated because the list is derived from what somebody bought, which
makes it a statement about a person rather than about the catalogue.

Every response carries `source` — `CONTENT`, `COLLAB`, `HYBRID` or `FALLBACK` — and each item
carries `reasons`, the shared features behind it. The score is a float nobody can interpret;
"because you like racing games" is the only part of the answer a user can check.

---

## Events

**Consumes** — `game-events` (`GamePublished`, `GameWithdrawn`), `purchase-events`
(`PurchaseCompleted`), `review-events` (`ReviewPosted`). Each has an anti-corruption handler in
`presentation/consumers/`; those services' payload shapes stop there.

**Produces** — `arcadia.reco.v1.RecommendationGenerated` on `reco-events`, consumed by Profile.
The whole list travels in the event rather than a "come and fetch it" notification, so a
regeneration for every user does not turn into a call back into this service at the moment the
batch is under most load.

Written through a **transactional outbox** in the same transaction as the ranking, and consumed
idempotently — belt and braces, because `purchase_count` only ever increments, so a duplicate
that slipped past both would not raise anything. It would quietly overstate a game's popularity
for ever, and the fallback ranking is built on that number.

---

## Layout

Clean Architecture, dependencies pointing inwards only.

```
domain/          policy/scoring.py is the recommender; policy/embedding.py the vector space
application/     use cases + ports; nothing here imports FastAPI or SQLAlchemy
infrastructure/  Postgres, Kafka, Redis, the scheduler, the outbox
presentation/    HTTP routers and the three Kafka consumers
composition.py   the only module allowed to import infrastructure.persistence
```

`PERSISTENCE_BACKEND`, `MESSAGING_BACKEND`, `CACHE_BACKEND` and `IDENTITY_BACKEND` each flip
independently, so the service runs entirely in-process with no infrastructure at all. See
`.env.example`.

The embedding is JSONB, not pgvector: that index earns its place for approximate
nearest-neighbour search over thousands of dense dimensions, and this space is a few dozen named
sparse ones scanned in full.

---

## Testing

This repository's own pipeline runs `ruff` and `mypy --strict`, then builds the image and boots
it with no database to prove `/readyz` reports 503 rather than lying.

The behaviour is covered by the platform's end-to-end suite,
[`infra/test/e2e/test_15_recommendations.py`](../infra/test/e2e/test_15_recommendations.py) — 24
checks against a running platform:

```bash
cd ../infra && make up && make wait && make e2e
```

That is deliberate rather than a gap. A unit test here can assert that a taste vector moves when a
handler is called. Nothing but a running platform can assert that Catalog still spells the field
`genres`, that Order still carries `recipient_id` rather than only `buyer_id`, or that Review still
sends `sentiment: "LIKE"`. Those three strings are a contract with three repositories and no
compiler checks them — and the same suite is what caught the collaborative scorer's normalisation
bug described above.

---

## Known limitations

**No TF-IDF.** Features are binary over genres and tags. Inverse document frequency needs a
corpus-wide pass and, at a few dozen games, mostly amplifies noise. It is an IDF weight on
`Embedding.of` when the catalogue is large enough to want it.

**No matrix factorisation.** Requirements §3.1 says "item-item / MF"; this takes the item-item
branch. MF means a training step, a model artifact and a serving story that no other service on
this platform has.

**Genre and tag only.** Browsing behaviour is listed as an input in §3.1 and no service currently
emits it. When one does, it is another `SignalKind` and a weight.
