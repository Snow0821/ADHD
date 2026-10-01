"""Credential-free snapshot bridge for the optional connector knowledge backend.

The caller executes emitted SQL through its authenticated connector. This module
never opens a network connection and never treats a local draft as a DB commit.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import tempfile
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from _storage import _atomic_write_text, _dump_yaml


CONFIG = "knowledge-db.yaml"
PENDING = ".knowledge-db-pending.json"
JOURNAL = ".knowledge-db-sync.json"
MODES = {"uninitialized", "clean", "editing", "pending", "verifying"}


class DatabaseError(RuntimeError):
    """The remote graph or its local working copy is not safe to use."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    def numbers(item: Any) -> Any:
        if isinstance(item, float):
            # JSONB may export 1e+20 as an integer. Compare JSON decimal values,
            # not Python's int/float distinction or binary float expansion.
            decimal = Decimal(str(item))
            if not decimal.is_finite():
                raise DatabaseError("Knowledge snapshots cannot contain NaN or Infinity.")
            return int(decimal) if decimal == decimal.to_integral_value() else item
        if isinstance(item, list):
            return [numbers(child) for child in item]
        if isinstance(item, dict):
            return {key: numbers(child) for key, child in item.items()}
        return item
    return hashlib.sha256(_json(numbers(value)).encode("utf-8")).hexdigest()


