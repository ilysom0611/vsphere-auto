"""Concurrent batch executor with idempotency check."""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from typing import Any, Callable

from .state import upsert_task

log = logging.getLogger(__name__)

DeployFn = Callable[[dict[str, Any]], dict[str, Any]]


def run_batch(
    batch_id: str,
    vms: list[dict[str, Any]],
    deploy_fn: DeployFn,
    concurrency: int = 5,
    on_error: str = "continue",
    state_dir=None,
    progress_cb: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Execute batch concurrently. deploy_fn(vmSpec) -> {ok, error?, vmMoid?}"""
    # Validate duplicates upfront
    names = [vm.get("name", "unnamed") for vm in vms]
    dup = next((n for n in names if names.count(n) > 1), None)
    if dup is not None:
        raise ValueError(f"Duplicate VM name: {dup!r} — names must be unique within a batch")

    try:
        concurrency = int(concurrency)
    except (TypeError, ValueError):
        concurrency = 5
    concurrency = max(1, min(concurrency, 32))

    results: dict[str, Any] = {}
    failed = 0
    stop_event = threading.Event()
    lock = threading.Lock()

    def _progress(name: str, msg: str) -> None:
        if progress_cb:
            with lock:
                try:
                    progress_cb(name, msg)
                except Exception:
                    pass

    def _run_one(vm: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = vm.get("name", "unnamed")
        if stop_event.is_set():
            return name, {"ok": False, "error": "Cancelled (fail-fast)"}
        spec_hash = vm.get("_specHash", "")
        task_id = f"{batch_id}:{name}"
        # idempotency: if existing task with same hash and success, skip
        from .state import get_task

        try:
            existing = get_task(task_id, state_dir)
        except Exception as e:
            log.warning("get_task %s failed: %s", task_id, e)
            existing = None
        if existing and existing.get("spec_hash") == spec_hash and existing.get("status") == "success":
            _progress(name, "skipped (unchanged)")
            return name, {"ok": True, "skipped": True}

        try:
            upsert_task(task_id, batch_id, name, spec_hash, "running", vm, state_dir=state_dir)
        except Exception as e:
            log.warning("upsert_task running %s failed: %s", task_id, e)
        _progress(name, "running")
        try:
            res = deploy_fn(vm)
            ok = bool(res.get("ok"))
            status = "success" if ok else "failed"
            try:
                upsert_task(task_id, batch_id, name, spec_hash, status, vm, res, state_dir=state_dir)
            except Exception as e:
                log.warning("upsert_task %s %s failed: %s", task_id, status, e)
            _progress(name, status)
            if not ok and on_error == "fail-fast":
                stop_event.set()
            return name, res
        except Exception as e:
            res = {"ok": False, "error": str(e)}
            try:
                upsert_task(task_id, batch_id, name, spec_hash, "failed", vm, res, state_dir=state_dir)
            except Exception as ee:
                log.warning("upsert_task failed %s failed: %s", task_id, ee)
            _progress(name, f"failed: {e}")
            if on_error == "fail-fast":
                stop_event.set()
            return name, res

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        fut_map = {pool.submit(_run_one, vm): vm for vm in vms}
        for fut in concurrent.futures.as_completed(fut_map):
            vm = fut_map[fut]
            name = vm.get("name", "unnamed")
            try:
                _, res = fut.result()
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            results[name] = res
            if not res.get("ok") and not res.get("skipped"):
                failed += 1
                if on_error == "fail-fast":
                    stop_event.set()
                    # best-effort cancel not-yet-started futures
                    for f in fut_map:
                        try:
                            f.cancel()
                        except Exception:
                            pass
                    # drain remaining without waiting for full pool join to be faster,
                    # but we still collect results for reporting
                    # (ThreadPoolExecutor will join on exit, running tasks still finish)
                    # Do not break — collect all results for accurate reporting.

    return {"batch_id": batch_id, "total": len(vms), "failed": failed, "results": results}
