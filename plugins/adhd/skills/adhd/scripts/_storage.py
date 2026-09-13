"""Shared file and Markdown operations for the ADHD stores."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

import yaml


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _dump_yaml(value: Any) -> str:
    return yaml.safe_dump(
        value, allow_unicode=True, sort_keys=False, default_flow_style=False,
    )


def _join_document(metadata: dict[str, Any], body: str) -> str:
    return f"---\n{_dump_yaml(metadata)}---\n\n{body.rstrip()}\n"


def _replace_section(text: str, title: str, content: str) -> str:
    section = f"## {title}\n\n{content.strip()}\n"
    pattern = re.compile(rf"(?ms)^## {re.escape(title)}\s*\n.*?(?=^##\s|\Z)")
    if pattern.search(text):
        # A callable keeps backslashes in paths, code, and formulas literal.
        return pattern.sub(lambda _: section + "\n", text, count=1).rstrip() + "\n"
    return text.rstrip() + "\n\n" + section


def _section(text: str, title: str) -> str:
    pattern = re.compile(rf"(?ms)^## {re.escape(title)}\s*\n(.*?)(?=^##\s|\Z)")
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _clean_list(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
