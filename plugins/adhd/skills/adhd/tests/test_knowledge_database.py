"""Local bridge behavior; connector/database execution is tested separately."""

from __future__ import annotations

import copy
import io
import json
import select
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _knowledge_db import (
    CONFIG, JOURNAL, PENDING, DatabaseBridge, DatabaseError, binding,
    cache_guard, fingerprint, snapshot,
)
from adhdctl import WorkspaceError, WorkspaceStore
from graphctl import GraphError, GraphStore
from graphdb import main
from taskctl import StateError, TaskStore
from treectl import TreeError, TreeStore


class KnowledgeDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / ".adhd"
        self.store = WorkspaceStore(self.root)
        self.store.initialize()
        self.graph = GraphStore(self.root)
        self.bridge = DatabaseBridge(self.root)

    def create(self, title="지식"):
        return self.graph.create_node(title=title, kind="idea", core="보존할 내용")

    def configure(self, *, new=True):
        return self.bridge.configure(project_id="example-project", graph_id="research", new=new)

    def publish(self):
        """Model trusted connector responses; this does not execute SQL."""
        self.bridge.write_query()
        pending = json.loads((self.root / PENDING).read_text())
        response = {"graph_id": pending["graph_id"],
                    "revision": pending["expected_revision"] + 1,
                    "operation_id": pending["operation_id"], "replayed": False}
        self.bridge.apply_write(response)
        envelope = {**response, "snapshot": pending["snapshot"]}
        self.bridge.apply_read(envelope)
        return envelope

    def test_initial_migration_round_trip_preserves_records_and_task_state(self):
        first = self.create()
        second = self.create("출처")
        self.graph.link(first, "supports", second, reason="근거")
        literal = "C:\\project\\new; ^\\d+\\s\\1$; 'quote'; 한국어 🧠"
        self.graph.update_node(first, content=literal, aliases=["alias"], sources=["https://example.com"])
        tasks = TaskStore(self.root)
        task = tasks.create_task(title="기존 작업", goal="이어가기", todos=[])
        tasks.dispatch()
        tasks.checkpoint(current="진행", next_action="다음", resume="계속")
        before = snapshot(self.root)
        task_before = (self.root / "task/open" / f"{task:06d}.md").read_bytes()
        self.configure()
        remote = self.publish()
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(remote["snapshot"], before)
        self.assertEqual((self.root / "task/open" / f"{task:06d}.md").read_bytes(), task_before)
        self.assertEqual(self.store.validate_all(), [])
        self.assertEqual(binding(self.root)["mode"], "clean")
        self.assertFalse((self.root / PENDING).exists())

    def test_database_edits_are_explicit_and_pending_drafts_are_frozen(self):
        self.configure()
        self.publish()
        with self.assertRaisesRegex(GraphError, "begin-edit"):
            self.create()
        self.bridge.begin_edit()
        self.create()
        self.bridge.write_query()
        with self.assertRaisesRegex(GraphError, "pending"):
            self.create()

    def test_retry_reuses_exact_sql_and_operation_id(self):
        self.create()
        self.configure()
        first = self.bridge.write_query()
        self.assertEqual(self.bridge.write_query(), first)
        self.assertEqual(binding(self.root)["mode"], "pending")

    def test_lost_acknowledgment_resolves_from_matching_readback(self):
        self.create()
        self.configure()
        self.bridge.write_query()
        pending = json.loads((self.root / PENDING).read_text())
        self.bridge.apply_read({"graph_id": "research", "revision": 1,
                                "operation_id": pending["operation_id"],
                                "snapshot": pending["snapshot"]})
        self.assertEqual(binding(self.root)["mode"], "clean")

    def test_acknowledgment_alone_does_not_allow_task_references(self):
        node = self.create()
        self.configure()
        self.bridge.write_query()
        pending = json.loads((self.root / PENDING).read_text())
        self.bridge.apply_write({"graph_id": "research", "revision": 1,
                                 "operation_id": pending["operation_id"]})
        with self.assertRaisesRegex(StateError, "verified cache"):
            TaskStore(self.root).create_task(title="작업", goal="확인", todos=[], knowledge=[node])

    def test_task_tree_and_unresolved_references_require_published_cache(self):
        node = self.create()
        project = self.workspace / "projects" / "demo"
        tree = TreeStore(project, knowledge_root=self.root)
        tree.initialize(title="프로젝트", project_id="demo", profile="generic",
                        purpose="목표", specification="문서", acceptance="확인")
        self.store.register_project(project_id="demo", name="Demo", path="projects/demo")
        self.configure()
        with self.assertRaisesRegex(StateError, "verified cache"):
            TaskStore(self.root).create_task(title="작업", goal="확인", todos=[], knowledge=[node])
        with self.assertRaisesRegex(TreeError, "verified cache"):
            tree.update_node("p000000", knowledge=[node])
        with self.assertRaisesRegex(WorkspaceError, "verified cache"):
            self.store._validate_refs(["knowledge:" + node])
        self.publish()
        task_id = TaskStore(self.root).create_task(title="작업", goal="확인", todos=[], knowledge=[node])
        self.assertIsInstance(task_id, int)
        tree.update_node("p000000", knowledge=[node])
        self.store._validate_refs(["knowledge:" + node])
        self.assertEqual(tree.validate(), [])
        self.assertEqual(TaskStore(self.root).validate(), [])
        self.bridge.begin_edit()
        self.assertTrue(any("verified cache" in error for error in tree.validate()))
        self.assertTrue(any("verified cache" in error for error in TaskStore(self.root).validate()))

    def test_no_silent_offline_initialization(self):
        self.configure(new=False)
        before = (self.root / "knowledge/graph.yaml").read_bytes()
        with self.assertRaisesRegex(GraphError, "Read the configured"):
            self.store.initialize()
        self.assertEqual((self.root / "knowledge/graph.yaml").read_bytes(), before)

    def test_existing_local_graph_requires_explicit_migration(self):
        self.create()
        with self.assertRaisesRegex(DatabaseError, "migrated explicitly"):
            self.configure(new=False)
        self.assertFalse((self.root / CONFIG).exists())

    def test_rebinding_is_rejected_without_touching_graph(self):
        self.configure()
        before = (self.root / CONFIG).read_bytes()
        with self.assertRaisesRegex(DatabaseError, "already bound"):
            self.bridge.configure(project_id="other", graph_id="other")
        self.assertEqual((self.root / CONFIG).read_bytes(), before)

    def test_conflicting_remote_read_preserves_draft_until_explicit_archive(self):
        node = self.create()
        self.configure()
        remote = self.publish()
        self.bridge.begin_edit()
        self.graph.update_node(node, core="ローカル draft")
        local = snapshot(self.root)
        newer = copy.deepcopy(remote)
        newer["revision"] += 1
        newer["operation_id"] = "different-operation"
        self.bridge.write_query()
        with self.assertRaisesRegex(DatabaseError, "conflicting draft"):
            self.bridge.apply_read(newer)
        self.assertEqual(snapshot(self.root), local)
        self.assertTrue((self.root / PENDING).exists())
        result = self.bridge.apply_read(newer, preserve_draft=True)
        archive = json.loads(Path(result["preserved_draft"]).read_text())
        self.assertEqual(archive["snapshot"], local)
        self.assertIn("pending", archive)
        self.assertEqual(snapshot(self.root), remote["snapshot"])

    def test_wrong_graph_revision_and_operation_are_rejected(self):
        self.create()
        self.configure()
        self.bridge.write_query()
        pending = json.loads((self.root / PENDING).read_text())
        good = {"graph_id": "research", "revision": 1, "operation_id": pending["operation_id"]}
        for field, value in [("graph_id", "wrong"), ("operation_id", "wrong"), ("revision", True), ("revision", 2)]:
            with self.subTest(field=field), self.assertRaises(DatabaseError):
                self.bridge.apply_write({**good, field: value})
        self.assertEqual(binding(self.root)["mode"], "pending")

    def test_manual_clean_cache_change_is_detected(self):
        node = self.create()
        self.configure()
        remote = self.publish()
        path = self.root / "knowledge/nodes" / (node + ".md")
        path.write_text(path.read_text().replace("보존할 내용", "수동 변경"))
        with self.assertRaisesRegex(GraphError, "changed outside"):
            self.graph.show(node)
        with self.assertRaisesRegex(DatabaseError, "unpublished"):
            self.bridge.apply_read(remote)
        result = self.bridge.apply_read(remote, preserve_draft=True)
        self.assertTrue(Path(result["preserved_draft"]).exists())
        self.assertIn("보존할 내용", self.graph.show(node))

    def test_older_or_same_revision_changed_content_is_rejected(self):
        self.create()
        self.configure()
        remote = self.publish()
        modified = copy.deepcopy(remote)
        modified["snapshot"]["graph"]["name"] = "changed"
        with self.assertRaisesRegex(DatabaseError, "without advancing"):
            self.bridge.apply_read(modified)
        self.bridge.begin_edit()
        self.graph.update_node("k000000", core="new")
        self.publish()
        with self.assertRaisesRegex(DatabaseError, "older"):
            self.bridge.apply_read(remote)

    def test_crash_mid_import_blocks_references_and_recovers(self):
        self.create()
        self.configure()
        remote = self.publish()
        incoming = copy.deepcopy(remote)
        incoming["revision"] += 1
        incoming["snapshot"]["graph"]["name"] = "new graph name"
        import _knowledge_db
        materialize = _knowledge_db._materialize
        def interrupted(root, value):
            if root == self.root:
                raise OSError("disk interruption")
            return materialize(root, value)
        with patch("_knowledge_db._materialize", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.bridge.apply_read(incoming)
        self.assertTrue((self.root / JOURNAL).exists())
        with self.assertRaisesRegex(DatabaseError, "Interrupted"):
            cache_guard(self.root, reference=True)
        self.bridge.recover()
        self.assertEqual(snapshot(self.root), incoming["snapshot"])
        self.assertFalse((self.root / JOURNAL).exists())
        self.assertEqual(self.store.validate_all(), [])

    def test_malformed_snapshots_cannot_change_cache(self):
        self.create()
        self.configure()
        remote = self.publish()
        cases = []
        duplicate = copy.deepcopy(remote)
        duplicate["snapshot"]["nodes"].append(duplicate["snapshot"]["nodes"][0])
        cases.append(duplicate)
        dangling = copy.deepcopy(remote)
        dangling["snapshot"]["edges"] = [{"from": "k000000", "to": "k000001", "relation": "supports", "reason": "why"}]
        cases.append(dangling)
        invalid = copy.deepcopy(remote)
        invalid["snapshot"]["nodes"][0]["metadata"]["kind"] = "invalid"
        cases.append(invalid)
        invalid = copy.deepcopy(remote)
        invalid["snapshot"]["next_id"] = False
        cases.append(invalid)
        before = snapshot(self.root)
        for value in cases:
            with self.subTest(snapshot=value), self.assertRaises(DatabaseError):
                self.bridge.apply_read(value)
            self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / JOURNAL).exists())

    def test_non_roundtrippable_body_is_rejected_before_any_import(self):
        self.create()
        self.configure()
        remote = self.publish()
        before = snapshot(self.root)
        for suffix in ("\n", "\r\n"):
            incoming = copy.deepcopy(remote)
            incoming["revision"] += 1
            incoming["snapshot"]["nodes"][0]["body"] += suffix
            with self.assertRaisesRegex(DatabaseError, "losslessly"):
                self.bridge.apply_read(incoming)
            self.assertEqual(snapshot(self.root), before)
            self.assertFalse((self.root / JOURNAL).exists())

    def test_pending_request_binding_tampering_cannot_change_destination(self):
        self.create()
        self.configure()
        self.bridge.write_query()
        pending = json.loads((self.root / PENDING).read_text())
        pending["graph_id"] = "another-users-graph"
        (self.root / PENDING).write_text(json.dumps(pending))
        with self.assertRaisesRegex(DatabaseError, "different graph"):
            self.bridge.write_query()

    def test_remote_project_scope_requires_local_project_registration(self):
        self.create()
        self.configure()
        remote = self.publish()
        before = snapshot(self.root)
        incoming = copy.deepcopy(remote)
        incoming["revision"] += 1
        incoming["snapshot"]["nodes"][0]["metadata"]["scope"] = "project:demo"
        with self.assertRaisesRegex(DatabaseError, "unregistered project"):
            self.bridge.apply_read(incoming)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / JOURNAL).exists())
        project = self.workspace / "projects" / "demo"
        TreeStore(project, knowledge_root=self.root).initialize(
            title="Demo", project_id="demo", profile="generic", purpose="Goal",
            specification="Document", acceptance="Verified")
        self.store.register_project(project_id="demo", name="Demo", path="projects/demo")
        self.bridge.apply_read(incoming)
        self.assertEqual(self.store.validate_all(), [])

    def test_corrupted_recovery_binding_does_not_touch_runtime(self):
        self.create()
        self.configure()
        remote = self.publish()
        before = snapshot(self.root)
        target = copy.deepcopy(binding(self.root))
        modified = copy.deepcopy(before)
        modified["nodes"][0]["body"] = "## 핵심\n\nChanged"
        target.update(base_digest=fingerprint(modified), revision=2)
        for field, value in [("revision", -1), ("schema_version", 2), ("backend", "other")]:
            invalid = dict(target, **{field: value})
            (self.root / JOURNAL).write_text(json.dumps({"binding": invalid, "snapshot": modified}))
            with self.subTest(field=field), self.assertRaises(DatabaseError):
                self.bridge.recover()
            self.assertEqual(snapshot(self.root), before)
            self.assertTrue((self.root / JOURNAL).exists())

    def test_jsonb_numeric_normalization_does_not_create_false_conflicts(self):
        self.create()
        path = self.root / "knowledge/graph.yaml"
        graph = yaml.safe_load(path.read_text())
        graph["custom"] = [1e20, 1.0000000000000002e20, -0.0, 1e-20]
        path.write_text(yaml.safe_dump(graph))
        self.configure()
        self.bridge.write_query()
        pending = json.loads((self.root / PENDING).read_text())
        exported = copy.deepcopy(pending["snapshot"])
        exported["graph"]["custom"] = [100000000000000000000, 100000000000000020000, 0, 1e-20]
        self.bridge.apply_read({"graph_id": "research", "revision": 1,
                                "operation_id": pending["operation_id"], "snapshot": exported})
        self.assertEqual(binding(self.root)["mode"], "clean")
        self.assertEqual(fingerprint(pending["snapshot"]), fingerprint(snapshot(self.root)))

    def test_tree_mutation_lock_excludes_database_mode_changes(self):
        self.create()
        self.configure()
        self.publish()
        project = self.workspace / "projects" / "demo"
        tree = TreeStore(project, knowledge_root=self.root)
        tree.initialize(title="프로젝트", project_id="demo", profile="generic",
                        purpose="목표", specification="문서", acceptance="확인")
        script = Path(__file__).resolve().parents[1] / "scripts"
        code = "import sys; sys.path.insert(0,sys.argv[1]); from _knowledge_db import DatabaseBridge; print('waiting',flush=True); DatabaseBridge(sys.argv[2]).begin_edit(); print('acquired',flush=True)"
        with tree.locked():
            process = subprocess.Popen([sys.executable, "-c", code, str(script), str(self.root)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            self.assertEqual(process.stdout.readline().strip(), "waiting")
            ready, _, _ = select.select([process.stdout], [], [], 0.1)
            self.assertEqual(ready, [], "Database edit passed the held tree/workspace lock")
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("acquired", stdout)

    def test_recovery_finishes_a_partially_replaced_cache(self):
        import _knowledge_db
        self.create()
        self.configure()
        remote = self.publish()
        incoming = copy.deepcopy(remote)
        incoming["revision"] += 1
        incoming["snapshot"]["nodes"][0]["body"] = "## 핵심\n\nRemote revision"
        writer = _knowledge_db._atomic_write_text
        def interrupted(path, text):
            if path == self.root / "knowledge/edges.yaml":
                raise OSError("interrupted after node replacement")
            return writer(path, text)
        with patch("_knowledge_db._atomic_write_text", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.bridge.apply_read(incoming)
        self.assertTrue((self.root / JOURNAL).exists())
        self.assertIn("Remote revision", (self.root / "knowledge/nodes/k000000.md").read_text())
        with self.assertRaisesRegex(GraphError, "Interrupted"):
            self.graph.show("k000000")
        self.bridge.recover()
        self.assertEqual(snapshot(self.root), incoming["snapshot"])
        self.assertEqual(self.store.validate_all(), [])

    def test_sql_is_credential_free_and_quotes_untrusted_values(self):
        node = self.create("Robert'); DROP SCHEMA public; --")
        self.graph.update_node(node, content="\\\\ '; 한국어")
        self.bridge.configure(project_id="example-project", graph_id="research'; SELECT 1; \\", new=True)
        call = self.bridge.write_query()
        self.assertEqual(set(call), {"project_id", "query"})
        self.assertTrue(call["query"].startswith("SELECT adhd_knowledge.replace_graph(E'"))
        self.assertIn("research''; SELECT 1; \\\\", call["query"])
        self.assertNotIn("service_role", call["query"])
        self.assertNotIn("password", call["query"])

    def test_cli_emits_tool_arguments_and_errors_are_actionable(self):
        self.configure()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--root", str(self.root), "read-query"]), 0)
        call = json.loads(output.getvalue())
        self.assertEqual(call["project_id"], "example-project")
        self.assertIn("export_graph", call["query"])
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(main(["--root", str(self.root), "recover"]), 2)
        self.assertIn("No interrupted", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
