from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from swesandbox.prep_plan import dump_json
from swesandbox.prep_plan import dump_jsonl
from swesandbox.prep_plan import load_instances
from swesandbox.prep_plan import summarize_instances


def to_simple_batch_instance(item: dict[str, Any]) -> dict[str, Any]:
    instance_id = str(item["instance_id"])
    return {
        "repo_type": item.get("repo_type", "github"),
        "ds": item,
        "image_name": item.get("image_name", "default"),
        "problem_statement": item.get("problem_statement", ""),
        "instance_id": instance_id,
        "traj_id": item.get("traj_id", instance_id),
        "repo_name": "testbed",
        "base_commit": item.get("base_commit", instance_id),
        "extra_fields": {"fail_to_pass": item.get("FAIL_TO_PASS", [])},
    }


def build_run_command(args: argparse.Namespace, instances_path: Path) -> list[str]:
    cmd = [
        args.python_bin,
        "-m",
        "sweagent",
        "run-batch",
        "--config",
        args.config,
        "--agent.type",
        "empty",
        "--instances.type",
        "file",
        "--instances.path",
        str(instances_path),
        "--output_dir",
        args.output_dir,
        "--num_workers",
        str(args.num_workers),
    ]
    optional_pairs = [
        ("--instances.deployment.root_base", args.root_base),
        ("--instances.deployment.git_base_path", args.git_base_path),
        ("--instances.deployment.shared_venv", args.shared_venv),
        ("--instances.deployment.conda_env", args.conda_env),
        ("--instances.deployment.wheelhouse", args.wheelhouse),
        ("--instances.deployment.tool_path", args.tool_path),
        ("--instances.deployment.eval_timeout", str(args.eval_timeout) if args.eval_timeout else ""),
    ]
    for key, value in optional_pairs:
        if value:
            cmd.extend([key, value])
    for extra in args.extra_arg:
        cmd.extend(shlex.split(extra))
    return cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a prewarm dataset and optionally run empty-agent environment preparation."
    )
    parser.add_argument("--path", required=True, help="HF dataset name, save_to_disk path, json or jsonl.")
    parser.add_argument("--data-type", default="swesmith", choices=["swebench", "swesmith", "skyrl"])
    parser.add_argument("--split", default="train")
    parser.add_argument("--subset", default="", help="Optional subset filter applied to loaded rows.")
    parser.add_argument("--instance-slice", default="", help="Optional Python-style slice, e.g. :100 or 1:500:2.")
    parser.add_argument("--load-from-disk", action="store_true")
    parser.add_argument("--prep-dir", required=True, help="Directory to store generated prep files.")
    parser.add_argument("--config", required=True, help="Base sweagent config YAML.")
    parser.add_argument("--output-dir", required=True, help="run-batch output directory.")
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--python-bin", default="python")
    parser.add_argument("--root-base", default="")
    parser.add_argument("--git-base-path", default="")
    parser.add_argument("--shared-venv", default="")
    parser.add_argument("--conda-env", default="")
    parser.add_argument("--wheelhouse", default="")
    parser.add_argument("--tool-path", default="")
    parser.add_argument("--eval-timeout", type=int, default=0)
    parser.add_argument("--extra-arg", action="append", default=[], help="Extra run-batch arg fragment.")
    parser.add_argument("--run", action="store_true", help="Actually execute run-batch after generating files.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    prep_dir = Path(args.prep_dir)
    prep_dir.mkdir(parents=True, exist_ok=True)

    instances = load_instances(
        path=args.path,
        split=args.split,
        load_from_disk=args.load_from_disk,
        subset=args.subset or None,
        slice_spec=args.instance_slice or None,
    )
    summary_rows, prewarm_rows = summarize_instances(instances, data_type=args.data_type)
    simple_rows = [to_simple_batch_instance(item) for item in prewarm_rows]

    summary = {
        "dataset_path": args.path,
        "data_type": args.data_type,
        "subset": args.subset or None,
        "instance_slice": args.instance_slice or None,
        "num_instances": len(instances),
        "num_env_buckets": len(summary_rows),
        "top_buckets": summary_rows[:20],
    }
    summary_path = prep_dir / "prep-summary.json"
    raw_path = prep_dir / "prewarm-dataset.jsonl"
    simple_path = prep_dir / "prewarm-instances.jsonl"
    cmd_path = prep_dir / "run-prewarm.sh"

    dump_json(str(summary_path), summary)
    dump_jsonl(str(raw_path), prewarm_rows)
    dump_jsonl(str(simple_path), simple_rows)

    cmd = build_run_command(args, simple_path)
    cmd_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + shlex.join(cmd) + "\n", encoding="utf-8")
    cmd_path.chmod(0o755)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nprewarm dataset: {raw_path}")
    print(f"run-batch instances: {simple_path}")
    print(f"launcher script: {cmd_path}")
    print(f"command: {shlex.join(cmd)}")

    if args.run:
        subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
