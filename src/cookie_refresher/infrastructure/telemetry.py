"""
OpenTelemetry setup — backend-agnostic via OTLP HTTP exporter.

Configured by two settings:
  OTLP_ENDPOINT  — default http://jaeger:4318  (any OTLP-compatible backend)
  SERVICE_NAME   — fixed to "cookie-refresher"

Swap backends by pointing OTLP_ENDPOINT at Grafana Tempo, Honeycomb,
Datadog Agent, etc. — no code change required.
"""
import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_SERVICE_NAME = "cookie-refresher"


def setup_telemetry(app: FastAPI, otlp_endpoint: str) -> None:
    resource = Resource.create({"service.name": _SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    logger.info("OpenTelemetry configured — exporting to %s", otlp_endpoint)
