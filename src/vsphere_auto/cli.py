"""Typer CLI entry."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="vSphere Auto — batch VM deployment")
creds_app = typer.Typer(help="Manage saved vCenter/ESXi credentials")
app.add_typer(creds_app, name="creds")

console = Console()


@creds_app.command("list")
def creds_list():
    from .creds.store import list_creds

    rows = list_creds()
    t = Table(title="Credentials")
    t.add_column("ID"); t.add_column("Name"); t.add_column("Host"); t.add_column("User"); t.add_column("Type")
    for c in rows:
        t.add_row(str(c.id), c.name, f"{c.host}:{c.port}", c.username, c.type)
    console.print(t)
    if not rows:
        console.print("[dim]No credentials saved. Use: vsphere-auto creds add[/dim]")


@creds_app.command("add")
def creds_add(
    name: str = typer.Option(..., help="Credential name"),
    host: str = typer.Option(..., help="vCenter/ESXi host"),
    username: str = typer.Option(..., help="Username"),
    password: str = typer.Option("", help="Password (or use VSPHERE_PASSWORD env)"),
    port: int = typer.Option(443),
    type: str = typer.Option("vcenter", help="vcenter|esxi"),
):
    from .creds.store import create_creds

    pwd = password or os.environ.get("VSPHERE_PASSWORD", "")
    c = create_creds(name, host, username, pwd, port, type)
    console.print(f"[green]Created[/green] {c.to_safe_dict()}")


@creds_app.command("update")
def creds_update(
    id: int = typer.Argument(..., help="Credential ID"),
    name: Optional[str] = typer.Option(None),
    host: Optional[str] = typer.Option(None),
    username: Optional[str] = typer.Option(None),
    password: Optional[str] = typer.Option(None, help="New password (omit to keep unchanged)"),
    port: Optional[int] = typer.Option(None),
    type: Optional[str] = typer.Option(None),
):
    from .creds.store import update_creds

    c = update_creds(id, name=name, host=host, username=username, password=password, port=port, cred_type=type)
    if not c:
        console.print(f"[red]Not found: {id}[/red]"); raise typer.Exit(1)
    console.print(f"[green]Updated[/green] {c.to_safe_dict()}")


@creds_app.command("remove")
def creds_remove(id: int = typer.Argument(..., help="Credential ID")):
    from .creds.store import delete_creds

    ok = delete_creds(id)
    console.print("Deleted" if ok else "Not found")
    if not ok:
        raise typer.Exit(1)


@creds_app.command("test")
def creds_test(id: str = typer.Argument(..., help="Credential ID or name")):
    import json
    import traceback

    from .creds.store import resolve_creds
    from .vsphere.discovery import test_connection

    c = resolve_creds(id)
    if not c:
        console.print(f"[red]Not found: {id}[/red]"); raise typer.Exit(1)
    # Decrypt explicitly so password issues show up
    try:
        pwd = c.decrypted_password()
    except Exception as e:
        console.print(f"[red]Failed to decrypt password for {id}: {e}[/red]")
        if __import__("os").environ.get("VSPHERE_DEBUG"):
            traceback.print_exc()
        raise typer.Exit(1)
    if not pwd:
        console.print(f"[yellow]Warning: credential {id} has no password stored (hasPassword=false). Testing without password will likely fail.[/yellow]")
    console.print(f"[dim]Testing {c.host}:{c.port} as {c.username} ...[/dim]")
    try:
        res = test_connection(c.host, c.port, c.username, pwd)
    except Exception as e:
        console.print(f"[red]test_connection raised: {e}[/red]")
        if __import__("os").environ.get("VSPHERE_DEBUG"):
            traceback.print_exc()
        # Also return a structured error so callers can parse it
        console.print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        raise typer.Exit(1)
    # Always print JSON so it never looks "empty"
    console.print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res.get("ok"):
        raise typer.Exit(1)


@app.command()
def discover(
    creds: Optional[str] = typer.Option(None, help="Saved credential name/id"),
    host: Optional[str] = typer.Option(None),
    user: Optional[str] = typer.Option(None),
    password: Optional[str] = typer.Option(None),
    port: int = typer.Option(443),
    out: Optional[str] = typer.Option(None, help="Write inventory to file"),
):
    from .creds.store import resolve_creds
    from .vsphere.client import connect, disconnect
    from .vsphere.discovery import discover
    from .inventory import save_inventory
    import json

    if creds:
        c = resolve_creds(creds)
        if not c:
            console.print(f"[red]Credential not found: {creds}[/red]"); raise typer.Exit(1)
        host, port, user, password = c.host, c.port, c.username, c.decrypted_password()
    password = password or os.environ.get("VSPHERE_PASSWORD", "")
    if not host or not user:
        console.print("[red]host and user required (or --creds)[/red]"); raise typer.Exit(1)
    si = connect(host, port, user, password)
    try:
        inv = discover(si)
        save_inventory(inv)
        console.print(json.dumps(inv, ensure_ascii=False, indent=2))
        if out:
            Path(out).write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
            console.print(f"[green]Saved to {out}[/green]")
    finally:
        disconnect(si)


@app.command()
def plan(config: str = typer.Option(..., "--config", "-c", help="Config YAML path"), creds: Optional[str] = typer.Option(None, help="Credential name/id override")):
    from .config import load_config
    from .inventory import load_inventory_any
    from .vsphere.selector import auto_select_all
    from .batch.planner import expand_batch
    import json

    cfg = load_config(config)
    if creds:
        cfg.vcenter.credsRef = creds
    cfg_dict = cfg.model_dump()
    inv = load_inventory_any()
    sel = auto_select_all(inv or {}, cfg_dict) if inv else {}
    vms = expand_batch(cfg_dict)
    console.print("[bold]Selection[/bold]"); console.print(json.dumps(sel, ensure_ascii=False, indent=2))
    console.print(f"[bold]VMs: {len(vms)}[/bold]"); console.print(json.dumps(vms, ensure_ascii=False, indent=2))


@app.command()
def deploy(config: str = typer.Option(..., "--config", "-c", help="Config YAML path"), creds: Optional[str] = typer.Option(None), yes: bool = typer.Option(False, help="Skip confirmation")):
    from .config import load_config, resolve_vcenter_creds
    from .batch.planner import expand_batch
    from .batch.state import create_batch, next_batch_id
    from .batch.executor import run_batch
    from .vsphere.client import connect, disconnect

    cfg = load_config(config)
    if creds:
        cfg.vcenter.credsRef = creds
    host, port, user, pwd = resolve_vcenter_creds(cfg)
    if not host or not user:
        console.print("[red]vCenter host/user not resolved[/red]"); raise typer.Exit(1)
    cfg_dict = cfg.model_dump()
    vms = expand_batch(cfg_dict)
    if not vms:
        console.print("[red]No VMs to deploy[/red]"); raise typer.Exit(1)
    if not yes:
        console.print(f"Deploy {len(vms)} VM(s) to {host}? Use --yes to skip prompt.")
        if not typer.confirm("Continue?"):
            raise typer.Exit(0)
    batch_id = next_batch_id()
    create_batch(batch_id, cfg_dict)
    console.print(f"[green]Batch {batch_id} started: {len(vms)} VM(s)[/green]")

    # Build deploy_fn similar to web deploy
    defaults = cfg_dict.get("defaults") or {}
    vc = cfg_dict.get("vcenter") or {}
    ippool = cfg_dict.get("ipPool") or {}

    def deploy_one(vm: dict):
        name = vm.get("name")
        template = vm.get("template")
        iso = vm.get("iso")
        cpu = int(vm.get("cpu") or 2)
        mem = int(vm.get("memoryMB") or 4096)
        folder = vm.get("folder") or defaults.get("folder")
        guest_id = vm.get("guestId") or defaults.get("guestId") or "ubuntu64Guest"
        nets = vm.get("networks") or []
        ip = nets[0].get("ip") if nets else None
        netmask = ippool.get("netmask")
        gateway = ippool.get("gateway")
        custom = None
        if ip and ip not in ("auto", "dhcp"):
            try:
                from .vsphere.customization import build_linux_customization

                custom = build_linux_customization(name, "", ippool.get("dns"), ip, netmask, gateway)
            except Exception:
                custom = None
        si = connect(host, port, user, pwd)
        try:
            from .vsphere.deploy import find_vm, clone_from_template, create_vm_from_iso
            from .vsphere.tasks import wait_for_task

            content = si.RetrieveContent()
            if find_vm(content, name, folder) is not None:
                return {"ok": True, "skipped": True}
            if template:
                task = clone_from_template(si, template, name, vc.get("datacenter"), vc.get("cluster") if vc.get("cluster") != "auto" else None, vc.get("datastore") if vc.get("datastore") != "auto" else None, folder, cpu=cpu, memory_mb=mem, customization_spec=custom)
                res = wait_for_task(task, timeout=1800)
                return {"ok": res["state"] == "success", "error": res.get("error")}
            elif iso:
                ds_name = vc.get("datastore") if vc.get("datastore") and vc.get("datastore") != "auto" else None
                if iso.startswith("["):
                    try:
                        ds_name = iso.split("]")[0].lstrip("[")
                    except Exception:
                        pass
                if not ds_name:
                    return {"ok": False, "error": "Datastore required for ISO"}
                vm_obj = create_vm_from_iso(si, name, ds_name, iso, guest_id, cpu, mem, int(vm.get("diskGB") or 40), None, folder, vc.get("datacenter"))
                return {"ok": True, "moid": getattr(vm_obj, "_moId", "") if vm_obj else ""}
            return {"ok": False, "error": "template or iso required"}
        finally:
            disconnect(si)

    concurrency = int((cfg_dict.get("batch") or {}).get("concurrency") or 5)
    on_error = (cfg_dict.get("batch") or {}).get("onError") or "continue"
    result = run_batch(batch_id, vms, deploy_one, concurrency=concurrency, on_error=on_error)
    console.print(result)
    if result["failed"]:
        raise typer.Exit(2)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8080),
    debug: bool = typer.Option(False, "--debug", help="Enable Flask debug + DEBUG log level (or set VSPHERE_DEBUG=1)"),
):
    import logging

    from .utils.logging import setup_logging
    from .web.app import create_app

    # Env var is an alias so `VSPHERE_DEBUG=1 bash start.sh` works without CLI flag.
    if os.environ.get("VSPHERE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        debug = True

    # Logging: DEBUG in debug mode unless LOG_LEVEL is explicitly set.
    level = os.environ.get("LOG_LEVEL") or ("DEBUG" if debug else "INFO")
    setup_logging(level)
    if debug:
        logging.getLogger().setLevel(getattr(logging, level.upper(), logging.DEBUG))

    app_flask = create_app()
    mode = "debug" if debug else "production"
    console.print(f"[green]Serving on http://{host}:{port}  ({mode})[/green]")
    if debug:
        console.print("[yellow]Debug mode ON — verbose logs + auto-reload; do not use in production.[/yellow]")
    app_flask.run(host=host, port=port, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    app()
