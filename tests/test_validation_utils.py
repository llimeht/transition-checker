"""Tests for transitionchecker.core.validation."""

from __future__ import annotations

from transitionchecker.core.validation import as_json_object, as_text


class TestAsJsonObject:
    def test_plain_dict(self):
        d = {"a": 1, "b": "hello"}
        assert as_json_object(d) == d

    def test_empty_dict(self):
        assert as_json_object({}) == {}

    def test_non_dict_returns_none(self):
        assert as_json_object([1, 2]) is None
        assert as_json_object("string") is None
        assert as_json_object(42) is None
        assert as_json_object(None) is None

    def test_non_string_keys_returns_none(self):
        assert as_json_object({1: "a"}) is None


class TestAsText:
    def test_string_trimmed(self):
        assert as_text("  hello  ") == "hello"

    def test_empty_string(self):
        assert as_text("") == ""

    def test_non_string_returns_empty(self):
        assert as_text(None) == ""
        assert as_text(42) == ""
        assert as_text([]) == ""
