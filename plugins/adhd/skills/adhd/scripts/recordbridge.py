#!/usr/bin/env python3
"""Sync explicit project records through a host-supplied authenticated tool caller.

The JSON-lines mode requests tools over stdout and receives results over stdin.
It never opens a network connection, loads credentials, or reads core ADHD stores.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import math
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

KINDS = {"project", "task", "decision", "document", "event"}
TOOLS = {"search_records", "read_record", "create_record", "update_record"}
RESERVED = {"bridge_key", "bridge_operation"}


class BridgeError(RuntimeError):
    """Stop without retrying an uncertain write or changing another record."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BridgeError(message)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def validate_body(body: Any) -> dict:
    require(type(body) is dict, "body must be an object")
    for key, value in body.items():
        require(type(key) is str and 0 < len(key) <= 200, "Invalid body key")
        require(value is None or type(value) in (str, int, float, bool), "Body values must be JSON scalars")
        if type(value) is str:
            require(utf16_length(value) <= 6000, "Body string exceeds the remote limit of 6000 characters")
        if type(value) in (int, float):
            require(abs(value) <= 9007199254740991 and math.isfinite(value), "Number is not safely representable by the connector")
    source = body.get("source")
    if type(source) is str and re.match(r"^\w+:", source, flags=re.ASCII):
        require(source.startswith(("https://", "http://")), "Source URLs must use http or https")
    return body


def validate_record(record: Any) -> dict:
    require(type(record) is dict, "Connector did not return a record")
    require(type(record.get("id")) is str and 0 < len(record["id"]) <= 100, "Invalid record ID")
    require(type(record.get("owner")) is str and bool(record["owner"]), "Missing owner-scoped record identity")
    require(type(record.get("kind")) is str and record["kind"] in KINDS, "Invalid record kind")
    require(type(record.get("revision")) is int and record["revision"] > 0, "Invalid record revision")
    require(type(record.get("title")) is str and 0 < utf16_length(record["title"]) <= 200, "Invalid record title")
    validate_body(record.get("body"))
    return record


