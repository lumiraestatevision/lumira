from typing import Literal, Self

from pydantic import SecretStr, model_validator

from lumira_shared import BaseServiceSettings

Provider = Literal["anthropic", "gemini"]
_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


class BLVSettings(BaseServiceSettings):
    service_name: str = "blv"
    port: int = 8004

    # Welcher LLM-Anbieter das LV auswertet. gemini: Gratistarif NUR für lokale Entwicklung
    # (siehe logic/gemini.py). Produktion: anthropic.
    blv_provider: Provider = "anthropic"
    # auto: LLM, wenn ein Key für den Anbieter gesetzt ist, sonst Stub | llm: Key Pflicht | stub
    blv_mode: Literal["auto", "llm", "stub"] = "auto"

    anthropic_api_key: SecretStr | None = None
    blv_model: str = "claude-opus-5"
    # Server-seitiger Fallback, falls die Sicherheitsklassifikatoren des Modells eine Anfrage
    # ablehnen (empfohlen für claude-opus-5; bei anderen Modellen ggf. abschalten).
    blv_llm_fallbacks: bool = True

    gemini_api_key: SecretStr | None = None
    blv_gemini_model: str = "gemini-3.8-flash"
    # Bei Überlastung (503), erschöpftem Kontingent (429) oder nicht freigeschaltetem Modell (404)
    # werden diese Modelle der Reihe nach versucht (kommagetrennt, leer = kein Ausweichen).
    blv_gemini_fallback_models: str = "gemini-3.5-flash,gemini-3.5-flash-lite"

    blv_max_pdf_mb: int = 30  # Claude: 32 MB, Gemini: 50 MB pro Anfrage
    blv_max_pages: int = 300

    @model_validator(mode="after")
    def _key_required_for_llm(self) -> Self:
        if self.blv_mode == "llm" and not self.api_key:
            raise ValueError(
                f"BLV_MODE=llm mit BLV_PROVIDER={self.blv_provider} benötigt {_KEY_ENV[self.blv_provider]}"
            )
        return self

    @property
    def api_key(self) -> str | None:
        secret = self.anthropic_api_key if self.blv_provider == "anthropic" else self.gemini_api_key
        key = secret.get_secret_value().strip() if secret else ""
        return key or None

    @property
    def model(self) -> str:
        return self.blv_model if self.blv_provider == "anthropic" else self.blv_gemini_model

    @property
    def gemini_models(self) -> list[str]:
        fallbacks = [m.strip() for m in self.blv_gemini_fallback_models.split(",") if m.strip()]
        return list(dict.fromkeys([self.blv_gemini_model, *fallbacks]))

    @property
    def use_llm(self) -> bool:
        return self.blv_mode == "llm" or (self.blv_mode == "auto" and self.api_key is not None)
