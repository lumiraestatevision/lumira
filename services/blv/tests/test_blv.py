from __future__ import annotations

import io
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from moto import mock_aws
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from lumira_blv.config import BLVSettings
from lumira_blv.handler import check_pdf, handle_rooms_classified
from lumira_blv.logic import claude, gemini, ollama
from lumira_blv.logic.extraction import (
    BLVExtraction,
    ExtractedMaterial,
    ExtractedVariant,
    to_blv_result,
)
from lumira_shared import (
    Artifact,
    Event,
    EventType,
    NonRetryableError,
    S3Storage,
    ServiceContext,
    StreamPublisher,
    get_logger,
    project_created,
)
from lumira_shared.models import BLVResult, ColorSource, MaterialCategory, RoomType
from lumira_shared.testing import PdfPage, simple_pdf

MaterialFactory = Callable[..., ExtractedMaterial]  # Fixture make_material aus conftest.py


def _settings(**overrides: Any) -> BLVSettings:
    return BLVSettings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


def _pdf(pages: int = 1) -> bytes:
    page = PdfPage(texts=[(72, 500, "Pos. 02.03.0010 Eichenparkett geölt", 11)])
    return simple_pdf([page] * pages)


@pytest.fixture
def extraction(make_material: MaterialFactory) -> BLVExtraction:
    return BLVExtraction(
        materials=[
            make_material("m1", color_hex="#AABBCC", blv_position="02.03.0010"),
            make_material(
                "m2",
                category=MaterialCategory.TILES,
                color_hex="weiß",
                room_types=[RoomType.BATHROOM],
            ),
            make_material("m1"),  # doppelt
        ],
        variants=[
            ExtractedVariant(
                name="Standard",
                description=None,
                material_ids=["m1", "m2", "m9"],
                surcharge_eur=None,
                is_default=True,
            ),
            ExtractedVariant(
                name="Premium",
                description=None,
                material_ids=["m1"],
                surcharge_eur=4500.0,
                is_default=True,
            ),
        ],
        notes=[],
    )


# ------------------------------------------------------------------ Settings
def test_auto_mode_uses_llm_only_with_key() -> None:
    assert _settings().use_llm is False
    assert _settings(anthropic_api_key="sk-test").use_llm is True
    assert _settings(anthropic_api_key="sk-test", blv_mode="stub").use_llm is False


def test_llm_mode_requires_key() -> None:
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        _settings(blv_mode="llm")
    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        _settings(blv_mode="llm", blv_provider="gemini", anthropic_api_key="sk-test")


def test_provider_selects_key_and_model() -> None:
    claude_settings = _settings(anthropic_api_key="sk-a", gemini_api_key="g-key")
    assert (claude_settings.api_key, claude_settings.model) == ("sk-a", "claude-opus-5")
    gemini_settings = _settings(
        blv_provider="gemini", anthropic_api_key="sk-a", gemini_api_key="g-key"
    )
    assert (gemini_settings.api_key, gemini_settings.model) == ("g-key", "gemini-3.8-flash")
    # Key nur für den anderen Anbieter → Stub
    assert _settings(blv_provider="gemini", anthropic_api_key="sk-a").use_llm is False
    assert _settings(blv_provider="gemini", gemini_api_key="  ").use_llm is False


def test_ollama_needs_no_key() -> None:
    local = _settings(blv_provider="ollama", anthropic_api_key="sk-a")
    assert (local.api_key, local.model, local.use_llm) == (None, "qwen3.5:4b", True)
    assert _settings(blv_provider="ollama", blv_mode="llm").use_llm is True
    assert _settings(blv_provider="ollama", blv_mode="stub").use_llm is False


# ------------------------------------------------------------------ Extraktion → BLVResult
def test_extraction_is_sanitised(extraction: BLVExtraction) -> None:
    result = to_blv_result(
        extraction, project_id=uuid.uuid4(), source_key="lv.pdf", extracted_by="claude-opus-5"
    )

    assert [m.id for m in result.materials] == ["m1", "m2"]
    assert result.materials[0].color_hex == "#AABBCC"
    # "weiß" ist kein Hex-Wert → Annahme aus dem Materialwort, als solche gekennzeichnet
    assert result.materials[1].color_source is ColorSource.ASSUMED
    standard, premium = result.variants
    assert standard.material_ids == ["m1", "m2"]  # m9 entfernt
    assert standard.is_default
    assert not premium.is_default  # nur eine Standardvariante
    assert premium.material_ids == ["m1", "m2"]  # Fliese m2 aus Standard übernommen
    assert str(premium.surcharge_eur) == "4500.0"
    assert any("m9" in n for n in result.notes)
    assert any("Doppelte" in n for n in result.notes)


def test_llm_schema_is_json_serialisable() -> None:
    schema = BLVExtraction.model_json_schema()
    assert set(schema["properties"]) == {"materials", "variants", "notes"}


