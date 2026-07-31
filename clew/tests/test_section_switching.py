"""Tests for Loop 1: Inline Section Switching (FEAT).

Tests the section_parser module, the /mode slash command, and
integration with TUI/GUI bridges.
"""

import pytest
from clew.agent_runtime.section_parser import parse_section_switch, Section


class TestSectionParser:
    """Parser tests: token detection, stripping, edge cases."""

    # ── Basic token detection ──────────────────────────────────────

    def test_brace_token_office(self):
        section, cleaned = parse_section_switch("{office} hello")
        assert section == Section.OFFICE
        assert cleaned == "hello"

    def test_brace_token_heavy_code(self):
        section, cleaned = parse_section_switch("{heavy_code} do stuff")
        assert section == Section.HEAVY_CODE
        assert cleaned == "do stuff"

    def test_brace_token_general(self):
        section, cleaned = parse_section_switch("{general} back to normal")
        assert section == Section.GENERAL
        assert cleaned == "back to normal"

    def test_slash_mode_office(self):
        section, cleaned = parse_section_switch("/mode office")
        assert section == Section.OFFICE
        assert cleaned == ""

    def test_slash_mode_heavy_code(self):
        section, cleaned = parse_section_switch("/mode heavy_code")
        assert section == Section.HEAVY_CODE
        assert cleaned == ""

    def test_slash_mode_general(self):
        section, cleaned = parse_section_switch("/mode general")
        assert section == Section.GENERAL
        assert cleaned == ""

    # ── No token found ─────────────────────────────────────────────

    def test_no_tag(self):
        section, cleaned = parse_section_switch("no tag")
        assert section is None
        assert cleaned == "no tag"

    def test_empty_string(self):
        section, cleaned = parse_section_switch("")
        assert section is None
        assert cleaned == ""

    # ── Case insensitivity ─────────────────────────────────────────

    def test_uppercase_brace(self):
        section, cleaned = parse_section_switch("{OFFICE} hello")
        assert section == Section.OFFICE
        assert cleaned == "hello"

    def test_mixed_case_slash(self):
        section, cleaned = parse_section_switch("/mode Heavy_Code")
        assert section == Section.HEAVY_CODE
        assert cleaned == ""

    # ── Whitespace handling ────────────────────────────────────────

    def test_leading_whitespace_brace(self):
        section, cleaned = parse_section_switch("  {general}  text")
        assert section == Section.GENERAL
        assert cleaned == "text"

    def test_leading_whitespace_slash(self):
        section, cleaned = parse_section_switch("  /mode office  ")
        assert section == Section.OFFICE
        assert cleaned == ""

    # ── No false positives ─────────────────────────────────────────

    def test_json_object_not_matched(self):
        """JSON objects like {"office": "value"} should NOT be parsed
        as a section switch — the pattern requires the token to be at
        the START of the message and contain ONLY the section name."""
        section, cleaned = parse_section_switch('{"office": "value"}')
        assert section is None
        assert cleaned == '{"office": "value"}'

    def test_mid_message_brace_not_matched(self):
        """A section token in the middle of a message should NOT be
        parsed — only leading tokens are valid."""
        section, cleaned = parse_section_switch("code {general} here")
        assert section is None
        assert cleaned == "code {general} here"

    def test_slash_mode_mid_message_not_matched(self):
        section, cleaned = parse_section_switch("use /mode office here")
        assert section is None
        assert cleaned == "use /mode office here"

    def test_invalid_section_name(self):
        section, cleaned = parse_section_switch("{invalid} hello")
        assert section is None
        assert cleaned == "{invalid} hello"

    def test_slash_mode_invalid_section(self):
        section, cleaned = parse_section_switch("/mode invalid")
        assert section is None
        assert cleaned == "/mode invalid"

    # ── Token with remaining text ──────────────────────────────────

    def test_brace_with_multiline_text(self):
        section, cleaned = parse_section_switch("{office} write a report about sales")
        assert section == Section.OFFICE
        assert cleaned == "write a report about sales"

    def test_slash_mode_with_remaining_text(self):
        section, cleaned = parse_section_switch("/mode heavy_code refactor the auth module")
        assert section == Section.HEAVY_CODE
        assert cleaned == "refactor the auth module"

    # ── Section enum values ────────────────────────────────────────

    def test_section_enum_values(self):
        assert Section.GENERAL.value == "general"
        assert Section.HEAVY_CODE.value == "heavy_code"
        assert Section.OFFICE.value == "office"

    def test_section_str_enum(self):
        """Section is a str Enum — its value should be usable as a string."""
        assert str(Section.OFFICE) == "Section.OFFICE"
        assert Section.OFFICE.value == "office"


class TestTUIBridgeIntegration:
    """Test that the TUI bridge set_section works correctly.

    Note: These tests require the clew_tui module to be importable,
    which needs Textual and other dependencies. If not available,
    the tests are skipped.
    """

    def test_bridge_set_section_valid(self):
        try:
            from clew_tui.bridge import ClewBridge
        except ImportError:
            pytest.skip("clew_tui not available (missing Textual)")
        bridge = ClewBridge(section="general")
        result = bridge.set_section("office")
        assert result.get("ok") is True
        assert result.get("section") == "office"
        assert bridge.section == "office"

    def test_bridge_set_section_invalid(self):
        try:
            from clew_tui.bridge import ClewBridge
        except ImportError:
            pytest.skip("clew_tui not available (missing Textual)")
        bridge = ClewBridge(section="general")
        result = bridge.set_section("invalid_section")
        assert result.get("ok") is False
        assert "error" in result

    def test_bridge_set_section_heavy_code(self):
        try:
            from clew_tui.bridge import ClewBridge
        except ImportError:
            pytest.skip("clew_tui not available (missing Textual)")
        bridge = ClewBridge(section="general")
        result = bridge.set_section("heavy_code")
        assert result.get("ok") is True
        assert bridge.section == "heavy_code"


class TestSectionParserIntegration:
    """Integration test: parse + switch."""

    def test_parse_then_switch(self):
        """Simulate the full flow: parse a section token, then call
        bridge.set_section() with the parsed value."""
        try:
            from clew_tui.bridge import ClewBridge
        except ImportError:
            pytest.skip("clew_tui not available (missing Textual)")
        bridge = ClewBridge(section="general")

        message = "{office} create a document"
        new_section, cleaned = parse_section_switch(message)
        assert new_section == Section.OFFICE

        result = bridge.set_section(new_section.value)
        assert result.get("ok") is True
        assert bridge.section == "office"
        assert cleaned == "create a document"

    def test_parse_no_switch_general(self):
        """A message without a section token should not trigger a switch."""
        try:
            from clew_tui.bridge import ClewBridge
        except ImportError:
            pytest.skip("clew_tui not available (missing Textual)")
        bridge = ClewBridge(section="general")

        message = "hello world"
        new_section, cleaned = parse_section_switch(message)
        assert new_section is None
        assert cleaned == "hello world"
        assert bridge.section == "general"
