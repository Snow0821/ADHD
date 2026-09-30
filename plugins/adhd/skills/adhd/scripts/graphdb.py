#!/usr/bin/env python3
"""Prepare connector calls and verify snapshots; never connect with credentials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from _knowledge_db import DatabaseBridge, DatabaseError, binding


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--project-id", required=True)
    configure.add_argument("--graph-id", required=True)
    configure.add_argument("--new", action="store_true", help="Stage this local graph for creation at remote revision 0; never overwrite an existing graph.")
    for name in ("read-query", "begin-edit", "write-query", "recover", "status"):
        commands.add_parser(name)
    read = commands.add_parser("apply-read")
    read.add_argument("file", type=Path, help="JSON object returned by export_graph, without SQL/tool wrapping.")
    read.add_argument("--preserve-draft", action="store_true", help="Archive an unpublished draft inside the runtime before accepting remote data.")
    write = commands.add_parser("apply-write")
    write.add_argument("file", type=Path, help="JSON object returned by replace_graph, without SQL/tool wrapping.")
    args = parser.parse_args(argv)
    bridge = DatabaseBridge(args.root)
    try:
        if args.command == "configure":
            result = bridge.configure(project_id=args.project_id, graph_id=args.graph_id, new=args.new)
        elif args.command == "read-query":
            result = bridge.read_query()
        elif args.command == "begin-edit":
            result = bridge.begin_edit()
        elif args.command == "write-query":
            result = bridge.write_query()
        elif args.command == "apply-read":
            result = bridge.apply_read(json.loads(args.file.read_text(encoding="utf-8")), preserve_draft=args.preserve_draft)
        elif args.command == "apply-write":
            result = bridge.apply_write(json.loads(args.file.read_text(encoding="utf-8")))
        elif args.command == "recover":
            result = bridge.recover()
        else:
            result = binding(args.root) or {"backend": "files"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, OSError, ValueError, TypeError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
