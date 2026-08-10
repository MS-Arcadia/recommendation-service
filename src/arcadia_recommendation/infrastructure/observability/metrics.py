from collections.abc import Callable

from opentelemetry import metrics

_METER_NAME = "arcadia.recommendation"


class RecommendationMetrics:
    """The domain metrics that map to real SLOs rather than to code structure. Outbox gauges are observable
    callbacks so the dispatcher does not have to push on every poll."""

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)
        self.signals_ingested = meter.create_counter(
            "reco_signals_ingested_total", description="Purchase and review signals folded into a profile"
        )
        self.recommendations_generated = meter.create_counter(
            "reco_recommendations_generated_total", description="Recommendations written by the batch"
        )
        self.served = meter.create_counter(
            "reco_served_total", description="Recommendation lists served, labelled by source"
        )
        self.generation_duration = meter.create_histogram(
            "reco_generation_duration", unit="ms", description="How long one batch sweep took"
        )
        self._meter = meter

    def observe_outbox(
        self, pending: Callable[[], int], dlq_depth: Callable[[], int], known_games: Callable[[], int]
    ) -> None:
        self._meter.create_observable_gauge(
            "reco_outbox_pending",
            callbacks=[lambda _options: [metrics.Observation(pending())]],
            description="Outbox records awaiting publication",
        )
        self._meter.create_observable_gauge(
            "reco_outbox_dlq_depth",
            callbacks=[lambda _options: [metrics.Observation(dlq_depth())]],
            description="Dead-lettered outbox records",
        )
        self._meter.create_observable_gauge(
            "reco_known_games",
            callbacks=[lambda _options: [metrics.Observation(known_games())]],
            description="Games this service can currently recommend",
        )
