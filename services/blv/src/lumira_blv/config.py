from typing import Literal, Self

from pydantic import SecretStr, model_validator

from lumira_shared import BaseServiceSettings

Provider = Literal["anthropic", "gemini", "ollama"]
_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


class BLVSettings(BaseServiceSettings):
    service_name: str = "blv"
    port: int = 8004

    # Welcher LLM-Anbieter das LV auswertet. gemini: Gratistarif NUR für lokale Entwicklung
    # (siehe logic/gemini.py). ollama: lokales Modell ohne Key (Compose-Profil "llm").
    # Produktion: anthropic.
    blv_provider: Provider = "anthropic"
    # auto: LLM, wenn der Anbieter nutzbar ist (Key gesetzt bzw. ollama), sonst Stub
    # llm: LLM Pflicht | stub: nie LLM
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

    # Lokales Modell über Ollama (siehe logic/ollama.py). Das Modell lädt der Compose-Service
    # "ollama-pull" beim Start herunter.
    ollama_url: str = "http://ollama:11434"
    blv_ollama_model: str = "qwen3.5:4b"
    # Kontextfenster in Tokens (Eingabe + Ausgabe). Größer = längere LVs am Stück, aber mehr
    # Grafikspeicher. Längere Dokumente werden seitenweise in Abschnitte geteilt.
    blv_ollama_num_ctx: int = 32_768
    # "Nachdenken" vor der Antwort: evtl. genauer, aber deutlich langsamer
    blv_ollama_think: bool = False
    blv_ollama_timeout_s: int = 1800

    blv_max_pdf_mb: int = 30  # Claude: 32 MB, Gemini: 50 MB pro Anfrage
    blv_max_pages: int = 300

    @model_validator(mode="after")
    def _key_required_for_llm(self) -> Self:
        if self.blv_mode == "llm" and self.blv_provider != "ollama" and not self.api_key:
            raise ValueError(
                f"BLV_MODE=llm mit BLV_PROVIDER={self.blv_provider} benötigt {_KEY_ENV[self.blv_provider]}"
            )
        return self

    @property
    def api_key(self) -> str | None:
        match self.blv_provider:
            case "anthropic":
                secret = self.anthropic_api_key
            case "gemini":
                secret = self.gemini_api_key
            case "ollama":
                return None
        key = secret.get_secret_value().strip() if secret else ""
        return key or None

    @property
    def model(self) -> str:
        match self.blv_provider:
            case "anthropic":
                return self.blv_model
            case "gemini":
                return self.blv_gemini_model
            case "ollama":
                return self.blv_ollama_model

    @property
    def gemini_models(self) -> list[str]:
        fallbacks = [m.strip() for m in self.blv_gemini_fallback_models.split(",") if m.strip()]
        return list(dict.fromkeys([self.blv_gemini_model, *fallbacks]))

    @property
    def use_llm(self) -> bool:
        if self.blv_mode != "auto":
            return self.blv_mode == "llm"
        return self.blv_provider == "ollama" or self.api_key is not None
