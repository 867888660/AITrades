from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from services.data_platform.process_guard import (
    GuardedProcessError,
    run_guarded_process,
)


class ProcessGuardTests(unittest.TestCase):
    def test_guarded_process_captures_bounded_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = run_guarded_process(
                [sys.executable, "-c", "print('guard-ok')"],
                cwd=Path.cwd(),
                log_path=Path(temp) / "worker.log",
                timeout_seconds=10,
                memory_limit_mb=256,
            )
        self.assertEqual(0, result.returncode)
        self.assertEqual("guard-ok", result.log_tail)

    def test_guarded_process_enforces_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(GuardedProcessError) as raised:
                run_guarded_process(
                    [sys.executable, "-c", "import time; time.sleep(5)"],
                    cwd=Path.cwd(),
                    log_path=Path(temp) / "worker.log",
                    timeout_seconds=1,
                    memory_limit_mb=256,
                )
        self.assertEqual("PROCESS_TIMEOUT", raised.exception.code)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object limit")
    def test_guarded_process_enforces_tree_memory_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(GuardedProcessError) as raised:
                run_guarded_process(
                    [
                        sys.executable,
                        "-c",
                        "payload = bytearray(400 * 1024 * 1024); print(len(payload))",
                    ],
                    cwd=Path.cwd(),
                    log_path=Path(temp) / "worker.log",
                    timeout_seconds=20,
                    memory_limit_mb=256,
                )
        self.assertEqual("PROCESS_RESOURCE_LIMIT", raised.exception.code)
