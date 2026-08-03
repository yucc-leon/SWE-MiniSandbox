from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EnvBucket:
    repo_id: str
    python_version: str
    image_name: str

    @property
    def key(self) -> str:
        return f"{self.repo_id}/{self.python_version}/{self.image_name}"


def map_to_git_id_light(ds: dict[str, Any], data_type: str) -> str:
    if data_type == "swebench":
        return ds["repo"]
    if data_type == "swesmith":
        if "repo" in ds and ds["repo"]:
            return ds["repo"]
        instance_id = ds["instance_id"]
        return ".".join(instance_id.split(".")[:2])
    return ds["repo"]


def get_python_version(ds: dict[str, Any]) -> str:
    return str(ds.get("version") or ds.get("python_version") or "latest")


def get_image_name(ds: dict[str, Any]) -> str:
    return str(ds.get("image_name") or "default")


def build_bucket(ds: dict[str, Any], data_type: str) -> EnvBucket:
    return EnvBucket(
        repo_id=map_to_git_id_light(ds, data_type=data_type),
        python_version=get_python_version(ds),
        image_name=get_image_name(ds),
    )


def summarize_instances(
    instances: list[dict[str, Any]],
    data_type: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    counter: Counter[str] = Counter()
    first_instance_by_bucket: dict[str, dict[str, Any]] = {}
    sample_ids_by_bucket: dict[str, list[str]] = {}
    bucket_meta: dict[str, EnvBucket] = {}

    for item in instances:
        bucket = build_bucket(item, data_type=data_type)
        bucket_key = bucket.key
        counter[bucket_key] += 1
        bucket_meta[bucket_key] = bucket
        if bucket_key not in first_instance_by_bucket:
            first_instance_by_bucket[bucket_key] = item
        sample_ids = sample_ids_by_bucket.setdefault(bucket_key, [])
        if len(sample_ids) < 5:
            sample_ids.append(str(item.get("instance_id", "unknown")))

    bucket_rows: list[dict[str, Any]] = []
    for bucket_key, count in counter.most_common():
        bucket = bucket_meta[bucket_key]
        bucket_rows.append(
            {
                "bucket_key": bucket_key,
                "repo_id": bucket.repo_id,
                "python_version": bucket.python_version,
                "image_name": bucket.image_name,
                "count": count,
                "sample_instance_ids": sample_ids_by_bucket[bucket_key],
            }
        )

    prewarm_rows: list[dict[str, Any]] = []
    for bucket_row in bucket_rows:
        bucket_key = bucket_row["bucket_key"]
        instance = dict(first_instance_by_bucket[bucket_key])
        instance["_prep_bucket"] = bucket_key
        prewarm_rows.append(instance)

    return bucket_rows, prewarm_rows


def _filter_subset(instances: list[dict[str, Any]], subset: str | None) -> list[dict[str, Any]]:
    if not subset:
        return instances
    return [item for item in instances if str(item.get("subset", "")) == subset]


def _apply_slice(instances: list[dict[str, Any]], slice_spec: str | None) -> list[dict[str, Any]]:
    if not slice_spec:
        return instances
    start_str, stop_str, step_str = (slice_spec.split(":") + ["", ""])[:3]
    start = int(start_str) if start_str else None
    stop = int(stop_str) if stop_str else None
    step = int(step_str) if step_str else None
    return instances[slice(start, stop, step)]


def load_instances(
    path: str,
    split: str,
    load_from_disk: bool,
    subset: str | None = None,
    slice_spec: str | None = None,
) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix == ".jsonl":
        with source.open("r", encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        return _apply_slice(_filter_subset(rows, subset), slice_spec)
    if source.suffix == ".json":
        rows = json.loads(source.read_text(encoding="utf-8"))
        return _apply_slice(_filter_subset(rows, subset), slice_spec)

    try:
        from datasets import load_dataset, load_from_disk
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "datasets is required for HF/save_to_disk inputs. "
            "Use a json/jsonl input, or install datasets in the current env."
        ) from exc

    parquet_files: list[str] = []
    if source.is_file() and source.suffix == ".parquet":
        parquet_files = [str(source)]
    elif source.is_dir():
        parquet_files = [str(p) for p in sorted(source.glob("*.parquet"))]

    if parquet_files:
        ds = load_dataset("parquet", data_files={split: parquet_files}, split=split)
    elif load_from_disk:
        ds = load_from_disk(path)
    else:
        ds = load_dataset(path, split=split)

    if hasattr(ds, "to_list"):
        rows = ds.to_list()
    else:
        rows = list(ds)
    return _apply_slice(_filter_subset(rows, subset), slice_spec)


def dump_json(path: str | None, content: Any) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dump_jsonl(path: str | None, rows: list[dict[str, Any]]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize dataset environment buckets and generate a prewarm dataset."
    )
    parser.add_argument("--path", required=True, help="HF dataset name or local dataset path.")
    parser.add_argument("--data-type", default="swesmith", choices=["swebench", "swesmith", "skyrl"])
    parser.add_argument("--split", default="train")
    parser.add_argument("--subset", default="", help="Optional subset filter applied to loaded rows.")
    parser.add_argument("--instance-slice", default="", help="Optional Python-style slice, e.g. :100 or 1:500:2.")
    parser.add_argument(
        "--load-from-disk",
        action="store_true",
        help="Load a local datasets.save_to_disk directory instead of HF hub.",
    )
    parser.add_argument(
        "--output-summary-json",
        default="",
        help="Write bucket summary JSON to this path.",
    )
    parser.add_argument(
        "--output-prewarm-jsonl",
        default="",
        help="Write one representative instance per environment bucket as JSONL.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="How many top buckets to print to stdout.",
    )
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
    bucket_rows, prewarm_rows = summarize_instances(instances, data_type=args.data_type)

    summary = {
        "dataset_path": args.path,
        "data_type": args.data_type,
        "subset": args.subset or None,
        "instance_slice": args.instance_slice or None,
        "num_instances": len(instances),
        "num_env_buckets": len(bucket_rows),
        "top_buckets": bucket_rows[: args.top_k],
    }

    dump_json(args.output_summary_json, summary)
    dump_jsonl(args.output_prewarm_jsonl, prewarm_rows)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.output_prewarm_jsonl:
        print(f"\nprewarm dataset written to: {args.output_prewarm_jsonl}")
    if args.output_summary_json:
        print(f"summary written to: {args.output_summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
