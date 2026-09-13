"""Behavioral checks for interruption, resumption, and durable task identity."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from adhdctl import WorkspaceStore
from taskctl import TaskStore


class TaskOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / ".adhd"
        self.workspace = WorkspaceStore(self.root)
        self.workspace.initialize()
        self.tasks = TaskStore(self.root)

    def test_nested_blockers_resume_before_fifo_queue(self):
        first = self.tasks.create_task(title="보고서", goal="보고서 완료", todos=[])
        second = self.tasks.create_task(title="강의", goal="강의 준비", todos=[])
        third = self.tasks.create_task(title="정리", goal="자료 정리", todos=[])
        self.assertEqual(self.tasks.dispatch(), first)
        child = self.tasks.spawn(
            title="분석", goal="결과 분석", parent_current="초안 완료",
            parent_next="결과 반영", parent_resume="초안 파일과 분석 결과",
        )
        grandchild = self.tasks.spawn(title="데이터", goal="데이터 확보")
        observed = [grandchild]
        for _ in range(5):
            # Reopen from disk, as in a later conversation.
            tasks = TaskStore(self.root)
            _, _, resumed = tasks.close(outcome="completed", summary="검증 완료")
            observed.append(resumed)
            self.assertEqual(self.workspace.validate_all(), [])
        self.assertEqual(observed, [grandchild, child, first, second, third, None])
        checkpoint = (self.root / "task/closed" / f"{first:06d}.md").read_text()
        self.assertIn("초안 완료", checkpoint)
        self.assertIn("결과 반영", checkpoint)

    def test_separate_processes_keep_checkpoint_and_never_reuse_ids(self):
        def command(script, *args):
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / script), "--root", str(self.root), *args],
                text=True, capture_output=True, check=True,
            )
            return result.stdout.strip()

        first = int(command("taskctl.py", "create", "첫 작업", "--goal", "완료"))
        self.assertEqual(int(command("taskctl.py", "dispatch")), first)
        command("taskctl.py", "checkpoint", "--current", "실험 실행",
                "--next", "결과 확인", "--resume", "기록된 결과 파일")
        saved = (self.root / "task/open" / f"{first:06d}.md").read_text()
        self.assertIn("결과 확인", saved)
        closed = yaml.safe_load(command("taskctl.py", "close", "--summary", "완료"))
        history_id = int(closed["history"])
        second = int(command("taskctl.py", "create", "둘째 작업", "--goal", "완료"))
        self.assertLess(first, history_id)
        self.assertLess(history_id, second)
        self.assertEqual(int(command("taskctl.py", "dispatch")), second)
        self.assertEqual(command("adhdctl.py", "validate"), "ok")


if __name__ == "__main__":
    unittest.main()
