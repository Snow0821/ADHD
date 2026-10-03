"""Behavior and safety checks for the optional, host-owned execution queue.

All runtime state, SQLite databases, and handler artifacts stay in temporary
workspaces. Real processes exercise the public CLI and SQLite claim locking.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import stat
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _execution import EXECUTION_SCHEMA, PACKAGE_VERSION, ExecutionError, JobQueue, work_once
from adhdctl import WorkspaceStore
from taskctl import TaskStore


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / ".adhd"
        self.store = WorkspaceStore(self.root)
        self.store.initialize()
        self.tasks = TaskStore(self.root)
        self.task_id = self.tasks.create_task(title="Bounded work", goal="Verified result", todos=[])
        self.clock = Clock()
        self.queue = JobQueue(self.root, clock=self.clock)
        self.counter = 0

    def submit(self, **overrides):
        self.counter += 1
        args = {"request_key": f"request-{self.counter}", "title": "Bounded job",
                "task_id": self.task_id}
        args.update(overrides)
        return self.queue.submit(**args)

    def claim(self, **overrides):
        return self.queue.claim(worker="worker-a", **overrides)

    def cli(self, *args, success=True, root=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "execctl.py"), "--root", str(root or self.root), *args],
            text=True, capture_output=True, timeout=15, check=False,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def core_snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*")
                if path.is_file() and "execution" not in path.relative_to(self.root).parts
                and path.suffix != ".lock"}

    def test_restart_retains_priority_fifo_ties_and_delayed_jobs(self):
        low = self.submit(priority=-1)
        first = self.submit(priority=10)
        second = self.submit(priority=10)
        delayed = self.submit(priority=100, available_at=1100)
        self.queue = JobQueue(self.root, clock=self.clock)
        claimed = [self.claim()["id"] for _ in range(3)]
        self.assertEqual(claimed, [first["id"], second["id"], low["id"]])
        self.assertIsNone(self.claim())
        self.clock.advance(100)
        self.assertEqual(self.claim()["id"], delayed["id"])
        self.assertEqual([j["id"] for j in self.queue.status()],
                         [low["id"], first["id"], second["id"], delayed["id"]])

    def test_process_restart_retains_running_claim_checkpoint_and_result(self):
        job = self.cli("submit", "--request-key", "durable", "--title", "Durable job",
                       "--task-id", str(self.task_id), "--payload", '{"text":"연구"}')
        claimed = self.cli("claim", "--worker", "process-a")
        self.assertEqual(claimed["id"], job["id"])
        checkpoint = {"verified": ["part one"], "next": "part two"}
        self.cli("checkpoint", job["id"], "--token", claimed["token"],
                 "--value", json.dumps(checkpoint))
        resumed = self.cli("get", job["id"])
        self.assertEqual(resumed["checkpoint"], checkpoint)
        self.assertEqual(resumed["token"], claimed["token"])
        finished = self.cli("finish", job["id"], "--token", claimed["token"],
                            "--result", '{"artifact":"verified.txt"}')
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual(self.cli("status")[0]["result"], {"artifact": "verified.txt"})

    def test_independent_processes_never_double_claim(self):
        jobs = [self.submit() for _ in range(6)]
        start = threading.Barrier(8)

        def run(index):
            start.wait(timeout=10)
            return self.cli("claim", "--worker", f"process-{index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        claimed = [result for result in results if result is not None]
        self.assertEqual(len(claimed), 6)
        self.assertEqual({j["id"] for j in claimed}, {j["id"] for j in jobs})
        self.assertEqual(len({j["token"] for j in claimed}), 6)
        self.assertTrue(all(j["attempt"] == 1 for j in claimed))
        self.assertEqual(sum(e["event"] == "claimed" for e in self.queue.events()), 6)

    def test_concurrent_idempotent_submission_creates_one_job_and_event(self):
        start = threading.Barrier(8)

        def run(_):
            queue = JobQueue(self.root, clock=self.clock)
            start.wait(timeout=10)
            return queue.submit(request_key="same", title="Same", task_id=self.task_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(run, range(8)))
        self.assertEqual(len({job["id"] for job in jobs}), 1)
        self.assertEqual(len(self.queue.status()), 1)
        self.assertEqual(len(self.queue.events()), 1)

    def test_idempotency_canonical_payload_conflicts_and_terminal_replay(self):
        args = dict(request_key="once", title="Literal ' title", task_id=self.task_id,
                    payload={"a": 1, "b": "한글"}, max_attempts=2)
        first = self.queue.submit(**args)
        reordered = dict(args, payload={"b": "한글", "a": 1})
        self.assertEqual(self.queue.submit(**reordered)["id"], first["id"])
        for changed in ({"payload": {"a": 2}}, {"title": "different"}, {"priority": 1},
                        {"kind": "other"}, {"max_attempts": 3}, {"retry_safe": True},
                        {"task_id": None}, {"available_at": 2000}):
            with self.subTest(changed=changed), self.assertRaises(ExecutionError):
                self.queue.submit(**dict(args, **changed))
        claim = self.claim()
        finished = self.queue.finish(first["id"], token=claim["token"], result={"ok": True})
        self.tasks.close(task_id=self.task_id, outcome="completed", summary="Verified")
        self.clock.advance(10000)
        self.assertEqual(self.queue.submit(**args), finished)
        self.assertEqual(self.queue.finish(first["id"], token=claim["token"], result={"ok": True}), finished)
        self.assertEqual(len(self.queue.events()), 3)
        for token, result in ((claim["token"], {"ok": False}), ("wrong", {"ok": True})):
            with self.assertRaises(ExecutionError):
                self.queue.finish(first["id"], token=token, result=result)

    def test_checkpoint_renews_lease_and_preserves_json(self):
        self.submit()
        claimed = self.claim(lease_seconds=10)
        self.clock.advance(9)
        value = {"verified": "경로", "next": ["bounded step"], "external_effect": None}
        updated = self.queue.checkpoint(claimed["id"], token=claimed["token"], value=value,
                                        lease_seconds=20)
        self.assertEqual(updated["lease_until"], 1029)
        self.assertEqual(updated["checkpoint"], value)
        self.assertEqual(updated["attempt"], 1)
        self.clock.advance(2)
        self.assertEqual(self.queue.reconcile(), 0)
        self.assertEqual(JobQueue(self.root, clock=self.clock).get(claimed["id"])["checkpoint"], value)

    def test_wrong_token_cannot_change_owned_record(self):
        self.submit()
        claimed = self.claim()
        before = self.queue.events()
        for operation in (
            lambda: self.queue.checkpoint(claimed["id"], token="wrong", value={}),
            lambda: self.queue.finish(claimed["id"], token="wrong", result={}),
            lambda: self.queue.fail(claimed["id"], token="wrong", error="No"),
        ):
            with self.assertRaises(ExecutionError):
                operation()
        self.assertEqual(self.queue.get(claimed["id"]), claimed)
        self.assertEqual(self.queue.events(), before)

    def test_expiration_blocks_instead_of_automatically_reclaiming(self):
        self.submit(retry_safe=True, max_attempts=3)
        claimed = self.claim(lease_seconds=10)
        self.clock.advance(10)
        for operation in (
            lambda: self.queue.checkpoint(claimed["id"], token=claimed["token"], value={}),
            lambda: self.queue.finish(claimed["id"], token=claimed["token"], result={}),
            lambda: self.queue.fail(claimed["id"], token=claimed["token"], error="No"),
        ):
            with self.assertRaises(ExecutionError):
                operation()
        self.assertIsNone(self.claim())
        blocked = self.queue.get(claimed["id"])
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["attempt"], 1)
        self.assertEqual(self.queue.reconcile(), 0)
        self.assertEqual(sum(e["event"] == "lease_expired" for e in self.queue.events()), 1)

    def test_manual_retry_requires_reconciliation_and_fences_old_claim(self):
        self.submit(max_attempts=2)
        old = self.claim(lease_seconds=10)
        self.queue.checkpoint(old["id"], token=old["token"], value={"next": "resume"}, lease_seconds=10)
        self.clock.advance(10)
        self.queue.reconcile()
        for safe, reason in ((False, "checked"), (1, "checked"), (True, ""), (True, " "), (True, None)):
            with self.subTest(safe=safe, reason=reason), self.assertRaises(ExecutionError):
                self.queue.retry(old["id"], safe_to_repeat=safe, reason=reason)
        retried = self.queue.retry(old["id"], safe_to_repeat=True,
                                   reason="Previous worker stopped; destination confirms no write")
        self.assertEqual(retried["state"], "queued")
        self.assertIsNone(retried["token"])
        self.assertIsNone(retried["owner"])
        self.assertEqual(retried["checkpoint"], {"next": "resume"})
        current = self.claim()
        self.assertEqual(current["attempt"], 2)
        self.assertNotEqual(current["token"], old["token"])
        for operation in (
            lambda: self.queue.checkpoint(old["id"], token=old["token"], value="late"),
            lambda: self.queue.finish(old["id"], token=old["token"], result="late"),
            lambda: self.queue.fail(old["id"], token=old["token"], error="late"),
        ):
            with self.assertRaises(ExecutionError):
                operation()
        self.assertEqual(self.queue.get(current["id"]), current)
        self.assertEqual(self.queue.events()[-2]["data"],
                         {"reason": "Previous worker stopped; destination confirms no write"})

    def test_manual_retry_only_accepts_failed_or_blocked_and_honors_budget(self):
        job = self.submit(max_attempts=2)
        with self.assertRaises(ExecutionError):
            self.queue.retry(job["id"], safe_to_repeat=True, reason="checked")
        claimed = self.claim()
        with self.assertRaises(ExecutionError):
            self.queue.retry(job["id"], safe_to_repeat=True, reason="checked")
        failed = self.queue.fail(job["id"], token=claimed["token"], error="Known failure")
        self.assertEqual(failed["state"], "failed")
        self.queue.retry(job["id"], safe_to_repeat=True, reason="Safe read-only work")
        current = self.claim()
        self.queue.fail(job["id"], token=current["token"], error="Known failure")
        with self.assertRaisesRegex(ExecutionError, "budget|attempt"):
            self.queue.retry(job["id"], safe_to_repeat=True, reason="Try again")
        self.assertIsNone(self.claim())

    def test_automatic_retry_backoff_is_opt_in_and_bounded(self):
        job = self.submit(max_attempts=3, retry_safe=True)
        for attempt, delay in ((1, 10), (2, 20), (3, 40)):
            claimed = self.claim()
            self.assertEqual(claimed["attempt"], attempt)
            state = self.queue.fail(job["id"], token=claimed["token"],
                                    error="TransientError", retry_delay=delay)
            self.assertEqual(state["state"], "queued" if attempt < 3 else "failed")
            self.assertIsNone(self.claim())
            if attempt < 3:
                self.clock.advance(delay - 0.01)
                self.assertIsNone(self.claim())
                self.clock.advance(0.01)
        self.clock.advance(10000)
        self.assertIsNone(self.claim())
        self.assertEqual([e["event"] for e in self.queue.events()].count("retry_scheduled"), 2)

    def test_non_repeat_safe_failure_never_automatically_retries(self):
        job = self.submit(max_attempts=5)
        claim = self.claim()
        failed = self.queue.fail(job["id"], token=claim["token"], error="Effect uncertain")
        before = self.queue.events()
        self.clock.advance(10000)
        self.assertIsNone(self.claim())
        self.assertEqual(self.queue.fail(job["id"], token=claim["token"], error="Effect uncertain"), failed)
        self.assertEqual(self.queue.events(), before)
        with self.assertRaises(ExecutionError):
            self.queue.fail(job["id"], token=claim["token"], error="Different explanation")

    def test_retry_submission_replay_cannot_affect_new_attempt(self):
        job = self.submit(max_attempts=2, retry_safe=True)
        old = self.claim()
        queued = self.queue.fail(job["id"], token=old["token"], error="Transient", retry_delay=10)
        self.assertEqual(self.queue.fail(job["id"], token=old["token"], error="Transient", retry_delay=10), queued)
        self.clock.advance(10)
        current = self.claim()
        with self.assertRaises(ExecutionError):
            self.queue.fail(job["id"], token=old["token"], error="Transient", retry_delay=10)
        self.assertEqual(self.queue.get(job["id"]), current)

    def test_cancellation_is_durable_and_fences_running_worker(self):
        job = self.submit(max_attempts=3, retry_safe=True)
        running = self.claim()
        cancelled = self.queue.cancel(job["id"], reason="User changed goal")
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(self.queue.cancel(job["id"], reason="User changed goal"), cancelled)
        for operation in (
            lambda: self.queue.checkpoint(job["id"], token=running["token"], value={}),
            lambda: self.queue.finish(job["id"], token=running["token"], result={}),
            lambda: self.queue.fail(job["id"], token=running["token"], error="No"),
            lambda: self.queue.retry(job["id"], safe_to_repeat=True, reason="No"),
        ):
            with self.assertRaises(ExecutionError):
                operation()
        self.clock.advance(10000)
        self.assertEqual(self.queue.reconcile(), 0)
        self.assertIsNone(self.claim())
        self.assertEqual(JobQueue(self.root, clock=self.clock).get(job["id"]), cancelled)
        self.assertEqual(sum(e["event"] == "cancelled" for e in self.queue.events()), 1)

    def test_queued_cancellation_and_success_are_terminal(self):
        queued = self.submit()
        self.queue.cancel(queued["id"], reason="No longer needed")
        self.assertIsNone(self.claim())
        job = self.submit()
        running = self.claim()
        self.queue.finish(job["id"], token=running["token"], result=None)
        with self.assertRaises(ExecutionError):
            self.queue.cancel(job["id"], reason="Too late")
        with self.assertRaises(ExecutionError):
            self.queue.retry(job["id"], safe_to_repeat=True, reason="Too late")

    def test_linked_job_does_not_modify_schema2_core_fifo_lifo_or_ids(self):
        later = self.tasks.create_task(title="Later", goal="FIFO", todos=[])
        self.assertEqual(self.tasks.dispatch(), self.task_id)
        child = self.tasks.spawn(title="Prerequisite", goal="Required first")
        before = self.core_snapshot()
        job = self.submit(task_id=child, priority=999, payload={"output": "checked.txt"})
        claimed = self.claim()
        self.queue.checkpoint(job["id"], token=claimed["token"], value={"checked": True})
        self.queue.finish(job["id"], token=claimed["token"], result={"ok": True})
        self.assertEqual(self.core_snapshot(), before)
        state = yaml.safe_load((self.root / "state.yaml").read_text())
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["active"], child)
        self.assertEqual(state["stack"], [self.task_id])
        self.assertEqual(state["queue"], [later])
        self.assertEqual(self.store.validate_all(), [])
        self.assertTrue((self.root / "task/open" / f"{child:06d}.md").is_file())
        _, history_id, resumed = self.tasks.close(outcome="completed", summary="Coordinator verified")
        self.assertEqual(resumed, self.task_id)
        self.assertGreater(history_id, max(child, later))

    def test_link_rejects_missing_or_closed_task(self):
        with self.assertRaises(ExecutionError):
            self.submit(task_id=999999)
        self.tasks.close(task_id=self.task_id, outcome="completed", summary="Done")
        with self.assertRaises(ExecutionError):
            self.submit(task_id=self.task_id)
        self.assertEqual(self.queue.status(), [])
        self.assertEqual(self.queue.events(), [])

    def test_event_pagination_is_ordered_lossless_and_durable(self):
        for _ in range(8):
            job = self.submit()
            claimed = self.claim()
            self.queue.checkpoint(job["id"], token=claimed["token"], value={"verified": True})
            self.queue.finish(job["id"], token=claimed["token"], result="ok")
        expected = self.queue.events(limit=1000)
        collected, cursor = [], 0
        while True:
            page = JobQueue(self.root, clock=self.clock).events(after=cursor, limit=3)
            if not page:
                break
            # A crash before persisting this cursor is harmless: the same page replays.
            self.assertEqual(self.queue.events(after=cursor, limit=3), page)
            collected.extend(page)
            cursor = page[-1]["seq"]
        self.assertEqual(collected, expected)
        self.assertEqual(len(collected), 32)
        self.assertEqual([e["seq"] for e in collected], sorted({e["seq"] for e in collected}))
        new = self.submit()
        self.assertEqual([e["job_id"] for e in self.queue.events(after=cursor)], [new["id"]])

    def test_job_transition_and_event_rollback_together(self):
        with patch.object(self.queue, "_event", side_effect=RuntimeError("write interrupted")):
            with self.assertRaises(RuntimeError):
                self.submit()
        self.assertEqual(self.queue.status(), [])
        self.assertEqual(self.queue.events(), [])
        job = self.submit()
        claim = self.claim()
        before = self.queue.events()
        with patch.object(self.queue, "_event", side_effect=RuntimeError("write interrupted")):
            with self.assertRaises(RuntimeError):
                self.queue.finish(job["id"], token=claim["token"], result="must roll back")
        self.assertEqual(self.queue.get(job["id"]), claim)
        self.assertEqual(self.queue.events(), before)

    def test_trusted_handler_executes_real_work_and_checkpoints(self):
        artifact = self.workspace / "verified-result.txt"
        job = self.submit(kind="sum", payload={"numbers": [2, 3, 5]})
        before = self.core_snapshot()

        def handler(claimed, context):
            self.assertEqual(claimed["id"], job["id"])
            self.clock.advance(9)
            context.checkpoint({"verified": "input shape", "next": "sum"})
            self.clock.advance(9)
            answer = sum(claimed["payload"]["numbers"])
            artifact.write_text(str(answer), encoding="utf-8")
            context.checkpoint({"artifact": str(artifact), "verified": answer})
            return {"answer": answer, "artifact": str(artifact)}

        finished = work_once(self.queue, worker="trusted-host", handlers={"sum": handler}, lease_seconds=10)
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual(finished["result"]["answer"], 10)
        self.assertEqual(artifact.read_text(), "10")
        self.assertEqual(finished["checkpoint"]["verified"], 10)
        self.assertEqual(self.core_snapshot(), before)
        self.assertEqual([e["event"] for e in self.queue.events()],
                         ["submitted", "claimed", "checkpointed", "checkpointed", "succeeded"])

    def test_unknown_kinds_and_payloads_never_execute_code(self):
        marker = self.workspace / "must-not-exist"
        unknown = self.submit(kind="os.system", payload={"command": f"touch {marker}"}, priority=100)
        known = self.submit(kind="read", payload=f"__import__('pathlib').Path({str(marker)!r}).touch()")
        seen = []
        done = work_once(self.queue, worker="host", handlers={"read": lambda job, _: seen.append(job["payload"]) or "ok"})
        self.assertEqual(done["id"], known["id"])
        self.assertEqual(seen, [known["payload"]])
        self.assertEqual(self.queue.get(unknown["id"])["state"], "queued")
        self.assertIsNone(work_once(self.queue, worker="host", handlers={"read": lambda *_: self.fail("No known job")}))
        self.assertFalse(marker.exists())

    def test_handler_failure_records_classification_without_sensitive_message(self):
        job = self.submit(kind="read", retry_safe=True, max_attempts=2)

        def handler(*_):
            raise ValueError("private-message-must-not-be-recorded")

        failed = work_once(self.queue, worker="host", handlers={"read": handler})
        self.assertEqual(failed["state"], "queued")
        self.assertEqual(failed["error"], "ValueError")
        self.assertNotIn("private-message-must-not-be-recorded", json.dumps(self.queue.status()))
        self.assertNotIn("private-message-must-not-be-recorded", json.dumps(self.queue.events()))
        self.clock.advance(30)
        terminal = work_once(self.queue, worker="host", handlers={"read": handler})
        self.assertEqual(terminal["state"], "failed")
        self.assertEqual(terminal["attempt"], 2)
        self.assertEqual(terminal["id"], job["id"])

    def test_handler_cancellation_prevents_result_commit(self):
        job = self.submit(kind="read")

        def handler(claimed, _):
            self.queue.cancel(claimed["id"], reason="Host cancelled")
            return "late result"

        with self.assertRaises(ExecutionError):
            work_once(self.queue, worker="host", handlers={"read": handler})
        self.assertEqual(self.queue.get(job["id"])["state"], "cancelled")
        self.assertIsNone(self.queue.get(job["id"])["result"])

    def test_bad_submission_fields_leave_no_records(self):
        bad = [
            {"request_key": ""}, {"request_key": 1}, {"title": " "}, {"kind": None},
            {"priority": True}, {"priority": 1.5}, {"max_attempts": 0}, {"max_attempts": 101},
            {"max_attempts": False}, {"retry_safe": 1}, {"task_id": -1}, {"task_id": True},
            {"task_id": "0"}, {"available_at": float("nan")}, {"available_at": float("inf")},
            {"available_at": 0}, {"payload": {"x": float("nan")}}, {"payload": object()},
        ]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises((ExecutionError, ValueError, TypeError)):
                self.submit(**fields)
        self.assertEqual(self.queue.status(), [])
        self.assertEqual(self.queue.events(), [])

    def test_bad_claim_clock_cursor_and_handler_inputs(self):
        self.submit()
        for args in ({"worker": ""}, {"worker": 1}, {"lease_seconds": 0},
                     {"lease_seconds": True}, {"lease_seconds": float("nan")},
                     {"lease_seconds": float("inf")}, {"kinds": "task"},
                     {"kinds": []}, {"kinds": [1]}, {"kinds": [""]}):
            with self.subTest(args=args), self.assertRaises(ExecutionError):
                self.queue.claim(**dict({"worker": "host"}, **args))
        for args in ({"after": -1}, {"after": True}, {"after": 0.5}, {"limit": 0},
                     {"limit": 1001}, {"limit": True}):
            with self.subTest(args=args), self.assertRaises(ExecutionError):
                self.queue.events(**args)
        for handlers in ({}, {"task": "os.system"}, {1: lambda *_: None}):
            with self.subTest(handlers=handlers), self.assertRaises(ExecutionError):
                work_once(self.queue, worker="host", handlers=handlers)
        for bad in (float("nan"), float("inf"), True, "now"):
            with self.subTest(clock=bad), self.assertRaises(ExecutionError):
                JobQueue(self.root, clock=lambda: bad).claim(worker="host")
        self.assertEqual(self.queue.status()[0]["state"], "queued")
        self.assertEqual(len(self.queue.events()), 1)

    def test_invalid_mutations_are_atomic(self):
        job = self.submit()
        claimed = self.claim()
        for operation in (
            lambda: self.queue.checkpoint(job["id"], token=claimed["token"], value={"bad": float("inf")}),
            lambda: self.queue.checkpoint(job["id"], token=claimed["token"], value={}, lease_seconds=-1),
            lambda: self.queue.finish(job["id"], token=claimed["token"], result={"bad": float("nan")}),
            lambda: self.queue.fail(job["id"], token=claimed["token"], error=" "),
            lambda: self.queue.fail(job["id"], token=claimed["token"], error="No", retry_delay=0),
            lambda: self.queue.cancel(job["id"], reason=""),
        ):
            with self.assertRaises((ExecutionError, ValueError, TypeError)):
                operation()
        self.assertEqual(self.queue.get(job["id"]), claimed)
        self.assertEqual(len(self.queue.events()), 2)

    def test_unknown_job_references_do_not_create_records(self):
        for operation in (
            lambda: self.queue.get("j-missing"),
            lambda: self.queue.checkpoint("j-missing", token="x", value={}),
            lambda: self.queue.finish("j-missing", token="x", result={}),
            lambda: self.queue.fail("j-missing", token="x", error="No"),
            lambda: self.queue.cancel("j-missing", reason="No"),
            lambda: self.queue.retry("j-missing", safe_to_repeat=True, reason="No"),
        ):
            with self.assertRaises(ExecutionError):
                operation()
        self.assertEqual(self.queue.status(), [])
        self.assertEqual(self.queue.events(), [])

    def test_sql_metacharacters_are_literal(self):
        value = "x'); DROP TABLE jobs; --"
        job = self.submit(request_key=value, title=value, kind=value, payload={"value": value})
        claimed = self.queue.claim(worker=value, kinds=[value])
        self.assertEqual(claimed["id"], job["id"])
        self.queue.finish(job["id"], token=claimed["token"], result=value)
        self.assertEqual(self.queue.status()[0]["result"], value)
        self.assertEqual(self.queue.events()[1]["data"]["worker"], value)

    def test_initialization_rejects_absent_legacy_and_future_runtime(self):
        root = self.workspace / "invalid"
        with self.assertRaises(ExecutionError):
            JobQueue(root)
        self.assertFalse(root.exists())
        root.mkdir()
        for state in ({"schema_version": 1}, {"schema_version": 3}, [], None):
            (root / "state.yaml").write_text(yaml.safe_dump(state))
            with self.subTest(state=state), self.assertRaises(ExecutionError):
                JobQueue(root)
            self.assertFalse((root / "execution").exists())

    def test_future_execution_schema_is_rejected_without_overwriting_data(self):
        with sqlite3.connect(self.queue.path) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ExecutionError, "Unsupported"):
            JobQueue(self.root)
        with sqlite3.connect(self.queue.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_cli_retry_cancel_reconcile_and_events(self):
        job = self.cli("submit", "--request-key", "cli", "--title", "CLI",
                       "--task-id", str(self.task_id), "--max-attempts", "2")
        claim = self.cli("claim", "--worker", "cli-host", "--kind", "task")
        failed = self.cli("fail", job["id"], "--token", claim["token"], "--error", "Checked failure")
        self.assertEqual(failed["state"], "failed")
        self.cli("retry", job["id"], "--reason", "not confirmed", success=False)
        self.cli("retry", job["id"], "--safe-to-repeat", "--reason", "Destination inspected")
        self.assertEqual(self.cli("cancel", job["id"], "--reason", "Goal changed")["state"], "cancelled")
        self.assertEqual(self.cli("reconcile"), 0)
        first = self.cli("events", "--limit", "2")
        rest = self.cli("events", "--after", str(first[-1]["seq"]))
        self.assertEqual([e["event"] for e in first + rest],
                         ["submitted", "claimed", "failed", "retry_authorized", "cancelled"])

    def test_cli_malformed_inputs_are_reported_without_tracebacks(self):
        for args in (
            ("submit", "--request-key", "bad", "--title", "Bad", "--payload", "{invalid"),
            ("submit", "--request-key", "bad", "--title", "Bad", "--payload", "NaN"),
            ("submit", "--request-key", "bad", "--title", "Bad", "--available-at", "inf"),
            ("claim", "--worker", "host", "--lease-seconds", "0"),
            ("events", "--after", "-1"), ("get", "j-missing"),
        ):
            with self.subTest(args=args):
                self.cli(*args, success=False)
        self.assertEqual(self.queue.status(), [])

    def test_cli_malformed_yaml_is_reported_without_traceback(self):
        root = self.workspace / "malformed"
        root.mkdir()
        (root / "state.yaml").write_text("schema_version: [unclosed")
        self.cli("status", success=False, root=root)
        self.assertFalse((root / "execution").exists())

    def test_cli_integer_overflow_is_reported_without_traceback(self):
        self.cli("submit", "--request-key", "overflow", "--title", "Overflow",
                 "--priority", str(2 ** 80), success=False)
        self.assertEqual(self.queue.status(), [])

    def test_block_is_owned_idempotent_and_not_automatically_retried(self):
        job = self.submit(max_attempts=3, retry_safe=True)
        claimed = self.claim()
        self.queue.checkpoint(job["id"], token=claimed["token"], value={"need": "confirmation"})
        for token, reason in (("wrong", "Waiting"), (claimed["token"], "")):
            with self.assertRaises(ExecutionError):
                self.queue.block(job["id"], token=token, reason=reason)
        blocked = self.queue.block(job["id"], token=claimed["token"], reason="Awaiting actual outcome")
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["checkpoint"], {"need": "confirmation"})
        self.clock.advance(10000)
        self.assertEqual(self.queue.block(job["id"], token=claimed["token"], reason="Awaiting actual outcome"), blocked)
        self.assertIsNone(self.claim())
        with self.assertRaises(ExecutionError):
            self.queue.block(job["id"], token=claimed["token"], reason="Changed explanation")
        with self.assertRaises(ExecutionError):
            self.queue.finish(job["id"], token=claimed["token"], result="late")
        self.assertEqual(sum(e["event"] == "blocked" for e in self.queue.events()), 1)

    def test_resolve_verifies_blocked_success_without_reexecuting(self):
        job = self.submit()
        claimed = self.claim(lease_seconds=10)
        with self.assertRaises(ExecutionError):
            self.queue.resolve(job["id"], result="unverified", reason="Still running")
        self.clock.advance(10)
        finished = self.queue.resolve(job["id"], result={"receipt": "verified-test-receipt"},
                                      reason="Coordinator checked actual destination")
        self.assertEqual(finished["state"], "succeeded")
        self.assertIsNone(finished["token"])
        self.assertEqual(finished["attempt"], 1)
        before = self.queue.events()
        self.assertEqual(self.queue.resolve(job["id"], result={"receipt": "verified-test-receipt"},
                                            reason="Coordinator checked actual destination"), finished)
        self.assertEqual(self.queue.events(), before)
        self.assertEqual([e["event"] for e in before], ["submitted", "claimed", "lease_expired", "resolved"])
        self.assertEqual(before[-1]["data"], {"reason": "Coordinator checked actual destination"})
        with self.assertRaises(ExecutionError):
            self.queue.resolve(job["id"], result="conflicting", reason="Other claim")
        with self.assertRaises(ExecutionError):
            self.queue.finish(job["id"], token=claimed["token"], result={"receipt": "verified-test-receipt"})
        self.assertIsNone(self.claim())

    def test_resolve_rejects_unblocked_jobs_and_missing_evidence(self):
        for state in ("queued", "running", "failed", "cancelled", "succeeded"):
            with self.subTest(state=state):
                job = self.submit()
                if state in ("running", "failed", "succeeded"):
                    claimed = self.claim()
                    if state == "failed":
                        self.queue.fail(job["id"], token=claimed["token"], error="Failure")
                    elif state == "succeeded":
                        self.queue.finish(job["id"], token=claimed["token"], result="accepted")
                elif state == "cancelled":
                    self.queue.cancel(job["id"], reason="Stopped")
                before = self.queue.get(job["id"])
                with self.assertRaises(ExecutionError):
                    self.queue.resolve(job["id"], result="replacement", reason="Evidence")
                self.assertEqual(self.queue.get(job["id"]), before)
                if state in ("queued", "running", "failed"):
                    self.queue.cancel(job["id"], reason="End test scenario")
        job = self.submit()
        claimed = self.claim()
        self.queue.block(job["id"], token=claimed["token"], reason="Waiting")
        for reason in ("", " ", None, 1):
            with self.subTest(reason=reason), self.assertRaises(ExecutionError):
                self.queue.resolve(job["id"], result="unchecked", reason=reason)
        self.assertEqual(self.queue.get(job["id"])["state"], "blocked")

    def test_handler_block_returns_durably_and_next_job_can_run(self):
        waiting = self.submit(kind="needs-input")
        independent = self.submit(kind="read")
        called = []

        def wait_for_input(job, context):
            called.append(job["id"])
            context.checkpoint({"verified": "partial work", "need": "user decision"})
            context.block("Need approval before next action")
            self.fail("Blocking must stop the handler")

        handlers = {"needs-input": wait_for_input, "read": lambda *_: "independent result"}
        blocked = work_once(self.queue, worker="host", handlers=handlers)
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["id"], waiting["id"])
        done = work_once(self.queue, worker="host", handlers=handlers)
        self.assertEqual(done["state"], "succeeded")
        self.assertEqual(done["id"], independent["id"])
        self.assertEqual(called, [waiting["id"]])
        self.assertIsNone(work_once(self.queue, worker="host", handlers=handlers))
        waiting_events = [e["event"] for e in self.queue.events() if e["job_id"] == waiting["id"]]
        self.assertEqual(waiting_events, ["submitted", "claimed", "checkpointed", "blocked"])
        self.assertEqual(self.queue.get(waiting["id"])["checkpoint"]["need"], "user decision")

    def test_doctor_is_read_only_and_reports_expired_leases(self):
        self.submit()
        claimed = self.claim(lease_seconds=10)
        self.clock.advance(10)
        events = self.queue.events()
        doctor = self.queue.doctor()
        self.assertEqual(doctor, {"package_version": PACKAGE_VERSION, "execution_schema": EXECUTION_SCHEMA,
                                  "task_schema": 2, "integrity": "ok", "worker_started": False,
                                  "expired_running": 1})
        self.assertEqual(self.queue.get(claimed["id"])["state"], "running")
        self.assertEqual(self.queue.events(), events)
        self.queue.reconcile()
        self.assertEqual(self.queue.doctor()["expired_running"], 0)
        manifest = json.loads((SCRIPTS.parents[2] / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(PACKAGE_VERSION, manifest["version"])

    def test_doctor_rejects_corrupt_state_attempt_or_ownership(self):
        job = self.submit(max_attempts=2)
        cases = ["state='unknown'", "attempt=-1", "attempt=3",
                 "state='running',owner=NULL,token=NULL,lease_until=NULL"]
        for change in cases:
            with self.subTest(change=change):
                with sqlite3.connect(self.queue.path) as db:
                    db.execute("UPDATE jobs SET state='queued',attempt=0,owner=NULL,token=NULL,lease_until=NULL")
                    db.execute("UPDATE jobs SET " + change + " WHERE id=?", (job["id"],))
                with self.assertRaisesRegex(ExecutionError, "invariant"):
                    self.queue.doctor()

    def test_cli_version_needs_no_workspace_and_other_commands_do(self):
        root = self.workspace / "never-created"
        result = subprocess.run([sys.executable, str(SCRIPTS / "execctl.py"), "version"],
                                text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"package_version": PACKAGE_VERSION,
                         "execution_schema": EXECUTION_SCHEMA, "task_schema": 2})
        result = subprocess.run([sys.executable, str(SCRIPTS / "execctl.py"), "status"],
                                text=True, capture_output=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--root is required", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(root.exists())

    def test_cli_block_resolve_doctor_flow(self):
        job = self.cli("submit", "--request-key", "block-cli", "--title", "CLI wait",
                       "--task-id", str(self.task_id))
        claimed = self.cli("claim", "--worker", "host")
        self.assertEqual(self.cli("block", job["id"], "--token", claimed["token"],
                                 "--reason", "Outcome uncertain")["state"], "blocked")
        finished = self.cli("resolve", job["id"], "--result", '{"verified":true}',
                            "--reason", "Destination verified")
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual(self.cli("doctor")["integrity"], "ok")
        self.assertFalse(self.cli("doctor")["worker_started"])

    def test_new_execution_storage_is_private_under_normal_umask(self):
        root = self.workspace / "private-storage"
        WorkspaceStore(root).initialize()
        previous = os.umask(0o022)
        try:
            queue = JobQueue(root)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(queue.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(queue.path.stat().st_mode), 0o600)
        self.assertEqual(queue.path.stat().st_uid, os.getuid())
        self.assertEqual(queue.path.parent.stat().st_uid, os.getuid())
        self.assertEqual(queue.doctor()["integrity"], "ok")

    def test_broad_existing_permissions_are_rejected_without_chmod(self):
        for target, mode in ((self.queue.path, 0o644), (self.queue.path.parent, 0o755)):
            with self.subTest(target=target):
                previous = stat.S_IMODE(target.stat().st_mode)
                before = self.queue.path.read_bytes()
                target.chmod(mode)
                try:
                    with self.assertRaisesRegex(ExecutionError, "private"):
                        JobQueue(self.root)
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
                    self.assertEqual(self.queue.path.read_bytes(), before)
                finally:
                    target.chmod(previous)

    def test_execution_storage_rejects_symlinks_and_nonregular_database(self):
        for kind in ("directory-link", "database-link", "database-directory"):
            with self.subTest(kind=kind):
                root = self.workspace / kind
                WorkspaceStore(root).initialize()
                execution = root / "execution"
                if kind == "directory-link":
                    execution.symlink_to(self.queue.path.parent, target_is_directory=True)
                else:
                    execution.mkdir(mode=0o700)
                    if kind == "database-link":
                        (execution / "jobs.sqlite3").symlink_to(self.queue.path)
                    else:
                        (execution / "jobs.sqlite3").mkdir(mode=0o700)
                before = self.queue.path.read_bytes()
                with self.assertRaises(ExecutionError):
                    JobQueue(root)
                self.assertEqual(self.queue.path.read_bytes(), before)

    def run_after_lock_wait(self, operation):
        """Advance time only after a mutation starts, before it acquires its lock."""
        reached_transaction = threading.Event()
        original = self.queue._transaction

        @contextmanager
        def gated_transaction():
            reached_transaction.set()
            with original() as db:
                yield db

        connection = sqlite3.connect(self.queue.path, isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        try:
            with patch.object(self.queue, "_transaction", gated_transaction):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(operation)
                    try:
                        self.assertTrue(reached_transaction.wait(timeout=5), "Mutation never reached transaction")
                        self.clock.advance(20)
                    finally:
                        connection.execute("COMMIT")
                    return result.result(timeout=5)
        finally:
            connection.close()

    def test_lease_fence_checks_time_after_waiting_for_database_lock(self):
        for name in ("finish", "checkpoint", "fail", "block"):
            with self.subTest(operation=name):
                self.submit()
                claimed = self.claim(lease_seconds=10)
                actions = {
                    "finish": lambda: self.queue.finish(claimed["id"], token=claimed["token"], result="late"),
                    "checkpoint": lambda: self.queue.checkpoint(claimed["id"], token=claimed["token"], value="late"),
                    "fail": lambda: self.queue.fail(claimed["id"], token=claimed["token"], error="late"),
                    "block": lambda: self.queue.block(claimed["id"], token=claimed["token"], reason="late"),
                }
                with self.assertRaises(ExecutionError):
                    self.run_after_lock_wait(actions[name])
                self.queue.reconcile()
                self.assertEqual(self.queue.get(claimed["id"])["state"], "blocked")

    def test_claim_lease_starts_after_database_lock_is_acquired(self):
        self.submit()
        claimed = self.run_after_lock_wait(lambda: self.claim(lease_seconds=10))
        self.assertEqual(claimed["lease_until"], self.clock.now + 10)
        self.assertGreater(claimed["lease_until"], self.clock.now)


if __name__ == "__main__":
    unittest.main()
