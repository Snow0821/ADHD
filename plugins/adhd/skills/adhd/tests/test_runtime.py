"""Regression checks for workspace initialization and literal record content."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from adhdctl import WorkspaceStore, main
from graphctl import GraphStore
from taskctl import TaskStore
from treectl import TreeStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / ".adhd"
        self.store = WorkspaceStore(self.root)

    def test_init_cli_creates_all_stores_and_preserves_records(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "init"]), 0)
        self.assertIsNotNone(self.store.status()["knowledge"])
        self.assertEqual(self.store.validate_all(), [])
        for name in ("task", "knowledge", "control", "history", "unresolved"):
            self.assertTrue((self.root / name).is_dir())
        tasks = TaskStore(self.root)
        tasks.create_task(title="현재 작업", goal="기존 기록 유지", todos=[])
        tasks.dispatch()
        graph = GraphStore(self.root)
        graph.create_node(title="지식", kind="idea", core="보존할 생각")
        self.store.create_control(title="원칙", kind="principle", statement="보존")
        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file() and p.suffix != ".lock"}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "init"]), 0)
        after = {p.relative_to(self.root): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file() and p.suffix != ".lock"}
        self.assertEqual(before, after)

    def test_init_preserves_custom_graph_metadata(self):
        self.store.ensure()
        GraphStore(self.root).initialize(
            graph_id="research", kind="workspace", name="연구 지식",
        )
        path = self.root / "knowledge" / "graph.yaml"
        before = path.read_bytes()
        self.store.initialize()
        self.assertEqual(path.read_bytes(), before)

    def test_validation_reports_missing_graph(self):
        self.store.ensure()
        self.assertTrue(any("graph is missing" in e for e in self.store.validate_all()))
        self.assertFalse((self.root / "knowledge" / "graph.yaml").exists())

    def test_records_preserve_windows_paths_regex_and_formulas(self):
        self.store.initialize()
        literal = r"C:\project\new\index.html; ^\d+\s\1$; \frac{1}{2}"
        tasks = TaskStore(self.root)
        task_id = tasks.create_task(title="경로", goal="문자열 보존", todos=[])
        tasks.dispatch()
        tasks.checkpoint(current=literal, next_action=literal, resume=literal)
        self.assertIn(literal, (self.root / "task/open" / f"{task_id:06d}.md").read_text())
        graph = GraphStore(self.root)
        node_id = graph.create_node(title="코드", kind="idea", core="표현")
        graph.update_node(node_id, content=literal)
        self.assertIn(literal, (self.root / "knowledge/nodes" / f"{node_id}.md").read_text())
        project = self.workspace / "projects" / "demo"
        tree = TreeStore(project, knowledge_root=self.root)
        tree.initialize(title="예시", project_id="demo", profile="generic",
                        purpose="보존", specification="문서", acceptance="문자열 유지")
        self.store.register_project(project_id="demo", name="예시", path="projects/demo")
        tree.update_node("p000000", specification=literal)
        self.assertIn(literal, (project / "project/nodes/p000000.md").read_text())
        unresolved_id = self.store.create_unresolved(
            title="표현", domain="knowledge", question="경로는?",
            reason="미확인", needed="경로 확인",
        )
        self.store.resolve_unresolved(unresolved_id, literal)
        self.assertIn(literal, (self.root / "unresolved/items" / f"{unresolved_id}.md").read_text())
        tasks.close(outcome="completed", summary=literal)
        self.assertEqual(self.store.validate_all(), [])


if __name__ == "__main__":
    unittest.main()
