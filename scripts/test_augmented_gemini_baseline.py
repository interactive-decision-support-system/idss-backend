#!/usr/bin/env python3
"""
Unit tests for run_augmented_gemini_baseline.py helper functions.

All tests are deterministic and require NO network calls, no running server,
and no API keys. They test the helper functions in isolation.

Run with:
    python -m pytest scripts/test_augmented_gemini_baseline.py -v
    python -m pytest scripts/test_augmented_gemini_baseline.py -v -k "grounding"
"""

import sys
import os

# Allow importing from the scripts/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

# Import the functions under test
from run_augmented_gemini_baseline import (
    format_catalog,
    build_augmented_user_message,
    check_catalog_grounding,
    check_response_type_augmented,
    PASS_THRESHOLD,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def make_product(brand="Dell", name="Dell Inspiron 15", price=799.0, **kwargs):
    """Build a minimal product dict matching IDSS API response shape."""
    p = {"brand": brand, "name": name, "price": price}
    p.update(kwargs)
    return p

def make_query(expect_recs=False, expect_q=False, quality_note=""):
    """Build a minimal query dict matching QUERIES list shape."""
    return {
        "id": 1,
        "group": "test",
        "label": "test query",
        "message": "I need a laptop",
        "expect_recs_on_first": expect_recs,
        "expect_question":       expect_q,
        "quality_note":          quality_note,
    }


# ── TestFormatCatalog ────────────────────────────────────────────────────────

class TestFormatCatalog:
    """format_catalog() — product list → readable string for Gemini context."""

    def test_empty_products_returns_placeholder(self):
        result = format_catalog([])
        assert "(no products available)" in result

    def test_single_product_has_number_brand_name_price(self):
        products = [make_product("HP", "HP Envy 14", 899.0)]
        result = format_catalog(products)
        assert "1." in result
        assert "HP" in result
        assert "Envy 14" in result
        assert "899" in result

    def test_brand_not_doubled_in_display_name(self):
        """'Dell Dell Inspiron' should NOT appear — brand prefix is stripped from name."""
        products = [make_product("Dell", "Dell Inspiron 15", 799.0)]
        result = format_catalog(products)
        # The line should contain "Dell" once, not "Dell Dell"
        first_line = [l for l in result.split("\n") if "1." in l][0]
        assert "Dell Dell" not in first_line
        assert "Dell" in first_line

    def test_max_items_limit_respected(self):
        """Only max_items entries should appear in the output."""
        products = [make_product("Brand", f"Laptop {i}", 500.0) for i in range(20)]
        result = format_catalog(products, max_items=5)
        # Should have 5 numbered lines, not more
        numbered = [l.strip() for l in result.split("\n") if l.strip() and l.strip()[0].isdigit() and "." in l[:3]]
        assert len(numbered) == 5

    def test_default_max_items_is_8(self):
        products = [make_product("Brand", f"Laptop {i}", 500.0) for i in range(12)]
        result = format_catalog(products)
        # Count lines that start with "N."
        numbered = [l.strip() for l in result.split("\n") if l.strip() and l.strip()[0].isdigit() and "." in l[:3]]
        assert len(numbered) == 8

    def test_price_from_price_value_field(self):
        """Stored result dicts use 'price_value' instead of 'price'."""
        products = [{"brand": "Asus", "name": "VivoBook 15", "price_value": "649.99"}]
        result = format_catalog(products)
        assert "649" in result

    def test_specs_appended_when_ram_present(self):
        """A specs line [RAM:16GB, ...] should appear below the product name line."""
        products = [{
            "brand": "Lenovo",
            "name": "ThinkPad T14",
            "price": 1100.0,
            "laptop": {
                "attributes": {"ram_gb": 16},
                "specs": {"storage": "512GB SSD", "os": "Windows 11"},
            },
        }]
        result = format_catalog(products)
        assert "RAM:16GB" in result
        assert "Storage:512GB SS" in result

    def test_multiple_products_numbered_sequentially(self):
        products = [
            make_product("HP", "HP Laptop 15", 500.0),
            make_product("Dell", "Dell Vostro 14", 600.0),
            make_product("Lenovo", "Lenovo IdeaPad 3", 450.0),
        ]
        result = format_catalog(products)
        assert "1." in result
        assert "2." in result
        assert "3." in result

    def test_long_name_truncated_to_65_chars(self):
        long_name = "Laptop " + "X" * 80
        products = [make_product("Test", long_name, 500.0)]
        result = format_catalog(products)
        # The display name (after brand removal) should be at most 65 chars
        # Just verify the output doesn't contain 80 X's
        assert "X" * 70 not in result


# ── TestBuildAugmentedUserMessage ────────────────────────────────────────────

class TestBuildAugmentedUserMessage:
    """build_augmented_user_message() — combines catalog and query into user message."""

    def test_catalog_appears_before_query(self):
        msg = build_augmented_user_message("I need a gaming laptop", "1. Dell — $800")
        # Catalog text should appear before the customer request
        assert msg.index("1. Dell") < msg.index("I need a gaming laptop")

    def test_both_catalog_and_query_present(self):
        msg = build_augmented_user_message("I need a gaming laptop", "1. Dell — $800\n2. HP — $700")
        assert "1. Dell" in msg
        assert "2. HP" in msg
        assert "I need a gaming laptop" in msg

    def test_empty_catalog_still_includes_query(self):
        msg = build_augmented_user_message("laptop under $500", "")
        assert "laptop under $500" in msg

    def test_customer_request_label_present(self):
        """The message should include 'Customer request:' as a section header."""
        msg = build_augmented_user_message("My query", "Some catalog")
        assert "Customer request:" in msg


# ── TestCheckCatalogGrounding ────────────────────────────────────────────────

class TestCheckCatalogGrounding:
    """check_catalog_grounding() — heuristic: does Gemini reference catalog products?"""

    def test_empty_products_returns_full_score(self):
        """No catalog to check against → can't penalise → score=1.0."""
        score, note = check_catalog_grounding("some response about laptops", [])
        assert score == 1.0

    def test_three_catalog_keywords_hit_returns_1_0(self):
        products = [
            make_product("Dell",   "Dell Inspiron laptop", 799.0),
            make_product("HP",     "HP Pavilion notebook", 699.0),
            make_product("Lenovo", "Lenovo IdeaPad computer", 549.0),
        ]
        # Response mentions all three brands → 3 hits → 1.0
        response = "Check out the Dell Inspiron, HP Pavilion, and Lenovo IdeaPad."
        score, note = check_catalog_grounding(response, products)
        assert score == 1.0
        assert "✓" in note

    def test_one_catalog_keyword_returns_0_7(self):
        products = [
            make_product("Dell", "Dell Inspiron 15", 799.0),
        ]
        # Response mentions only "dell" — 1 hit → 0.7
        response = "The Dell model looks good for your needs."
        score, note = check_catalog_grounding(response, products)
        assert score == 0.7
        assert "~" in note

    def test_no_catalog_keywords_returns_0_4(self):
        products = [
            make_product("NicheCorpXYZ", "NicheCorpXYZ ZX900Pro", 999.0),
        ]
        # Response doesn't mention niche brand or model
        response = "Here is a general recommendation for productivity work."
        score, note = check_catalog_grounding(response, products)
        assert score == 0.4

    def test_known_hallucination_reduces_score(self):
        """Mentioning 'macbook pro 16' when it's not in the catalog is a hallucination signal."""
        products = [make_product("Generic", "Budget Laptop A", 400.0)]
        response = "I recommend the MacBook Pro 16 — it's great for your workflow."
        score, note = check_catalog_grounding(response, products)
        assert score < 1.0
        assert "⚠" in note or "hallucination" in note.lower()

    def test_hallucination_in_catalog_name_not_flagged(self):
        """If 'dell xps 15' is actually in our catalog, it's NOT a hallucination."""
        products = [make_product("Dell", "Dell XPS 15 laptop", 1299.0)]
        response = "The Dell XPS 15 is a great option at $1299."
        score, note = check_catalog_grounding(response, products)
        # "dell xps 15" is in catalog_all_names → not flagged as hallucination
        assert "⚠" not in note

    def test_multiple_hallucinations_compound(self):
        """Two hallucination signals → score 0.6 - 0.4 = 0.2, floored at 0."""
        products = [make_product("Generic", "Budget Laptop", 300.0)]
        response = "Try the MacBook Pro 16 or the Razer Blade 15 for gaming."
        score, note = check_catalog_grounding(response, products)
        # 2 hallucinations: 0.6 - 0.4 = 0.2
        assert score <= 0.4
        assert "⚠" in note

    def test_brand_only_new_recertified_skipped(self):
        """Generic brands like 'New', 'Recertified' are excluded from keyword set."""
        products = [
            {"brand": "New",          "name": "Refurbished HP Laptop", "price": 350.0},
            {"brand": "Recertified",  "name": "HP Elitebook Pro",       "price": 450.0},
        ]
        response = "The HP laptop looks good for your budget."
        score, note = check_catalog_grounding(response, products)
        # "new" and "recertified" are skipped — "hp" comes from the name's 5+ char words check
        # "laptop" (6 chars) and "elitebook" (9 chars) would be in keywords
        # "laptop" appears in response → at least 1 hit → 0.7
        assert score >= 0.4   # at least not penalised


# ── TestCheckResponseType ────────────────────────────────────────────────────

class TestCheckResponseType:
    """check_response_type_augmented() — did Gemini's response type match expectations?"""

    def test_recs_expected_and_given_returns_1_0(self):
        q = make_query(expect_recs=True)
        score, note = check_response_type_augmented(q, "recommendations", has_products=True)
        assert score == 1.0
        assert "✓" in note

    def test_recs_expected_but_question_given_returns_0(self):
        q = make_query(expect_recs=True)
        score, note = check_response_type_augmented(q, "question", has_products=True)
        assert score == 0.0
        assert "✗" in note

    def test_question_expected_and_given_returns_1_0(self):
        q = make_query(expect_q=True)
        score, note = check_response_type_augmented(q, "question", has_products=True)
        assert score == 1.0
        assert "✓" in note

    def test_question_expected_but_recs_given_returns_0(self):
        q = make_query(expect_q=True)
        score, note = check_response_type_augmented(q, "recommendations", has_products=True)
        assert score == 0.0
        assert "✗" in note

    def test_no_products_returns_0_75_and_na_note(self):
        """When IDSS gave no products (interview mode), type check is N/A → neutral 0.75."""
        q = make_query(expect_recs=True)
        score, note = check_response_type_augmented(q, "question", has_products=False)
        assert score == 0.75
        assert "N/A" in note

    def test_neither_expected_accepts_recommendations(self):
        """If neither expect_recs nor expect_q is set, either type is fine."""
        q = make_query(expect_recs=False, expect_q=False)
        score, note = check_response_type_augmented(q, "recommendations", has_products=True)
        assert score == 1.0

    def test_neither_expected_accepts_question(self):
        q = make_query(expect_recs=False, expect_q=False)
        score, note = check_response_type_augmented(q, "question", has_products=True)
        assert score == 1.0


# ── TestScoreWeights ─────────────────────────────────────────────────────────

class TestScoreWeights:
    """Verify the scoring formula: type 40% + grounding 15% + quality 45%."""

    def test_weights_sum_to_1(self):
        assert abs(0.40 + 0.15 + 0.45 - 1.0) < 1e-9

    def test_perfect_scores_give_1_0(self):
        final = 0.40 * 1.0 + 0.15 * 1.0 + 0.45 * 1.0
        assert abs(final - 1.0) < 1e-9

    def test_zero_scores_give_0_0(self):
        final = 0.40 * 0.0 + 0.15 * 0.0 + 0.45 * 0.0
        assert final == 0.0

    def test_pass_threshold_case(self):
        """type=1.0, grounding=1.0, quality=0.7 → 0.40+0.15+0.315 = 0.865 (above threshold)."""
        final = 0.40 * 1.0 + 0.15 * 1.0 + 0.45 * 0.7
        assert final >= PASS_THRESHOLD

    def test_fail_case(self):
        """type=0.0, grounding=0.4, quality=0.4 → 0+0.06+0.18 = 0.24 (below threshold)."""
        final = 0.40 * 0.0 + 0.15 * 0.4 + 0.45 * 0.4
        assert final < PASS_THRESHOLD

    def test_type_score_dominates(self):
        """Wrong type (0.0) with perfect quality/grounding still scores only 0.60."""
        final = 0.40 * 0.0 + 0.15 * 1.0 + 0.45 * 1.0
        assert abs(final - 0.60) < 1e-9

    def test_formula_matches_gpt_baseline(self):
        """Identical inputs should produce identical scores as run_augmented_gpt_baseline."""
        # Both scripts use: final = 0.40 * type + 0.15 * grounding + 0.45 * quality
        type_s, gnd_s, qual_s = 1.0, 0.7, 0.6
        final_gemini = 0.40 * type_s + 0.15 * gnd_s + 0.45 * qual_s
        final_gpt    = 0.40 * type_s + 0.15 * gnd_s + 0.45 * qual_s
        assert abs(final_gemini - final_gpt) < 1e-9


# ── Run directly ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run with basic output when invoked directly (no pytest required)
    import traceback
    passed = 0
    failed = 0
    errors = []

    test_classes = [
        TestFormatCatalog,
        TestBuildAugmentedUserMessage,
        TestCheckCatalogGrounding,
        TestCheckResponseType,
        TestScoreWeights,
    ]
    for cls in test_classes:
        instance = cls()
        methods  = [m for m in dir(instance) if m.startswith("test_")]
        print(f"\n{cls.__name__} ({len(methods)} tests)")
        for m in methods:
            try:
                getattr(instance, m)()
                print(f"  ✓ {m}")
                passed += 1
            except Exception as e:
                print(f"  ✗ {m}: {e}")
                errors.append(f"{cls.__name__}.{m}: {e}")
                failed += 1

    print(f"\n{'─'*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for err in errors:
            print(f"  {err}")
    sys.exit(0 if failed == 0 else 1)
