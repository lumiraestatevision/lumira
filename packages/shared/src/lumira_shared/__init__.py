"""Lumira Shared – gemeinsame Modelle, Events, Streams, Storage."""

from lumira_shared.events import (
    PIPELINE,
    Artifact,
    ErrorInfo,
    Event,
    EventType,
    project_created,
    stream_name,
)
from lumira_shared.log import configure_logging, get_logger
from lumira_shared.service import ServiceContext, create_service_app, get_context
from lumira_shared.settings import BaseServiceSettings
from lumira_shared.storage import ObjectNotFoundError, S3Storage, artifact_key
from lumira_shared.streams import (
    ConsumerOptions,
    NonRetryableError,
    StreamConsumer,
    StreamPublisher,
)

__version__ = "0.1.0"

__all__ = [
    "PIPELINE",
    "Artifact",
    "BaseServiceSettings",
    "ConsumerOptions",
    "ErrorInfo",
    "Event",
    "EventType",
    "NonRetryableError",
    "ObjectNotFoundError",
    "S3Storage",
    "ServiceContext",
    "StreamConsumer",
    "StreamPublisher",
    "artifact_key",
    "configure_logging",
    "create_service_app",
    "get_context",
    "get_logger",
    "project_created",
    "stream_name",
]
