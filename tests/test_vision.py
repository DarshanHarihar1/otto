from pathlib import Path
from unittest.mock import MagicMock, patch

from openai import RateLimitError

from app.vision import Identification, _identify_sync, identify

FIXTURE = Path(__file__).parent / "fixtures" / "minimalist_serum.jpg"


async def test_identify_real_product_photo():
    image_bytes = FIXTURE.read_bytes()
    result = await identify(image_bytes)
    assert result.brand is not None
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasoning


def test_system_prompt_includes_exact_registry_categories():
    from app.categories import load_known_categories
    from app.vision import _build_system_prompt

    prompt = _build_system_prompt(load_known_categories())

    assert "Beauty & Personal Care/Skin Care" in prompt
    assert "Health/Pharmacy" in prompt
    assert "set category to null" in prompt


def _fake_result() -> Identification:
    return Identification(
        object_type="bottle",
        brand="Minimalist",
        product="Serum",
        variant="30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["serum"],
        confidence=0.95,
        reasoning="clear",
        missing_info=None,
        suggested_photo=None,
    )


def _rate_limit() -> RateLimitError:
    response = MagicMock()
    response.status_code = 429
    response.headers = {}
    response.json.return_value = {
        "error": {"message": "rate_limit_exceeded", "code": "rate_limit_exceeded"}
    }
    body = {"error": {"message": "rate_limit_exceeded", "code": "rate_limit_exceeded"}}
    return RateLimitError("rate limited", response=response, body=body)


def test_identify_falls_back_to_terra_on_sol_rate_limit():
    calls: list[str] = []

    def fake_parse(b64, cats, *, model, detail="auto"):
        calls.append(model)
        if model == "gpt-5.6-sol":
            raise _rate_limit()
        return _fake_result()

    with patch("app.vision._parse_identification", side_effect=fake_parse):
        result = _identify_sync(b"fakepngbytes", {"Beauty & Personal Care/Skin Care"})

    assert result is not None
    assert result.brand == "Minimalist"
    assert calls == ["gpt-5.6-sol", "gpt-5.6-terra"]


def test_identify_downscales_after_both_models_rate_limit():
    calls: list[tuple[str, str]] = []

    def fake_parse(b64, cats, *, model, detail="auto"):
        calls.append((model, detail))
        if len(calls) < 3:
            raise _rate_limit()
        return _fake_result()

    with (
        patch("app.vision._parse_identification", side_effect=fake_parse),
        patch(
            "app.vision._downscale_for_vision", return_value=b"smaller"
        ) as mock_downscale,
    ):
        result = _identify_sync(b"hugeimagebytes", {"Beauty & Personal Care/Skin Care"})

    assert result is not None
    mock_downscale.assert_called_once_with(b"hugeimagebytes")
    assert calls == [
        ("gpt-5.6-sol", "auto"),
        ("gpt-5.6-terra", "auto"),
        ("gpt-5.6-terra", "low"),
    ]
