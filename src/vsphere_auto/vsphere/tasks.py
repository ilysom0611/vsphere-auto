"""vCenter task helpers."""
from __future__ import annotations

import time
from typing import Any


def _format_task_error(task) -> str | None:
    err = getattr(getattr(task, "info", None), "error", None)
    if err is None:
        return None
    parts: list[str] = []
    for attr in ("localizedMessage", "msg"):
        try:
            v = getattr(err, attr, None)
            if v:
                parts.append(str(v))
        except Exception:
            pass
    try:
        fault = getattr(err, "fault", None)
        if fault:
            parts.append(f"fault={fault!r}")
    except Exception:
        pass
    return " | ".join(parts) if parts else str(err)


def wait_for_task(task, timeout: int = 600, interval: float = 1.0, on_poll=None) -> dict[str, Any]:
    """Poll task until done. Returns {state, result, error}.

    on_poll(task.info) fires on every successful poll — used by the web layer
    to surface live progress (task.info.progress percent + descriptionId).
    """
    start = time.monotonic()
    while True:
        try:
            state = getattr(task.info, "state", "unknown")
        except Exception as e:
            if time.monotonic() - start > timeout:
                try:
                    task.CancelTask()
                except Exception:
                    pass
                return {"state": "timeout", "result": None, "error": f"Timeout polling task: {e}"}
            time.sleep(interval)
            continue
        if state in ("success", "error"):
            return {
                "state": state,
                "result": getattr(task.info, "result", None),
                "error": _format_task_error(task) if state == "error" else None,
            }
        if on_poll is not None:
            try:
                on_poll(task.info)
            except Exception:
                pass
        if time.monotonic() - start > timeout:
            try:
                task.CancelTask()
            except Exception:
                pass
            return {"state": "timeout", "result": None, "error": "Timeout waiting for task"}
        time.sleep(interval)
