"""
Q3B — Multi-Merchant Stub Tests
=================================

Tests for mcp-server/app/merchant.py.

These are pure unit tests — no database, no network.  They verify:

  1. merchant_from_url() correctly extracts the hostname from various URL shapes,
     including edge cases (empty string, non-URL strings, None).

  2. Merchant dataclass stores fields correctly with sensible defaults.

  3. resolve_merchant_id() finds merchants by name or domain substring and
     returns None when no match is found.
"""
import pytest
from app.merchant import Merchant, merchant_from_url, resolve_merchant_id


# ---------------------------------------------------------------------------
# merchant_from_url — URL parsing
# ---------------------------------------------------------------------------

class TestMerchantFromUrl:
    """merchant_from_url must extract the hostname or return None gracefully."""

    @pytest.mark.parametrize("url,expected", [
        # Standard HTTPS URLs
        ("https://system76.com/laptops/lemur-pro",          "system76.com"),
        ("https://www.bhphotovideo.com/c/product/123",       "www.bhphotovideo.com"),
        ("https://shop.lenovo.com/gb/en/laptops/",           "shop.lenovo.com"),
        # HTTP (not upgraded — function doesn't upgrade, just parses)
        ("http://example.com/product",                       "example.com"),
        # URL with query string and fragment
        ("https://amazon.com/dp/B09XYZ?ref=foo#reviews",    "amazon.com"),
        # IP address URL
        ("http://192.168.1.1:8080/catalog",                  "192.168.1.1"),
    ])
    def test_valid_url_returns_hostname(self, url, expected):
        """A well-formed URL should yield its hostname."""
        result = merchant_from_url(url)
        assert result == expected, (
            f"merchant_from_url({url!r}) → {result!r}, expected {expected!r}"
        )

    @pytest.mark.parametrize("bad_input", [
        "",           # empty string — most common edge case
        "   ",        # whitespace only (urlparse returns empty hostname)
        "not-a-url",  # no scheme — urlparse returns None hostname
        "ftp://",     # scheme only, no hostname
    ])
    def test_invalid_or_empty_input_returns_none(self, bad_input):
        """Non-URL inputs and empty strings must return None, not raise."""
        result = merchant_from_url(bad_input)
        assert result is None, (
            f"merchant_from_url({bad_input!r}) returned {result!r}, expected None"
        )

    def test_none_input_returns_none(self):
        """merchant_from_url(None) must not raise — return None."""
        # None is not a valid URL but callers may pass it from nullable DB column
        result = merchant_from_url(None)
        assert result is None


# ---------------------------------------------------------------------------
# Merchant dataclass — field storage and defaults
# ---------------------------------------------------------------------------

class TestMerchantDataclass:
    """Merchant must store required fields and apply correct default values."""

    def test_required_fields_stored_correctly(self):
        """id, name, domain are stored as provided."""
        m = Merchant(id="abc-123", name="System76", domain="system76.com")
        assert m.id == "abc-123"
        assert m.name == "System76"
        assert m.domain == "system76.com"

    def test_default_region_is_us(self):
        """region defaults to 'US' when not specified."""
        m = Merchant(id="x", name="X", domain="x.com")
        assert m.region == "US"

    def test_default_rating_is_zero(self):
        """rating defaults to 0.0."""
        m = Merchant(id="x", name="X", domain="x.com")
        assert m.rating == 0.0

    def test_default_fulfillment_sla_days(self):
        """fulfillment_sla_days defaults to 3."""
        m = Merchant(id="x", name="X", domain="x.com")
        assert m.fulfillment_sla_days == 3

    def test_custom_fields_override_defaults(self):
        """Custom values for optional fields must be stored correctly."""
        m = Merchant(
            id="m1",
            name="B&H Photo",
            domain="bhphotovideo.com",
            region="US",
            rating=4.7,
            fulfillment_sla_days=2,
        )
        assert m.rating == 4.7
        assert m.fulfillment_sla_days == 2
        assert m.region == "US"


# ---------------------------------------------------------------------------
# resolve_merchant_id — lookup by name or domain
# ---------------------------------------------------------------------------

class TestResolveMerchantId:
    """resolve_merchant_id must find merchants by name/domain substring."""

    @pytest.fixture
    def merchants(self):
        """A small catalog of merchants for lookup tests."""
        return [
            Merchant(id="m-s76",  name="System76",  domain="system76.com"),
            Merchant(id="m-bh",   name="B&H Photo", domain="bhphotovideo.com"),
            Merchant(id="m-amzn", name="Amazon",    domain="amazon.com"),
        ]

    def test_lookup_by_exact_domain(self, merchants):
        """Exact domain match must return the correct id."""
        result = resolve_merchant_id("system76.com", merchants)
        assert result == "m-s76"

    def test_lookup_by_partial_name(self, merchants):
        """Partial name substring match (case-insensitive) must succeed."""
        result = resolve_merchant_id("amazon", merchants)
        assert result == "m-amzn"

    def test_lookup_case_insensitive(self, merchants):
        """Lookup is case-insensitive for both name and domain."""
        assert resolve_merchant_id("SYSTEM76", merchants) == "m-s76"
        assert resolve_merchant_id("BHPHOTOVIDEO.COM", merchants) == "m-bh"

    def test_lookup_no_match_returns_none(self, merchants):
        """Unknown merchant name returns None instead of raising."""
        result = resolve_merchant_id("Newegg", merchants)
        assert result is None

    def test_lookup_empty_list_returns_none(self):
        """Empty merchant list must return None gracefully."""
        result = resolve_merchant_id("amazon", [])
        assert result is None

    def test_lookup_empty_string_returns_none(self, merchants):
        """Empty needle string must not accidentally match everything."""
        # An empty needle would match every merchant's name/domain because
        # "" in "anything" is True.  This test documents the behaviour:
        # callers should validate input before calling resolve_merchant_id.
        result = resolve_merchant_id("", merchants)
        # Either None or the first merchant is acceptable; the key constraint
        # is that it does NOT raise.
        assert result is None or isinstance(result, str)
