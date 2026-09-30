"""Private SQL contract checks and optional disposable local PostgreSQL tests.

No installed server binaries means integration checks are explicitly skipped.
These tests never use a DSN, credentials, a network listener, or a remote database.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCHEMA = SKILL / "assets" / "knowledge_schema.sql"


class SqlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = SCHEMA.read_text(encoding="utf-8")
        cls.statements = re.sub(r"--[^\n]*", "", cls.sql)

    def test_private_invoker_functions_and_tables(self):
        functions = re.findall(
            r"CREATE OR REPLACE FUNCTION\s+adhd_knowledge\.\w+.*?AS \$function\$",
            self.statements, re.DOTALL,
        )
        self.assertEqual(len(functions), 4)
        for declaration in functions:
            self.assertIn("SECURITY INVOKER", declaration)
            self.assertIn("SET search_path = ''", declaration)
        self.assertNotRegex(self.statements, r"\bSECURITY\s+DEFINER\b|\bGRANT\b")
        self.assertNotRegex(self.statements, r"\bCREATE\s+POLICY\b")
        for table in ("graphs", "nodes", "edges"):
            self.assertIn(f"ALTER TABLE adhd_knowledge.{table} ENABLE ROW LEVEL SECURITY;", self.sql)
        self.assertIn("REVOKE ALL ON SCHEMA adhd_knowledge FROM PUBLIC", self.sql)
        self.assertIn("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA adhd_knowledge FROM PUBLIC", self.sql)
        self.assertIn("REVOKE ALL ON ALL TABLES IN SCHEMA adhd_knowledge FROM PUBLIC", self.sql)
        self.assertIn("ARRAY['anon', 'authenticated', 'service_role']", self.sql)

    def test_collision_guard_precedes_permission_and_function_changes(self):
        marker = "adhd:knowledge-store:1"
        self.assertIn("pg_catalog.obj_description(v_schema_oid, 'pg_namespace')", self.sql)
        self.assertIn(f"IS DISTINCT FROM '{marker}'", self.sql)
        self.assertIn(f"COMMENT ON SCHEMA adhd_knowledge IS '{marker}'", self.sql)
        self.assertNotIn("CREATE SCHEMA IF NOT EXISTS", self.statements)
        guard_end = self.sql.index("$schema$;", self.sql.index("DO $schema$"))
        self.assertLess(guard_end, self.sql.index("REVOKE ALL ON SCHEMA"))
        self.assertLess(guard_end, self.sql.index("CREATE OR REPLACE FUNCTION"))

    def test_scoped_keys_atomic_compare_and_swap_and_replay(self):
        self.assertIn("PRIMARY KEY (graph_id, node_id)", self.sql)
        self.assertIn("FOREIGN KEY (graph_id, source_id)", self.sql)
        self.assertIn("FOREIGN KEY (graph_id, target_id)", self.sql)
        self.assertIn("canonical_source_id, relation, canonical_target_id", self.sql)
        self.assertIn("pg_catalog.pg_advisory_xact_lock", self.sql)
        self.assertIn("FOR UPDATE", self.sql)
        self.assertIn("v_current.revision <> p_expected_revision", self.sql)
        self.assertIn("v_current.last_operation_id = p_operation_id", self.sql)
        self.assertIn("v_current.last_payload_sha256 <> v_payload_sha256", self.sql)
        self.assertIn("v_current.last_expected_revision <> p_expected_revision", self.sql)
        self.assertIn("Existing node IDs must be preserved", self.sql)
        self.assertIn("'operation_id', g.last_operation_id", self.sql)
        self.assertIn("ORDER BY n.position", self.sql)
        self.assertIn("ORDER BY e.position", self.sql)
        self.assertIn("'body', n.body", self.sql)
        self.assertRegex(self.statements, r"(?s)^\s*BEGIN;.*COMMIT;\s*$")


def postgres_bin_dir() -> Path | None:
    """Find existing binaries only; never install dependencies for these tests."""
    candidates = []
    found = shutil.which("initdb")
    if found:
        candidates.append(Path(found).parent)
    candidates.extend(sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True))
    for directory in candidates:
        if all((directory / name).is_file() for name in ("initdb", "pg_ctl", "psql")):
            return directory
    return None


class LocalPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = postgres_bin_dir()
        if binary is None or (hasattr(os, "geteuid") and os.geteuid() == 0):
            raise unittest.SkipTest("Disposable PostgreSQL tests need installed binaries and a non-root user")
        cls.temporary = tempfile.TemporaryDirectory(prefix="adhd-pg-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        cls.data = directory / "data"
        cls.pg_ctl = str(binary / "pg_ctl")
        subprocess.run(
            [str(binary / "initdb"), "-D", str(cls.data), "--auth=trust",
             "--no-locale", "--encoding=UTF8"],
            check=True, capture_output=True, text=True,
        )
        # A private Unix socket and disabled TCP make external connections impossible.
        with (cls.data / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write(f"\nlisten_addresses = ''\nunix_socket_directories = '{directory}'\n")
        subprocess.run(
            [cls.pg_ctl, "-D", str(cls.data), "-l", str(directory / "server.log"), "-w", "start"],
            check=True, capture_output=True, text=True,
        )
        cls.addClassCleanup(
            subprocess.run,
            [cls.pg_ctl, "-D", str(cls.data), "-m", "immediate", "-w", "stop"],
            capture_output=True, text=True, check=False,
        )
        cls.command = [str(binary / "psql"), "-X", "-h", str(directory), "-d", "postgres",
                       "-v", "ON_ERROR_STOP=1", "-tA"]
        cls.query("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;")
        cls.query(SCHEMA.read_text(encoding="utf-8"))
        cls.query(SCHEMA.read_text(encoding="utf-8"))  # Safe installation retry.

    @classmethod
    def query(cls, sql: str, *, succeeds: bool = True) -> str:
        result = subprocess.run(cls.command, input=sql, capture_output=True, text=True)
        if succeeds and result.returncode:
            raise AssertionError(result.stderr)
        if not succeeds and not result.returncode:
            raise AssertionError("Invalid SQL operation unexpectedly succeeded")
        return result.stdout.strip() if succeeds else result.stderr

    @staticmethod
    def literal(value) -> str:
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"

    @classmethod
    def replace_sql(cls, graph_id, snapshot, revision=0, operation=None):
        operation = operation or str(uuid.uuid4())
        return (
            "SELECT adhd_knowledge.replace_graph("
            f"'{graph_id}', {revision}, '{operation}'::uuid, {cls.literal(snapshot)});"
        )

    def setUp(self):
        self.query("TRUNCATE adhd_knowledge.edges, adhd_knowledge.nodes, adhd_knowledge.graphs;")
        self.snapshot = {
            "graph": {"schema_version": 2, "graph_id": "workspace", "kind": "workspace",
                      "name": "General methods", "default_scope": "common", "imports": [],
                      "extension": {"retained": True}},
            "next_id": 3,
            "nodes": [
                {"id": "k000002", "metadata": {
                    "title": "Literal examples", "kind": "idea", "scope": "common",
                    "aliases": ["Sample"], "sources": ["https://example.org/"],
                    "status": "active", "extension": [1, {"nested": "value"}],
                }, "body": "\n## 핵심\n\nA general method\n\n## 내용\nC:\\new\\file\n'quoted'\n\n"},
                {"id": "k000000", "metadata": {
                    "title": "Reusable concept", "kind": "concept", "scope": "common",
                    "aliases": [], "sources": [], "status": "active",
                }, "body": "## 핵심\r\n\r\nA concept\r\n\r\n"},
            ],
            "edges": [{"from": "k000002", "relation": "related_to", "to": "k000000",
                       "reason": "  Shared method\n"}],
        }
        self.snapshot["nodes"].sort(key=lambda node: node["id"])

    def export(self, graph_id="graph-one"):
        raw = self.query(f"SELECT adhd_knowledge.export_graph('{graph_id}');")
        return json.loads(raw) if raw else None

    def test_lossless_round_trip_idempotency_and_stale_revision(self):
        operation = str(uuid.uuid4())
        sql = self.replace_sql("graph-one", self.snapshot, operation=operation)
        response = json.loads(self.query(sql))
        self.assertEqual(response["revision"], 1)
        self.assertFalse(response["replayed"])
        exported = self.export()
        self.assertEqual(exported["snapshot"], self.snapshot)
        self.assertEqual(exported["operation_id"], operation)
        self.assertTrue(json.loads(self.query(sql))["replayed"])
        changed = copy.deepcopy(self.snapshot)
        changed["graph"]["name"] = "Updated methods"
        self.assertIn("Operation UUID reused", self.query(
            self.replace_sql("graph-one", changed, operation=operation), succeeds=False))
        self.assertIn("revision conflict", self.query(
            self.replace_sql("graph-one", changed), succeeds=False))
        next_sql = self.replace_sql("graph-one", changed, revision=1)
        self.assertEqual(json.loads(self.query(next_sql))["revision"], 2)
        self.assertIn("revision conflict", self.query(sql, succeeds=False))
        self.assertEqual(self.export()["snapshot"], changed)
        self.assertIsNone(self.export("missing-graph"))

    def test_rejects_invalid_snapshots_without_partial_changes(self):
        self.query(self.replace_sql("graph-one", self.snapshot))
        baseline = self.export()
        cases = []
        for field, value in (("schema_version", 1), ("kind", "other"), ("imports", [1]),
                             ("default_scope", "project:"), ("name", "\n\t")):
            candidate = copy.deepcopy(self.snapshot)
            candidate["graph"][field] = value
            cases.append(candidate)
        for field, value in (("title", "\n\t"), ("kind", "other"), ("status", "other"),
                             ("aliases", [1]), ("sources", "url"), ("scope", "other")):
            candidate = copy.deepcopy(self.snapshot)
            candidate["nodes"][0]["metadata"][field] = value
            cases.append(candidate)
        for body in ("No core", "## 핵심\n \t\n## 내용\nNot a core"):
            candidate = copy.deepcopy(self.snapshot)
            candidate["nodes"][0]["body"] = body
            cases.append(candidate)
        for value in (-1, 2, True, "3", 3.5, 1000001):
            candidate = copy.deepcopy(self.snapshot)
            candidate["next_id"] = value
            cases.append(candidate)
        for field, value in (("from", "k000999"), ("from", "other:k000000"),
                             ("from", "k000000"), ("relation", "unknown"),
                             ("reason", "\t\n")):
            candidate = copy.deepcopy(self.snapshot)
            candidate["edges"][0][field] = value
            cases.append(candidate)
        candidate = copy.deepcopy(self.snapshot)
        candidate["nodes"].append(copy.deepcopy(candidate["nodes"][0]))
        cases.append(candidate)
        candidate = copy.deepcopy(self.snapshot)
        candidate["nodes"].reverse()
        cases.append(candidate)
        candidate = copy.deepcopy(self.snapshot)
        candidate["edges"].append({"from": "k000000", "relation": "related_to",
                                   "to": "k000002", "reason": "Duplicate reverse"})
        cases.append(candidate)
        candidate = copy.deepcopy(self.snapshot)
        candidate["extra"] = "Would be lost"
        cases.append(candidate)
        for candidate in cases:
            with self.subTest(snapshot=candidate):
                self.query(self.replace_sql("graph-one", candidate, revision=1), succeeds=False)
                self.assertEqual(self.export(), baseline)

    def test_scoped_nodes_replacement_and_monotonic_allocator(self):
        self.query(self.replace_sql("graph-one", self.snapshot))
        self.query(self.replace_sql("graph-two", self.snapshot))
        other = self.export("graph-two")
        fewer = copy.deepcopy(self.snapshot)
        fewer["nodes"] = [node for node in fewer["nodes"] if node["id"] == "k000000"]
        fewer["edges"] = []
        fewer["next_id"] = 1
        self.assertIn("cannot move backwards", self.query(
            self.replace_sql("graph-one", fewer, revision=1), succeeds=False))
        fewer["next_id"] = 3
        self.assertIn("Existing node IDs must be preserved", self.query(
            self.replace_sql("graph-one", fewer, revision=1), succeeds=False))
        archived = copy.deepcopy(self.snapshot)
        archived["nodes"][0]["metadata"]["status"] = "archived"
        archived["edges"] = []
        self.query(self.replace_sql("graph-one", archived, revision=1))
        self.assertEqual(self.export()["snapshot"], archived)
        self.assertEqual(self.export("graph-two"), other)

    def test_concurrent_publications_have_one_winner(self):
        self.query(self.replace_sql("graph-one", self.snapshot))
        commands = [self.replace_sql("graph-one", self.snapshot, revision=1) for _ in range(2)]
        processes = [subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in commands]
        for process, sql in zip(processes, commands):
            process.stdin.write(sql)
            process.stdin.close()
            process.stdin = None
        results = [(process.communicate(timeout=15), process.returncode) for process in processes]
        self.assertEqual(sum(code == 0 for _, code in results), 1)
        self.assertEqual(self.export()["revision"], 2)

    def test_api_roles_have_no_access_and_rls_is_enabled(self):
        for role in ("anon", "authenticated", "service_role"):
            self.assertEqual(self.query(
                f"SELECT has_schema_privilege('{role}', 'adhd_knowledge', 'USAGE');"), "f")
            self.assertEqual(self.query(
                f"SELECT has_function_privilege('{role}', 'adhd_knowledge.export_graph(text)', 'EXECUTE');"), "f")
        self.assertEqual(self.query(
            "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = 'adhd_knowledge' "
            "AND c.relkind = 'r' AND c.relrowsecurity;"), "3")

    def test_unmarked_or_incompatible_schema_is_not_modified(self):
        self.query("CREATE TABLE adhd_knowledge.unrelated_sentinel (value text); "
                   "INSERT INTO adhd_knowledge.unrelated_sentinel VALUES ('keep'); "
                   "GRANT USAGE ON SCHEMA adhd_knowledge TO anon;")
        function_before = self.query(
            "SELECT pg_catalog.pg_get_functiondef('adhd_knowledge.export_graph(text)'::regprocedure);")
        try:
            for marker in (None, "unrelated schema", "adhd:knowledge-store:999"):
                with self.subTest(marker=marker):
                    value = "NULL" if marker is None else "'" + marker + "'"
                    self.query("COMMENT ON SCHEMA adhd_knowledge IS " + value + ";")
                    error = self.query(SCHEMA.read_text(encoding="utf-8"), succeeds=False)
                    self.assertIn("refusing to modify", error)
                    self.assertEqual(self.query("SELECT value FROM adhd_knowledge.unrelated_sentinel;"), "keep")
                    self.assertEqual(self.query(
                        "SELECT has_schema_privilege('anon', 'adhd_knowledge', 'USAGE');"), "t")
                    self.assertEqual(self.query(
                        "SELECT pg_catalog.obj_description(oid, 'pg_namespace') "
                        "FROM pg_catalog.pg_namespace WHERE nspname = 'adhd_knowledge';"), marker or "")
                    self.assertEqual(self.query(
                        "SELECT pg_catalog.pg_get_functiondef('adhd_knowledge.export_graph(text)'::regprocedure);"),
                        function_before)
        finally:
            self.query("COMMENT ON SCHEMA adhd_knowledge IS 'adhd:knowledge-store:1'; "
                       "REVOKE USAGE ON SCHEMA adhd_knowledge FROM anon; "
                       "DROP TABLE adhd_knowledge.unrelated_sentinel;")


if __name__ == "__main__":
    unittest.main()
