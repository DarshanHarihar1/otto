from pathlib import Path

from app.vision import identify

FIXTURE = Path(__file__).parent / "fixtures" / "minimalist_serum.jpg"


async def test_identify_real_product_photo():
    image_bytes = FIXTURE.read_bytes()
    result = await identify(image_bytes)
    assert result.brand is not None
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasoning
