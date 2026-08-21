"""Concurrent batch executor with idempotency check."""
from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Callable

from .state import upsert_task

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
    results: dict[str, Any] = {}
    failed = 0

    def _run_one(vm: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = vm.get("name", "unnamed")
        spec_hash = vm.get("_specHash", "")
        task_id = f"{batch_id}:{name}"
        # idempotency: if existing task with same hash and success, skip
        from .state import get_task

        existing = get_task(task_id, state_dir)
        if existing and existing.get("spec_hash") == spec_hash and existing.get("status") == "success":
            if progress_cb:
                progress_cb(name, "skipped (unchanged)")
            return name, {"ok": True, "skipped": True}

        upsert_task(task_id, batch_id, name, spec_hash, "running", vm, state_dir=state_dir)
        if progress_cb:
            progress_cb(name, "running")
        try:
            # pre-check: if VM already exists and hash matches, skip creation
            res = deploy_fn(vm)
            ok = bool(res.get("ok"))
            status = "success" if ok else "failed"
            upsert_task(task_id, batch_id, name, spec_hash, status, vm, res, state_dir=state_dir)
            if progress_cb:
                progress_cb(name, status)
            return name, res
        except Exception as e:
            res = {"ok": False, "error": str(e)}
            upsert_task(task_id, batch_id, name, spec_hash, "failed", vm, res, state_dir=state_dir)
            if progress_cb:
                progress_cb(name, f"failed: {e}")
            return name, res

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        fut_map = {pool.submit(_run_one, vm): vm for vm in vms}
        for fut in concurrent.futures.as_completed(fut_map):
            vm = fut_map[fut]
            name = vm.get("name", "unnamed")
            try:
                _, res = fut.result()
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            results[name] = res
            if not res.get("ok"):
                failed += 1
                if on_error == "fail-fast":
                    # cancel remaining
                    for f in fut_map:
                        f.cancel()
                    break

    return {"batch_id": batch_id, "total": len(vms), "failed": failed, "results": results}
