#!/usr/bin/env python3
"""
Static tests for install/install.sh.
"""
import subprocess
import unittest
from pathlib import Path


INSTALL_SH = Path(__file__).parent / "install.sh"
DAEMON_SH = Path(__file__).parent / "daemon.sh"


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

    def test_team_role_helper_is_installed(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('copy_or_download "install/ai-collab-team.py"', text)
        self.assertIn('chmod +x "$CLAUDE_DIR/ai-collab-team.py"', text)
        self.assertIn("Team role onboarding", text)

    def test_self_updater_is_installed_and_configured(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('copy_or_download "install/ai-collab-update.py"', text)
        self.assertIn('chmod +x "$CLAUDE_DIR/ai-collab-update.py"', text)
        self.assertIn("Self-updater", text)
        self.assertIn('add_plist_env "AI_COLLAB_AUTO_UPDATE" "${AI_COLLAB_AUTO_UPDATE:-1}"', text)
        self.assertIn('add_plist_env "AI_COLLAB_UPDATE_INTERVAL_SECONDS" "${AI_COLLAB_UPDATE_INTERVAL_SECONDS:-21600}"', text)

    def test_unified_setup_is_installed_without_recursive_project_setup(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('copy_or_download "install/ai-collab-setup.py"', text)
        self.assertIn('chmod +x "$CLAUDE_DIR/ai-collab-setup.py"', text)
        self.assertIn('copy_or_download "SKILL.md"                      "$CODEX_SKILL_DIR/SKILL.md"', text)
        self.assertIn('SKIP_PROJECT_SETUP="${AI_COLLAB_SKIP_PROJECT_SETUP:-}"', text)
        self.assertIn('if [[ -n "$SKIP_PROJECT_SETUP" ]]; then', text)

    def test_recovery_is_installed_and_configured(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('copy_or_download "install/ai-collab-recover.py"', text)
        self.assertIn('chmod +x "$CLAUDE_DIR/ai-collab-recover.py"', text)
        self.assertIn("Reboot recovery", text)
        self.assertIn('add_plist_env "AI_COLLAB_RECOVERY" "${AI_COLLAB_RECOVERY:-1}"', text)
        self.assertIn('add_plist_env "AI_COLLAB_RECOVERY_INTERVAL_SECONDS" "${AI_COLLAB_RECOVERY_INTERVAL_SECONDS:-300}"', text)

    def test_codex_bridge_is_installed(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn('copy_or_download "install/ai-collab-codex-bridge.py"', text)
        self.assertIn('chmod +x "$CLAUDE_DIR/ai-collab-codex-bridge.py"', text)
        self.assertIn("Codex bridge API", text)
        self.assertIn('BRIDGE_PLIST_LABEL="com.gsepcore.ai-collab-codex-bridge"', text)
        self.assertIn("Codex bridge loaded", text)
        self.assertIn("AI_COLLAB_NO_CODEX_BRIDGE", text)


class TestDaemonScript(unittest.TestCase):
    def test_shell_syntax(self):
        completed = subprocess.run(["bash", "-n", str(DAEMON_SH)], capture_output=True, text=True, check=False)

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_all_wakeups_run_before_observers(self):
        text = DAEMON_SH.read_text(encoding="utf-8")

        wake_loop = text.index('for COLLAB_DIR in "${COLLAB_DIRS[@]}"; do')
        observer_loop = text.index('for COLLAB_DIR in "${COLLAB_DIRS[@]}"; do', wake_loop + 1)
        wake_command = text.index('python3 "$WAKEUP_SCRIPT"', wake_loop)
        observer_command = text.index('python3 "$OBSERVER_SCRIPT"', observer_loop)

        self.assertLess(wake_loop, observer_loop)
        self.assertLess(wake_command, observer_loop)
        self.assertGreater(observer_command, observer_loop)

    def test_project_discovery_prunes_expensive_non_project_trees(self):
        text = DAEMON_SH.read_text(encoding="utf-8")

        self.assertIn("-name node_modules", text)
        self.assertIn("-name Library", text)
        self.assertIn("-name .git", text)


if __name__ == "__main__":
    unittest.main()
