#!/usr/bin/env python3
"""Deterministic state engine for the ADHD task queue-stack workflow."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import yaml

from _storage import (
    _atomic_write_text,
    _dump_yaml,
    _join_document,
    _replace_section,
)


ID_WIDTH = 6
KNOWLEDGE_ID_PATTERN = re.compile(r"^k\d{6}$")
PROJECT_REF_PATTERN = re.compile(
    r"^(?P<project>[a-z0-9][a-z0-9-]*):(?P<node>p\d{6})$"
)
OUTCOMES = ("completed", "cancelled", "obsolete")
EVENT_BY_OUTCOME = {
    "completed": "task_completed",
    "cancelled": "task_cancelled",
    "obsolete": "task_obsolete",
}


class StateError(RuntimeError):
    """Raised when persisted task state violates an invariant."""


def format_id(value: int) -> str:
    return f"{value:0{ID_WIDTH}d}"


def _split_document(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    try:
        closing = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as error:
        raise StateError("Markdown frontmatter is not closed with '---'.") from error

    metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
    if not isinstance(metadata, dict):
        raise StateError("Markdown frontmatter must be a mapping.")
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    return metadata, body


def _dedupe(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalize_references(
    values: Iterable[str], pattern: re.Pattern[str], label: str
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        reference = value.strip().lower()
        if not pattern.fullmatch(reference):
            raise StateError(f"{label} reference has an invalid ID: {value}")
        if reference not in seen:
            seen.add(reference)
            result.append(reference)
    return result


class TaskStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.state_path = self.root / "state.yaml"
        self.task_dir = self.root / "task"
        self.tasks_dir = self.task_dir / "open"
        self.closed_dir = self.task_dir / "closed"
        self.history_dir = self.root / "history"
        self.lock_path = self.root / ".taskctl.lock"
        self.projects_path = self.root / "control" / "projects.yaml"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            existing = yaml.safe_load(self.state_path.read_text(encoding="utf-8")) or {}
            if not isinstance(existing, dict) or existing.get("schema_version") != 2:
                raise StateError(
                    "Unsupported task state schema; migrate the workspace with adhdctl.py."
                )
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.closed_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._save_state(
                {
                    "schema_version": 2,
                    "next_id": 0,
                    "active": None,
                    "stack": [],
                    "queue": [],
                }
            )

    @contextmanager
    def locked(self):
        self.ensure()
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load_state(self) -> dict[str, Any]:
        state = yaml.safe_load(self.state_path.read_text(encoding="utf-8")) or {}
        if state.get("schema_version") != 2:
            raise StateError(
                "Unsupported task state schema; migrate the workspace with adhdctl.py."
            )
        required = {"next_id", "active", "stack", "queue"}
        missing = required.difference(state)
        if missing:
            raise StateError(f"state.yaml is missing: {', '.join(sorted(missing))}")
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        _atomic_write_text(self.state_path, _dump_yaml(state))

    def _reserve_id(self, state: dict[str, Any]) -> int:
        value = state["next_id"]
        if not isinstance(value, int) or value < 0:
            raise StateError("next_id must be a non-negative integer.")
        state["next_id"] = value + 1
        # Persist the reservation first. A failed operation may leave a gap,
        # but an ID is never reused after an interruption.
        self._save_state(state)
        return value

    def _open_path(self, task_id: int) -> Path:
        return self.tasks_dir / f"{format_id(task_id)}.md"

    def _closed_path(self, task_id: int) -> Path:
        return self.closed_dir / f"{format_id(task_id)}.md"

    def _history_path(self, history_id: int) -> Path:
        return self.history_dir / f"{format_id(history_id)}.md"

    def _task_title(self, task_id: int) -> str:
        path = self._open_path(task_id)
        if not path.exists():
            path = self._closed_path(task_id)
        if not path.exists():
            return "<missing>"
        metadata, _ = _split_document(path.read_text(encoding="utf-8"))
        return str(metadata.get("title", "<untitled>"))

    def _task_document(
        self,
        *,
        title: str,
        goal: str,
        todos: list[str],
        memo: str,
        parent: int | None,
        mode: str,
        knowledge: list[str],
        project: list[str],
    ) -> str:
        if not todos:
            todos = ["목표를 달성하고 결과를 확인한다."]
        todo_text = "\n".join(f"- [ ] {item}" for item in todos)
        next_action = todos[0]
        body = f"""## 목표

