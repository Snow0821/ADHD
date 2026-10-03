#!/usr/bin/env python3
"""Manage optional durable jobs. Does not start workers or schedule reminders."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

import yaml

from _execution import EXECUTION_SCHEMA, PACKAGE_VERSION, ExecutionError, JobQueue


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', help='Established private .adhd runtime')
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('submit')
    s.add_argument('--request-key', required=True)
    s.add_argument('--title', required=True)
    s.add_argument('--kind', default='task')
    s.add_argument('--payload', type=json.loads, default=None, help='JSON; do not include secrets')
    s.add_argument('--task-id', type=int)
    s.add_argument('--priority', type=int, default=0)
    s.add_argument('--max-attempts', type=int, default=1)
    s.add_argument('--retry-safe', action='store_true', help='Explicitly allow bounded automatic retries for repeat-safe work')
    s.add_argument('--available-at', type=float, help='UTC Unix seconds; not a scheduled host wake')
    s = sub.add_parser('claim')
    s.add_argument('--worker', required=True)
    s.add_argument('--lease-seconds', type=float, default=300)
    s.add_argument('--kind', action='append', dest='kinds')
    s = sub.add_parser('checkpoint')
    s.add_argument('job_id')
    s.add_argument('--token', required=True)
    s.add_argument('--value', required=True, type=json.loads)
    s.add_argument('--lease-seconds', type=float, default=300)
    s = sub.add_parser('finish')
    s.add_argument('job_id')
    s.add_argument('--token', required=True)
    s.add_argument('--result', required=True, type=json.loads)
    s = sub.add_parser('fail')
    s.add_argument('job_id')
    s.add_argument('--token', required=True)
    s.add_argument('--error', required=True)
    s.add_argument('--retry-delay', type=float, default=30)
    s = sub.add_parser('block')
    s.add_argument('job_id')
    s.add_argument('--token', required=True)
    s.add_argument('--reason', required=True)
    s = sub.add_parser('resolve')
    s.add_argument('job_id')
    s.add_argument('--result', required=True, type=json.loads)
    s.add_argument('--reason', required=True, help='Coordinator evidence of external success; never guess')
    s = sub.add_parser('retry')
    s.add_argument('job_id')
    s.add_argument('--safe-to-repeat', action='store_true', required=True)
    s.add_argument('--reason', required=True)
    s = sub.add_parser('cancel')
    s.add_argument('job_id')
    s.add_argument('--reason', required=True)
    s = sub.add_parser('get')
    s.add_argument('job_id')
    sub.add_parser('version')
    sub.add_parser('doctor')
    sub.add_parser('status')
    sub.add_parser('reconcile')
    s = sub.add_parser('events')
    s.add_argument('--after', type=int, default=0)
    s.add_argument('--limit', type=int, default=100)
    return p


def main():
    args = vars(parser().parse_args())
    root, command = args.pop('root'), args.pop('command')
    try:
        if command == 'version':
            print(json.dumps(dict(package_version=PACKAGE_VERSION, execution_schema=EXECUTION_SCHEMA, task_schema=2)))
            return 0
        if not root:
            raise ExecutionError('--root is required except for version')
        q = JobQueue(root)
        job_id = args.pop('job_id', None)
        method = getattr(q, command)
        result = method(job_id, **args) if job_id is not None else method(**args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ExecutionError, ValueError, TypeError, OSError, OverflowError, sqlite3.Error, yaml.YAMLError) as exc:
        print(f'Execution error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
