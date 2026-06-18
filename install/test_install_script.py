#!/usr/bin/env python3
"""
Static tests for install/install.sh.
"""
import subprocess
import unittest
from pathlib import Path


INSTALL_SH = Path(__file__).parent / "install.sh"


class TestInstallScript(unittest.TestCase):
    def test_shell_syntax(self):
        completed = subprocess.run(["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True, check=False)

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_ocr_auto_install_defaults_are_present(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('INSTALL_OCR="${AI_COLLAB_INSTALL_OCR:-1}"', text)
        self.assertIn("Step 3/6 — Installing semantic vision OCR", text)
        self.assertIn("brew install tesseract", text)
        self.assertIn("apt-get install -y tesseract-ocr", text)
        self.assertIn('add_plist_env "AI_COLLAB_OBSERVER_SEMANTIC_OCR" "${AI_COLLAB_OBSERVER_SEMANTIC_OCR:-1}"', text)

    def test_conversation_helper_is_installed(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('copy_or_download "install/ai-collab-converse.py"', text)
        self.assertIn('chmod +x "$CLAUDE_DIR/ai-collab-converse.py"', text)
        self.assertIn("Conversation helper", text)

    def test_self_updater_is_installed_and_configured(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('copy_or_download "install/ai-collab-update.py"', text)
        self.assertIn('chmod +x "$CLAUDE_DIR/ai-collab-update.py"', text)
        self.assertIn("Self-updater", text)
        self.assertIn('add_plist_env "AI_COLLAB_AUTO_UPDATE" "${AI_COLLAB_AUTO_UPDATE:-1}"', text)
        self.assertIn('add_plist_env "AI_COLLAB_UPDATE_INTERVAL_SECONDS" "${AI_COLLAB_UPDATE_INTERVAL_SECONDS:-21600}"', text)


if __name__ == "__main__":
    unittest.main()
