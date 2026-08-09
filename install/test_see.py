#!/usr/bin/env python3
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path


_spec = importlib.util.spec_from_file_location("ai_collab_see", Path(__file__).parent / "ai-collab-see.py")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_see"] = _mod
_spec.loader.exec_module(_mod)


class Completed:
    returncode = 0
    stdout = "tsv"
    stderr = ""


class FakeObserver:
    @staticmethod
    def tesseract_bin():
        return "/tmp/fake-tesseract"

    @staticmethod
    def prepare_ocr_image(image, root, runner):
        return image, ""

    @staticmethod
    def run_command(*args, **kwargs):
        return Completed()

    @staticmethod
    def parse_tesseract_tsv(_text):
        return "demo Claude Code OpenCode Codex", [], 1800, 1100

    @staticmethod
    def visual_agent_hits(words, text, width, height):
        return {agent: [{"source": "top-band-ocr"}] for agent in ("claude-code", "opencode", "codex")}

    @staticmethod
    def truncate(value, limit):
        return value[:limit]


class TestDirectVision(unittest.TestCase):
    def test_direct_pixel_inspection_hashes_png_and_requires_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo"
            root.mkdir()
            image = root / "team.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 4096, 2560) + b"pixels")
            original = _mod.load_observer
            _mod.load_observer = lambda: FakeObserver()
            try:
                result = _mod.inspect(image, root, ["claude-code", "opencode", "codex"])
            finally:
                _mod.load_observer = original

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["method"], "direct-pixel-ocr")
        self.assertEqual(result["image_size"], {"width": 4096, "height": 2560})
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(result["missing_agents"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
