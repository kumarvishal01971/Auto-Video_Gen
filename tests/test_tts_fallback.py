import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.providers import tts


class TTSFallbackTests(unittest.TestCase):
    def test_sarvam_failure_uses_edge_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"

            def write_audio(text, language, path):
                path.write_bytes(b"audio" * 150)

            with (
                patch.object(tts.config, "TTS_PROVIDER", "sarvam"),
                patch.object(tts, "env", side_effect=lambda name, default="": "edge"
                             if name == "TTS_FALLBACK" else default),
                patch.object(tts, "_sarvam", side_effect=RuntimeError("Sarvam quota exhausted")),
                patch.object(tts, "_edge", side_effect=write_audio) as edge,
            ):
                result = tts.speak("[warm] Hello", "en", output)

        self.assertEqual(result, "edge")
        edge.assert_called_once_with("Hello", "en", output)

    def test_sarvam_error_is_preserved_when_fallback_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with (
                patch.object(tts.config, "TTS_PROVIDER", "sarvam"),
                patch.object(tts, "env", side_effect=lambda name, default="": ""
                             if name == "TTS_FALLBACK" else default),
                patch.object(tts, "_sarvam", side_effect=RuntimeError("Sarvam quota exhausted")),
                patch.object(tts, "_edge") as edge,
            ):
                with self.assertRaisesRegex(RuntimeError, "Sarvam quota exhausted"):
                    tts.speak("Hello", "en", output)

        edge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
