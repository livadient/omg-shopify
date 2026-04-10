"""Tests for app/agents/google_search_console.py — query/page blocklist."""
from app.agents.google_search_console import _is_blocklisted


class TestBeautyBlocklist:
    """Beauty/wellness queries and pages must be filtered out so they
    never reach Atlas's prompt or email."""

    def test_blocks_hot_water_bottle_query(self):
        assert _is_blocklisted("hot water bottle alternatives")
        assert _is_blocklisted("what to use instead of a hot water bottle")

    def test_blocks_period_pain_terms(self):
        assert _is_blocklisted("period pain remedies")
        assert _is_blocklisted("menstrual cramp relief")
        assert _is_blocklisted("how to deal with period cramps")

    def test_blocks_legacy_beauty_product_terms(self):
        assert _is_blocklisted("LED facial mask review")
        assert _is_blocklisted("eye massager benefits")
        assert _is_blocklisted("hair growth massage brush")
        assert _is_blocklisted("Korean collagen mask")
        assert _is_blocklisted("guasha set face roller")
        assert _is_blocklisted("skin tightening machine")

    def test_blocks_beauty_blog_urls(self):
        assert _is_blocklisted(
            "https://omg.com.cy/blogs/news/the-best-replacement-of-a-hot-water-bottle"
        )
        assert _is_blocklisted(
            "https://omg.com.cy/blogs/how-women-in-cyprus-deal-with-period-pain"
        )

    def test_blocks_beauty_product_handles(self):
        assert _is_blocklisted("/products/electric-period-cramp-massager-vibrator")
        assert _is_blocklisted("/products/led-facial-mask-red-light-therapy-mask")
        assert _is_blocklisted("/products/skin-tightening-and-neck-lifting-machine")

    def test_case_insensitive(self):
        assert _is_blocklisted("HOT WATER BOTTLE")
        assert _is_blocklisted("Period Pain")
        assert _is_blocklisted("MENSTRUAL Cramps")

    def test_does_not_block_tshirt_queries(self):
        assert not _is_blocklisted("graphic tees cyprus")
        assert not _is_blocklisted("funny t-shirt")
        assert not _is_blocklisted("astous na laloun")
        assert not _is_blocklisted("summer graphic tees")
        assert not _is_blocklisted("programmer humor tee")

    def test_does_not_block_tshirt_product_urls(self):
        assert not _is_blocklisted(
            "https://omg.com.cy/products/emotional-damage-calculator-typography-tee"
        )
        assert not _is_blocklisted(
            "https://omg.com.cy/collections/summer-graphic-tees"
        )

    def test_empty_input_is_safe(self):
        assert not _is_blocklisted("")
        assert not _is_blocklisted(None)  # type: ignore