def _yaml(path: Path) -> Any:
    if not path.exists():
        raise DatabaseError(f"Required cache file is missing: {path.name}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def binding(root: Path | str) -> dict[str, Any] | None:
    path = Path(root) / CONFIG
    if not path.exists():
        return None
    return _validate_binding(_yaml(path))


def _validate_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise DatabaseError("Unsupported knowledge database binding.")
    if value.get("backend") != "supabase-connector" or value.get("mode") not in MODES:
        raise DatabaseError("Invalid knowledge database backend or mode.")
    if not all(isinstance(value.get(k), str) and value[k].strip()
               for k in ("project_id", "graph_id")):
        raise DatabaseError("Database project and graph IDs must be non-empty strings.")
    revision = value.get("revision")
    if type(revision) is not int or revision < 0:
        raise DatabaseError("Database revision must be a non-negative integer.")
    if value["mode"] == "clean" and (revision < 1 or not isinstance(value.get("base_digest"), str)
                                      or not re.fullmatch(r"[0-9a-f]{64}", value["base_digest"])):
        raise DatabaseError("A clean database binding needs a published revision and snapshot digest.")
    return value


def snapshot(root: Path | str) -> dict[str, Any]:
    """Read the existing format without initializing or changing any store."""
    from graphctl import _split_document

    root = Path(root)
    state = _yaml(root / "state.yaml")
    if not isinstance(state, dict) or not isinstance(state.get("knowledge"), dict):
        raise DatabaseError("Knowledge state is missing from state.yaml.")
    directory = root / "knowledge"
    nodes = []
    for path in sorted((directory / "nodes").glob("*.md")):
        if not re.fullmatch(r"k\d{6}", path.stem):
            raise DatabaseError(f"Unexpected knowledge filename: {path.name}")
        metadata, body = _split_document(path.read_text(encoding="utf-8"))
        nodes.append({"id": path.stem, "metadata": metadata, "body": body})
    return {"graph": _yaml(directory / "graph.yaml"),
            "next_id": state["knowledge"].get("next_id"),
            "nodes": nodes, "edges": _yaml(directory / "edges.yaml")}


def _validate_scopes(value: dict[str, Any], root: Path) -> None:
    from graphctl import GraphStore

    graph = GraphStore(root)
    scopes = {value["graph"].get("default_scope", "")}
    scopes.update(node["metadata"].get("scope", "") for node in value["nodes"])
    for scope in scopes:
        try:
            graph._validate_scope(scope)
        except RuntimeError as error:
            raise DatabaseError(str(error)) from error


def cache_guard(root: Path | str, *, mutate: bool = False,
                reference: bool = False, error_type: type[Exception] = DatabaseError) -> None:
    """File-mode calls are unchanged; DB mode fails closed on unverified data."""
    try:
        root = Path(root)
        config = binding(root)
        if config is None:
            return
        if (root / JOURNAL).exists():
            raise DatabaseError("Interrupted knowledge cache update; run graphdb.py recover first.")
        mode = config["mode"]
        if mode == "uninitialized":
            raise DatabaseError("Read the configured database graph before using its cache.")
        if reference and mode != "clean":
            raise DatabaseError("Knowledge references require a published, verified cache; finish the database round trip first.")
        if mutate and mode != "editing":
            raise DatabaseError("Database knowledge is read-only until graphdb.py begin-edit; pending writes must be resolved first.")
        if mode == "clean":
            cached = snapshot(root)
            if fingerprint(cached) != config.get("base_digest"):
                raise DatabaseError("The published knowledge cache changed outside the bridge; preserve it and reread the database.")
            _validate_scopes(cached, root)
    except (RuntimeError, OSError, ValueError, TypeError, yaml.YAMLError) as error:
        raise error_type(str(error)) from error


def _validate_snapshot(value: Any, *, context_root: Path | None = None) -> None:
    from graphctl import GraphStore

    if not isinstance(value, dict) or set(value) != {"graph", "next_id", "nodes", "edges"}:
        raise DatabaseError("Snapshot must contain graph, next_id, nodes, and edges.")
    if not isinstance(value["graph"], dict) or not isinstance(value["nodes"], list) or not isinstance(value["edges"], list):
        raise DatabaseError("Snapshot graph must be an object; nodes and edges must be lists.")
    if type(value["next_id"]) is not int or not 0 <= value["next_id"] <= 1000000:
        raise DatabaseError("Snapshot next_id is outside the supported six-digit ID range.")
    graph = value["graph"]
    if not isinstance(graph.get("name"), str) or not graph["name"].strip():
        raise DatabaseError("Snapshot graph name must be a non-empty string.")
    for edge in value["edges"]:
        if not isinstance(edge, dict) or set(edge) != {"from", "relation", "to", "reason"}:
            raise DatabaseError("Each snapshot edge needs from, relation, to, and reason.")
        if not all(isinstance(v, str) for v in edge.values()):
            raise DatabaseError("Snapshot edge fields must be strings.")
    ids = []
    for node in value["nodes"]:
        if not isinstance(node, dict) or set(node) != {"id", "metadata", "body"}:
            raise DatabaseError("Each snapshot node needs id, metadata, and body.")
        if not isinstance(node["id"], str) or not re.fullmatch(r"k\d{6}", node["id"]):
            raise DatabaseError("Invalid snapshot node ID.")
        if not isinstance(node["metadata"], dict) or not isinstance(node["body"], str):
            raise DatabaseError("Invalid snapshot node metadata or body.")
        ids.append(node["id"])
    if len(ids) != len(set(ids)) or ids != sorted(ids):
        raise DatabaseError("Snapshot node IDs must be unique and sorted.")
    # Reuse the same behavioral contract as the existing file backend.
    with tempfile.TemporaryDirectory(prefix="adhd-snapshot-check-") as temporary:
        root = Path(temporary)
        _materialize(root, value)
        errors = GraphStore(root).validate()
        if errors:
            raise DatabaseError("Invalid database snapshot: " + "; ".join(errors))
        if fingerprint(snapshot(root)) != fingerprint(value):
            raise DatabaseError("Snapshot cannot be represented losslessly by the current file format; original cache retained.")
    if context_root is not None:
        _validate_scopes(value, context_root)


def _materialize(root: Path, value: dict[str, Any]) -> None:
    """Caller holds the workspace lock and has written a recovery journal."""
    directory = root / "knowledge"
    nodes_dir = directory / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    wanted = {node["id"] for node in value["nodes"]}
    for node in value["nodes"]:
        # Preserve body bytes rather than normalizing whitespace on every pull.
        document = "---\n" + _dump_yaml(node["metadata"]) + "---\n\n" + node["body"]
        _atomic_write_text(nodes_dir / (node["id"] + ".md"), document)
    for path in nodes_dir.glob("k*.md"):
        if path.stem not in wanted:
            path.unlink()
    _atomic_write_text(directory / "graph.yaml", _dump_yaml(value["graph"]))
    _atomic_write_text(directory / "edges.yaml", _dump_yaml(value["edges"]))
    state_path = root / "state.yaml"
    state = _yaml(state_path) if state_path.exists() else {}
    if not isinstance(state, dict):
        raise DatabaseError("state.yaml must be a mapping.")
    linked = {edge[key] for edge in value["edges"] for key in ("from", "to")}
    knowledge = state.setdefault("knowledge", {})
    if not isinstance(knowledge, dict):
        raise DatabaseError("state.yaml knowledge must be a mapping.")
    knowledge.update(next_id=value["next_id"], unlinked=sorted(wanted - linked))
    _atomic_write_text(state_path, _dump_yaml(state))


def _literal(value: str) -> str:
    # E strings are independent of standard_conforming_strings session settings.
    return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


class DatabaseBridge:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".taskctl.lock").open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _config(self) -> dict[str, Any]:
        config = binding(self.root)
        if config is None:
            raise DatabaseError("Configure a knowledge database first.")
        return config

    def _save(self, config: dict[str, Any]) -> None:
        _atomic_write_text(self.root / CONFIG, _dump_yaml(config))

    def _no_journal(self) -> None:
        if (self.root / JOURNAL).exists():
            raise DatabaseError("Recover the interrupted cache update first.")

    def configure(self, *, project_id: str, graph_id: str, new: bool = False) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", project_id):
            raise DatabaseError("Project ID must be a connector project reference, not a URL or secret.")
        if not graph_id.strip() or len(graph_id) > 200 or any(ord(c) < 32 for c in graph_id):
            raise DatabaseError("Graph ID must be a non-empty identifier of at most 200 characters.")
        with self.locked():
            self._no_journal()
            if binding(self.root) is not None:
                raise DatabaseError("This runtime is already bound; do not silently switch database destinations.")
            # Require an initialized workspace, preserving all five current stores.
            current = snapshot(self.root)
            _validate_snapshot(current, context_root=self.root)
            if current["nodes"] and not new:
                raise DatabaseError("Existing local knowledge must be migrated explicitly with configure --new, or kept in another runtime.")
            config = {"schema_version": 1, "backend": "supabase-connector",
                      "project_id": project_id, "graph_id": graph_id,
                      "revision": 0, "mode": "editing" if new else "uninitialized",
                      "base_digest": None}
            self._save(config)
            return config

    def read_query(self) -> dict[str, str]:
        with self.locked():
            config = self._config()
            return {"project_id": config["project_id"],
                    "query": "SELECT adhd_knowledge.export_graph(" + _literal(config["graph_id"]) + ") AS snapshot;"}

    def begin_edit(self) -> dict[str, Any]:
        with self.locked():
            config = self._config()
            self._no_journal()
            cache_guard(self.root, reference=True)
            config["mode"] = "editing"
            self._save(config)
            return config

    def _pending(self, config: dict[str, Any]) -> dict[str, Any]:
        pending = json.loads((self.root / PENDING).read_text(encoding="utf-8"))
        if not isinstance(pending, dict) or pending.get("graph_id") != config["graph_id"]:
            raise DatabaseError("Pending request belongs to a different graph.")
        if type(pending.get("expected_revision")) is not int or pending["expected_revision"] != config["revision"]:
            raise DatabaseError("Pending request revision disagrees with the database binding.")
        try:
            uuid.UUID(pending["operation_id"])
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise DatabaseError("Pending request has an invalid operation UUID.") from error
        _validate_snapshot(pending.get("snapshot"), context_root=self.root)
        return pending

    def write_query(self) -> dict[str, str]:
        with self.locked():
            config = self._config()
            self._no_journal()
            if config["mode"] == "pending":
                pending = self._pending(config)
                if fingerprint(snapshot(self.root)) != fingerprint(pending["snapshot"]):
                    raise DatabaseError("Pending snapshot changed; preserve the draft and resolve it before retrying.")
            elif config["mode"] == "editing":
                current = snapshot(self.root)
                _validate_snapshot(current, context_root=self.root)
                pending = {"graph_id": config["graph_id"], "expected_revision": config["revision"],
                           "operation_id": str(uuid.uuid4()), "snapshot": current}
                _atomic_write_text(self.root / PENDING, _json(pending) + "\n")
                config["mode"] = "pending"
                self._save(config)
            else:
                raise DatabaseError("Begin an edit before publishing; verify an acknowledged write before editing again.")
            query = "SELECT adhd_knowledge.replace_graph(" + ", ".join([
                _literal(pending["graph_id"]), str(pending["expected_revision"]),
                _literal(pending["operation_id"]) + "::uuid", _literal(_json(pending["snapshot"])) + "::jsonb",
            ]) + ") AS result;"
            return {"project_id": config["project_id"], "query": query}

    def apply_write(self, response: Any) -> dict[str, Any]:
        with self.locked():
            config = self._config()
            self._no_journal()
            if config["mode"] not in {"pending", "verifying"}:
                raise DatabaseError("No pending database write to acknowledge.")
            pending = self._pending(config)
            if not isinstance(response, dict) or response.get("graph_id") != config["graph_id"] or response.get("operation_id") != pending["operation_id"]:
                raise DatabaseError("Write response does not match the pending graph and operation.")
            if type(response.get("revision")) is not int or response["revision"] != pending["expected_revision"] + 1:
                raise DatabaseError("Write response has an unexpected revision.")
            if fingerprint(snapshot(self.root)) != fingerprint(pending["snapshot"]):
                raise DatabaseError("Local draft changed while the database write was pending.")
            config["mode"] = "verifying"
            config["acknowledged_revision"] = response["revision"]
            self._save(config)
            return config

    def _archive_draft(self, config: dict[str, Any]) -> Path:
        record = {"binding": config, "snapshot": snapshot(self.root)}
        if (self.root / PENDING).exists():
            record["pending"] = json.loads((self.root / PENDING).read_text(encoding="utf-8"))
        path = self.root / "knowledge-drafts" / (str(uuid.uuid4()) + ".json")
        _atomic_write_text(path, _json(record) + "\n")
        return path

    def apply_read(self, response: Any, *, preserve_draft: bool = False) -> dict[str, Any]:
        if not isinstance(response, dict) or type(response.get("revision")) is not int or response["revision"] < 1:
            raise DatabaseError("Expected an existing database graph envelope with a positive revision.")
        with self.locked():
            config = self._config()
            self._no_journal()
            _validate_snapshot(response.get("snapshot"), context_root=self.root)
            if response.get("graph_id") != config["graph_id"]:
                raise DatabaseError("Read response belongs to a different graph.")
            if response["revision"] < config["revision"]:
                raise DatabaseError("Refusing to replace the cache with an older database revision.")
            incoming = fingerprint(response["snapshot"])
            archived = None
            mode = config["mode"]
            current = fingerprint(snapshot(self.root))
            pending_matches = False
            if mode in {"pending", "verifying"}:
                pending = self._pending(config)
                pending_matches = (
                    response.get("operation_id") == pending["operation_id"]
                    and response["revision"] == pending["expected_revision"] + 1
                    and incoming == fingerprint(pending["snapshot"])
                    and current == incoming
                )
            clean_draft = mode == "editing" and current == config.get("base_digest")
            dirty = mode in {"editing", "pending", "verifying"} and not (pending_matches or clean_draft)
            changed_clean = mode == "clean" and current != config.get("base_digest")
            if dirty or changed_clean:
                if not preserve_draft:
                    raise DatabaseError("Read would replace an unpublished or conflicting draft; publish it or use --preserve-draft to archive it first.")
                archived = self._archive_draft(config)
            if mode == "clean" and response["revision"] == config["revision"] and incoming != config.get("base_digest"):
                raise DatabaseError("Database content changed without advancing its revision.")
            config.update(revision=response["revision"], mode="clean", base_digest=incoming)
            config.pop("acknowledged_revision", None)
            journal = {"snapshot": response["snapshot"], "binding": config}
            _atomic_write_text(self.root / JOURNAL, _json(journal) + "\n")
            self._finish_import(journal)
            result = dict(config)
            if archived is not None:
                result["preserved_draft"] = str(archived)
            return result

    def _finish_import(self, journal: dict[str, Any]) -> None:
        _materialize(self.root, journal["snapshot"])
        if fingerprint(snapshot(self.root)) != journal["binding"]["base_digest"]:
            raise DatabaseError("Cache round-trip changed the snapshot; recovery journal retained.")
        self._save(journal["binding"])
        (self.root / PENDING).unlink(missing_ok=True)
        (self.root / JOURNAL).unlink()

    def recover(self) -> dict[str, Any]:
        with self.locked():
            path = self.root / JOURNAL
            if not path.exists():
                raise DatabaseError("No interrupted knowledge cache import to recover.")
            journal = json.loads(path.read_text(encoding="utf-8"))
            config = self._config()
            if not isinstance(journal, dict):
                raise DatabaseError("Recovery journal must be an object.")
            target = _validate_binding(journal.get("binding"))
            if target.get("graph_id") != config["graph_id"] or target.get("project_id") != config["project_id"]:
                raise DatabaseError("Recovery journal belongs to a different database binding.")
            if target["revision"] < config["revision"]:
                raise DatabaseError("Recovery journal would move the cache revision backwards.")
            if (config["mode"] == "clean" and target["revision"] == config["revision"]
                    and target.get("base_digest") != config.get("base_digest")):
                raise DatabaseError("Recovery journal changes content without advancing the revision.")
            _validate_snapshot(journal.get("snapshot"), context_root=self.root)
            if target.get("mode") != "clean" or target.get("base_digest") != fingerprint(journal["snapshot"]):
                raise DatabaseError("Recovery journal failed integrity checks.")
            self._finish_import(journal)
            return target
