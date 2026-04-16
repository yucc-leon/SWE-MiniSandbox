from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from swesandbox.prep_plan import build_bucket
from swesandbox.prep_plan import dump_json
from swesandbox.prep_plan import load_instances


def load_status_map(status_yaml: str) -> dict[str, list[str]]:
    content = yaml.safe_load(Path(status_yaml).read_text(encoding="utf-8")) or {}
    return content.get("instances_by_exit_status", {})


def summarize_failures(
    instances: list[dict[str, Any]],
    status_map: dict[str, list[str]],
    data_type: str,
) -> dict[str, Any]:
    instance_map = {str(item.get("instance_id")): item for item in instances}
    bucket_failures: dict[str, Counter[str]] = {}
    bucket_examples: dict[str, list[str]] = {}
    missing_instances: list[str] = []

    for exit_status, instance_ids in status_map.items():
        for instance_id in instance_ids:
            item = instance_map.get(str(instance_id))
            if item is None:
                missing_instances.append(str(instance_id))
                continue
            bucket = build_bucket(item, data_type=data_type)
            bucket_key = bucket.key
            status_counter = bucket_failures.setdefault(bucket_key, Counter())
            status_counter[exit_status] += 1
            samples = bucket_examples.setdefault(bucket_key, [])
            if len(samples) < 5:
                samples.append(str(instance_id))

    rows: list[dict[str, Any]] = []
    for bucket_key, status_counter in sorted(
        bucket_failures.items(),
        key=lambda item: sum(item[1].values()),
        reverse=True,
    ):
        total = sum(status_counter.values())
        repo_id, python_version, image_name = bucket_key.rsplit("/", 2)
        rows.append(
            {
                "bucket_key": bucket_key,
                "repo_id": repo_id,
                "python_version": python_version,
                "image_name": image_name,
                "total_failures": total,
                "exit_status_counts": dict(status_counter),
                "sample_instance_ids": bucket_examples.get(bucket_key, []),
            }
        )

    return {
        "num_buckets_with_failures": len(rows),
        "missing_instances": missing_instances,
        "bucket_failure_summary": rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize run-batch exit statuses by environment bucket."
    )
    parser.add_argument("--path", required=True, help="HF dataset name, local save_to_disk path, json or jsonl.")
    parser.add_argument("--status-yaml", required=True, help="run_batch_exit_statuses.yaml path.")
    parser.add_argument("--data-type", default="swesmith", choices=["swebench", "swesmith", "skyrl"])
    parser.add_argument("--split", default="train")
    parser.add_argument("--subset", default="", help="Optional subset filter applied to loaded rows.")
    parser.add_argument("--instance-slice", default="", help="Optional Python-style slice, e.g. :100 or 1:500:2.")
    parser.add_argument("--load-from-disk", action="store_true")
    parser.add_argument("--output-json", default="", help="Write failure summary JSON to this path.")
    parser.add_argument("--top-k", type=int, default=20)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    instances = load_instances(
        path=args.path,
        split=args.split,
        load_from_disk=args.load_from_disk,
        subset=args.subset or None,
        slice_spec=args.instance_slice or None,
    )
    status_map = load_status_map(args.status_yaml)
    summary = summarize_failures(instances, status_map=status_map, data_type=args.data_type)
    summary["dataset_path"] = args.path
    summary["subset"] = args.subset or None
    summary["instance_slice"] = args.instance_slice or None
    summary["status_yaml"] = args.status_yaml
    summary["top_buckets"] = summary["bucket_failure_summary"][: args.top_k]

    dump_json(args.output_json, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.output_json:
        print(f"\nsummary written to: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
