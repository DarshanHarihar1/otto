from app.luna import match_variant
from app.vision import Identification


async def test_match_variant_picks_a_handle_from_real_candidates():
    identification = Identification(
        object_type="serum",
        brand="Minimalist",
        product="Salicylic Acid Serum",
        variant="2%, 30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"],
        confidence=0.95,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )
    candidates = [
        {"handle": "salicylic-acid-2-serum-30ml", "title": "Salicylic Acid 2% Serum 30ml"},
        {"handle": "niacinamide-serum-30ml", "title": "Niacinamide 10% Serum 30ml"},
    ]
    result = await match_variant(identification, candidates)
    assert result.best_match_handle == "salicylic-acid-2-serum-30ml"
    assert result.similarity > 0.6
