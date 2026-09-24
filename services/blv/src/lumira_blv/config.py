from typing import Literal, Self

from pydantic import SecretStr, model_validator

from lumira_shared import BaseServiceSettings


class BLVSettings(BaseServiceSettings):
    service_name: str = "blv"
    port: int = 8004

    anthropic_api_key: SecretStr | None = None
    # auto: LLM, wenn ein API-Key gesetzt ist, sonst Stub | llm: Key Pflicht | stub: nie LLM
    blv_mode: Literal["auto", "llm", "stub"] = "auto"
    blv_model: str = "claude-opus-5"
    # Server-seitiger Fallback, falls die Sicherheitsklassifikatoren des Modells eine Anfrage
    # ablehnen (empfohlen für claude-opus-5; bei anderen Modellen ggf. abschalten).
    blv_llm_fallbacks: bool = True
    blv_max_pdf_mb: int = 30  # API-Limit: 32 MB pro Anfrage
    blv_max_pages: int = 300

    @model_validator(mode="after")
    def _key_required_for_llm(self) -> Self:
        if self.blv_mode == "llm" and not self.api_key:
            raise ValueError("BLV_MODE=llm benötigt ANTHROPIC_API_KEY")
        return self

    @property
    def api_key(self) -> str | None:
        key = self.anthropic_api_key.get_secret_value() if self.anthropic_api_key else ""
        return key or None

    @property
    def use_llm(self) -> bool:
        return self.blv_mode == "llm" or (self.blv_mode == "auto" and self.api_key is not None)
