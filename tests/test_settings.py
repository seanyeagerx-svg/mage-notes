import json
from pathlib import Path
import tempfile
import unittest
from settings import DEFAULTS, load_settings, save_settings
from engine import AudioPacket
import numpy as np


class PreferencesTests(unittest.TestCase):
    def test_invalid_preferences_cannot_break_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.assertEqual(load_settings(path), DEFAULTS)
            for value in (None, 123, [], "oops", {"dictation_key": 1}, {"dictation_key": "f8", "workflow_key": "f8"}):
                path.write_text(json.dumps(value), "utf-8")
                self.assertEqual(load_settings(path), DEFAULTS)
            path.write_text("{broken", "utf-8")
            self.assertEqual(load_settings(path), DEFAULTS)

    def test_preferences_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            values = dict(DEFAULTS, dictation_key="f8", workflow_key="ctrl+shift+space", enabled=False,
                          resume_at_end=False, activation="continuous", device=3)
            save_settings(path, values)
            self.assertEqual(load_settings(path), values)
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_audio_boundary_preserves_every_new_sample(self):
        packet = AudioPacket(np.arange(8), 8, 2.0)
        np.testing.assert_array_equal(packet.after(1.25), np.arange(2, 8))
        np.testing.assert_array_equal(packet.after(0), np.arange(8))
        self.assertEqual(len(packet.after(2.0)), 0)


if __name__ == "__main__": unittest.main()
