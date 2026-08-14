from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Every knob this service reads from the environment. `jwt_secret` must match JWT_SECRET on the Auth
    service — it signs what this one verifies — and issuer and audience are required claims rather than
    decoration: every service checks both, so a token missing either is rejected platform-wide."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = "local"
    log_level: LogLevel = "INFO"

    persistence_backend: Literal["memory", "postgres"] = "memory"
    messaging_backend: Literal["inproc", "kafka"] = "inproc"
    identity_backend: Literal["fake", "jwt"] = "fake"
    cache_backend: Literal["memory", "redis"] = "memory"
    embedding_backend: Literal["hashing", "huggingface"] = "hashing"
    explanation_backend: Literal["none", "openai"] = "none"

    # Which space the content half of §3.1 ranks in. `sparse` is genre/tag cosine, explains itself, and is
    # the default because it needs nothing; `dense` is the semantic one and needs an embedding provider
    # behind it. Both are always ingested, so this flips either way without a migration or a reingest.
    scoring_backend: Literal["sparse", "dense"] = "sparse"

    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"  # noqa: S105
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "arcadia-auth"
    jwt_audience: str = "arcadia"

    database_url: str = (
        "postgresql+asyncpg://recommendation:recommendation@localhost:5432/arcadia_recommendation"
    )
    sql_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    run_migrations: bool = True

    # This service produces one topic and consumes three. The retry and DLQ companions of the produced topic
    # are created alongside it at boot; the three consumed ones belong to Catalog, Store and Review, and
    # creating them from here would mean choosing somebody else's partition count.
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_reco_events: str = "reco-events"
    kafka_topic_game_events: str = "game-events"
    kafka_topic_purchase_events: str = "purchase-events"
    kafka_topic_review_events: str = "review-events"
    kafka_consumer_group: str = "recommendation-service"
    kafka_consumer_max_retries: int = 3

    redis_url: str = "redis://localhost:6379/0"

    outbox_poll_interval_ms: int = 500
    outbox_batch_size: int = 50
    gauge_refresh_seconds: float = 15.0

    service_name: str = "recommendation-service"

    # How often the batch sweep regenerates every profile. Five minutes matches the platform's other
    # scheduled job (Marketplace's matching engine) so one machine is not running two unrelated cadences.
    generation_interval_seconds: int = 300
    # Off by default so a local run does not start ranking in the background before anyone asked it to.
    generation_enabled: bool = False
    # How many users one sweep will regenerate, and how much of the catalogue each ranking considers. Both
    # bound the work a single pass can do — an unbounded sweep is how a scheduled job becomes an outage.
    generation_batch_size: int = 500
    catalogue_scan_limit: int = 500

    # The stored width of a semantic vector, and the one setting a restart cannot change: it is baked into
    # the `vector(n)` column, so pointing at a model of a different width needs a migration and a
    # re-embedding of the catalogue. 1024 matches BAAI/bge-m3 and intfloat/multilingual-e5-large.
    embedding_dimensions: int = 1024
    embedding_endpoint: str = ""
    embedding_api_key: str = ""
    embedding_batch_size: int = 32
    embedding_timeout_seconds: float = 30.0
    embedding_prefix: str = ""

    explanation_base_url: str = ""
    explanation_api_key: str = ""
    explanation_model: str = "gpt-4o-mini"
    explanation_timeout_seconds: float = 30.0
    explanation_max_tokens: int = 800

    recommendation_cache_ttl_seconds: int = 30
    otel_exporter_otlp_endpoint: str | None = None
    otel_console_export: bool = False

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def is_production(self) -> bool:
        return self.app_env in ("staging", "prod")

    @property
    def uses_postgres(self) -> bool:
        return self.persistence_backend == "postgres"

    @property
    def uses_kafka(self) -> bool:
        return self.messaging_backend == "kafka"

    @property
    def uses_redis(self) -> bool:
        return self.cache_backend == "redis"

    @property
    def ranks_in_dense_space(self) -> bool:
        return self.scoring_backend == "dense"

    @model_validator(mode="after")
    def _reject_placeholder_secrets(self) -> Self:
        """Refuse to start on a development placeholder outside development.

        The defaults above are convenient and dangerous in equal measure: a deployment that forgets one
        variable boots happily and verifies every token against a secret published in this file. At boot,
        because a secret found wrong at boot costs a failed deploy and one found wrong later costs every
        request that trusted it."""
        if not self.is_production:
            return self

        if len(self.jwt_secret) < 32 or "change" in self.jwt_secret.lower():
            raise ValueError("JWT_SECRET is a development placeholder or shorter than 32 characters")
        if not self.jwt_issuer or not self.jwt_audience:
            raise ValueError("JWT_ISSUER and JWT_AUDIENCE are required; every service verifies both")
        if self.uses_postgres and "localhost" in self.database_url:
            raise ValueError("DATABASE_URL still points at localhost")
        return self

    @model_validator(mode="after")
    def _reject_unreachable_providers(self) -> Self:
        """Refuse to start when a third-party backend was selected without an address to reach it at.

        Checked at boot in every environment, not only production, because the failure it prevents is
        invisible: an embedding backend with no endpoint does not raise on the read path, it just leaves
        every game unembedded, and a dense ranking over empty vectors returns a plausible-looking list
        ordered by nothing at all.
        """
        if self.embedding_backend == "huggingface" and not self.embedding_endpoint:
            raise ValueError("EMBEDDING_BACKEND=huggingface requires EMBEDDING_ENDPOINT")
        if self.explanation_backend == "openai" and not self.explanation_base_url:
            raise ValueError("EXPLANATION_BACKEND=openai requires EXPLANATION_BASE_URL")
        if self.embedding_dimensions < 1:
            raise ValueError("EMBEDDING_DIMENSIONS must be positive")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
