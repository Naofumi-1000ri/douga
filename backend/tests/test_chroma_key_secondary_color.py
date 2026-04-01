"""Tests for chroma key secondary color computation and filter improvements."""

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Bootstrap: src.services.__init__ imports storage_service which uses
# datetime.UTC (Python 3.11+).  Stub it out before importing our module so
# these tests run on Python 3.10 too.
# ---------------------------------------------------------------------------
_storage_stub = types.ModuleType("src.services.storage_service")
_storage_stub.StorageService = MagicMock()
sys.modules.setdefault("src.services.storage_service", _storage_stub)

from src.services.chroma_key_service import compute_secondary_key_color  # noqa: E402


class TestComputeSecondaryKeyColor:
    """Test compute_secondary_key_color helper."""

    def test_green_screen_brightens_green_channel(self):
        # Dark green #006800 → G * 1.5 = 156 → #009C00
        result = compute_secondary_key_color("#006800")
        assert result == "0x009C00"

    def test_bright_green_screen(self):
        # Bright green #00FF00 → G * 1.5 = 382 clamped to 255 → #00FF00
        result = compute_secondary_key_color("#00FF00")
        assert result == "0x00FF00"

    def test_blue_screen_brightens_blue_channel(self):
        # Blue #0000AA → B * 1.5 = 255 → #0000FF
        result = compute_secondary_key_color("#0000AA")
        assert result == "0x0000FF"

    def test_pure_blue(self):
        result = compute_secondary_key_color("#0000FF")
        assert result == "0x0000FF"

    def test_hex_with_0x_prefix(self):
        result = compute_secondary_key_color("0x006800")
        assert result == "0x009C00"

    def test_invalid_color_returns_fallback(self):
        result = compute_secondary_key_color("invalid")
        assert result == "0x00B000"

    def test_short_hex(self):
        # Short input gets zero-padded
        result = compute_secondary_key_color("#080")
        assert isinstance(result, str)
        assert result.startswith("0x")

    def test_mixed_color_defaults_to_green(self):
        # When R is dominant, still brightens G (default path)
        result = compute_secondary_key_color("#804020")
        # G = 64 * 1.5 = 96 = 0x60
        assert result == "0x806020"
