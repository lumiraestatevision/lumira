"""Basis-Konfiguration aller Services (pydantic-settings, Werte aus Umgebung bzw. .env).

Jeder Service leitet davon ab und setzt mindestens ``service_name`` und ``port``:

    class ParserSettings(BaseServiceSettings):
        service_name: str = "parser"
        port: int = 8001
"""

from __future__ import annotations

import os
import socket
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lumira_shared.streams import ConsumerOptions


def _default_consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "lumira"
    service_version: str = "0.1.0"
    port: int = 8000
    environment: Literal["local", "test", "ci", "staging", "production"] = "local"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    redis_url: str = "redis://localhost:6379/0"
    s3_bucket: str = "lumira"

    consumer_name: str = Field(default_factory=_default_consumer_name)
    consumer_batch_size: int = Field(default=10, ge=1)
    consumer_block_ms: int | None = Field(
        default=5_000, ge=10, description="None = nicht blockieren"
    )
    consumer_claim_idle_ms: int = Field(default=30_000, ge=0)
    consumer_max_attempts: int = Field(default=3, ge=1)

    def consumer_options(self) -> ConsumerOptions:
        return ConsumerOptions(
            batch_size=self.consumer_batch_size,
            block_ms=self.consumer_block_ms,
            claim_idle_ms=self.consumer_claim_idle_ms,
            max_attempts=self.consumer_max_attempts,
        )
