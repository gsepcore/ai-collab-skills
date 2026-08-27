#!/usr/bin/env python3
"""Tests for ai-collab-team.py."""
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_team",
    Path(__file__).parent / "ai-collab-team.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_team"] = _mod
_spec.loader.exec_module(_mod)


class TestTeamRoles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        collab = self.root / ".ai-collab"
        collab.mkdir()
        (collab / "agents.json").write_text(
            json.dumps({"agents": [
                {"agent": "codex", "agent_id": "agt_codex"},
                {"agent": "claude-code", "agent_id": "agt_claude"},
                {"agent": "opencode", "agent_id": "agt_opencode"},
            ]}),
            encoding="utf-8",
        )
        (collab / "TEAM.md").write_text(
            "## Roster\n\n- codex\n- claude-code\n- opencode\n",
            encoding="utf-8",
        )
        self.now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_configures_multi_role_team_and_vacancy(self):
        profile = _mod.configure_team(
            self.root,
            {
                "senior-director": "codex",
                "frontend": "claude-code",
                "backend": "claude-code",
                "database": "claude-code",
                "devops": "opencode",
                "qa": "opencode",
                "security-review": "opencode",
                "architecture-review": "opencode",
                "functional-review": "opencode",
                "deployment": "opencode",
                "ui-ux-design": None,
            },
            replace=True,
            now=self.now,
        )

        self.assertEqual(profile["assignments"]["senior-director"]["primary"], "codex")
        self.assertEqual(profile["assignments"]["backend"]["primary"], "claude-code")
        self.assertIsNone(profile["assignments"]["ui-ux-design"]["primary"])
        saved = json.loads((self.root / ".ai-collab/roles.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"], "ai-collab.roles.v2")
        self.assertEqual(saved["assignments"]["senior-director"]["primary_agent_id"], "agt_codex")
        self.assertEqual(saved["assignments"]["backend"]["primary_agent_id"], "agt_claude")
        team = (self.root / ".ai-collab/TEAM.md").read_text(encoding="utf-8")
        self.assertIn("## Development Team Roles", team)
        self.assertIn("Senior director (`senior-director`) | codex", team)
        self.assertIn("UI/UX designer (`ui-ux-design`) | unassigned", team)

    def test_partial_update_preserves_existing_roles(self):
        _mod.configure_team(
            self.root,
            {"frontend": "claude-code", "qa": "opencode", "product-research": "codex"},
            now=self.now,
        )
        profile = _mod.configure_team(self.root, {"frontend": "codex"}, now=self.now)

        self.assertEqual(profile["assignments"]["frontend"]["primary"], "codex")
        self.assertEqual(profile["assignments"]["qa"]["primary"], "opencode")
        self.assertEqual(profile["assignments"]["product-research"]["primary"], "codex")

    def test_rejects_unregistered_agent(self):
        with self.assertRaises(SystemExit):
            _mod.configure_team(self.root, {"ui-ux-design": "unknown-agent"}, now=self.now)

    def test_role_aliases(self):
        parsed = _mod.parse_assignments(["director=codex", "design=-", "db=claude-code"])
        self.assertEqual(parsed["senior-director"], "codex")
        self.assertIsNone(parsed["ui-ux-design"])
        self.assertEqual(parsed["database"], "claude-code")

    def test_catalog_roles_get_default_related_roles(self):
        # RESUMEN DE EJECUCION discussion-20260820-113730: proactive cross-role
        # review needs explicit adjacency, not inferred, so every catalog role
        # ships with a related_roles list out of the box.
        profile = _mod.configure_team(
            self.root,
            {"backend": "claude-code", "frontend": "codex", "ui-ux-design": "codex"},
            now=self.now,
        )

        self.assertEqual(profile["assignments"]["backend"]["related_roles"], ["database", "frontend", "security-review"])
        self.assertEqual(profile["assignments"]["ui-ux-design"]["related_roles"], ["frontend"])
        self.assertEqual(profile["assignments"]["senior-director"]["related_roles"], [])

    def test_custom_role_defaults_to_no_related_roles(self):
        profile = _mod.configure_team(self.root, {"product-research": "codex"}, now=self.now)

        self.assertEqual(profile["assignments"]["product-research"]["related_roles"], [])

    def test_related_roles_survive_partial_update(self):
        _mod.configure_team(self.root, {"backend": "claude-code"}, now=self.now)
        saved = json.loads((self.root / ".ai-collab/roles.json").read_text(encoding="utf-8"))
        saved["assignments"]["backend"]["related_roles"] = ["database"]
        (self.root / ".ai-collab/roles.json").write_text(json.dumps(saved), encoding="utf-8")

        profile = _mod.configure_team(self.root, {"frontend": "codex"}, now=self.now)

        self.assertEqual(profile["assignments"]["backend"]["related_roles"], ["database"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
