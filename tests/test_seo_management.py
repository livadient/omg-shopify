"""Tests for app/seo_management.py — SEO constants and structures."""
from app.seo_management import (
    COLLECTIONS_TO_CREATE,
    HANDLE_FIXES,
    HOMEPAGE_META_DESCRIPTION,
    HOMEPAGE_TITLE,
    PRODUCT_HANDLE_UPDATES,
    SPELLING_STANDARDIZATION,
)


class TestHandleFixes:
    def test_handle_fixes_is_dict(self):
        assert isinstance(HANDLE_FIXES, dict)

    def test_product_handle_updates_structure(self):
        for rule in PRODUCT_HANDLE_UPDATES:
            assert "match_title" in rule
            assert "match_handle_contains" in rule
            assert "new_handle" in rule

    def test_spelling_standardization(self):
        assert "astous-na-laloun" in SPELLING_STANDARDIZATION
        assert SPELLING_STANDARDIZATION["astous-na-laloun"] == "astous-va-laloun"


class TestCollectionsToCreate:
    def test_has_entries(self):
        assert len(COLLECTIONS_TO_CREATE) == 2

    def test_collection_structure(self):
        for coll in COLLECTIONS_TO_CREATE:
            assert "title" in coll
            assert "handle" in coll
            assert "body_html" in coll
            assert "meta_title" in coll
            assert "meta_description" in coll
            assert "sort_order" in coll
            assert "published" in coll

    def test_collection_handles(self):
        handles = [c["handle"] for c in COLLECTIONS_TO_CREATE]
        assert "cyprus-graphic-tees" in handles
        assert "greek-cyprus-shirts" in handles

    def test_all_published(self):
        for coll in COLLECTIONS_TO_CREATE:
            assert coll["published"] is True

    def test_sort_order_is_best_selling(self):
        for coll in COLLECTIONS_TO_CREATE:
            assert coll["sort_order"] == "best-selling"


class TestHomepageSeo:
    def test_title_not_empty(self):
        assert len(HOMEPAGE_TITLE) > 0
        assert "Cyprus" in HOMEPAGE_TITLE

    def test_meta_description_not_empty(self):
        assert len(HOMEPAGE_META_DESCRIPTION) > 0
        assert "Cyprus" in HOMEPAGE_META_DESCRIPTION
