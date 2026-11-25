import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config


class BrandFontTests(unittest.TestCase):
    def test_uses_system_font_when_configured_fallback_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fallback = root / "system-font.ttf"
            fallback.touch()
            (root / "brand.json").write_text(
                json.dumps({"fonts": {"fallback": "/missing/dejavu.ttf"}}),
                encoding="utf-8",
            )
            with (
                patch.object(config, "BRAND_DIR", root),
                patch.object(config, "_system_font_candidates", return_value=[fallback]),
            ):
                brand = config.load_brand()

        self.assertEqual(brand["fonts"]["fallback"], str(fallback))


if __name__ == "__main__":
    unittest.main()
