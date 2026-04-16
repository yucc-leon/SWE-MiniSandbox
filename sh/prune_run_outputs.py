#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import shutil
from pathlib import Path

import yaml


def load_status_map(path: Path) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text()) or {}
    mapping = data.get("instances_by_exit_status", data)
    if not isinstance(mapping, dict):
        return {}
    return {str(k): [str(x) for x in (v or [])] for k, v in mapping.items()}


def should_match(status: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(status, pat) for pat in patterns)


def prune_output_dir(output_dir: Path, patterns: list[str], dry_run: bool) -> dict[str, list[str]]:
    status_yaml = output_dir / "run_batch_exit_statuses.yaml"
    if not status_yaml.exists():
        raise FileNotFoundError(f"missing status yaml: {status_yaml}")
    status_map = load_status_map(status_yaml)
    removed: dict[str, list[str]] = {}
    for status, instance_ids in status_map.items():
        if not should_match(status, patterns):
            continue
        bucket = removed.setdefault(status, [])
        for instance_id in instance_ids:
            instance_dir = output_dir / instance_id
            bucket.append(instance_id)
            if dry_run:
                continue
            if instance_dir.exists():
                shutil.rmtree(instance_dir)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete selected instance output dirs so run-batch can rerun only those instances."
    )
    parser.add_argument("--output-dir", type=Path, action="append", required=True)
    parser.add_argument(
        "--status",
        action="append",
        required=True,
        help="Status glob to prune, e.g. 'Uncaught*' or 'exit_error' or 'submitted (exit_error)'.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    total = 0
    for output_dir in args.output_dir:
        removed = prune_output_dir(output_dir.resolve(), args.status, args.dry_run)
        print(f"[prune] output_dir={output_dir}")
        for status, instance_ids in removed.items():
            total += len(instance_ids)
            print(f"  {status}: {len(instance_ids)}")
            for instance_id in instance_ids[:10]:
                print(f"    - {instance_id}")
    print(f"[prune] total_instances={total} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
