"""vCenter task helpers."""
from __future__ import annotations

import time
from typing import Any


def wait_for_task(task, timeout: int = 600, interval: float = 1.0) -> dict[str, Any]:
    """Poll task until done. Returns {state, result, error}."""
    start = time.time()
    while True:
        state = getattr(task.info, "state", "unknown")
        if state in ("success", "error"):
            return {
                "state": state,
                "result": getattr(task.info, "result", None),
                "error": str(getattr(task.info, "error", "")) if state == "error" else None,
            }
        if time.time() - start > timeout:
            return {"state": "timeout", "result": None, "error": "Timeout waiting for task"}
        time.sleep(interval)
