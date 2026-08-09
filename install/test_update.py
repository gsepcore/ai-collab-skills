#!/usr/bin/env python3
"""
Tests for ai-collab-update.py.
Run with: python3 install/test_update.py
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_update",
    Path(__file__).parent / "ai-collab-update.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovers_existing_collab_projects(self):
        project = self.home / "work" / "app"
        (project / ".ai-collab").mkdir(parents=True)
        ignored = self.home / "work" / "app" / "node_modules" / "dep"
        (ignored / ".ai-collab").mkdir(parents=True)

        projects = _mod.discover_projects(self.home, max_depth=4)

        self.assertIn(project, projects)
        self.assertNotIn(ignored, projects)

    def test_project_args_from_manifest_preserves_agents_models_and_container(self):
        project = self.home / "app"
        collab = project / ".ai-collab"
        collab.mkdir(parents=True)
        (collab / "agents.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {"agent": "opencode", "container": "antigravity", "model": "minimax/m2.5"},
                        {"agent": "codex", "container": "antigravity", "model": "openai/gpt-5.5"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        agents, container, models = _mod.project_args_from_manifest(project)

        self.assertEqual(agents, ["claude-code", "opencode", "codex"])
        self.assertEqual(container, "antigravity")
        self.assertEqual(models["opencode"], "minimax/m2.5")
        self.assertEqual(models["codex"], "openai/gpt-5.5")

    def test_project_args_omits_unknown_model_to_keep_generated_snippets_stable(self):
        project = self.home / "app"
        collab = project / ".ai-collab"
        collab.mkdir(parents=True)
        (collab / "agents.json").write_text(
            json.dumps({"agents": [{"agent": "codex", "container": "unknown", "model": "unknown"}]}),
            encoding="utf-8",
        )

        agents, container, models = _mod.project_args_from_manifest(project)

        self.assertEqual(agents, ["claude-code", "codex"])
        self.assertEqual(container, "unknown")
        self.assertEqual(models, {})

    def test_global_update_includes_codex_bridge_and_recovery(self):
        rels = [rel for rel, _dest, _executable in _mod.GLOBAL_FILES]
        destinations = [str(dest) for _rel, dest, _executable in _mod.GLOBAL_FILES]

        self.assertIn("install/ai-collab-codex-bridge.py", rels)
        self.assertIn("install/ai-collab-recover.py", rels)
        self.assertIn("install/ai-collab-team.py", rels)
        self.assertIn("install/ai-collab-setup.py", rels)
        self.assertTrue(any(".codex/skills/collab/SKILL.md" in path for path in destinations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
