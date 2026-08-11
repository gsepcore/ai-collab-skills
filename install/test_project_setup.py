#!/usr/bin/env python3
"""
Tests for ai-collab-project-setup.py
Run with: python3 install/test_project_setup.py
"""
import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_project_setup",
    Path(__file__).parent / "ai-collab-project-setup.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestProjectSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 5, 13, 14, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def setup(self, agents=("opencode", "codex"), models=None):
        return _mod.setup_project(
            self.root,
            list(agents),
            "antigravity",
            models or {"opencode": "minimax/m2.5", "codex": "openai/gpt-5.5"},
            now=self.now,
        )

    def test_onboards_agents_with_container_and_models(self):
        result = self.setup()

        self.assertEqual(result["gitignore"], "updated")
        self.assertTrue((self.root / ".ai-collab" / "PROTOCOL.md").exists())
        self.assertTrue((self.root / ".ai-collab" / "TEAM.md").exists())
        self.assertTrue((self.root / ".ai-collab" / "agents.json").exists())
        self.assertTrue((self.root / ".ai-collab" / "capabilities.json").exists())
        self.assertTrue((self.root / ".ai-collab" / "inbox-all.md").exists())

        team = (self.root / ".ai-collab" / "TEAM.md").read_text(encoding="utf-8")
        self.assertIn("- claude-code (director)", team)
        self.assertIn("- opencode", team)
        self.assertIn("- codex", team)
        self.assertIn("| opencode | worker | antigravity | minimax/m2.5 |", team)
        self.assertIn("| codex | worker | antigravity | openai/gpt-5.5 |", team)
        inbox_all = (self.root / ".ai-collab" / "inbox-all.md").read_text(encoding="utf-8")
        self.assertIn("Read recent logs from other agents", inbox_all)
        self.assertIn("before answering or analyzing", inbox_all)
        import json
        capabilities = json.loads((self.root / ".ai-collab" / "capabilities.json").read_text(encoding="utf-8"))
        agents_manifest = json.loads((self.root / ".ai-collab" / "agents.json").read_text(encoding="utf-8"))
        self.assertEqual(agents_manifest["schema"], "ai-collab.agents.v2")
        self.assertTrue(agents_manifest["project_id"].startswith("prj_"))
        self.assertTrue(all(row["agent_id"].startswith("agt_") for row in agents_manifest["agents"]))
        self.assertEqual(capabilities["conversation_policy"]["delivery_order"], ["internal", "wait-for-response", "notify-user", "visible-chat"])
        codex = next(item for item in capabilities["agents"] if item["agent"] == "codex")
        self.assertTrue(codex["visible"]["native_chat_only"])
        self.assertEqual(codex["visible"]["availability"], "verify-at-runtime")
        self.assertTrue(codex["visible"]["delivery_is_not_response"])
        self.assertFalse(codex["wake_policy"]["hidden_fallback_allowed"])
        self.assertEqual(codex["delivery"]["primary"], "visible-chat")
        self.assertFalse(codex["wake_policy"]["internal_first"])
        opencode = next(item for item in capabilities["agents"] if item["agent"] == "opencode")
        self.assertEqual(opencode["delivery"]["primary"], "internal-inbox")
        self.assertEqual(opencode["delivery"]["fallback"], "visible-chat")
        self.assertTrue(opencode["wake_policy"]["internal_first"])

    def test_agent_identity_is_stable_across_setup_reruns(self):
        self.setup()
        import json
        first = json.loads((self.root / ".ai-collab/agents.json").read_text(encoding="utf-8"))
        self.setup()
        second = json.loads((self.root / ".ai-collab/agents.json").read_text(encoding="utf-8"))
        self.assertEqual(first["project_id"], second["project_id"])
        self.assertEqual({r["agent"]: r["agent_id"] for r in first["agents"]}, {r["agent"]: r["agent_id"] for r in second["agents"]})

    def test_shared_agents_md_can_hold_multiple_agent_snippets(self):
        self.setup()

        agents_md = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=opencode", agents_md)
        self.assertIn("AI-COLLAB-START agent=codex", agents_md)
        self.assertIn("agent_slug: `opencode`", agents_md)
        self.assertIn("agent_slug: `codex`", agents_md)
        self.assertIn("ai-collab-converse.py", agents_md)
        self.assertIn("Natural conversation contract:", agents_md)
        self.assertIn("Mandatory preflight before EVERY response, analysis, or tool action:", agents_md)
        self.assertIn("Read `.ai-collab/roles.json` if it exists", agents_md)
        self.assertIn("Read `.ai-collab/capabilities.json`", agents_md)
        self.assertIn("Every other agent gets the short internal grace period", agents_md)
        self.assertIn("Codex is submitted immediately to its exact visible chat", agents_md)
        self.assertIn("director is sleeping or stale", agents_md)
        self.assertIn("Development-team role contract:", agents_md)
        self.assertIn("Read the latest session logs in `.ai-collab/*.md` from other agents", agents_md)
        self.assertIn("Respect every `Do Not Touch (Avoid Conflicts)` section before analyzing, replying, or editing", agents_md)

    def test_opencode_gets_agent_specific_rules_file(self):
        self.setup()

        rules = (self.root / ".opencode" / "rules" / "ai-collab.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=opencode", rules)
        self.assertIn("inbox-opencode.md", rules)
        self.assertIn(".ai-collab/live/opencode.agent.json", rules)
        self.assertIn(".ai-collab/live/opencode.agent.events.jsonl", rules)
        self.assertIn("Mandatory preflight before EVERY response, analysis, or tool action:", rules)
        self.assertIn(".ai-collab/live/summary.json", rules)
        self.assertIn("roles.json", rules)

    def test_rerun_preserves_roles_in_team_manifest(self):
        self.setup()
        roles = {
            "agents": ["claude-code", "opencode", "codex"],
            "assignments": {
                "senior-director": {
                    "primary": "codex",
                    "label": "Senior director",
                    "responsibility": "Own planning and delegation.",
                }
            },
        }
        import json

        (self.root / ".ai-collab/roles.json").write_text(json.dumps(roles), encoding="utf-8")
        self.setup()

        team = (self.root / ".ai-collab/TEAM.md").read_text(encoding="utf-8")
        self.assertIn("## Development Team Roles", team)
        self.assertIn("Senior director (`senior-director`) | codex", team)

    def test_idempotent_rerun_does_not_duplicate_snippets(self):
        self.setup()
        self.setup()

        agents_md = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents_md.count("AI-COLLAB-START agent=opencode"), 1)
        self.assertEqual(agents_md.count("AI-COLLAB-START agent=codex"), 1)
        team = (self.root / ".ai-collab" / "TEAM.md").read_text(encoding="utf-8")
        self.assertEqual(team.count("- opencode"), 1)
        self.assertEqual(team.count("- codex"), 1)

    def test_rerun_updates_managed_snippet_without_duplication(self):
        self.setup()
        agents_md = self.root / "AGENTS.md"
        original = agents_md.read_text(encoding="utf-8")
        older = original.replace("Live observability contract:", "Older live contract:")
        agents_md.write_text(older, encoding="utf-8")

        self.setup()

        updated = agents_md.read_text(encoding="utf-8")
        self.assertIn("Live observability contract:", updated)
        self.assertNotIn("Older live contract:", updated)
        self.assertEqual(updated.count("AI-COLLAB-START agent=opencode"), 1)

    def test_refresh_protocol_updates_existing_copy_with_backup(self):
        self.setup()
        protocol = self.root / ".ai-collab" / "PROTOCOL.md"
        protocol.write_text("# stale protocol\n", encoding="utf-8")

        result = _mod.setup_project(
            self.root,
            ["opencode", "codex"],
            "antigravity",
            {"opencode": "minimax/m2.5", "codex": "openai/gpt-5.5"},
            now=self.now,
            refresh_protocol=True,
        )

        self.assertEqual(result["protocol"], "updated")
        self.assertIn("AI Collab Protocol", protocol.read_text(encoding="utf-8"))
        backups = list((self.root / ".ai-collab").glob("PROTOCOL.md.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "# stale protocol\n")

    def test_legacy_aliases_normalize_to_agent_runtime_names(self):
        self.setup(agents=("cursor", "windsurf", "copilot", "antigravity"), models={})

        self.assertTrue((self.root / ".cursorrules").exists())
        self.assertTrue((self.root / ".windsurfrules").exists())
        self.assertTrue((self.root / ".github" / "copilot-instructions.md").exists())
        team = (self.root / ".ai-collab" / "TEAM.md").read_text(encoding="utf-8")
        self.assertIn("- cursor-native", team)
        self.assertIn("- windsurf-native", team)
        self.assertIn("- copilot-chat", team)
        self.assertIn("- codex", team)

    def test_kimi_and_kilo_onboard_as_first_class_agents(self):
        self.setup(agents=("kimi-code", "kilo-code", "hermes"), models={"kimi": "moonshot/kimi-k2", "kilo": "kilo/default"})

        agents_md = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=kimi", agents_md)
        self.assertIn("AI-COLLAB-START agent=kilo", agents_md)
        self.assertIn("AI-COLLAB-START agent=hermes", agents_md)
        team = (self.root / ".ai-collab" / "TEAM.md").read_text(encoding="utf-8")
        self.assertIn("- kimi", team)
        self.assertIn("- kilo", team)
        self.assertIn("| kimi | worker | antigravity | moonshot/kimi-k2 |", team)
        self.assertIn("| kilo | worker | antigravity | kilo/default |", team)

    def test_native_claude_has_identity_specific_rules_not_terminal_claude_rules(self):
        self.setup(agents=("claude-code-ide",), models={})

        native_rules = self.root / ".ai-collab" / "rules" / "claude-code-ide.md"
        self.assertIn("AI-COLLAB-START agent=claude-code-ide", native_rules.read_text(encoding="utf-8"))
        claude_md = (self.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=claude-code", claude_md)
        self.assertNotIn("AI-COLLAB-START agent=claude-code-ide", claude_md)

    def test_native_claude_migration_removes_stale_shared_rule_block(self):
        collab = self.root / ".ai-collab"
        collab.mkdir()
        (collab / "agents.json").write_text(
            '{"agents":[{"agent":"claude-code-ide","agent_id":"agt_native","rules":["AGENTS.md"]}]}',
            encoding="utf-8",
        )
        (self.root / "AGENTS.md").write_text(
            "user content\n\n<!-- AI-COLLAB-START agent=claude-code-ide -->\nstale identity\n"
            "<!-- AI-COLLAB-END agent=claude-code-ide -->\n",
            encoding="utf-8",
        )

        self.setup(agents=("claude-code-ide",), models={})

        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "user content\n")
        self.assertIn(
            "agent_id: `agt_native`",
            (collab / "rules" / "claude-code-ide.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
