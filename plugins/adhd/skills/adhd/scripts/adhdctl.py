#!/usr/bin/env python3
"""Workspace control, unresolved registry, validation, and migration for ADHD."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from _storage import (
    _atomic_write_text,
    _dump_yaml,
    _join_document,
    _replace_section,
    _clean_list as _dedupe,
)

from graphctl import GraphError, GraphStore
from taskctl import StateError, TaskStore
from treectl import TreeError, TreeStore


CONTROL_KINDS = ("principle", "policy", "constraint", "assumption", "decision")
CONTROL_STATUSES = ("active", "superseded", "archived")
UNRESOLVED_DOMAINS = ("knowledge", "control")
PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CONTROL_ID_PATTERN = re.compile(r"^c(\d{6})$")
UNRESOLVED_ID_PATTERN = re.compile(r"^u(\d{6})$")
KNOWLEDGE_REF_PATTERN = re.compile(r"^knowledge:k\d{6}$")
CONTROL_REF_PATTERN = re.compile(r"^control:c\d{6}$")
PROJECT_REF_PATTERN = re.compile(
    r"^project:[a-z0-9][a-z0-9-]*:p\d{6}$"
)


class WorkspaceError(RuntimeError):
    """Raised when ADHD workspace metadata violates an invariant."""


def _read_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _split_document(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise WorkspaceError("Markdown document must begin with YAML frontmatter.")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise WorkspaceError("Markdown frontmatter is not closed with '---'.") from error
    metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
    if not isinstance(metadata, dict):
        raise WorkspaceError("Markdown frontmatter must be a mapping.")
    return metadata, "\n".join(lines[closing + 1 :]).lstrip("\n")


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise WorkspaceError(f"Path must remain inside the workspace: {value}")
    return path


class WorkspaceStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.workspace_root = self.root.parent
        self.lock_path = self.root / ".taskctl.lock"
        self.control_dir = self.root / "control"
        self.control_entries_dir = self.control_dir / "entries"
        self.control_index_path = self.control_dir / "index.yaml"
        self.projects_path = self.control_dir / "projects.yaml"
        self.unresolved_dir = self.root / "unresolved"
        self.unresolved_items_dir = self.unresolved_dir / "items"
        self.unresolved_index_path = self.unresolved_dir / "index.yaml"

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _ensure_unlocked(self) -> None:
        self.control_entries_dir.mkdir(parents=True, exist_ok=True)
        self.unresolved_items_dir.mkdir(parents=True, exist_ok=True)
        if not self.control_index_path.exists():
            _atomic_write_text(
                self.control_index_path,
                _dump_yaml({"schema_version": 1, "next_id": 0, "entries": []}),
            )
        if not self.projects_path.exists():
            _atomic_write_text(
                self.projects_path,
                _dump_yaml({"schema_version": 1, "projects": {}}),
            )
        if not self.unresolved_index_path.exists():
            _atomic_write_text(
                self.unresolved_index_path,
                _dump_yaml({"schema_version": 1, "next_id": 0, "open": []}),
            )

    def ensure(self) -> None:
        TaskStore(self.root).ensure()
        with self.locked():
            self._ensure_unlocked()

    def initialize(self) -> None:
        """Create all workspace stores without replacing existing metadata."""
        self.ensure()
        GraphStore(self.root).ensure()

    def _load_control(self) -> dict[str, Any]:
        value = _read_yaml(self.control_index_path, {})
        if not isinstance(value, dict):
            raise WorkspaceError("control/index.yaml must be a mapping.")
        return value

    def _load_projects(self) -> dict[str, Any]:
        value = _read_yaml(self.projects_path, {})
        if not isinstance(value, dict):
            raise WorkspaceError("control/projects.yaml must be a mapping.")
        return value

    def _load_unresolved(self) -> dict[str, Any]:
        value = _read_yaml(self.unresolved_index_path, {})
        if not isinstance(value, dict):
            raise WorkspaceError("unresolved/index.yaml must be a mapping.")
        return value

    def create_control(
        self,
        *,
        title: str,
        kind: str,
        statement: str,
        rationale: str = "",
        sources: list[str] | None = None,
        supersedes: list[str] | None = None,
    ) -> str:
        if kind not in CONTROL_KINDS:
            raise WorkspaceError(f"Unsupported control kind: {kind}")
        if not title.strip() or not statement.strip():
            raise WorkspaceError("Control title and statement cannot be empty.")
        with self.locked():
            self._ensure_unlocked()
            index = self._load_control()
            value = index.get("next_id")
            if not isinstance(value, int) or value < 0:
                raise WorkspaceError("control next_id must be a non-negative integer.")
            control_id = f"c{value:06d}"
            superseded = _dedupe(value.lower() for value in supersedes or [])
            for reference in superseded:
                if not CONTROL_ID_PATTERN.fullmatch(reference):
                    raise WorkspaceError(f"Invalid superseded control ID: {reference}")
                path = self.control_entries_dir / f"{reference}.md"
                if not path.exists():
                    raise WorkspaceError(f"Superseded control entry does not exist: {reference}")
            metadata = {
                "title": title.strip(),
                "kind": kind,
                "status": "active",
                "sources": _dedupe(sources or []),
                "supersedes": superseded,
            }
            body = (
                f"## 내용\n\n{statement.strip()}\n\n"
                f"## 근거\n\n{rationale.strip() if rationale.strip() else '없음'}\n"
            )
            index["next_id"] = value + 1
            index.setdefault("entries", []).append(control_id)
            _atomic_write_text(self.control_index_path, _dump_yaml(index))
            _atomic_write_text(
                self.control_entries_dir / f"{control_id}.md",
                _join_document(metadata, body),
            )
            for reference in superseded:
                path = self.control_entries_dir / f"{reference}.md"
                old_metadata, old_body = _split_document(path.read_text(encoding="utf-8"))
                old_metadata["status"] = "superseded"
                _atomic_write_text(path, _join_document(old_metadata, old_body))
            return control_id

    def register_project(
        self, *, project_id: str, name: str, path: str, require_tree: bool = True
    ) -> None:
        if not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise WorkspaceError("Project ID must use lowercase letters, digits, and hyphens.")
        relative = _safe_relative_path(path)
        if relative.parts[0] == self.root.name:
            raise WorkspaceError("Managed projects must remain outside the ADHD runtime.")
        project_root = self.workspace_root / relative
        tree_path = project_root / "project" / "tree.yaml"
        if require_tree and not tree_path.exists():
            raise WorkspaceError(f"Registered project tree does not exist: {tree_path}")
        with self.locked():
            self._ensure_unlocked()
            registry = self._load_projects()
            projects = registry.setdefault("projects", {})
            if not isinstance(projects, dict):
                raise WorkspaceError("projects must be a mapping.")
            requested = {"name": name.strip(), "path": relative.as_posix(), "status": "active"}
            current = projects.get(project_id)
            if current is not None and current != requested:
                raise WorkspaceError(f"Project is already registered differently: {project_id}")
            projects[project_id] = requested
            _atomic_write_text(self.projects_path, _dump_yaml(registry))

    def create_unresolved(
        self,
        *,
        title: str,
        domain: str,
        question: str,
        reason: str,
        needed: str,
        refs: list[str] | None = None,
        task: int | None = None,
    ) -> str:
        if domain not in UNRESOLVED_DOMAINS:
            raise WorkspaceError(f"Unsupported unresolved domain: {domain}")
        if not all(value.strip() for value in (title, question, reason, needed)):
            raise WorkspaceError("Unresolved title, question, reason, and needed are required.")
        references = _dedupe(refs or [])
        self._validate_refs(references)
        self._validate_task_reference(task)
        with self.locked():
            self._ensure_unlocked()
            index = self._load_unresolved()
            value = index.get("next_id")
            if not isinstance(value, int) or value < 0:
                raise WorkspaceError("unresolved next_id must be a non-negative integer.")
            unresolved_id = f"u{value:06d}"
            metadata = {
                "title": title.strip(),
                "domain": domain,
                "status": "open",
                "refs": references,
                "task": task,
            }
            body = (
                f"## 미해결 내용\n\n{question.strip()}\n\n"
                f"## 현재 해결할 수 없는 이유\n\n{reason.strip()}\n\n"
                f"## 해결 조건\n\n{needed.strip()}\n\n"
                "## 해결\n\n미해결\n"
            )
            index["next_id"] = value + 1
            index.setdefault("open", []).append(unresolved_id)
            _atomic_write_text(self.unresolved_index_path, _dump_yaml(index))
            _atomic_write_text(
                self.unresolved_items_dir / f"{unresolved_id}.md",
                _join_document(metadata, body),
            )
            return unresolved_id

    def resolve_unresolved(self, unresolved_id: str, resolution: str) -> None:
        normalized = unresolved_id.strip().lower()
        if not UNRESOLVED_ID_PATTERN.fullmatch(normalized):
            raise WorkspaceError("Unresolved ID must look like u000000.")
        if not resolution.strip():
            raise WorkspaceError("Resolution cannot be empty.")
        with self.locked():
            self._ensure_unlocked()
            path = self.unresolved_items_dir / f"{normalized}.md"
            if not path.exists():
                raise WorkspaceError(f"Unresolved item does not exist: {normalized}")
            metadata, body = _split_document(path.read_text(encoding="utf-8"))
            metadata["status"] = "resolved"
            body = _replace_section(body, "해결", resolution)
            index = self._load_unresolved()
            index["open"] = [value for value in index.get("open", []) if value != normalized]
            _atomic_write_text(path, _join_document(metadata, body))
            _atomic_write_text(self.unresolved_index_path, _dump_yaml(index))

    def _validate_task_reference(self, task: int | None) -> None:
        if task is None:
            return
        if task < 0:
            raise WorkspaceError("Task reference cannot be negative.")
        name = f"{task:06d}.md"
        if not any(
            (self.root / directory / name).exists()
            for directory in ("task/open", "task/closed")
        ):
            raise WorkspaceError(f"Task reference does not exist: {task:06d}")

    def _validate_refs(self, refs: list[str]) -> None:
        projects = self._load_projects().get("projects", {}) if self.projects_path.exists() else {}
        for reference in refs:
            if KNOWLEDGE_REF_PATTERN.fullmatch(reference):
                node_id = reference.split(":", 1)[1]
                path = self.root / "knowledge" / "nodes" / f"{node_id}.md"
            elif CONTROL_REF_PATTERN.fullmatch(reference):
                control_id = reference.split(":", 1)[1]
                path = self.control_entries_dir / f"{control_id}.md"
            elif PROJECT_REF_PATTERN.fullmatch(reference):
                _, project_id, node_id = reference.split(":", 2)
                entry = projects.get(project_id) if isinstance(projects, dict) else None
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise WorkspaceError(f"Unresolved reference uses an unknown project: {reference}")
                path = self.workspace_root / entry["path"] / "project" / "nodes" / f"{node_id}.md"
            else:
                raise WorkspaceError(f"Unsupported unresolved reference: {reference}")
            if not path.exists():
                raise WorkspaceError(f"Unresolved reference does not exist: {reference}")

    def validate(self) -> list[str]:
        self.ensure()
        errors: list[str] = []
        try:
            control = self._load_control()
            if control.get("schema_version") != 1:
                errors.append("control schema_version must be 1")
            entries = control.get("entries")
            if not isinstance(entries, list) or not all(
                isinstance(value, str) and CONTROL_ID_PATTERN.fullmatch(value)
                for value in entries or []
            ):
                errors.append("control entries must be a list of control IDs")
                entries = []
            files = {path.stem for path in self.control_entries_dir.glob("c*.md")}
            if set(entries) != files:
                errors.append("control entries must match control files")
            next_id = control.get("next_id")
            if not isinstance(next_id, int) or next_id < 0:
                errors.append("control next_id must be a non-negative integer")
            elif files and next_id <= max(int(value[1:]) for value in files):
                errors.append("control next_id must exceed every control ID")
            for control_id in sorted(files):
                metadata, _ = _split_document(
                    (self.control_entries_dir / f"{control_id}.md").read_text(encoding="utf-8")
                )
                if metadata.get("kind") not in CONTROL_KINDS:
                    errors.append(f"{control_id}: unsupported control kind")
                if metadata.get("status") not in CONTROL_STATUSES:
                    errors.append(f"{control_id}: unsupported control status")

            registry = self._load_projects()
            if registry.get("schema_version") != 1:
                errors.append("project registry schema_version must be 1")
            projects = registry.get("projects")
            if not isinstance(projects, dict):
                errors.append("project registry projects must be a mapping")
                projects = {}
            for project_id, entry in projects.items():
                if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
                    errors.append(f"invalid registered project ID: {project_id}")
                    continue
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    errors.append(f"{project_id}: project registry entry is invalid")
                    continue
                try:
                    relative = _safe_relative_path(entry["path"])
                except WorkspaceError as error:
                    errors.append(f"{project_id}: {error}")
                    continue
                project_root = self.workspace_root / relative
                if self.root == project_root or self.root in project_root.parents:
                    errors.append(f"{project_id}: project must remain outside ADHD runtime")
                tree = _read_yaml(project_root / "project" / "tree.yaml", {})
                if not isinstance(tree, dict) or tree.get("project_id") != project_id:
                    errors.append(f"{project_id}: registered project tree is missing or mismatched")

            unresolved = self._load_unresolved()
            if unresolved.get("schema_version") != 1:
                errors.append("unresolved schema_version must be 1")
            open_ids = unresolved.get("open")
            if not isinstance(open_ids, list) or not all(
                isinstance(value, str) and UNRESOLVED_ID_PATTERN.fullmatch(value)
                for value in open_ids or []
            ):
                errors.append("unresolved open must be a list of unresolved IDs")
                open_ids = []
            unresolved_files = {
                path.stem for path in self.unresolved_items_dir.glob("u*.md")
            }
            next_unresolved = unresolved.get("next_id")
            if not isinstance(next_unresolved, int) or next_unresolved < 0:
                errors.append("unresolved next_id must be a non-negative integer")
            elif unresolved_files and next_unresolved <= max(
                int(value[1:]) for value in unresolved_files
            ):
                errors.append("unresolved next_id must exceed every unresolved ID")
            actual_open: set[str] = set()
            for unresolved_id in sorted(unresolved_files):
                metadata, _ = _split_document(
                    (self.unresolved_items_dir / f"{unresolved_id}.md").read_text(encoding="utf-8")
                )
                if metadata.get("domain") not in UNRESOLVED_DOMAINS:
                    errors.append(f"{unresolved_id}: unsupported unresolved domain")
                if metadata.get("status") not in ("open", "resolved"):
                    errors.append(f"{unresolved_id}: unsupported unresolved status")
                if metadata.get("status") == "open":
                    actual_open.add(unresolved_id)
                refs = metadata.get("refs", [])
                if not isinstance(refs, list) or not all(isinstance(value, str) for value in refs):
                    errors.append(f"{unresolved_id}: refs must be a list of strings")
                else:
                    try:
                        self._validate_refs(refs)
                    except WorkspaceError as error:
                        errors.append(f"{unresolved_id}: {error}")
                task = metadata.get("task")
                if task is not None and not isinstance(task, int):
                    errors.append(f"{unresolved_id}: task must be null or an integer")
                else:
                    try:
                        self._validate_task_reference(task)
                    except WorkspaceError as error:
                        errors.append(f"{unresolved_id}: {error}")
            if set(open_ids) != actual_open:
                errors.append("unresolved open IDs must match open item files")
        except (WorkspaceError, yaml.YAMLError) as error:
            errors.append(str(error))
        return errors

    def validate_all(self) -> list[str]:
        errors = [f"workspace: {value}" for value in self.validate()]
        errors.extend(f"task: {value}" for value in TaskStore(self.root).validate())
        if (self.root / "knowledge" / "graph.yaml").exists():
            errors.extend(f"knowledge: {value}" for value in GraphStore(self.root).validate())
        else:
            errors.append(
                "knowledge: workspace graph is missing; inspect existing records "
                "before running adhdctl.py init"
            )
        projects = self._load_projects().get("projects", {})
        if isinstance(projects, dict):
            for project_id, entry in projects.items():
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    project_root = self.workspace_root / entry["path"]
                    errors.extend(
                        f"project {project_id}: {value}"
                        for value in TreeStore(
                            project_root, knowledge_root=self.root
                        ).validate()
                    )
        return errors

    def status(self) -> dict[str, Any]:
        self.ensure()
        projects = self._load_projects().get("projects", {})
        control = self._load_control()
        unresolved = self._load_unresolved()
        return {
            "root": str(self.root),
            "task": TaskStore(self.root).snapshot(),
            "knowledge": GraphStore(self.root).status()
            if (self.root / "knowledge" / "graph.yaml").exists()
            else None,
            "control_entries": len(control.get("entries", [])),
            "projects": sorted(projects) if isinstance(projects, dict) else [],
            "unresolved_open": list(unresolved.get("open", [])),
        }


def _rewrite_frontmatter(path: Path, transform) -> None:
    metadata, body = _split_document(path.read_text(encoding="utf-8"))
    transform(metadata)
    _atomic_write_text(path, _join_document(metadata, body))


def _qualify_project_values(metadata: dict[str, Any], key: str, project_id: str) -> None:
    values = metadata.get(key, [])
    if isinstance(values, list):
        metadata[key] = [
            f"{project_id}:{value}" if isinstance(value, str) and re.fullmatch(r"p\d{6}", value) else value
            for value in values
        ]


def migrate_legacy(
    *,
    legacy_root: Path,
    adhd_root: Path,
    project_root: Path,
    project_id: str,
    project_name: str,
    backup_root: Path,
) -> None:
    legacy_root = legacy_root.resolve()
    adhd_root = adhd_root.resolve()
    project_root = project_root.resolve()
    backup_root = backup_root.resolve()
    workspace_root = adhd_root.parent
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise WorkspaceError("Project ID must use lowercase letters, digits, and hyphens.")
    if not legacy_root.is_dir() or not (legacy_root / "state.yaml").exists():
        raise WorkspaceError("Legacy ADHD root with state.yaml does not exist.")
    for destination in (adhd_root, project_root, backup_root):
        if destination.exists():
            raise WorkspaceError(f"Migration destination already exists: {destination}")
        if legacy_root == destination or legacy_root in destination.parents:
            raise WorkspaceError("Migration destinations must remain outside the legacy root.")
    if adhd_root == project_root or adhd_root in project_root.parents:
        raise WorkspaceError("Managed project must remain outside the ADHD runtime.")
    try:
        project_root.relative_to(workspace_root)
    except ValueError as error:
        raise WorkspaceError("Managed project must remain inside the workspace.") from error

    legacy_state = _read_yaml(legacy_root / "state.yaml", {})
    if not isinstance(legacy_state, dict):
        raise WorkspaceError("Legacy state.yaml must be a mapping.")
    required = {"next_id", "active", "stack", "queue", "waiting", "knowledge", "project"}
    if not required.issubset(legacy_state):
        raise WorkspaceError("Legacy state.yaml is not the supported schema 1 shape.")
    for required_path in ("tasks", "closed", "history", "knowledge", "project"):
        if not (legacy_root / required_path).exists():
            raise WorkspaceError(f"Legacy store is missing: {required_path}")

    backup_root.parent.mkdir(parents=True, exist_ok=True)
    project_root.parent.mkdir(parents=True, exist_ok=True)
    os.replace(legacy_root, backup_root)
    temporary_adhd = Path(tempfile.mkdtemp(prefix=".adhd-migrate-", dir=workspace_root))
    temporary_project = Path(tempfile.mkdtemp(prefix=".project-migrate-", dir=project_root.parent))
    try:
        for source_name, destination in (
            ("tasks", temporary_adhd / "task" / "open"),
            ("closed", temporary_adhd / "task" / "closed"),
            ("history", temporary_adhd / "history"),
            ("knowledge", temporary_adhd / "knowledge"),
        ):
            shutil.copytree(backup_root / source_name, destination, dirs_exist_ok=True)

        waiting = [value for value in legacy_state.get("waiting", []) if isinstance(value, int)]
        queue = [value for value in legacy_state.get("queue", []) if isinstance(value, int)]
        for task_id in waiting:
            if task_id not in queue and task_id != legacy_state.get("active"):
                queue.append(task_id)
        new_state = {
            "schema_version": 2,
            "next_id": legacy_state["next_id"],
            "active": legacy_state.get("active"),
            "stack": list(legacy_state.get("stack", [])),
            "queue": queue,
            "knowledge": legacy_state["knowledge"],
        }
        _atomic_write_text(temporary_adhd / "state.yaml", _dump_yaml(new_state))

        graph_path = temporary_adhd / "knowledge" / "graph.yaml"
        graph = _read_yaml(graph_path, {})
        if not isinstance(graph, dict):
            raise WorkspaceError("Legacy knowledge graph metadata is invalid.")
        graph.update(
            {
                "schema_version": 2,
                "graph_id": "workspace",
                "kind": "workspace",
                "name": "Workspace knowledge",
                "default_scope": "common",
            }
        )
        graph.setdefault("imports", [])
        _atomic_write_text(graph_path, _dump_yaml(graph))

        shutil.copytree(backup_root / "project", temporary_project / "project", dirs_exist_ok=True)
        runtime_names = {
            "state.yaml",
            "tasks",
            "closed",
            "history",
            "knowledge",
            "project",
            ".taskctl.lock",
            "__pycache__",
        }
        for source in backup_root.iterdir():
            if source.name in runtime_names:
                continue
            destination = temporary_project / source.name
            if source.is_dir():
                shutil.copytree(
                    source,
                    destination,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            else:
                shutil.copy2(source, destination)

        tree_path = temporary_project / "project" / "tree.yaml"
        tree = _read_yaml(tree_path, {})
        if not isinstance(tree, dict):
            raise WorkspaceError("Legacy project tree metadata is invalid.")
        tree["schema_version"] = 2
        tree["project_id"] = project_id
        project_state = legacy_state.get("project", {})
        tree["next_id"] = project_state.get("next_id", 0)
        _atomic_write_text(tree_path, _dump_yaml(tree))

        for path in (temporary_adhd / "task" / "open").glob("*.md"):
            _rewrite_frontmatter(path, lambda metadata: _qualify_project_values(metadata, "project", project_id))
        for path in (temporary_adhd / "task" / "closed").glob("*.md"):
            _rewrite_frontmatter(path, lambda metadata: _qualify_project_values(metadata, "project", project_id))
        for path in (temporary_adhd / "history").glob("*.md"):
            _rewrite_frontmatter(path, lambda metadata: _qualify_project_values(metadata, "affects", project_id))

        final_project_relative = project_root.relative_to(workspace_root).as_posix()
        workspace = WorkspaceStore(temporary_adhd)
        workspace._ensure_unlocked()
        registry = {
            "schema_version": 1,
            "projects": {
                project_id: {
                    "name": project_name,
                    "path": final_project_relative,
                    "status": "active",
                }
            },
        }
        _atomic_write_text(workspace.projects_path, _dump_yaml(registry))
        workspace.create_control(
            title="ADHD 런타임과 프로젝트의 분리",
            kind="decision",
            statement=(
                "ADHD는 task, knowledge, control, history, unresolved를 소유하고 "
                "프로젝트 트리와 산출물은 등록된 외부 프로젝트가 소유한다."
            ),
            rationale="여러 프로젝트를 하나의 실행 체계로 관리하고 코드와 가변 상태를 분리한다.",
            sources=["migration:v1-v2"],
        )
        for task_id in waiting:
            workspace.create_unresolved(
                title=f"이전 대기 작업 {task_id:06d}",
                domain="control",
                question="이 작업을 재개할 외부 조건이 충족되었는가?",
                reason="기존 waiting 상태를 새 구조에서 영구 대기열로 유지하지 않는다.",
                needed="조건을 확인한 뒤 작업을 수행하거나 obsolete로 종료한다.",
                task=task_id,
            )

        os.replace(temporary_project, project_root)
        os.replace(temporary_adhd, adhd_root)
    except Exception:
        shutil.rmtree(temporary_adhd, ignore_errors=True)
        shutil.rmtree(temporary_project, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd() / ".adhd")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    commands.add_parser("validate")

    register = commands.add_parser("register-project")
    register.add_argument("project_id")
    register.add_argument("path")
    register.add_argument("--name", required=True)

    control = commands.add_parser("control-create")
    control.add_argument("title")
    control.add_argument("--kind", choices=CONTROL_KINDS, required=True)
    control.add_argument("--statement", required=True)
    control.add_argument("--rationale", default="")
    control.add_argument("--source", action="append", default=[])
    control.add_argument("--supersedes", action="append", default=[])

    unresolved = commands.add_parser("unresolved-create")
    unresolved.add_argument("title")
    unresolved.add_argument("--domain", choices=UNRESOLVED_DOMAINS, required=True)
    unresolved.add_argument("--question", required=True)
    unresolved.add_argument("--reason", required=True)
    unresolved.add_argument("--needed", required=True)
    unresolved.add_argument("--ref", action="append", default=[])
    unresolved.add_argument("--task", type=int)

    resolve = commands.add_parser("unresolved-resolve")
    resolve.add_argument("id")
    resolve.add_argument("--resolution", required=True)

    migrate = commands.add_parser("migrate")
    migrate.add_argument("legacy_root", type=Path)
    migrate.add_argument("--project-root", type=Path, required=True)
    migrate.add_argument("--project-id", required=True)
    migrate.add_argument("--project-name", required=True)
    migrate.add_argument("--backup-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    store = WorkspaceStore(arguments.root)
    try:
        if arguments.command == "init":
            store.initialize()
            print(_dump_yaml(store.status()).rstrip())
        elif arguments.command == "status":
            print(_dump_yaml(store.status()).rstrip())
        elif arguments.command == "validate":
            errors = store.validate_all()
            if errors:
                print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
                return 1
            print("ok")
        elif arguments.command == "register-project":
            store.register_project(
                project_id=arguments.project_id,
                name=arguments.name,
                path=arguments.path,
            )
            print("ok")
        elif arguments.command == "control-create":
            print(
                store.create_control(
                    title=arguments.title,
                    kind=arguments.kind,
                    statement=arguments.statement,
                    rationale=arguments.rationale,
                    sources=arguments.source,
                    supersedes=arguments.supersedes,
                )
            )
        elif arguments.command == "unresolved-create":
            print(
                store.create_unresolved(
                    title=arguments.title,
                    domain=arguments.domain,
                    question=arguments.question,
                    reason=arguments.reason,
                    needed=arguments.needed,
                    refs=arguments.ref,
                    task=arguments.task,
                )
            )
        elif arguments.command == "unresolved-resolve":
            store.resolve_unresolved(arguments.id, arguments.resolution)
            print("ok")
        elif arguments.command == "migrate":
            migrate_legacy(
                legacy_root=arguments.legacy_root,
                adhd_root=arguments.root,
                project_root=arguments.project_root,
                project_id=arguments.project_id,
                project_name=arguments.project_name,
                backup_root=arguments.backup_root,
            )
            print("ok")
        return 0
    except (
        WorkspaceError,
        StateError,
        GraphError,
        TreeError,
        yaml.YAMLError,
        OSError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
