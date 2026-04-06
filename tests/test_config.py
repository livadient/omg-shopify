"""Tests for app/config.py — settings and helpers."""
from app.config import Settings, _parse_recipients


class TestParseRecipients:
    def test_comma_separated(self):
        result = _parse_recipients("a@b.com, c@d.com, e@f.com")
        assert result == ["a@b.com", "c@d.com", "e@f.com"]

    def test_single_email(self):
        result = _parse_recipients("only@one.com")
        assert result == ["only@one.com"]

    def test_empty_string(self):
        assert _parse_recipients("") == []

    def test_trailing_comma(self):
        result = _parse_recipients("a@b.com,")
        assert result == ["a@b.com"]

    def test_whitespace_only(self):
        assert _parse_recipients("   ") == []

    def test_multiple_commas(self):
        result = _parse_recipients("a@b.com,,c@d.com")
        assert result == ["a@b.com", "c@d.com"]


class TestSettingsDefaults:
    def test_default_values(self):
        s = Settings()
        assert s.tshirtjunkies_base_url == "https://tshirtjunkies.co"
        assert s.smtp_port == 587
        assert s.omg_shopify_domain == "52922c-2.myshopify.com"
        assert s.email_recipients == []
        assert s.shopify_webhook_secret == ""
        assert s.agent_timezone == "Europe/Nicosia"

    def test_custom_values(self):
        s = Settings(
            smtp_host="smtp.example.com",
            smtp_port=465,
            email_recipients=["test@example.com"],
        )
        assert s.smtp_host == "smtp.example.com"
        assert s.smtp_port == 465
        assert s.email_recipients == ["test@example.com"]
