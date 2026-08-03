#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    cmd: str


def _load_status_map(status_path: Path) -> dict[str, list[str]]:
    if not status_path.exists():
        return {}
    data = yaml.safe_load(status_path.read_text()) or {}
    status_map = data.get("instances_by_exit_status", data)
    if not isinstance(status_map, dict):
        return {}
    return {str(k): [str(x) for x in (v or [])] for k, v in status_map.items()}


def _finished_instances(status_path: Path) -> set[str]:
    finished: set[str] = set()
    for ids in _load_status_map(status_path).values():
        finished.update(ids)
    return finished


def _instance_dirs(output_root: Path) -> list[Path]:
    if not output_root.exists():
        return []
    return sorted(p for p in output_root.iterdir() if p.is_dir())


def _latest_mtime(path: Path) -> float:
    latest = path.stat().st_mtime
    if not path.exists():
        return 0.0
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except FileNotFoundError:
            continue
    return latest


def _collect_processes() -> dict[int, ProcInfo]:
    out = subprocess.check_output(["ps", "-eo", "pid=,ppid=,cmd="], text=True)
    procs: dict[int, ProcInfo] = {}
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid = int(parts[0])
        ppid = int(parts[1])
        cmd = parts[2]
        procs[pid] = ProcInfo(pid=pid, ppid=ppid, cmd=cmd)
    return procs


def _find_instance_tree_pids(
    *,
    instance_id: str,
    runtime_root: str,
    run_pid: int,
    self_pid: int,
) -> list[int]:
    procs = _collect_processes()
    selected: set[int] = set()
    for proc in procs.values():
        if proc.pid == self_pid:
            continue
        if instance_id not in proc.cmd:
            continue
        if runtime_root not in proc.cmd:
            continue
        selected.add(proc.pid)
        parent = proc.ppid
        while parent not in (0, 1, run_pid):
            if parent == self_pid or parent in selected:
                break
            info = procs.get(parent)
            if info is None:
                break
            selected.add(parent)
            parent = info.ppid
    return sorted(selected)


def _kill_pids(pids: list[int], sig: int) -> list[int]:
    killed: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
        else:
            killed.append(pid)
    return killed


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch an evaluation run and break single-instance deadlocks.")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--run-pid", type=int, required=True)
    parser.add_argument("--stale-seconds", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--recovery-grace-seconds", type=int, default=180)
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    output_root = runtime_root / "output"
    status_path = output_root / "run_batch_exit_statuses.yaml"
    log_path = runtime_root / "watchdog.log"

    state: dict[str, object] = {
        "stale_since": None,
        "stale_mtime": None,
        "last_unfinished": [],
        "kill_attempted": False,
    }

    def log(event: str, **extra: object) -> None:
        row = {
            "ts": time.time(),
            "event": event,
            "run_pid": args.run_pid,
            **extra,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log(
        "watchdog_started",
        runtime_root=str(runtime_root),
        stale_seconds=args.stale_seconds,
        poll_seconds=args.poll_seconds,
        recovery_grace_seconds=args.recovery_grace_seconds,
    )

    while _is_alive(args.run_pid):
        dirs = _instance_dirs(output_root)
        finished = _finished_instances(status_path)
        unfinished = [p.name for p in dirs if p.name not in finished]

        if not unfinished:
            state["stale_since"] = None
            state["stale_mtime"] = None
            state["last_unfinished"] = []
            state["kill_attempted"] = False
            time.sleep(args.poll_seconds)
            continue

        latest_output_mtime = max((_latest_mtime(output_root),), default=0.0)
        age = time.time() - latest_output_mtime

        if age < args.stale_seconds:
            state["stale_since"] = None
            state["stale_mtime"] = None
            state["last_unfinished"] = unfinished
            state["kill_attempted"] = False
            time.sleep(args.poll_seconds)
            continue

        stale_since = state.get("stale_since")
        stale_mtime = state.get("stale_mtime")
        if stale_since is None or stale_mtime != latest_output_mtime:
            state["stale_since"] = time.time()
            state["stale_mtime"] = latest_output_mtime
            state["last_unfinished"] = unfinished
            state["kill_attempted"] = False
            log(
                "stale_detected",
                unfinished=unfinished,
                unfinished_count=len(unfinished),
                stale_age_seconds=age,
            )
            time.sleep(args.poll_seconds)
            continue

        if not state.get("kill_attempted"):
            all_killed: list[int] = []
            for instance_id in unfinished:
                pids = _find_instance_tree_pids(
                    instance_id=instance_id,
                    runtime_root=str(runtime_root),
                    run_pid=args.run_pid,
                    self_pid=os.getpid(),
                )
                if not pids:
                    continue
                all_killed.extend(_kill_pids(pids, signal.SIGTERM))
            state["kill_attempted"] = True
            log("instance_tree_sigterm", unfinished=unfinished, killed_pids=sorted(set(all_killed)))
            time.sleep(args.recovery_grace_seconds)
            continue

        if not _is_alive(args.run_pid):
            break

        latest_after_kill = _latest_mtime(output_root) if output_root.exists() else 0.0
        if latest_after_kill > float(state["stale_mtime"] or 0):
            log("recovered_after_kill", latest_output_mtime=latest_after_kill)
            state["stale_since"] = None
            state["stale_mtime"] = None
            state["kill_attempted"] = False
            time.sleep(args.poll_seconds)
            continue

        log(
            "run_pid_sigterm",
            unfinished=unfinished,
            stale_age_seconds=age,
            latest_output_mtime=latest_after_kill,
        )
        _kill_pids([args.run_pid], signal.SIGTERM)
        time.sleep(5)
        if _is_alive(args.run_pid):
            log("run_pid_sigkill", unfinished=unfinished)
            _kill_pids([args.run_pid], signal.SIGKILL)
        break

    log("watchdog_stopped", run_alive=_is_alive(args.run_pid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