{goal.strip()}

## 할 일

{todo_text}

## 진행

- 현재: 시작 전
- 다음: {next_action}
- 재개: 작업 파일과 현재 산출물을 확인한 뒤 다음 단계부터 계속한다.

## 메모

{memo.strip() if memo.strip() else '없음'}
"""
        return _join_document(
            {
                "title": title.strip(),
                "parent": parent,
                "mode": mode,
                "knowledge": knowledge,
                "project": project,
            },
            body,
        )

    def _validated_references(
        self,
        *,
        knowledge: Iterable[str],
        project: Iterable[str],
    ) -> tuple[list[str], list[str]]:
        knowledge_ids = _normalize_references(
            knowledge, KNOWLEDGE_ID_PATTERN, "Knowledge"
        )
        project_ids = _normalize_references(project, PROJECT_REF_PATTERN, "Project")
        for node_id in knowledge_ids:
            if not (self.root / "knowledge" / "nodes" / f"{node_id}.md").exists():
                raise StateError(f"Knowledge reference does not exist: {node_id}")
        for reference in project_ids:
            if not self._project_node_path(reference).exists():
                raise StateError(f"Project reference does not exist: {reference}")
        return knowledge_ids, project_ids

    def _load_projects(self) -> dict[str, Any]:
        if not self.projects_path.exists():
            return {}
        value = yaml.safe_load(self.projects_path.read_text(encoding="utf-8")) or {}
        projects = value.get("projects", {}) if isinstance(value, dict) else {}
        if not isinstance(projects, dict):
            raise StateError("control/projects.yaml projects must be a mapping.")
        return projects

    def _project_node_path(self, reference: str) -> Path:
        match = PROJECT_REF_PATTERN.fullmatch(reference)
        if match is None:
            raise StateError(f"Project reference has an invalid ID: {reference}")
        project_id = match.group("project")
        node_id = match.group("node")
        entry = self._load_projects().get(project_id)
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise StateError(f"Project is not registered: {project_id}")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise StateError(f"Registered project path is unsafe: {entry['path']}")
        return self.root.parent / relative / "project" / "nodes" / f"{node_id}.md"

    def create_task(
        self,
        *,
        title: str,
        goal: str,
        todos: list[str] | None = None,
        memo: str = "",
        enqueue: bool = True,
        parent: int | None = None,
        mode: str = "queued",
        knowledge: list[str] | None = None,
        project: list[str] | None = None,
    ) -> int:
        with self.locked():
            knowledge_ids, project_ids = self._validated_references(
                knowledge=knowledge or [], project=project or []
            )
            state = self._load_state()
            task_id = self._reserve_id(state)
            path = self._open_path(task_id)
            _atomic_write_text(
                path,
                self._task_document(
                    title=title,
                    goal=goal,
                    todos=todos or [],
                    memo=memo,
                    parent=parent,
                    mode=mode,
                    knowledge=knowledge_ids,
                    project=project_ids,
                ),
            )
            if enqueue:
                state["queue"].append(task_id)
            self._save_state(state)
            return task_id

    def _checkpoint_unlocked(
        self,
        task_id: int,
        *,
        current: str,
        next_action: str,
        resume: str,
    ) -> None:
        path = self._open_path(task_id)
        if not path.exists():
            raise StateError(f"Open task {task_id} does not exist.")
        text = path.read_text(encoding="utf-8")
        progress = (
            f"- 현재: {current.strip()}\n"
            f"- 다음: {next_action.strip()}\n"
            f"- 재개: {resume.strip()}"
        )
        _atomic_write_text(path, _replace_section(text, "진행", progress))

    def checkpoint(
        self,
        *,
        current: str,
        next_action: str,
        resume: str,
        task_id: int | None = None,
    ) -> int:
        with self.locked():
            state = self._load_state()
            target = state["active"] if task_id is None else task_id
            if target is None:
                raise StateError("There is no active task to checkpoint.")
            self._checkpoint_unlocked(
                target,
                current=current,
                next_action=next_action,
                resume=resume,
            )
            return target

    def spawn(
        self,
        *,
        title: str,
        goal: str,
        todos: list[str] | None = None,
        memo: str = "",
        parent_current: str | None = None,
        parent_next: str | None = None,
        parent_resume: str | None = None,
        knowledge: list[str] | None = None,
        project: list[str] | None = None,
    ) -> int:
        with self.locked():
            knowledge_ids, project_ids = self._validated_references(
                knowledge=knowledge or [], project=project or []
            )
            state = self._load_state()
            parent = state["active"]
            if parent is None:
                raise StateError("A blocking child requires an active parent task.")
            if any((parent_current, parent_next, parent_resume)):
                if not all((parent_current, parent_next, parent_resume)):
                    raise StateError("Provide all three parent checkpoint fields together.")
                self._checkpoint_unlocked(
                    parent,
                    current=parent_current or "",
                    next_action=parent_next or "",
                    resume=parent_resume or "",
                )

            child = self._reserve_id(state)
            _atomic_write_text(
                self._open_path(child),
                self._task_document(
                    title=title,
                    goal=goal,
                    todos=todos or [],
                    memo=memo,
                    parent=parent,
                    mode="blocking",
                    knowledge=knowledge_ids,
                    project=project_ids,
                ),
            )
            state["stack"].append(parent)
            state["active"] = child
            self._save_state(state)
            return child

    def _dispatch_unlocked(self, state: dict[str, Any]) -> int | None:
        active = state["active"]
        if active is not None:
            if self._open_path(active).exists():
                return active
            if self._closed_path(active).exists():
                state["active"] = None
            else:
                raise StateError(f"Active task {active} has no task file.")

        while state["stack"]:
            candidate = state["stack"].pop()
            if self._open_path(candidate).exists():
                state["active"] = candidate
                return candidate
            if not self._closed_path(candidate).exists():
                raise StateError(f"Stack task {candidate} has no task file.")

        while state["queue"]:
            candidate = state["queue"].pop(0)
            if self._open_path(candidate).exists():
                state["active"] = candidate
                return candidate
            if not self._closed_path(candidate).exists():
                raise StateError(f"Queued task {candidate} has no task file.")

        state["active"] = None
        return None

    def dispatch(self) -> int | None:
        with self.locked():
            state = self._load_state()
            task_id = self._dispatch_unlocked(state)
            self._save_state(state)
            return task_id

    def _remove_from_runtime(self, state: dict[str, Any], task_id: int) -> None:
        if state["active"] == task_id:
            state["active"] = None
        for key in ("stack", "queue"):
            state[key] = [value for value in state[key] if value != task_id]

    def close(
        self,
        *,
        outcome: str,
        summary: str,
        task_id: int | None = None,
        affects: list[str] | None = None,
        created_tasks: list[int] | None = None,
        decisions: list[str] | None = None,
        artifacts: list[str] | None = None,
    ) -> tuple[int, int | None, int | None]:
        if outcome not in OUTCOMES:
            raise StateError(f"Unsupported outcome: {outcome}")

        with self.locked():
            state = self._load_state()
            target = state["active"] if task_id is None else task_id
            if target is None:
                raise StateError("There is no task to close.")

            open_path = self._open_path(target)
            closed_path = self._closed_path(target)
            if closed_path.exists() and not open_path.exists():
                self._remove_from_runtime(state, target)
                resumed = self._dispatch_unlocked(state)
                self._save_state(state)
                return target, None, resumed
            if not open_path.exists():
                raise StateError(f"Open task {target} does not exist.")

            text = open_path.read_text(encoding="utf-8")
            existing_history = re.search(r"(?m)^- 히스토리:\s*(\d+)\s*$", text)
            if existing_history:
                history_id = int(existing_history.group(1))
            else:
                history_id = self._reserve_id(state)

            artifact_lines = artifacts or []
            result_lines = [
                f"- 종료: {outcome}",
                f"- 히스토리: {history_id}",
                f"- 요약: {summary.strip()}",
            ]
            if artifact_lines:
                result_lines.append("- 산출물:")
                result_lines.extend(f"  - {item}" for item in artifact_lines)
            text = _replace_section(text, "결과", "\n".join(result_lines))
            _atomic_write_text(open_path, text)

            history_metadata = {
                "event": EVENT_BY_OUTCOME[outcome],
                "subject": target,
                "affects": affects or [],
                "created_tasks": created_tasks or [],
            }
            history_body = summary.strip()
            if decisions:
                history_body += "\n\n## 결정\n\n" + "\n".join(
                    f"- {item}" for item in decisions
                )
            if artifact_lines:
                history_body += "\n\n## 산출물\n\n" + "\n".join(
                    f"- {item}" for item in artifact_lines
                )
            history_path = self._history_path(history_id)
            if not history_path.exists():
                _atomic_write_text(
                    history_path,
                    _join_document(history_metadata, history_body),
                )

            os.replace(open_path, closed_path)
            self._remove_from_runtime(state, target)
            resumed = self._dispatch_unlocked(state)
            self._save_state(state)
            return target, history_id, resumed

    def recover(self) -> dict[str, Any]:
        with self.locked():
            state = self._load_state()
            closed_ids = {
                int(path.stem) for path in self.closed_dir.glob("*.md") if path.stem.isdigit()
            }
            if state["active"] in closed_ids:
                state["active"] = None
            for key in ("stack", "queue"):
                state[key] = _dedupe(
                    value for value in state[key] if value not in closed_ids
                )

            referenced = set(state["stack"] + state["queue"])
            if state["active"] is not None:
                referenced.add(state["active"])
            open_ids = sorted(
                int(path.stem) for path in self.tasks_dir.glob("*.md") if path.stem.isdigit()
            )
            orphans = [task_id for task_id in open_ids if task_id not in referenced]
            for task_id in orphans:
                metadata, _ = _split_document(
                    self._open_path(task_id).read_text(encoding="utf-8")
                )
                parent = metadata.get("parent")
                mode = metadata.get("mode")
                if mode == "blocking" and state["active"] == parent:
                    state["stack"].append(parent)
                    state["active"] = task_id
                else:
                    state["queue"].append(task_id)

            if state["active"] is None:
                self._dispatch_unlocked(state)
            self._save_state(state)
            return state

    def validate(self) -> list[str]:
        self.ensure()
        state = self._load_state()
        errors: list[str] = []
        if not isinstance(state["next_id"], int) or state["next_id"] < 0:
            errors.append("next_id must be a non-negative integer")
        for key in ("stack", "queue"):
            if not isinstance(state[key], list) or not all(
                isinstance(value, int) for value in state[key]
            ):
                errors.append(f"{key} must be a list of integer task IDs")
        if state["active"] is not None and not isinstance(state["active"], int):
            errors.append("active must be null or an integer task ID")

        runtime: list[int] = []
        if isinstance(state["active"], int):
            runtime.append(state["active"])
        for key in ("stack", "queue"):
            if isinstance(state[key], list):
                runtime.extend(value for value in state[key] if isinstance(value, int))
        if len(runtime) != len(set(runtime)):
            errors.append("a task ID appears in more than one runtime location")

        open_ids = {
            int(path.stem) for path in self.tasks_dir.glob("*.md") if path.stem.isdigit()
        }
        closed_ids = {
            int(path.stem) for path in self.closed_dir.glob("*.md") if path.stem.isdigit()
        }
        history_ids = {
            int(path.stem) for path in self.history_dir.glob("*.md") if path.stem.isdigit()
        }
        for task_id in runtime:
            if task_id not in open_ids:
                errors.append(f"runtime task {task_id} has no open task file")
        for task_id in sorted(open_ids.difference(runtime)):
            errors.append(f"open task {task_id} is not referenced by runtime state")
        overlap = open_ids.intersection(closed_ids)
        if overlap:
            errors.append(f"tasks exist in both open and closed: {sorted(overlap)}")
        all_ids = open_ids | closed_ids | history_ids
        if all_ids and state["next_id"] <= max(all_ids):
            errors.append("next_id must be greater than every persisted task/history ID")

        for path in sorted(self.tasks_dir.glob("*.md")) + sorted(
            self.closed_dir.glob("*.md")
        ):
            try:
                metadata, _ = _split_document(path.read_text(encoding="utf-8"))
            except StateError as error:
                errors.append(f"{path.name}: {error}")
                continue
            references = (
                ("knowledge", KNOWLEDGE_ID_PATTERN),
                ("project", PROJECT_REF_PATTERN),
            )
            for key, pattern in references:
                values = metadata.get(key, [])
                if not isinstance(values, list) or not all(
                    isinstance(value, str) and pattern.fullmatch(value)
                    for value in values
                ):
                    errors.append(f"{path.name}: {key} must be a list of valid IDs")
                    continue
                if len(values) != len(set(values)):
                    errors.append(f"{path.name}: {key} contains duplicate IDs")
                for value in values:
                    try:
                        target = (
                            self.root / "knowledge" / "nodes" / f"{value}.md"
                            if key == "knowledge"
                            else self._project_node_path(value)
                        )
                    except StateError as error:
                        errors.append(f"{path.name}: {error}")
                        continue
                    if not target.exists():
                        errors.append(f"{path.name}: dangling {key} reference {value}")
        return errors

    def snapshot(self) -> dict[str, Any]:
        self.ensure()
        state = self._load_state()

        def describe(task_id: int) -> str:
            return f"{format_id(task_id)} {self._task_title(task_id)}"

        return {
            "next_id": state["next_id"],
            "active": describe(state["active"]) if state["active"] is not None else None,
            "stack": [describe(value) for value in state["stack"]],
            "queue": [describe(value) for value in state["queue"]],
        }


def _print_snapshot(snapshot: dict[str, Any]) -> None:
    print(_dump_yaml(snapshot).rstrip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd() / ".adhd",
        help="ADHD runtime directory containing state.yaml and task/.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init")
    commands.add_parser("dispatch")
    commands.add_parser("status")
    commands.add_parser("validate")
    commands.add_parser("recover")

    create = commands.add_parser("create")
    create.add_argument("title")
    create.add_argument("--goal", required=True)
    create.add_argument("--todo", action="append", default=[])
    create.add_argument("--memo", default="")
    create.add_argument("--knowledge", action="append", default=[])
    create.add_argument("--project", action="append", default=[])

    spawn = commands.add_parser("spawn")
    spawn.add_argument("title")
    spawn.add_argument("--goal", required=True)
    spawn.add_argument("--todo", action="append", default=[])
    spawn.add_argument("--memo", default="")
    spawn.add_argument("--knowledge", action="append", default=[])
    spawn.add_argument("--project", action="append", default=[])
    spawn.add_argument("--parent-current")
    spawn.add_argument("--parent-next")
    spawn.add_argument("--parent-resume")

    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("--id", type=int)
    checkpoint.add_argument("--current", required=True)
    checkpoint.add_argument("--next", dest="next_action", required=True)
    checkpoint.add_argument("--resume", required=True)

    close = commands.add_parser("close")
    close.add_argument("--id", type=int)
    close.add_argument("--outcome", choices=OUTCOMES, default="completed")
    close.add_argument("--summary", required=True)
    close.add_argument("--affect", action="append", default=[])
    close.add_argument("--created-task", type=int, action="append", default=[])
    close.add_argument("--decision", action="append", default=[])
    close.add_argument("--artifact", action="append", default=[])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TaskStore(args.root)
    try:
        if args.command == "init":
            store.ensure()
            _print_snapshot(store.snapshot())
        elif args.command == "create":
            task_id = store.create_task(
                title=args.title,
                goal=args.goal,
                todos=args.todo,
                memo=args.memo,
                knowledge=args.knowledge,
                project=args.project,
            )
            print(format_id(task_id))
        elif args.command == "dispatch":
            task_id = store.dispatch()
            print("none" if task_id is None else format_id(task_id))
        elif args.command == "spawn":
            task_id = store.spawn(
                title=args.title,
                goal=args.goal,
                todos=args.todo,
                memo=args.memo,
                parent_current=args.parent_current,
                parent_next=args.parent_next,
                parent_resume=args.parent_resume,
                knowledge=args.knowledge,
                project=args.project,
            )
            print(format_id(task_id))
        elif args.command == "checkpoint":
            task_id = store.checkpoint(
                task_id=args.id,
                current=args.current,
                next_action=args.next_action,
                resume=args.resume,
            )
            print(format_id(task_id))
        elif args.command == "close":
            task_id, history_id, resumed = store.close(
                task_id=args.id,
                outcome=args.outcome,
                summary=args.summary,
                affects=args.affect,
                created_tasks=args.created_task,
                decisions=args.decision,
                artifacts=args.artifact,
            )
            print(
                _dump_yaml(
                    {
                        "closed": format_id(task_id),
                        "history": format_id(history_id) if history_id is not None else None,
                        "active": format_id(resumed) if resumed is not None else None,
                    }
                ).rstrip()
            )
        elif args.command == "recover":
            store.recover()
            _print_snapshot(store.snapshot())
        elif args.command == "validate":
            errors = store.validate()
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            print("ok")
        elif args.command == "status":
            _print_snapshot(store.snapshot())
    except StateError as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
