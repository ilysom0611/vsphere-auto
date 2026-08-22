"""Concurrent batch executor with idempotency check."""
from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Any, Callable

from .state import update_batch_status, upsert_task

log = logging.getLogger(__name__)

DeployFn = Callable[[dict[str, Any]], dict[str, Any]]

# Guards the per-task idempotency check+claim (check status -> mark running)
# against in-process races. Cross-process safety comes from the DB primary key.
_claim_lock = threading.Lock()


def run_batch(
    batch_id: str,
    vms: list[dict[str, Any]],
    deploy_fn: DeployFn,
    concurrency: int = 5,
    on_error: str = "continue",
    state_dir=None,
    progress_cb: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Execute batch concurrently. deploy_fn(vmSpec) -> {ok, error?, vmMoid?}

    Never leaves the batch row stuck in 'pending': normal completion writes an
    overall status (success/partial/failed) and any unexpected exception marks
    the batch failed before re-raising.
    """
    if on_error not in ("continue", "fail-fast"):
        raise ValueError(f"on_error must be 'continue' or 'fail-fast', got {on_error!r}")

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

    try:
        return _run_batch_inner(
            batch_id, vms, deploy_fn, concurrency, on_error, state_dir, progress_cb
        )
    except Exception as e:
        log.exception("batch %s crashed: %s", batch_id, e)
        _mark_batch_failed(batch_id, str(e), state_dir)
        raise


def _mark_batch_failed(batch_id: str, error: str, state_dir) -> None:
    """Best-effort: fail any unfinished tasks and the batch itself."""
    try:
        from .state import list_tasks

        for t in list_tasks(batch_id, state_dir):
            if t.get("status") in ("pending", "running"):
                try:
                    upsert_task(
                        t["id"], batch_id, t.get("vm_name", ""), t.get("spec_hash", ""),
                        "failed", {}, {"ok": False, "error": f"batch crashed: {error}"},
                        state_dir=state_dir,
                    )
                except Exception:
                    pass
        update_batch_status(batch_id, "failed", state_dir)
    except Exception as e:
        log.error("failed to mark batch %s as failed: %s", batch_id, e)


def _run_batch_inner(
    batch_id: str,
    vms: list[dict[str, Any]],
    deploy_fn: DeployFn,
    concurrency: int,
    on_error: str,
    state_dir,
    progress_cb: Callable[[str, str], None] | None,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
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

        with _claim_lock:
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
            if not ok and "errorKey" not in res:
                from ..vsphere.errmap import decorate

                decorate(res, res.get("error"))
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
            from ..vsphere.errmap import decorate

            res = decorate({"ok": False, "error": str(e)}, e)
            try:
                upsert_task(task_id, batch_id, name, spec_hash, "failed", vm, res, state_dir=state_dir)
            except Exception as ee:
                log.warning("upsert_task failed %s failed: %s", task_id, ee)
            _progress(name, f"failed: {e!r}".replace("\n", " "))
            if on_error == "fail-fast":
                stop_event.set()
            return name, res

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        fut_map = {pool.submit(_run_one, vm): vm for vm in vms}
        for fut in concurrent.futures.as_completed(fut_map):
            name = fut_map[fut].get("name", "unnamed")
            try:
                _, res = fut.result()
            except Exception as e:
                res = {"ok": False, "error": repr(e).replace("\n", " ")}
            results[name] = res
            if not res.get("ok") and not res.get("skipped") and on_error == "fail-fast":
                stop_event.set()
                # best-effort cancel not-yet-started futures; running tasks
                # finish and are still collected for accurate reporting
                for f in fut_map:
                    try:
                        f.cancel()
                    except Exception:
                        pass

    succeeded = sum(1 for r in results.values() if r.get("ok"))
    skipped = sum(1 for r in results.values() if r.get("skipped"))
    cancelled = sum(1 for r in results.values() if "Cancelled" in str(r.get("error", "")))
    if not vms:
        overall = "success"
    elif succeeded == len(vms):
        overall = "success"
    elif succeeded > 0:
        overall = "partial"
    else:
        overall = "failed"
    try:
        update_batch_status(batch_id, overall, state_dir)
    except Exception as e:
        log.warning("update_batch_status %s=%s failed: %s", batch_id, overall, e)

    return {
        "batch_id": batch_id,
        "total": len(vms),
        "succeeded": succeeded,
        "skipped": skipped,
        "cancelled": cancelled,
        "failed": len(vms) - succeeded,
        "status": overall,
        "results": results,
    }
