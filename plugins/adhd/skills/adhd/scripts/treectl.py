#!/usr/bin/env python3
"""Deterministic ordered project-tree store for the ADHD workflow."""

from __future__ import annotations

import argparse
import fcntl
import re
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

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
NODE_ID_PATTERN = re.compile(r"^p(\d{6})$")
KNOWLEDGE_ID_PATTERN = re.compile(r"^k\d{6}$")
PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MATURITIES = ("stub", "defined", "realized", "verified")
PROFILES = ("generic", "paper", "slides", "web", "game", "plugin")


class TreeError(RuntimeError):
    """Raised when the persisted project tree violates an invariant."""


def format_node_id(value: int) -> str:
    return f"p{value:0{ID_WIDTH}d}"


def normalize_node_id(value: str | int) -> str:
    if isinstance(value, int):
        if value < 0:
            raise TreeError("Project node IDs cannot be negative.")
        return format_node_id(value)
    text = str(value).strip().lower()
    if text.isdigit():
        return format_node_id(int(text))
    if NODE_ID_PATTERN.fullmatch(text):
        return text
    raise TreeError("Project node ID must look like p000000 or 0.")


def _split_document(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise TreeError("Project node must begin with YAML frontmatter.")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise TreeError("Project node frontmatter is not closed with '---'.") from error
    metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
    if not isinstance(metadata, dict):
        raise TreeError("Project node frontmatter must be a mapping.")
    return metadata, "\n".join(lines[closing + 1 :]).lstrip("\n")


class TreeStore:
    def __init__(
        self, root: Path | str, knowledge_root: Path | str | None = None
    ):
        self.root = Path(root).resolve()
        self.project_dir = self.root / "project"
        self.lock_path = self.project_dir / ".treectl.lock"
        self.nodes_dir = self.project_dir / "nodes"
        self.tree_path = self.project_dir / "tree.yaml"
        self.knowledge_root = (
            Path(knowledge_root).resolve() if knowledge_root is not None else None
        )

    @contextmanager
    def _raw_lock(self):
        self.project_dir.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def _read_yaml(path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def _ensure_unlocked(self) -> None:
        self.nodes_dir.mkdir(parents=True, exist_ok=True)

    def ensure(self) -> None:
        with self.locked():
            return

    def _load_tree(self) -> dict[str, Any]:
        if not self.tree_path.exists():
            raise TreeError("Project tree is not initialized.")
        tree = self._read_yaml(self.tree_path, default={})
        if not isinstance(tree, dict):
            raise TreeError("project/tree.yaml must be a mapping.")
        return tree

    def _save_tree(self, tree: dict[str, Any]) -> None:
        _atomic_write_text(self.tree_path, _dump_yaml(tree))

    def _node_path(self, node_id: str | int) -> Path:
        return self.nodes_dir / f"{normalize_node_id(node_id)}.md"

    def _node_ids(self) -> list[str]:
        return sorted(
            path.stem
            for path in self.nodes_dir.glob("p*.md")
            if NODE_ID_PATTERN.fullmatch(path.stem)
        )

    def _read_node(self, node_id: str | int) -> tuple[str, dict[str, Any], str]:
        normalized = normalize_node_id(node_id)
        path = self._node_path(normalized)
        if not path.exists():
            raise TreeError(f"Project node {normalized} does not exist.")
        metadata, body = _split_document(path.read_text(encoding="utf-8"))
        return normalized, metadata, body

    def _reserve_id(self, tree: dict[str, Any]) -> str:
        value = tree.get("next_id")
        if not isinstance(value, int) or value < 0:
            raise TreeError("tree next_id must be a non-negative integer.")
        tree["next_id"] = value + 1
        self._save_tree(tree)
        return format_node_id(value)

    def _validated_knowledge(self, values: Iterable[str]) -> list[str]:
        result = _clean_list(value.lower() for value in values)
        if result and self.knowledge_root is None:
            raise TreeError(
                "Knowledge references require the ADHD runtime path via --knowledge-root."
            )
        for node_id in result:
            if not KNOWLEDGE_ID_PATTERN.fullmatch(node_id):
                raise TreeError(f"Knowledge reference has an invalid ID: {node_id}")
            if not (
                self.knowledge_root / "knowledge" / "nodes" / f"{node_id}.md"
            ).exists():
                raise TreeError(f"Knowledge reference does not exist: {node_id}")
        return result

    def _validated_artifacts(self, values: Iterable[str]) -> list[str]:
        result = _clean_list(values)
        for artifact in result:
            path = Path(artifact)
            if path.is_absolute() or ".." in path.parts:
                raise TreeError(
                    f"Artifact path must remain inside the project: {artifact}"
                )
            if not (self.root / path).exists():
                raise TreeError(f"Artifact does not exist: {artifact}")
        return result

    @staticmethod
    def _node_document(
        *,
        title: str,
        maturity: str,
        knowledge: list[str],
        artifacts: list[str],
        purpose: str,
        specification: str,
        acceptance: str,
        memo: str,
    ) -> str:
        body = f"""## 역할

{purpose.strip()}

## 명세

{specification.strip()}

## 완료 조건

{acceptance.strip()}

## 메모

{memo.strip() if memo.strip() else '없음'}
"""
        return _join_document(
            {
                "title": title.strip(),
                "maturity": maturity,
                "knowledge": knowledge,
                "artifacts": artifacts,
            },
            body,
        )

    @staticmethod
    def _check_text_fields(
        *, title: str, purpose: str, specification: str, acceptance: str
    ) -> None:
        if not all(
            value.strip() for value in (title, purpose, specification, acceptance)
        ):
            raise TreeError(
                "Project node title, purpose, specification, and acceptance cannot be empty."
            )

    @staticmethod
    def _insert(values: list[str], value: str, index: int | None) -> None:
        if index is None:
            values.append(value)
            return
        if index < 0 or index > len(values):
            raise TreeError(f"Child index must be between 0 and {len(values)}.")
        values.insert(index, value)

    @staticmethod
    def _parent_map(tree: dict[str, Any]) -> dict[str, str]:
        parents: dict[str, str] = {}
        children = tree.get("children", {})
        if not isinstance(children, dict):
            raise TreeError("tree children must be a mapping.")
        for parent, values in children.items():
            if not isinstance(values, list):
                raise TreeError(f"Children of {parent} must be a list.")
            for child in values:
                if child in parents:
                    raise TreeError(f"Project node {child} has more than one parent.")
                parents[child] = parent
        return parents

    @staticmethod
    def _collect_subtree(tree: dict[str, Any], root: str) -> set[str]:
        children = tree.get("children", {})
        found: set[str] = set()
        pending = [root]
        while pending:
            current = pending.pop()
            if current in found:
                raise TreeError(f"Cycle detected at project node {current}.")
            found.add(current)
            pending.extend(children.get(current, []))
        return found

    def _active_ids(self, tree: dict[str, Any]) -> set[str]:
        root = tree.get("root")
        if not isinstance(root, str):
            raise TreeError("Project tree root is invalid.")
        return self._collect_subtree(tree, root)

    def initialize(
        self,
        *,
        title: str,
        project_id: str,
        profile: str,
        purpose: str,
        specification: str,
        acceptance: str,
        memo: str = "",
        maturity: str = "defined",
        knowledge: list[str] | None = None,
        artifacts: list[str] | None = None,
    ) -> str:
        self._check_text_fields(
            title=title,
            purpose=purpose,
            specification=specification,
            acceptance=acceptance,
        )
        if not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise TreeError("Project ID must use lowercase letters, digits, and hyphens.")
        if profile not in PROFILES:
            raise TreeError(f"Unsupported project profile: {profile}")
        if maturity not in MATURITIES:
            raise TreeError(f"Unsupported project maturity: {maturity}")
        with self.locked():
            if self.tree_path.exists():
                raise TreeError("Project tree is already initialized.")
            knowledge_ids = self._validated_knowledge(knowledge or [])
            artifact_paths = self._validated_artifacts(artifacts or [])
            node_id = format_node_id(0)
            _atomic_write_text(
                self._node_path(node_id),
                self._node_document(
                    title=title,
                    maturity=maturity,
                    knowledge=knowledge_ids,
                    artifacts=artifact_paths,
                    purpose=purpose,
                    specification=specification,
                    acceptance=acceptance,
                    memo=memo,
                ),
            )
            self._save_tree(
                {
                    "schema_version": 2,
                    "project_id": project_id,
                    "profile": profile,
                    "next_id": 1,
                    "root": node_id,
                    "children": {node_id: []},
                    "archived": [],
                }
            )
            return node_id

    def add_node(
        self,
        parent: str | int,
        *,
        title: str,
        purpose: str,
        specification: str,
        acceptance: str,
        memo: str = "",
        maturity: str = "stub",
        knowledge: list[str] | None = None,
        artifacts: list[str] | None = None,
        index: int | None = None,
    ) -> str:
        self._check_text_fields(
            title=title,
            purpose=purpose,
            specification=specification,
            acceptance=acceptance,
        )
        if maturity not in MATURITIES:
            raise TreeError(f"Unsupported project maturity: {maturity}")
        parent_id = normalize_node_id(parent)
        with self.locked():
            tree = self._load_tree()
            if parent_id not in self._active_ids(tree):
                raise TreeError("New nodes require an active parent.")
            knowledge_ids = self._validated_knowledge(knowledge or [])
            artifact_paths = self._validated_artifacts(artifacts or [])
            node_id = self._reserve_id(tree)
            _atomic_write_text(
                self._node_path(node_id),
                self._node_document(
                    title=title,
                    maturity=maturity,
                    knowledge=knowledge_ids,
                    artifacts=artifact_paths,
                    purpose=purpose,
                    specification=specification,
                    acceptance=acceptance,
                    memo=memo,
                ),
            )
            tree["children"][node_id] = []
            self._insert(tree["children"][parent_id], node_id, index)
            self._save_tree(tree)
            return node_id

    def update_node(
        self,
        node_id: str | int,
        *,
        title: str | None = None,
        maturity: str | None = None,
        knowledge: list[str] | None = None,
        artifacts: list[str] | None = None,
        purpose: str | None = None,
        specification: str | None = None,
        acceptance: str | None = None,
        memo: str | None = None,
    ) -> str:
        if title is not None and not title.strip():
            raise TreeError("Project node title cannot be empty.")
        if maturity is not None and maturity not in MATURITIES:
            raise TreeError(f"Unsupported project maturity: {maturity}")
        for label, value in (
            ("purpose", purpose),
            ("specification", specification),
            ("acceptance", acceptance),
        ):
            if value is not None and not value.strip():
                raise TreeError(f"Project node {label} cannot be empty.")
        with self.locked():
            normalized, metadata, body = self._read_node(node_id)
            if title is not None:
                metadata["title"] = title.strip()
            if maturity is not None:
                metadata["maturity"] = maturity
            if knowledge is not None:
                metadata["knowledge"] = self._validated_knowledge(knowledge)
            if artifacts is not None:
                metadata["artifacts"] = self._validated_artifacts(artifacts)
            if purpose is not None:
                body = _replace_section(body, "역할", purpose)
            if specification is not None:
                body = _replace_section(body, "명세", specification)
            if acceptance is not None:
                body = _replace_section(body, "완료 조건", acceptance)
            if memo is not None:
                body = _replace_section(body, "메모", memo or "없음")
            _atomic_write_text(self._node_path(normalized), _join_document(metadata, body))
            return normalized

    def move(self, node_id: str | int, parent: str | int, *, index: int | None = None) -> None:
        normalized = normalize_node_id(node_id)
        parent_id = normalize_node_id(parent)
        with self.locked():
            tree = self._load_tree()
            active = self._active_ids(tree)
            if normalized == tree.get("root"):
                raise TreeError("The project root cannot be moved.")
            if normalized not in active or parent_id not in active:
                raise TreeError("Only active project nodes can be moved.")
            if parent_id in self._collect_subtree(tree, normalized):
                raise TreeError("A project node cannot move below its own subtree.")
            parents = self._parent_map(tree)
            old_parent = parents.get(normalized)
            if old_parent is None:
                raise TreeError(f"Project node {normalized} has no parent.")
            tree["children"][old_parent].remove(normalized)
            self._insert(tree["children"][parent_id], normalized, index)
            self._save_tree(tree)

    def archive(self, node_id: str | int) -> None:
        normalized = normalize_node_id(node_id)
        with self.locked():
            tree = self._load_tree()
            if normalized == tree.get("root"):
                raise TreeError("The project root cannot be archived.")
            if normalized not in self._active_ids(tree):
                raise TreeError("Only an active project node can be archived.")
            parents = self._parent_map(tree)
            parent = parents.get(normalized)
            if parent is None:
                raise TreeError(f"Project node {normalized} has no parent.")
            tree["children"][parent].remove(normalized)
            tree["archived"].append(normalized)
            self._save_tree(tree)

    def restore(
        self, node_id: str | int, parent: str | int, *, index: int | None = None
    ) -> None:
        normalized = normalize_node_id(node_id)
        parent_id = normalize_node_id(parent)
        with self.locked():
            tree = self._load_tree()
            if normalized not in tree.get("archived", []):
                raise TreeError("Only an archived subtree root can be restored.")
            if parent_id not in self._active_ids(tree):
                raise TreeError("Archived nodes require an active restore parent.")
            tree["archived"].remove(normalized)
            self._insert(tree["children"][parent_id], normalized, index)
            self._save_tree(tree)

    def recover(self) -> list[str]:
        """Preserve interrupted, unreachable node files as archived subtree roots."""
        with self.locked():
            tree = self._load_tree()
            node_ids = set(self._node_ids())
            children = tree.get("children")
            if not isinstance(children, dict):
                raise TreeError("tree children must be a mapping before recovery.")
            referenced = set(children)
            for values in children.values():
                if not isinstance(values, list):
                    raise TreeError("tree child lists must be valid before recovery.")
                referenced.update(values)
            missing_files = referenced.difference(node_ids)
            if missing_files:
                raise TreeError(
                    f"Cannot recover references with missing files: {sorted(missing_files)}"
                )

            recovered: list[str] = []
            known = set()
            if isinstance(tree.get("root"), str):
                known.update(self._collect_subtree(tree, tree["root"]))
            for archived_root in tree.get("archived", []):
                known.update(self._collect_subtree(tree, archived_root))
            for node_id in sorted(node_ids.difference(known)):
                children.setdefault(node_id, [])
                tree["archived"].append(node_id)
                recovered.append(node_id)

            next_id = tree.get("next_id")
            if node_ids:
                required = max(
                    int(NODE_ID_PATTERN.fullmatch(node_id).group(1))
                    for node_id in node_ids
                ) + 1
                if not isinstance(next_id, int) or next_id < required:
                    tree["next_id"] = required
            self._save_tree(tree)
            return recovered

    def show(self, node_id: str | int) -> str:
        with self.locked():
            normalized = normalize_node_id(node_id)
            path = self._node_path(normalized)
            if not path.exists():
                raise TreeError(f"Project node {normalized} does not exist.")
            return path.read_text(encoding="utf-8")

    def flatten(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self.locked():
            tree = self._load_tree()
            result: list[dict[str, Any]] = []

            def visit(node_id: str, depth: int, archived: bool) -> None:
                _, metadata, body = self._read_node(node_id)
                result.append(
                    {
                        "id": node_id,
                        "depth": depth,
                        "title": metadata.get("title"),
                        "maturity": metadata.get("maturity"),
                        "archived": archived,
                        "purpose": _section(body, "역할"),
                    }
                )
                for child in tree["children"].get(node_id, []):
                    visit(child, depth + 1, archived)

            visit(tree["root"], 0, False)
            if include_archived:
                for archived_root in tree.get("archived", []):
                    visit(archived_root, 0, True)
            return result

    def outline(self, *, include_archived: bool = False) -> str:
        lines = []
        for node in self.flatten(include_archived=include_archived):
            marker = " (archived)" if node["archived"] else ""
            lines.append(
                f"{'  ' * int(node['depth'])}- {node['id']} {node['title']} "
                f"[{node['maturity']}]{marker}"
            )
        return "\n".join(lines) + ("\n" if lines else "")

    def status(self) -> dict[str, Any]:
        with self.locked():
            tree = self._load_tree()
            active = self._active_ids(tree)
            archived_ids: set[str] = set()
            for archived_root in tree.get("archived", []):
                archived_ids.update(self._collect_subtree(tree, archived_root))
            maturities: Counter[str] = Counter()
            for node_id in active:
                _, metadata, _ = self._read_node(node_id)
                maturities[str(metadata.get("maturity", "<unknown>"))] += 1
            _, root_metadata, _ = self._read_node(tree["root"])
            return {
                "project_id": tree.get("project_id"),
                "profile": tree.get("profile"),
                "root": f"{tree['root']} {root_metadata.get('title', '<untitled>')}",
                "active_nodes": len(active),
                "archived_nodes": len(archived_ids),
                "maturity": {
                    value: maturities.get(value, 0) for value in MATURITIES
                },
            }

    def validate(self) -> list[str]:
        try:
            with self.locked():
                if not self.tree_path.exists():
                    return ["Project tree is not initialized."]
                errors: list[str] = []
                tree = self._load_tree()
                node_ids = set(self._node_ids())
                if tree.get("schema_version") != 2:
                    errors.append("tree schema_version must be 2")
                if not isinstance(tree.get("project_id"), str) or not PROJECT_ID_PATTERN.fullmatch(
                    tree.get("project_id", "")
                ):
                    errors.append("tree project_id is invalid")
                if tree.get("profile") not in PROFILES:
                    errors.append("tree profile is invalid")
                root = tree.get("root")
                if root not in node_ids:
                    errors.append("tree root must reference an existing project node")
                archived = tree.get("archived")
                if not isinstance(archived, list) or not all(
                    isinstance(value, str) for value in archived
                ):
                    errors.append("tree archived must be a list of project IDs")
                    archived = []
                elif len(archived) != len(set(archived)):
                    errors.append("tree archived contains duplicate roots")
                children = tree.get("children")
                if not isinstance(children, dict):
                    return errors + ["tree children must be a mapping"]
                if set(children) != node_ids:
                    errors.append("tree children keys must match all project node IDs")

                parent_counts: Counter[str] = Counter()
                for parent, values in children.items():
                    if parent not in node_ids:
                        errors.append(f"children mapping has missing parent {parent}")
                    if not isinstance(values, list):
                        errors.append(f"children of {parent} must be a list")
                        continue
                    if len(values) != len(set(values)):
                        errors.append(f"children of {parent} contains duplicates")
                    for child in values:
                        if child not in node_ids:
                            errors.append(f"{parent} references missing child {child}")
                        elif child == parent:
                            errors.append(f"{parent} cannot contain itself")
                        parent_counts[child] += 1
                for node_id, count in parent_counts.items():
                    if count > 1:
                        errors.append(f"{node_id} has more than one parent")
                for root_id in [root, *archived]:
                    if root_id in parent_counts:
                        errors.append(f"subtree root {root_id} must not have a parent")

                visited: set[str] = set()

                def visit(node_id: str, ancestors: set[str]) -> None:
                    if node_id in ancestors:
                        errors.append(f"cycle detected at {node_id}")
                        return
                    if node_id in visited:
                        errors.append(f"{node_id} is reachable through multiple paths")
                        return
                    if node_id not in node_ids:
                        return
                    visited.add(node_id)
                    for child in children.get(node_id, []):
                        visit(child, ancestors | {node_id})

                if isinstance(root, str):
                    visit(root, set())
                for archived_root in archived:
                    visit(archived_root, set())
                missing = node_ids.difference(visited)
                if missing:
                    errors.append(f"unreachable project nodes: {sorted(missing)}")

                for node_id in sorted(node_ids):
                    try:
                        _, metadata, body = self._read_node(node_id)
                    except TreeError as error:
                        errors.append(f"{node_id}: {error}")
                        continue
                    if not isinstance(metadata.get("title"), str) or not metadata["title"].strip():
                        errors.append(f"{node_id}: title must be a non-empty string")
                    if metadata.get("maturity") not in MATURITIES:
                        errors.append(f"{node_id}: unsupported maturity")
                    knowledge = metadata.get("knowledge")
                    if not isinstance(knowledge, list) or not all(
                        isinstance(value, str) and KNOWLEDGE_ID_PATTERN.fullmatch(value)
                        for value in knowledge
                    ):
                        errors.append(f"{node_id}: knowledge must be a list of valid IDs")
                    else:
                        if len(knowledge) != len(set(knowledge)):
                            errors.append(f"{node_id}: duplicate knowledge references")
                        for value in knowledge:
                            if self.knowledge_root is None or not (
                                self.knowledge_root
                                / "knowledge"
                                / "nodes"
                                / f"{value}.md"
                            ).exists():
                                errors.append(f"{node_id}: dangling knowledge reference {value}")
                    artifacts = metadata.get("artifacts")
                    if not isinstance(artifacts, list) or not all(
                        isinstance(value, str) for value in artifacts
                    ):
                        errors.append(f"{node_id}: artifacts must be a list of paths")
                    else:
                        if len(artifacts) != len(set(artifacts)):
                            errors.append(f"{node_id}: duplicate artifacts")
                        for artifact in artifacts:
                            path = Path(artifact)
                            if path.is_absolute() or ".." in path.parts:
                                errors.append(f"{node_id}: unsafe artifact path {artifact}")
                            elif not (self.root / path).exists():
                                errors.append(f"{node_id}: missing artifact {artifact}")
                    for section in ("역할", "명세", "완료 조건"):
                        if not _section(body, section):
                            errors.append(f"{node_id}: {section} section cannot be empty")

                next_id = tree.get("next_id")
                if not isinstance(next_id, int) or next_id < 0:
                    errors.append("tree next_id must be a non-negative integer")
                elif node_ids:
                    highest = max(
                        int(NODE_ID_PATTERN.fullmatch(node_id).group(1))
                        for node_id in node_ids
                    )
                    if next_id <= highest:
                        errors.append("tree next_id must exceed every project node ID")
                return errors
        except (TreeError, yaml.YAMLError) as error:
            return [str(error)]


def _add_node_arguments(parser: argparse.ArgumentParser, *, include_title: bool) -> None:
    if include_title:
        parser.add_argument("title")
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--spec", dest="specification", required=True)
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--memo", default="")
    parser.add_argument("--maturity", choices=MATURITIES, default="stub")
    parser.add_argument("--knowledge", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Managed project directory containing project/.",
    )
    parser.add_argument(
        "--knowledge-root",
        type=Path,
        help="ADHD runtime directory containing knowledge/.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("title")
    init.add_argument("--project-id", default="adhd")
    init.add_argument("--profile", choices=PROFILES, default="generic")
    _add_node_arguments(init, include_title=False)
    init.set_defaults(maturity="defined")

    add = commands.add_parser("add")
    add.add_argument("parent")
    _add_node_arguments(add, include_title=True)
    add.add_argument("--index", type=int)

    update = commands.add_parser("update")
    update.add_argument("id")
    update.add_argument("--title")
    update.add_argument("--purpose")
    update.add_argument("--spec", dest="specification")
    update.add_argument("--acceptance")
    update.add_argument("--memo")
    update.add_argument("--maturity", choices=MATURITIES)
    update.add_argument("--knowledge", action="append")
    update.add_argument("--artifact", action="append")

    move = commands.add_parser("move")
    move.add_argument("id")
    move.add_argument("parent")
    move.add_argument("--index", type=int)

    archive = commands.add_parser("archive")
    archive.add_argument("id")

    restore = commands.add_parser("restore")
    restore.add_argument("id")
    restore.add_argument("parent")
    restore.add_argument("--index", type=int)

    show = commands.add_parser("show")
    show.add_argument("id")

    outline = commands.add_parser("outline")
    outline.add_argument("--include-archived", action="store_true")

    flatten = commands.add_parser("flatten")
    flatten.add_argument("--include-archived", action="store_true")

    commands.add_parser("recover")
    commands.add_parser("status")
    commands.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    store = TreeStore(arguments.root, knowledge_root=arguments.knowledge_root)
    try:
        if arguments.command == "init":
            print(
                store.initialize(
                    title=arguments.title,
                    project_id=arguments.project_id,
                    profile=arguments.profile,
                    purpose=arguments.purpose,
                    specification=arguments.specification,
                    acceptance=arguments.acceptance,
                    memo=arguments.memo,
                    maturity=arguments.maturity,
                    knowledge=arguments.knowledge,
                    artifacts=arguments.artifact,
                )
            )
        elif arguments.command == "add":
            print(
                store.add_node(
                    arguments.parent,
                    title=arguments.title,
                    purpose=arguments.purpose,
                    specification=arguments.specification,
                    acceptance=arguments.acceptance,
                    memo=arguments.memo,
                    maturity=arguments.maturity,
                    knowledge=arguments.knowledge,
                    artifacts=arguments.artifact,
                    index=arguments.index,
                )
            )
        elif arguments.command == "update":
            print(
                store.update_node(
                    arguments.id,
                    title=arguments.title,
                    maturity=arguments.maturity,
                    knowledge=arguments.knowledge,
                    artifacts=arguments.artifact,
                    purpose=arguments.purpose,
                    specification=arguments.specification,
                    acceptance=arguments.acceptance,
                    memo=arguments.memo,
                )
            )
        elif arguments.command == "move":
            store.move(arguments.id, arguments.parent, index=arguments.index)
            print("ok")
        elif arguments.command == "archive":
            store.archive(arguments.id)
            print("ok")
        elif arguments.command == "restore":
            store.restore(arguments.id, arguments.parent, index=arguments.index)
            print("ok")
        elif arguments.command == "show":
            print(store.show(arguments.id), end="")
        elif arguments.command == "outline":
            print(
                store.outline(include_archived=arguments.include_archived), end=""
            )
        elif arguments.command == "flatten":
            print(
                _dump_yaml(
                    store.flatten(include_archived=arguments.include_archived)
                ).rstrip()
            )
        elif arguments.command == "recover":
            print(_dump_yaml({"recovered": store.recover()}).rstrip())
        elif arguments.command == "status":
            print(_dump_yaml(store.status()).rstrip())
        elif arguments.command == "validate":
            errors = store.validate()
            if errors:
                print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
                return 1
            print("ok")
        return 0
    except (TreeError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
