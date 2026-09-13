#!/usr/bin/env python3
"""Deterministic file store for the ADHD knowledge-graph workflow."""

from __future__ import annotations

import argparse
import fcntl
import re
import sys
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from _storage import (
    _atomic_write_text,
    _clean_list,
    _dump_yaml,
    _join_document,
    _replace_section,
    _section,
)


ID_WIDTH = 6
KINDS = ("concept", "claim", "idea", "question", "source")
STATUSES = ("active", "superseded", "archived")
RELATIONS = (
    "related_to",
    "part_of",
    "depends_on",
    "supports",
    "contradicts",
    "extends",
    "supersedes",
)
SYMMETRIC_RELATIONS = {"related_to", "contradicts"}
NODE_ID_PATTERN = re.compile(r"^k(\d{6})$")
SCOPE_PATTERN = re.compile(r"^(?:common|project:[a-z0-9][a-z0-9-]*)$")


class GraphError(RuntimeError):
    """Raised when the persisted knowledge graph violates an invariant."""


def format_node_id(value: int) -> str:
    return f"k{value:0{ID_WIDTH}d}"


def normalize_node_id(value: str | int) -> str:
    if isinstance(value, int):
        if value < 0:
            raise GraphError("Knowledge node IDs cannot be negative.")
        return format_node_id(value)
    text = str(value).strip().lower()
    if text.isdigit():
        return format_node_id(int(text))
    if NODE_ID_PATTERN.fullmatch(text):
        return text
    raise GraphError("Knowledge node ID must look like k000000 or 0.")


