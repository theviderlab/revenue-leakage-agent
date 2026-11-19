"""Sandbox files under state_dir (D4, D5): append-only ledgers plus the audit log.

Writes are atomic (temp file + os.replace) and every read-modify-write goes under one
process-wide lock: FastAPI runs sync routes in a threadpool and the sandbox is global.
"""

import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

LEDGER_FILES = {
    "invoices": "invoices.json",
    "credit_memos": "credit_memos.json",
    "billing_plans": "billing_plans.json",
    "audit_log": "audit_log.json",
}

# One lock per process; reentrant so an action can read while it holds the lock.
LOCK = threading.RLock()

ID_PREFIXES = {
    "make_good_invoice": "INV-MG",
    "credit_memo": "CM",
    "plan_amendment": "AMD",
    "link_invoice": "LNK",
    "dismiss_invoice": "DSM",
}


def retry_on_windows_lock[T](fn: Callable[[], T], attempts: int = 10) -> T:
    """On Windows, os.replace and open fail with PermissionError while another handle reads or
    replaces the same file; the other side is quick, so wait a little and try again."""
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (i + 1))
    raise AssertionError("unreachable")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        retry_on_windows_lock(lambda: os.replace(tmp, path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return retry_on_windows_lock(lambda: _load(path))


def _load(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Sandbox:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "sandbox"

    def read(self, name: str) -> list[dict]:
        return read_json_list(self.dir / LEDGER_FILES[name])

    def write(self, name: str, rows: list[dict]) -> None:
        atomic_write_json(self.dir / LEDGER_FILES[name], rows)

    def next_id(self, action_type: str) -> str:
        """Sequential per type, never reused: the audit log keeps reverted actions too."""
        prefix = ID_PREFIXES[action_type]
        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        highest = 0
        for entry in self.read("audit_log"):
            m = pattern.match(entry.get("action_id", ""))
            if m:
                highest = max(highest, int(m.group(1)))
        return f"{prefix}-{highest + 1:03d}"
