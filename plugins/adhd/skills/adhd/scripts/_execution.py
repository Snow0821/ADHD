"""Opt-in durable jobs for a host-owned executor; never launches a worker."""
from __future__ import annotations

import json
import math
import os
import stat
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Mapping

import yaml


PACKAGE_VERSION = "0.6.0"
EXECUTION_SCHEMA = 1


class ExecutionError(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _positive(value, label):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ExecutionError(f"{label} must be a finite positive number")


class JobQueue:
    """Each mutation and its event commit together; old task state is untouched."""

    def __init__(self, root, *, clock: Callable[[], float] = time.time):
        self.root = Path(root).resolve()
        self.clock = clock
        state_path = self.root / "state.yaml"
        if not state_path.is_file():
            raise ExecutionError("Initialize the established ADHD runtime before enabling jobs")
        state = yaml.safe_load(state_path.read_text())
        if not isinstance(state, dict) or state.get("schema_version") != 2:
            raise ExecutionError("Jobs require a validated schema-2 ADHD runtime")
        self.path = self.root / "execution" / "jobs.sqlite3"
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        directory = self.path.parent.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
                or stat.S_IMODE(directory.st_mode) & 0o077):
            raise ExecutionError("Execution directory must be owned by this user and private (0700)")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        database = self.path.lstat()
        if (not stat.S_ISREG(database.st_mode) or database.st_uid != os.getuid()
                or stat.S_IMODE(database.st_mode) & 0o077):
            raise ExecutionError("Execution database must be owned by this user and private (0600)")
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ExecutionError(f"Unsupported execution schema: {version}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                  request_key TEXT UNIQUE NOT NULL, request_json TEXT NOT NULL,
                  task_id INTEGER, title TEXT NOT NULL, kind TEXT NOT NULL,
                  payload TEXT NOT NULL, priority INTEGER NOT NULL,
                  state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL, retry_safe INTEGER NOT NULL,
                  available_at REAL NOT NULL, owner TEXT, token TEXT, lease_until REAL,
                  checkpoint TEXT, result TEXT, error TEXT, created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, available_at, priority, seq);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                  event TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _now(self):
        now = self.clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise ExecutionError("Invalid host clock")
        return float(now)

    def _event(self, db, job_id, event, now, data=None):
        db.execute("INSERT INTO events(job_id,event,at,data) VALUES(?,?,?,?)", (job_id, event, now, _json(data or {})))

    def _row(self, db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ExecutionError(f"Unknown job: {job_id}")
        return row

    @staticmethod
    def _public(row):
        result = dict(row)
        for key in ("payload", "checkpoint", "result"):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        result.pop("request_json", None)
        result["retry_safe"] = bool(result["retry_safe"])
        return result

    def submit(self, *, request_key, title, kind="task", payload=None, task_id=None,
               priority=0, max_attempts=1, retry_safe=False, available_at=None):
        for label, value in (("request_key", request_key), ("title", title), ("kind", kind)):
            if not isinstance(value, str) or not value.strip():
                raise ExecutionError(f"{label} must be nonempty")
        for label, value in (("priority", priority), ("max_attempts", max_attempts)):
            if type(value) is not int:
                raise ExecutionError(f"{label} must be an integer")
        if not -(2**31) <= priority < 2**31:
            raise ExecutionError("priority must be a signed 32-bit integer")
        if not 1 <= max_attempts <= 100:
            raise ExecutionError("max_attempts must be between 1 and 100")
        if type(retry_safe) is not bool:
            raise ExecutionError("retry_safe must be boolean")
        if task_id is not None:
            if type(task_id) is not int or not 0 <= task_id < 2**63:
                raise ExecutionError("task_id must be a non-negative integer")
        if available_at is not None:
            _positive(available_at, "available_at")
        request = dict(title=title, kind=kind, payload=payload, task_id=task_id,
                       priority=priority, max_attempts=max_attempts, retry_safe=retry_safe,
                       available_at=available_at)
        encoded = _json(request)
        with self._transaction() as db:
            now = self._now()
            existing = db.execute("SELECT * FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                if existing["request_json"] != encoded:
                    raise ExecutionError("Idempotency key already belongs to a different request")
                return self._public(existing)
            if task_id is not None and not (self.root / "task/open" / f"{task_id:06}.md").is_file():
                raise ExecutionError("Linked task must exist and be open")
            job_id = "j-" + str(uuid.uuid4())
            db.execute("""INSERT INTO jobs(id,request_key,request_json,task_id,title,kind,payload,
                priority,state,max_attempts,retry_safe,available_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,'queued',?,?,?,?,?)""",
                (job_id, request_key, encoded, task_id, title, kind, _json(payload), priority,
                 max_attempts, int(retry_safe), now if available_at is None else available_at, now, now))
            self._event(db, job_id, "submitted", now)
            return self._public(self._row(db, job_id))

    def _expire(self, db, now):
        expired = db.execute("SELECT id FROM jobs WHERE state='running' AND lease_until<=?", (now,)).fetchall()
        for row in expired:
            db.execute("UPDATE jobs SET state='blocked',error='Lease expired; reconcile external effects before retry',updated_at=? WHERE id=?", (now, row["id"]))
            self._event(db, row["id"], "lease_expired", now)
        return len(expired)

    def reconcile(self):
        with self._transaction() as db:
            return self._expire(db, self._now())

    def claim(self, *, worker, lease_seconds=300, kinds=None):
        if not isinstance(worker, str) or not worker.strip():
            raise ExecutionError("worker must be nonempty")
        _positive(lease_seconds, "lease_seconds")
        if kinds is not None and (not isinstance(kinds, (list, tuple)) or not kinds or any(not isinstance(k, str) or not k for k in kinds)):
            raise ExecutionError("kinds must be a nonempty list")
        with self._transaction() as db:
            now = self._now()
            self._expire(db, now)
            sql = "SELECT * FROM jobs WHERE state='queued' AND available_at<=? AND attempt<max_attempts"
            args = [now]
            if kinds:
                sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
                args.extend(kinds)
            row = db.execute(sql + " ORDER BY priority DESC,seq ASC LIMIT 1", args).fetchone()
            if row is None:
                return None
            token = str(uuid.uuid4())
            db.execute("UPDATE jobs SET state='running',attempt=attempt+1,owner=?,token=?,lease_until=?,updated_at=? WHERE id=?",
                       (worker, token, now + lease_seconds, now, row["id"]))
            self._event(db, row["id"], "claimed", now, {"worker": worker, "attempt": row["attempt"] + 1})
            return self._public(self._row(db, row["id"]))

    def _owned(self, db, job_id, token, now):
        row = self._row(db, job_id)
        if not token or row["token"] != token or row["state"] != "running" or row["lease_until"] <= now:
            raise ExecutionError("Lease is expired or no longer owned; reconcile instead of committing")
        return row

    def checkpoint(self, job_id, *, token, value, lease_seconds=300):
        _positive(lease_seconds, "lease_seconds")
        encoded = _json(value)
        with self._transaction() as db:
            now = self._now()
            self._owned(db, job_id, token, now)
            db.execute("UPDATE jobs SET checkpoint=?,lease_until=?,updated_at=? WHERE id=?", (encoded, now + lease_seconds, now, job_id))
            self._event(db, job_id, "checkpointed", now)
            return self._public(self._row(db, job_id))

    def finish(self, job_id, *, token, result):
        encoded = _json(result)
        with self._transaction() as db:
            now = self._now()
            row = self._row(db, job_id)
            if row["state"] == "succeeded" and row["token"] == token and row["result"] == encoded:
                return self._public(row)
            self._owned(db, job_id, token, now)
            db.execute("UPDATE jobs SET state='succeeded',result=?,error=NULL,updated_at=? WHERE id=?", (encoded, now, job_id))
            self._event(db, job_id, "succeeded", now)
            return self._public(self._row(db, job_id))

    def fail(self, job_id, *, token, error, retry_delay=30):
        _positive(retry_delay, "retry_delay")
        if not isinstance(error, str) or not error.strip():
            raise ExecutionError("error must be nonempty")
        with self._transaction() as db:
            now = self._now()
            row = self._row(db, job_id)
            if row["state"] in ("queued", "failed") and row["token"] == token and row["error"] == error:
                return self._public(row)
            row = self._owned(db, job_id, token, now)
            state = "queued" if row["retry_safe"] and row["attempt"] < row["max_attempts"] else "failed"
            db.execute("UPDATE jobs SET state=?,error=?,available_at=?,updated_at=? WHERE id=?", (state, error, now + retry_delay, now, job_id))
            self._event(db, job_id, "retry_scheduled" if state == "queued" else "failed", now)
            return self._public(self._row(db, job_id))

    def block(self, job_id, *, token, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ExecutionError("Blocking requires a reason")
        with self._transaction() as db:
            now = self._now()
            row = self._row(db, job_id)
            if row["state"] == "blocked" and row["token"] == token and row["error"] == reason:
                return self._public(row)
            self._owned(db, job_id, token, now)
            db.execute("UPDATE jobs SET state='blocked',error=?,updated_at=? WHERE id=?", (reason, now, job_id))
            self._event(db, job_id, "blocked", now, {"reason": reason})
            return self._public(self._row(db, job_id))

    def resolve(self, job_id, *, result, reason):
        """Coordinator confirms an uncertain external success without replaying it."""
        if not isinstance(reason, str) or not reason.strip():
            raise ExecutionError("Resolution requires verification evidence")
        encoded = _json(result)
        with self._transaction() as db:
            now = self._now()
            self._expire(db, now)
            row = self._row(db, job_id)
            if row["state"] == "succeeded" and row["token"] is None and row["result"] == encoded:
                return self._public(row)
            if row["state"] != "blocked":
                raise ExecutionError("Resolve only blocked jobs after checking the actual outcome")
            db.execute("UPDATE jobs SET state='succeeded',result=?,error=NULL,token=NULL,updated_at=? WHERE id=?", (encoded, now, job_id))
            self._event(db, job_id, "resolved", now, {"reason": reason})
            return self._public(self._row(db, job_id))

    def retry(self, job_id, *, safe_to_repeat, reason):
        if safe_to_repeat is not True or not isinstance(reason, str) or not reason.strip():
            raise ExecutionError("Retry requires explicit safe-to-repeat confirmation and reconciliation reason")
        with self._transaction() as db:
            now = self._now()
            self._expire(db, now)
            row = self._row(db, job_id)
            if row["state"] not in ("blocked", "failed"):
                raise ExecutionError("Only blocked or failed jobs can be retried")
            if row["attempt"] >= row["max_attempts"]:
                raise ExecutionError("Attempt budget exhausted; preserve the job for a coordinator decision")
            db.execute("UPDATE jobs SET state='queued',owner=NULL,token=NULL,lease_until=NULL,available_at=?,updated_at=? WHERE id=?", (now, now, job_id))
            self._event(db, job_id, "retry_authorized", now, {"reason": reason})
            return self._public(self._row(db, job_id))

    def cancel(self, job_id, *, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ExecutionError("Cancellation requires a reason")
        with self._transaction() as db:
            row = self._row(db, job_id)
            if row["state"] == "cancelled":
                return self._public(row)
            if row["state"] == "succeeded":
                raise ExecutionError("Succeeded jobs cannot be cancelled")
            now = self._now()
            db.execute("UPDATE jobs SET state='cancelled',updated_at=? WHERE id=?", (now, job_id))
            self._event(db, job_id, "cancelled", now, {"reason": reason})
            return self._public(self._row(db, job_id))

    def doctor(self):
        with self._connection() as db:
            checks = [row[0] for row in db.execute("PRAGMA quick_check")]
            if checks != ["ok"]:
                raise ExecutionError("SQLite integrity check failed; restore a verified backup")
            bad = db.execute("""SELECT COUNT(*) FROM jobs WHERE
                state NOT IN ('queued','running','succeeded','failed','blocked','cancelled')
                OR attempt < 0 OR attempt > max_attempts
                OR (state='running' AND (owner IS NULL OR token IS NULL OR lease_until IS NULL))""").fetchone()[0]
            if bad:
                raise ExecutionError("Execution record invariant failure; inspect before resuming")
            return {"package_version": PACKAGE_VERSION, "execution_schema": EXECUTION_SCHEMA,
                    "task_schema": 2, "integrity": "ok", "worker_started": False,
                    "expired_running": db.execute("SELECT COUNT(*) FROM jobs WHERE state='running' AND lease_until<=?", (self._now(),)).fetchone()[0]}

    def get(self, job_id):
        with self._connection() as db:
            return self._public(self._row(db, job_id))

    def status(self):
        with self._connection() as db:
            return [self._public(row) for row in db.execute("SELECT * FROM jobs ORDER BY seq")]

    def events(self, *, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ExecutionError("Use a nonnegative event cursor and limit 1..1000")
        with self._connection() as db:
            rows = db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit))
            result = [dict(row) for row in rows]
            for row in result:
                row["data"] = json.loads(row["data"])
            return result


class _WorkBlocked(Exception):
    """Internal stop signal after a handler durably blocks its own claim."""


class WorkContext:
    """A trusted handler checkpoints/renews between bounded units of work."""
    def __init__(self, queue, job, lease_seconds):
        self.queue, self.job, self.lease_seconds = queue, job, lease_seconds

    def checkpoint(self, value):
        self.job = self.queue.checkpoint(self.job["id"], token=self.job["token"], value=value,
                                         lease_seconds=self.lease_seconds)

    def block(self, reason):
        self.job = self.queue.block(self.job["id"], token=self.job["token"], reason=reason)
        raise _WorkBlocked()


def work_once(queue: JobQueue, *, worker: str, handlers: Mapping[str, Callable], lease_seconds=300):
    """Run one registered trusted handler in the CALLING host worker, not in chat.

    No shell/import strings from jobs are executed. The host supplies permissions,
    scheduling, independent worker process, cancellation, and timely renewal.
    """
    if not handlers or any(not isinstance(k, str) or not callable(v) for k, v in handlers.items()):
        raise ExecutionError("Supply trusted kind-to-handler callables")
    job = queue.claim(worker=worker, lease_seconds=lease_seconds, kinds=list(handlers))
    if job is None:
        return None
    context = WorkContext(queue, job, lease_seconds)
    try:
        result = handlers[job["kind"]](job, context)
    except _WorkBlocked:
        return context.job
    except Exception as exc:
        # Store a bounded exception classification, not possibly sensitive input.
        return queue.fail(job["id"], token=job["token"], error=type(exc).__name__)
    return queue.finish(job["id"], token=job["token"], result=result)