def _split_document(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise GraphError("Knowledge node must begin with YAML frontmatter.")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise GraphError("Knowledge node frontmatter is not closed with '---'.") from error
    metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
    if not isinstance(metadata, dict):
        raise GraphError("Knowledge node frontmatter must be a mapping.")
    return metadata, "\n".join(lines[closing + 1 :]).lstrip("\n")


class GraphStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.state_path = self.root / "state.yaml"
        self.lock_path = self.root / ".taskctl.lock"
        self.knowledge_dir = self.root / "knowledge"
        self.nodes_dir = self.knowledge_dir / "nodes"
        self.graph_path = self.knowledge_dir / "graph.yaml"
        self.edges_path = self.knowledge_dir / "edges.yaml"

    @contextmanager
    def _raw_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def locked(self):
        with self._raw_lock():
            self._ensure_unlocked()
            yield

    def _ensure_unlocked(self) -> None:
        self.nodes_dir.mkdir(parents=True, exist_ok=True)
        state = self._read_yaml(self.state_path, default={})
        if not isinstance(state, dict):
            raise GraphError("state.yaml must be a mapping.")
        changed = False
        if "knowledge" not in state:
            state["knowledge"] = {"next_id": 0, "unlinked": []}
            changed = True
        knowledge = state["knowledge"]
        if not isinstance(knowledge, dict):
            raise GraphError("state.yaml knowledge must be a mapping.")
        if "next_id" not in knowledge:
            knowledge["next_id"] = 0
            changed = True
        if "unlinked" not in knowledge:
            knowledge["unlinked"] = []
            changed = True
        if changed or not self.state_path.exists():
            self._save_state(state)

        if not self.graph_path.exists():
            _atomic_write_text(
                self.graph_path,
                _dump_yaml(
                    {
                        "schema_version": 2,
                        "graph_id": "workspace",
                        "kind": "workspace",
                        "name": "Workspace knowledge",
                        "default_scope": "common",
                        "imports": [],
                    }
                ),
            )
        if not self.edges_path.exists():
            _atomic_write_text(self.edges_path, "[]\n")

    def ensure(self) -> None:
        with self.locked():
            return

    def initialize(
        self, *, graph_id: str, kind: str, name: str, default_scope: str = "common"
    ) -> None:
        if kind != "workspace":
            raise GraphError("Graph kind must be workspace.")
        if not graph_id.strip() or not name.strip():
            raise GraphError("Graph ID and name cannot be empty.")
        self._validate_scope(default_scope)
        with self._raw_lock():
            existed = self.graph_path.exists()
            self._ensure_unlocked()
            requested = {
                "schema_version": 2,
                "graph_id": graph_id.strip(),
                "kind": kind,
                "name": name.strip(),
                "default_scope": default_scope,
                "imports": [],
            }
            if existed:
                current = self._load_graph()
                comparable = {key: current.get(key) for key in requested}
                if comparable != requested:
                    raise GraphError(
                        "Graph is already initialized with different metadata."
                    )
            else:
                _atomic_write_text(self.graph_path, _dump_yaml(requested))

    @staticmethod
    def _read_yaml(path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def _load_state(self) -> dict[str, Any]:
        state = self._read_yaml(self.state_path, default={})
        if not isinstance(state, dict) or not isinstance(state.get("knowledge"), dict):
            raise GraphError("state.yaml knowledge state is invalid.")
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        _atomic_write_text(self.state_path, _dump_yaml(state))

    def _load_graph(self) -> dict[str, Any]:
        graph = self._read_yaml(self.graph_path, default={})
        if not isinstance(graph, dict):
            raise GraphError("knowledge/graph.yaml must be a mapping.")
        return graph

    def _load_edges(self) -> list[dict[str, Any]]:
        edges = self._read_yaml(self.edges_path, default=[])
        if edges is None:
            return []
        if not isinstance(edges, list) or not all(isinstance(edge, dict) for edge in edges):
            raise GraphError("knowledge/edges.yaml must be a list of mappings.")
        return edges

    def _validate_scope(self, scope: str) -> str:
        normalized = scope.strip().lower()
        if not SCOPE_PATTERN.fullmatch(normalized):
            raise GraphError(
                "Knowledge scope must be common or project:<project-id>."
            )
        if normalized.startswith("project:"):
            projects_path = self.root / "control" / "projects.yaml"
            if projects_path.exists():
                value = self._read_yaml(projects_path, default={})
                projects = value.get("projects", {}) if isinstance(value, dict) else {}
                project_id = normalized.split(":", 1)[1]
                if not isinstance(projects, dict) or project_id not in projects:
                    raise GraphError(
                        f"Knowledge scope references an unregistered project: {project_id}"
                    )
        return normalized

    def _save_edges(self, edges: list[dict[str, Any]]) -> None:
        _atomic_write_text(self.edges_path, _dump_yaml(edges))

    def _node_path(self, node_id: str | int) -> Path:
        return self.nodes_dir / f"{normalize_node_id(node_id)}.md"

    def _node_ids(self) -> list[str]:
        return sorted(
            path.stem
            for path in self.nodes_dir.glob("k*.md")
            if NODE_ID_PATTERN.fullmatch(path.stem)
        )

    def _read_node(self, node_id: str | int) -> tuple[str, dict[str, Any], str]:
        normalized = normalize_node_id(node_id)
        path = self._node_path(normalized)
        if not path.exists():
            raise GraphError(f"Knowledge node {normalized} does not exist.")
        metadata, body = _split_document(path.read_text(encoding="utf-8"))
        return normalized, metadata, body

    def _reserve_id(self, state: dict[str, Any]) -> str:
        knowledge = state["knowledge"]
        value = knowledge.get("next_id")
        if not isinstance(value, int) or value < 0:
            raise GraphError("knowledge.next_id must be a non-negative integer.")
        knowledge["next_id"] = value + 1
        self._save_state(state)
        return format_node_id(value)

    @staticmethod
    def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
        source = str(edge.get("from", ""))
        relation = str(edge.get("relation", ""))
        target = str(edge.get("to", ""))
        if relation in SYMMETRIC_RELATIONS and target < source:
            source, target = target, source
        return source, relation, target

    def _sync_unlinked(self, state: dict[str, Any], edges: list[dict[str, Any]]) -> None:
        linked: set[str] = set()
        for edge in edges:
            linked.add(str(edge.get("from", "")))
            linked.add(str(edge.get("to", "")))
        state["knowledge"]["unlinked"] = [
            node_id for node_id in self._node_ids() if node_id not in linked
        ]
        self._save_state(state)

    def create_node(
        self,
        *,
        title: str,
        kind: str,
        core: str,
        content: str = "",
        memo: str = "",
        aliases: list[str] | None = None,
        sources: list[str] | None = None,
        status: str = "active",
        scope: str | None = None,
    ) -> str:
        if not title.strip() or not core.strip():
            raise GraphError("Knowledge node title and core cannot be empty.")
        if kind not in KINDS:
            raise GraphError(f"Unsupported knowledge node kind: {kind}")
        if status not in STATUSES:
            raise GraphError(f"Unsupported knowledge node status: {status}")
        with self.locked():
            state = self._load_state()
            graph = self._load_graph()
            node_scope = self._validate_scope(
                scope if scope is not None else str(graph.get("default_scope", "common"))
            )
            node_id = self._reserve_id(state)
            metadata = {
                "title": title.strip(),
                "kind": kind,
                "scope": node_scope,
                "aliases": _clean_list(aliases or []),
                "sources": _clean_list(sources or []),
                "status": status,
            }
            body = f"""## 핵심

{core.strip()}

## 내용

{content.strip() if content.strip() else '없음'}

## 메모

{memo.strip() if memo.strip() else '없음'}
"""
            _atomic_write_text(self._node_path(node_id), _join_document(metadata, body))
            self._sync_unlinked(state, self._load_edges())
            return node_id

    def update_node(
        self,
        node_id: str | int,
        *,
        title: str | None = None,
        kind: str | None = None,
        core: str | None = None,
        content: str | None = None,
        memo: str | None = None,
        aliases: list[str] | None = None,
        sources: list[str] | None = None,
        status: str | None = None,
        scope: str | None = None,
    ) -> str:
        if kind is not None and kind not in KINDS:
            raise GraphError(f"Unsupported knowledge node kind: {kind}")
        if status is not None and status not in STATUSES:
            raise GraphError(f"Unsupported knowledge node status: {status}")
        if title is not None and not title.strip():
            raise GraphError("Knowledge node title cannot be empty.")
        if core is not None and not core.strip():
            raise GraphError("Knowledge node core cannot be empty.")
        with self.locked():
            normalized, metadata, body = self._read_node(node_id)
            if title is not None:
                metadata["title"] = title.strip()
            if kind is not None:
                metadata["kind"] = kind
            if aliases is not None:
                metadata["aliases"] = _clean_list(aliases)
            if sources is not None:
                metadata["sources"] = _clean_list(sources)
            if status is not None:
                metadata["status"] = status
            if scope is not None:
                metadata["scope"] = self._validate_scope(scope)
            if core is not None:
                body = _replace_section(body, "핵심", core)
            if content is not None:
                body = _replace_section(body, "내용", content or "없음")
            if memo is not None:
                body = _replace_section(body, "메모", memo or "없음")
            _atomic_write_text(self._node_path(normalized), _join_document(metadata, body))
            return normalized

    def link(
        self,
        source: str | int,
        relation: str,
        target: str | int,
        *,
        reason: str,
    ) -> dict[str, str]:
        source_id = normalize_node_id(source)
        target_id = normalize_node_id(target)
        if relation not in RELATIONS:
            raise GraphError(f"Unsupported relation: {relation}")
        if source_id == target_id:
            raise GraphError("A knowledge node cannot link to itself.")
        if not reason.strip():
            raise GraphError("Every knowledge edge requires a reason.")
        if relation in SYMMETRIC_RELATIONS and target_id < source_id:
            source_id, target_id = target_id, source_id
        edge = {
            "from": source_id,
            "relation": relation,
            "to": target_id,
            "reason": reason.strip(),
        }
        with self.locked():
            self._read_node(source_id)
            self._read_node(target_id)
            state = self._load_state()
            edges = self._load_edges()
            if self._edge_key(edge) in {self._edge_key(item) for item in edges}:
                raise GraphError("The same knowledge edge already exists.")
            edges.append(edge)
            self._save_edges(edges)
            self._sync_unlinked(state, edges)
            return edge

    def unlink(self, source: str | int, relation: str, target: str | int) -> None:
        source_id = normalize_node_id(source)
        target_id = normalize_node_id(target)
        candidate = {"from": source_id, "relation": relation, "to": target_id}
        key = self._edge_key(candidate)
        with self.locked():
            state = self._load_state()
            edges = self._load_edges()
            remaining = [edge for edge in edges if self._edge_key(edge) != key]
            if len(remaining) == len(edges):
                raise GraphError("Knowledge edge does not exist.")
            self._save_edges(remaining)
            self._sync_unlinked(state, remaining)

    def show(self, node_id: str | int) -> str:
        with self.locked():
            normalized = normalize_node_id(node_id)
            path = self._node_path(normalized)
            if not path.exists():
                raise GraphError(f"Knowledge node {normalized} does not exist.")
            return path.read_text(encoding="utf-8")

    def list_nodes(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, str]]:
        with self.locked():
            result: list[dict[str, str]] = []
            for node_id in self._node_ids():
                _, metadata, body = self._read_node(node_id)
                if kind is not None and metadata.get("kind") != kind:
                    continue
                if status is not None and metadata.get("status") != status:
                    continue
                result.append(
                    {
                        "id": node_id,
                        "title": str(metadata.get("title", "<untitled>")),
                        "kind": str(metadata.get("kind", "<unknown>")),
                        "status": str(metadata.get("status", "<unknown>")),
                        "core": _section(body, "핵심"),
                    }
                )
            return result

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        needle = query.strip().casefold()
        if not needle:
            raise GraphError("Search query cannot be empty.")
        if limit < 1:
            raise GraphError("Search limit must be positive.")
        tokens = [token for token in re.split(r"\s+", needle) if token]
        with self.locked():
            matches: list[dict[str, Any]] = []
            for node_id in self._node_ids():
                _, metadata, body = self._read_node(node_id)
                title = str(metadata.get("title", ""))
                aliases = [str(value) for value in metadata.get("aliases", [])]
                core = _section(body, "핵심")
                title_folded = title.casefold()
                aliases_folded = [alias.casefold() for alias in aliases]
                body_folded = body.casefold()
                score = 0
                if needle == node_id:
                    score += 100
                if needle == title_folded:
                    score += 80
                elif needle in title_folded:
                    score += 40
                if needle in aliases_folded:
                    score += 35
                elif any(needle in alias for alias in aliases_folded):
                    score += 20
                if needle in core.casefold():
                    score += 15
                elif needle in body_folded:
                    score += 8
                score += sum(1 for token in tokens if token in body_folded)
                if score:
                    matches.append(
                        {
                            "id": node_id,
                            "title": title,
                            "kind": metadata.get("kind"),
                            "core": core,
                            "score": score,
                        }
                    )
            matches.sort(key=lambda item: (-int(item["score"]), str(item["id"])))
            return matches[:limit]

    def neighbors(
        self,
        node_id: str | int,
        *,
        hops: int = 1,
        relation: str | None = None,
    ) -> dict[str, Any]:
        root = normalize_node_id(node_id)
        if hops < 1 or hops > 5:
            raise GraphError("Neighbor traversal supports 1 to 5 hops.")
        if relation is not None and relation not in RELATIONS:
            raise GraphError(f"Unsupported relation: {relation}")
        with self.locked():
            self._read_node(root)
            edges = [
                edge
                for edge in self._load_edges()
                if relation is None or edge.get("relation") == relation
            ]
            distance = {root: 0}
            queue: deque[str] = deque([root])
            selected_edges: list[dict[str, Any]] = []
            selected_keys: set[tuple[str, str, str]] = set()
            while queue:
                current = queue.popleft()
                if distance[current] >= hops:
                    continue
                for edge in edges:
                    source = str(edge.get("from"))
                    target = str(edge.get("to"))
                    if current not in (source, target):
                        continue
                    other = target if current == source else source
                    key = self._edge_key(edge)
                    if key not in selected_keys:
                        selected_keys.add(key)
                        selected_edges.append(edge)
                    if other not in distance:
                        distance[other] = distance[current] + 1
                        queue.append(other)
            nodes: list[dict[str, Any]] = []
            for current, current_distance in sorted(
                distance.items(), key=lambda item: (item[1], item[0])
            ):
                _, metadata, body = self._read_node(current)
                nodes.append(
                    {
                        "id": current,
                        "distance": current_distance,
                        "title": metadata.get("title"),
                        "kind": metadata.get("kind"),
                        "core": _section(body, "핵심"),
                    }
                )
            return {"root": root, "nodes": nodes, "edges": selected_edges}

    def status(self) -> dict[str, Any]:
        with self.locked():
            state = self._load_state()
            graph = self._load_graph()
            unlinked = []
            for node_id in state["knowledge"]["unlinked"]:
                _, metadata, _ = self._read_node(node_id)
                unlinked.append(f"{node_id} {metadata.get('title', '<untitled>')}")
            return {
                "graph_id": graph.get("graph_id"),
                "kind": graph.get("kind"),
                "name": graph.get("name"),
                "next_id": state["knowledge"].get("next_id"),
                "nodes": len(self._node_ids()),
                "edges": len(self._load_edges()),
                "unlinked": unlinked,
                "imports": graph.get("imports", []),
                "scopes": sorted(
                    {
                        str(self._read_node(node_id)[1].get("scope"))
                        for node_id in self._node_ids()
                    }
                ),
            }

    def validate(self) -> list[str]:
        try:
            with self.locked():
                errors: list[str] = []
                state = self._load_state()
                knowledge = state["knowledge"]
                graph = self._load_graph()
                if graph.get("schema_version") != 2:
                    errors.append("graph schema_version must be 2")
                if graph.get("kind") != "workspace":
                    errors.append("graph kind must be workspace")
                if not isinstance(graph.get("graph_id"), str) or not graph["graph_id"]:
                    errors.append("graph_id must be a non-empty string")
                try:
                    self._validate_scope(str(graph.get("default_scope", "")))
                except GraphError as error:
                    errors.append(str(error))
                if not isinstance(graph.get("imports"), list) or not all(
                    isinstance(value, str) for value in graph.get("imports", [])
                ):
                    errors.append("graph imports must be a list of strings")

                node_ids = self._node_ids()
                for node_id in node_ids:
                    try:
                        _, metadata, body = self._read_node(node_id)
                    except GraphError as error:
                        errors.append(f"{node_id}: {error}")
                        continue
                    if not isinstance(metadata.get("title"), str) or not metadata["title"].strip():
                        errors.append(f"{node_id}: title must be a non-empty string")
                    if metadata.get("kind") not in KINDS:
                        errors.append(f"{node_id}: unsupported kind")
                    if metadata.get("status") not in STATUSES:
                        errors.append(f"{node_id}: unsupported status")
                    try:
                        self._validate_scope(str(metadata.get("scope", "")))
                    except GraphError as error:
                        errors.append(f"{node_id}: {error}")
                    for key in ("aliases", "sources"):
                        values = metadata.get(key)
                        if not isinstance(values, list) or not all(
                            isinstance(value, str) for value in values
                        ):
                            errors.append(f"{node_id}: {key} must be a list of strings")
                    if not _section(body, "핵심"):
                        errors.append(f"{node_id}: 핵심 section cannot be empty")

                edges = self._load_edges()
                seen: set[tuple[str, str, str]] = set()
                linked: set[str] = set()
                for index, edge in enumerate(edges):
                    source = edge.get("from")
                    target = edge.get("to")
                    relation = edge.get("relation")
                    reason = edge.get("reason")
                    if source not in node_ids:
                        errors.append(f"edge {index}: missing source node {source}")
                    if target not in node_ids:
                        errors.append(f"edge {index}: missing target node {target}")
                    if source == target:
                        errors.append(f"edge {index}: self-links are not allowed")
                    if relation not in RELATIONS:
                        errors.append(f"edge {index}: unsupported relation {relation}")
                    if not isinstance(reason, str) or not reason.strip():
                        errors.append(f"edge {index}: reason must be a non-empty string")
                    key = self._edge_key(edge)
                    if key in seen:
                        errors.append(f"edge {index}: duplicate edge")
                    seen.add(key)
                    if isinstance(source, str):
                        linked.add(source)
                    if isinstance(target, str):
                        linked.add(target)

                expected_unlinked = [node_id for node_id in node_ids if node_id not in linked]
                if knowledge.get("unlinked") != expected_unlinked:
                    errors.append("knowledge.unlinked does not match nodes with zero edges")
                next_id = knowledge.get("next_id")
                if not isinstance(next_id, int) or next_id < 0:
                    errors.append("knowledge.next_id must be a non-negative integer")
                elif node_ids:
                    highest = max(int(NODE_ID_PATTERN.fullmatch(node_id).group(1)) for node_id in node_ids)
                    if next_id <= highest:
                        errors.append("knowledge.next_id must exceed every node ID")
                return errors
        except (GraphError, yaml.YAMLError) as error:
            return [str(error)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd() / ".adhd",
        help="Directory containing state.yaml and knowledge/.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--graph-id", default="workspace")
    init.add_argument("--kind", choices=("workspace",), default="workspace")
    init.add_argument("--name", default="Workspace knowledge")
    init.add_argument("--default-scope", default="common")

    create = commands.add_parser("create")
    create.add_argument("title")
    create.add_argument("--kind", choices=KINDS, required=True)
    create.add_argument("--core", required=True)
    create.add_argument("--content", default="")
    create.add_argument("--memo", default="")
    create.add_argument("--alias", action="append", default=[])
    create.add_argument("--source", action="append", default=[])
    create.add_argument("--status", choices=STATUSES, default="active")
    create.add_argument("--scope")

    update = commands.add_parser("update")
    update.add_argument("id")
    update.add_argument("--title")
    update.add_argument("--kind", choices=KINDS)
    update.add_argument("--core")
    update.add_argument("--content")
    update.add_argument("--memo")
    update.add_argument("--alias", action="append")
    update.add_argument("--source", action="append")
    update.add_argument("--status", choices=STATUSES)
    update.add_argument("--scope")

    link = commands.add_parser("link")
    link.add_argument("source")
    link.add_argument("relation", choices=RELATIONS)
    link.add_argument("target")
    link.add_argument("--reason", required=True)

    unlink = commands.add_parser("unlink")
    unlink.add_argument("source")
    unlink.add_argument("relation", choices=RELATIONS)
    unlink.add_argument("target")

    show = commands.add_parser("show")
    show.add_argument("id")

    listing = commands.add_parser("list")
    listing.add_argument("--kind", choices=KINDS)
    listing.add_argument("--status", choices=STATUSES)

    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)

    neighbors = commands.add_parser("neighbors")
    neighbors.add_argument("id")
    neighbors.add_argument("--hops", type=int, default=1)
    neighbors.add_argument("--relation", choices=RELATIONS)

    commands.add_parser("status")
    commands.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    store = GraphStore(arguments.root)
    try:
        if arguments.command == "init":
            store.initialize(
                graph_id=arguments.graph_id,
                kind=arguments.kind,
                name=arguments.name,
                default_scope=arguments.default_scope,
            )
            print(_dump_yaml(store.status()).rstrip())
        elif arguments.command == "create":
            print(
                store.create_node(
                    title=arguments.title,
                    kind=arguments.kind,
                    core=arguments.core,
                    content=arguments.content,
                    memo=arguments.memo,
                    aliases=arguments.alias,
                    sources=arguments.source,
                    status=arguments.status,
                    scope=arguments.scope,
                )
            )
        elif arguments.command == "update":
            print(
                store.update_node(
                    arguments.id,
                    title=arguments.title,
                    kind=arguments.kind,
                    core=arguments.core,
                    content=arguments.content,
                    memo=arguments.memo,
                    aliases=arguments.alias,
                    sources=arguments.source,
                    status=arguments.status,
                    scope=arguments.scope,
                )
            )
        elif arguments.command == "link":
            print(
                _dump_yaml(
                    store.link(
                        arguments.source,
                        arguments.relation,
                        arguments.target,
                        reason=arguments.reason,
                    )
                ).rstrip()
            )
        elif arguments.command == "unlink":
            store.unlink(arguments.source, arguments.relation, arguments.target)
            print("ok")
        elif arguments.command == "show":
            print(store.show(arguments.id), end="")
        elif arguments.command == "list":
            print(
                _dump_yaml(
                    store.list_nodes(kind=arguments.kind, status=arguments.status)
                ).rstrip()
            )
        elif arguments.command == "search":
            print(_dump_yaml(store.search(arguments.query, limit=arguments.limit)).rstrip())
        elif arguments.command == "neighbors":
            print(
                _dump_yaml(
                    store.neighbors(
                        arguments.id,
                        hops=arguments.hops,
                        relation=arguments.relation,
                    )
                ).rstrip()
            )
        elif arguments.command == "status":
            print(_dump_yaml(store.status()).rstrip())
        elif arguments.command == "validate":
            errors = store.validate()
            if errors:
                print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
                return 1
            print("ok")
        return 0
    except (GraphError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