def unwrap(result: Any) -> Any:
    """Accept native results or the existing MCP text envelope, never fake success."""
    if type(result) is dict and "content" in result:
        require(not result.get("isError"), "Connector rejected the request; inspect the retained host response")
        blocks = result.get("content")
        require(type(blocks) is list and len(blocks) == 1 and type(blocks[0]) is dict and blocks[0].get("type") == "text", "Expected one JSON text result")
        try:
            return json.loads(blocks[0]["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BridgeError("Connector result was not JSON") from exc
    require(not (type(result) is dict and (result.get("isError") or "error" in result)), "Connector returned an error")
    return result


def validate_spec(raw: Any) -> dict:
    require(type(raw) is dict and set(raw) <= {"key", "id", "kind", "title", "body"}, "Unsupported record specification fields")
    require(type(raw.get("key")) is str and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}", raw["key"]) is not None, "Use a stable ASCII bridge key, up to 120 characters")
    require(type(raw.get("kind")) is str and raw["kind"] in KINDS, "Unsupported record kind")
    require(type(raw.get("title")) is str and 0 < utf16_length(raw["title"].strip()) <= 200, "Title must contain 1–200 characters")
    if "id" in raw:
        require(type(raw["id"]) is str and 0 < len(raw["id"]) <= 100, "Invalid existing record ID")
    body = validate_body(raw.get("body"))
    require(not RESERVED.intersection(body), "Bridge metadata is reserved")
    # This is a patch, so status may be inherited from an existing record.
    if "status" in body and raw["kind"] in {"decision", "task"}:
        valid = {"proposed", "confirmed"} if raw["kind"] == "decision" else {"todo", "done"}
        require(body["status"] in valid, "Unsupported status for this record kind")
    return {**raw, "title": raw["title"].strip(), "body": dict(body)}


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json_text(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class RecordBridge:
    """One coordinator, one destination, explicit user-facing record patches only."""

    def __init__(self, state_dir: Path, destination: str, call_tool: Callable[[str, dict], Any], *, expected_owner: str, anchor_id: str):
        require(type(destination) is str and 0 < len(destination) <= 500, "An explicit destination label is required")
        self.state_dir = Path(state_dir).resolve()
        require(not self.state_dir.is_relative_to(Path(__file__).resolve().parents[1]), "Keep mutable bridge state outside the installed skill")
        require(type(expected_owner) is str and bool(expected_owner), "Use the site-scoped owner from a verified connector read")
        require(type(anchor_id) is str and 0 < len(anchor_id) <= 100, "A verified existing anchor record ID is required")
        self.expected_owner = expected_owner
        self.anchor_id = anchor_id
        self.destination = destination
        self.call_tool = call_tool
        self.state: dict = {}

    @contextlib.contextmanager
    def locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.state_dir / ".lock").open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BridgeError("Another coordinator is using this bridge state") from exc
            path = self.state_dir / "records.json"
            if path.exists():
                self.state = json.loads(path.read_text(encoding="utf-8"))
                require(type(self.state) is dict and self.state.get("schema") == 1, "Unsupported bridge state")
                require(self.state.get("destination") == self.destination, "Destination differs from the saved binding")
                require(type(self.state.get("records")) is dict, "Invalid saved bridge records")
                require(self.state.get("owner") in (None, self.expected_owner), "Expected owner differs from the saved binding")
                require(self.state.get("anchor_id") in (None, self.anchor_id), "Anchor differs from the saved binding")
            else:
                self.state = {"schema": 1, "destination": self.destination, "records": {}}
                self.save()
            yield

    def save(self):
        atomic_json(self.state_dir / "records.json", self.state)

    def call(self, tool: str, arguments: dict) -> Any:
        require(tool in TOOLS, "Tool is outside this bridge's record scope")
        return unwrap(self.call_tool(tool, arguments))

    def record(self, raw: Any) -> dict:
        row = validate_record(raw)
        require(row["owner"] == self.expected_owner, "Owner identity changed; do not continue")
        previous = self.state.get("owner")
        require(previous is None or previous == row["owner"], "Owner identity changed; do not continue")
        if previous is None:
            self.state["owner"] = row["owner"]
            self.save()
        return row

    def search(self, key: str) -> list[dict]:
        result = self.call("search_records", {"query": key})
        require(type(result) is list and len(result) < 200, "Search is invalid or may be truncated; use a known record ID")
        matches = []
        for raw in result:
            row = self.record(raw)
            if row["body"].get("bridge_key") == key:
                matches.append(row)
        require(len(matches) <= 1, "Duplicate bridge keys found; reconcile manually without another write")
        return matches

    def read(self, record_id: str) -> dict:
        row = self.record(self.call("read_record", {"id": record_id}))
        require(row["id"] == record_id, "Connector returned a different record ID")
        return row

    def verified(self, row: dict, expected: dict, record_id: str | None = None, revision: int | None = None):
        if record_id is not None:
            require(row["id"] == record_id, "Read-back ID differs")
        if revision is not None:
            require(row["revision"] == revision, "Record changed during read-back; reconcile before continuing")
        require(all(row[field] == expected[field] for field in ("kind", "title", "body")), "Read-back differs from the intended record; pending operation retained")

    def recover(self, key: str, entry: dict) -> dict:
        pending = entry["pending"]
        if pending["tool"] == "update_record":
            row = self.read(pending["arguments"]["id"])
            require(row["revision"] == pending["arguments"]["revision"] + 1, "Uncertain update was not found at the expected revision; never replay automatically")
        else:
            matches = self.search(key)
            require(len(matches) == 1, "Uncertain create was not found; never replay automatically")
            row = self.read(matches[0]["id"])
            require(row["revision"] == 1, "Created record changed before reconciliation")
        self.verified(row, pending["arguments"])
        return self.finish(key, row, "recovered")

    def finish(self, key: str, row: dict, action: str) -> dict:
        self.state["records"][key] = {"id": row["id"], "revision": row["revision"], "status": "verified"}
        self.save()
        return {"key": key, "id": row["id"], "kind": row["kind"], "revision": row["revision"], "action": action, "status": "verified"}

    def sync_one(self, spec: dict) -> dict:
        key = spec["key"]
        entry = self.state["records"].get(key, {})
        if "pending" in entry:
            require(entry["pending"]["spec"] == spec, "An uncertain operation for this key needs reconciliation before different changes")
            return self.recover(key, entry)
        mapped_id = entry.get("id")
        require(not (mapped_id and spec.get("id") and mapped_id != spec["id"]), "Record ID differs from its saved binding")
        record_id = spec.get("id") or mapped_id
        matches = [] if record_id else self.search(key)
        old = self.read(record_id or matches[0]["id"]) if record_id or matches else None
        if old:
            require(old["kind"] == spec["kind"], "Cannot change an existing record's kind")
            require(old["body"].get("bridge_key") in (None, key), "Existing record is already bound to another key")
        body = {**(old["body"] if old else {}), **spec["body"], "bridge_key": key}
        kind = spec["kind"]
        if kind == "decision":
            require(body.get("status") in {"proposed", "confirmed"}, "A decision must be proposed or confirmed")
        if kind == "task":
            require(body.get("status") in {"todo", "done"}, "A task must be todo or done")
        if kind == "event":
            from datetime import datetime
            pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:\d{2})"
            require(all(type(body.get(k)) is str and re.fullmatch(pattern, body[k]) for k in ("start", "end")), "Events require conventional timezone-aware ISO timestamps")
            try:
                start = datetime.fromisoformat(str(body["start"]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(body["end"]).replace("Z", "+00:00"))
                require(start.tzinfo is not None and end.tzinfo is not None and end >= start, "Events require ordered, timezone-aware timestamps")
            except (KeyError, ValueError) as exc:
                raise BridgeError("Events require start and end ISO timestamps") from exc
        validate_body(body)
        expected = {"kind": kind, "title": spec["title"], "body": body}
        if old and all(old[field] == expected[field] for field in ("kind", "title", "body")):
            return self.finish(key, old, "unchanged")
        body["bridge_operation"] = str(uuid.uuid4())
        arguments = dict(expected)
        tool = "create_record"
        if old:
            tool = "update_record"
            arguments.update(id=old["id"], revision=old["revision"])
        self.state["records"][key] = {"status": "pending", "pending": {"spec": spec, "tool": tool, "arguments": arguments}}
        self.save()  # Must precede every external mutation, including an uncertain one.
        written = self.record(self.call(tool, arguments))
        self.verified(written, arguments, old["id"] if old else None, old["revision"] + 1 if old else 1)
        fresh = self.read(written["id"])
        self.verified(fresh, arguments, written["id"], written["revision"])
        return self.finish(key, fresh, "updated" if old else "created")

    def reconcile(self, key: str, record_id: str, revision: int, reason: str) -> dict:
        """Archive a pending operation and accept an explicitly reviewed remote base.

        This does not report the old write as successful and never writes remotely.
        A new sync will freshly read and merge the user's still-intended patch.
        """
        require(type(reason) is str and 0 < len(reason.strip()) <= 1000, "Record a concise reconciliation reason")
        require(type(revision) is int and revision > 0, "Use the freshly reviewed remote revision")
        with self.locked():
            self.read(self.anchor_id)
            entry = self.state["records"].get(key, {})
            require("pending" in entry, "This key has no pending operation")
            pending = entry["pending"]
            if pending["tool"] == "update_record":
                require(pending["arguments"]["id"] == record_id, "Cannot reconcile an update against a different record")
            else:
                matches = self.search(key)
                require(len(matches) == 1 and matches[0]["id"] == record_id, "An uncertain create needs exactly one existing matching record")
            row = self.read(record_id)
            require(row["revision"] == revision, "Remote revision changed; review it again")
            require(row["kind"] == pending["spec"]["kind"], "Reconciliation kind differs")
            require(row["body"].get("bridge_key") in (None, key), "Record belongs to another bridge key")
            archive = self.state_dir / ("reconciled-" + str(uuid.uuid4()) + ".json")
            atomic_json(archive, {"key": key, "reason": reason, "pending": pending, "observed": row})
            self.state["records"][key] = {"id": record_id, "revision": revision, "status": "reconciled", "archive": archive.name}
            self.state["anchor_id"] = self.anchor_id
            self.save()
            return {"key": key, "id": record_id, "revision": revision, "status": "reconciled", "previous_write_verified": False, "archive": archive.name}

    def sync(self, specs: Any) -> list[dict]:
        require(type(specs) is list and 0 < len(specs) <= 100, "Supply 1–100 explicit record specifications")
        checked = [validate_spec(spec) for spec in specs]
        require(len({spec["key"] for spec in checked}) == len(checked), "Duplicate input keys")
        ids = [spec["id"] for spec in checked if "id" in spec]
        require(len(set(ids)) == len(ids), "One record cannot have two input keys")
        results = []
        with self.locked():
            # A harmless existing-record read verifies the account before even a
            # search key, let alone private record content, reaches the dispatcher.
            self.read(self.anchor_id)
            self.state["anchor_id"] = self.anchor_id
            self.save()
            for spec in checked:
                results.append(self.sync_one(spec))
        return results


def stdio_call(tool: str, arguments: dict) -> Any:
    request_id = str(uuid.uuid4())
    print(json_text({"type": "tool_call", "request_id": request_id, "tool": tool, "arguments": arguments}), flush=True)
    line = sys.stdin.readline()
    require(bool(line), "Host disconnected; pending operation, if any, remains blocked")
    response = json.loads(line)
    require(type(response) is dict and response.get("type") == "tool_result" and response.get("request_id") == request_id and "result" in response, "Host returned a mismatched tool result")
    return response["result"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--destination", required=True, help="Verified connector/destination label, not a credential")
    parser.add_argument("--expected-owner", required=True, help="Site-scoped owner from a verified read; never a credential")
    parser.add_argument("--anchor-id", required=True, help="Existing owner record verified before any payload-bearing call")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--input", type=Path, help="Explicit user-facing record patches; never a core-store export")
    operation.add_argument("--reconcile", type=Path, help="JSON key/id/revision/reason for an explicitly reviewed remote record")
    parser.add_argument("--stdio", action="store_true", required=True, help="Use a host-provided authenticated JSON-lines tool dispatcher")
    args = parser.parse_args(argv)
    try:
        bridge = RecordBridge(args.state_dir, args.destination, stdio_call, expected_owner=args.expected_owner, anchor_id=args.anchor_id)
        if args.input:
            result = {"records": bridge.sync(json.loads(args.input.read_text(encoding="utf-8")))}
        else:
            request = json.loads(args.reconcile.read_text(encoding="utf-8"))
            require(type(request) is dict and set(request) == {"key", "id", "revision", "reason"}, "Reconciliation requires key/id/revision/reason only")
            result = {"reconciliation": bridge.reconcile(request["key"], request["id"], request["revision"], request["reason"])}
        print(json_text({"type": "result", **result}), flush=True)
        return 0
    except (BridgeError, OSError, ValueError, TypeError, OverflowError) as exc:
        print(json_text({"type": "error", "error": str(exc), "status": "blocked"}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