# ------------------------------------------------------------------ PDF-Prüfung
def test_check_pdf_limits() -> None:
    assert check_pdf(_pdf(2), _settings()) == 2
    with pytest.raises(NonRetryableError, match="Seiten"):
        check_pdf(_pdf(3), _settings(blv_max_pages=2))
    with pytest.raises(NonRetryableError, match="nicht lesbar"):
        check_pdf(b"kein pdf", _settings())


def test_check_pdf_rejects_password_protection() -> None:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(_pdf())))
    writer.encrypt(user_password="geheim", owner_password="owner", algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(NonRetryableError, match="passwortgeschützt"):
        check_pdf(buffer.getvalue(), _settings())


# ------------------------------------------------------------------ Handler
@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Storage]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        yield S3Storage("lumira-test")


async def _ctx(storage: S3Storage, settings: BLVSettings) -> ServiceContext:
    await storage.ensure_bucket()
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    return ServiceContext(
        settings=settings,
        redis=redis,
        publisher=StreamPublisher(redis),
        storage=storage,
        log=get_logger("t"),
    )


def _classified(blv_key: str | None) -> Event:
    project_id = uuid.uuid4()
    return project_created(project_id, floor_plan_key="plan.dxf", blv_key=blv_key).follow_up(
        EventType.ROOMS_CLASSIFIED,
        producer="classifier",
        artifacts={Artifact.CLASSIFIED_PLAN: "c.json"},
    )


@pytest.fixture
async def stub_ctx(storage: S3Storage) -> ServiceContext:
    return await _ctx(storage, _settings())


async def test_without_blv_uses_standard_equipment(stub_ctx: ServiceContext) -> None:
    result_event = await handle_rooms_classified(_classified(None), stub_ctx)

    assert result_event.type is EventType.BLV_PROCESSED
    assert result_event.data["extracted_by"] == "stub"
    result = await stub_ctx.storage.get_model(
        result_event.artifacts[Artifact.BLV_RESULT], BLVResult
    )
    assert result.default_variant is not None
    assert result.default_variant.name == "Standard"
    assert result.source_key is None


async def test_without_key_blv_is_not_sent_to_llm(
    stub_ctx: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def must_not_be_called(*args: Any, **kwargs: Any) -> BLVExtraction:
        raise AssertionError("LLM darf im Stub-Modus nicht aufgerufen werden")

    monkeypatch.setattr(claude, "extract_with_claude", must_not_be_called)
    monkeypatch.setattr(gemini, "extract_with_gemini", must_not_be_called)
    monkeypatch.setattr(ollama, "extract_with_ollama", must_not_be_called)
    result_event = await handle_rooms_classified(_classified("projects/x/upload/blv.pdf"), stub_ctx)
    assert result_event.data["extracted_by"] == "stub"


@pytest.mark.parametrize(
    ("settings", "module", "function", "expected_call", "used_model"),
    [
        (
            {"anthropic_api_key": "sk-test"},
            claude,
            "extract_with_claude",
            {"api_key": "sk-test", "model": "claude-opus-5", "use_fallbacks": True},
            "claude-opus-5",
        ),
        (
            {"blv_provider": "gemini", "gemini_api_key": "g-test"},
            gemini,
            "extract_with_gemini",
            {
                "api_key": "g-test",
                "models": ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"],
            },
            "gemini-3.5-flash",  # z. B. nach Ausweichen wegen Überlastung
        ),
        (
            {"blv_provider": "ollama", "ollama_url": "http://gpu-box:11434"},
            ollama,
            "extract_with_ollama",
            {
                "base_url": "http://gpu-box:11434",
                "model": "qwen3.5:4b",
                "num_ctx": 32_768,
                "think": False,
                "timeout_s": 1800,
            },
            "ollama/qwen3.5:4b",
        ),
    ],
    ids=["anthropic", "gemini", "ollama"],
)
async def test_llm_path(
    storage: S3Storage,
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, Any],
    module: Any,
    function: str,
    expected_call: dict[str, Any],
    used_model: str,
    extraction: BLVExtraction,
) -> None:
    ctx = await _ctx(storage, _settings(**settings))
    calls: list[dict[str, Any]] = []

    async def fake_extract(pdf: bytes, **kwargs: Any) -> Any:
        calls.append({"pdf": pdf[:5], **kwargs})
        return extraction if module is claude else (extraction, used_model)

    monkeypatch.setattr(module, function, fake_extract)
    event = _classified("projects/x/upload/blv.pdf")
    await storage.put_bytes("projects/x/upload/blv.pdf", _pdf())

    result_event = await handle_rooms_classified(event, ctx)

    assert calls == [{"pdf": b"%PDF-", **expected_call}]
    assert result_event.data == {
        "extracted_by": used_model,
        "materials": 2,
        "variants": ["Standard", "Premium"],
    }
