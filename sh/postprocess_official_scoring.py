#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

import yaml


def _collect_output_dirs(runtime_root: Path, explicit: list[Path]) -> list[Path]:
    if explicit:
        return explicit
    single = runtime_root / "output"
    if single.exists():
        return [single]
    shard_dirs = sorted(p for p in runtime_root.glob("shard*/output") if p.is_dir())
    if shard_dirs:
        return shard_dirs
    msg = f"No output directories found under {runtime_root}"
    raise FileNotFoundError(msg)


def _load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        return {str(k): dict(v or {}) for k, v in payload.items()}
    msg = f"Unsupported predictions format in {path}"
    raise ValueError(msg)


def _load_statuses(paths: list[Path]) -> dict[str, str]:
    status_by_instance: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        payload = yaml.safe_load(path.read_text()) or {}
        status_map = payload.get("instances_by_exit_status", payload)
        if not isinstance(status_map, dict):
            continue
        for status, instance_ids in status_map.items():
            if not isinstance(instance_ids, list):
                continue
            for instance_id in instance_ids:
                status_by_instance.setdefault(str(instance_id), str(status))
    return status_by_instance


def _load_eval_outputs(output_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for output_dir in output_dirs:
        for pred_path in sorted(output_dir.rglob("*.pred")):
            try:
                payload = json.loads(pred_path.read_text())
            except Exception:
                continue
            instance_id = str(payload.get("instance_id") or pred_path.stem)
            results[instance_id] = payload
    return results


def _safe_stats(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    ordered = sorted(values)

    def percentile(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        idx = (len(ordered) - 1) * p
        lower = int(idx)
        upper = min(lower + 1, len(ordered) - 1)
        frac = idx - lower
        return ordered[lower] * (1 - frac) + ordered[upper] * frac

    total = sum(ordered)
    return {
        "count": len(ordered),
        "sum": total,
        "avg": total / len(ordered),
        "min": ordered[0],
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "max": ordered[-1],
    }


def _load_traj_infos(output_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    traj_infos: dict[str, dict[str, Any]] = {}
    for output_dir in output_dirs:
        for traj_path in sorted(output_dir.rglob("*.traj")):
            try:
                payload = json.loads(traj_path.read_text())
            except Exception:
                continue
            instance_id = str(payload.get("info", {}).get("instance_id") or traj_path.stem)
            info = payload.get("info")
            if isinstance(info, dict):
                traj_infos[instance_id] = info
    return traj_infos


def _build_exit_diagnostics_summary(traj_infos: dict[str, dict[str, Any]]) -> dict[str, Any]:
    categories = Counter()
    format_failure_types = Counter()
    exception_types = Counter()
    timeout_like_instances = 0
    instance_count = 0

    for info in traj_infos.values():
        diagnostics = info.get("exit_diagnostics")
        if not isinstance(diagnostics, dict):
            continue
        instance_count += 1
        category = diagnostics.get("category")
        if category:
            categories[str(category)] += 1
        if diagnostics.get("request_timeout_like"):
            timeout_like_instances += 1
        exception_type = diagnostics.get("exception_type")
        if exception_type:
            exception_types[str(exception_type)] += 1
        failures = diagnostics.get("format_failures")
        if isinstance(failures, list):
            for failure in failures:
                if not isinstance(failure, dict):
                    continue
                failure_type = failure.get("failure_type")
                if failure_type:
                    format_failure_types[str(failure_type)] += 1

    return {
        "instance_count_with_exit_diagnostics": instance_count,
        "categories": dict(categories),
        "format_failure_types": dict(format_failure_types),
        "exception_types": dict(exception_types),
        "request_timeout_like_instances": timeout_like_instances,
    }


def _build_lm_request_summary(output_dirs: list[Path]) -> dict[str, Any]:
    requests_payload: list[dict[str, Any]] = []

    for output_dir in output_dirs:
        for records_path in sorted(output_dir.rglob("lm_request_records.json")):
            instance_id = records_path.parent.name
            try:
                payload = json.loads(records_path.read_text())
            except Exception:
                continue
            if not isinstance(payload, list):
                continue
            for record in payload:
                if not isinstance(record, dict):
                    continue
                enriched = dict(record)
                enriched["instance_id"] = instance_id
                requests_payload.append(enriched)

    duration_values = [
        float(record["duration_s"])
        for record in requests_payload
        if isinstance(record.get("duration_s"), (int, float))
    ]
    input_token_values = [
        float(record["input_tokens"])
        for record in requests_payload
        if isinstance(record.get("input_tokens"), (int, float))
    ]
    message_char_values = [
        float(record["message_chars"])
        for record in requests_payload
        if isinstance(record.get("message_chars"), (int, float))
    ]
    status_counts = Counter(str(record.get("status") or "unknown") for record in requests_payload)
    exception_types = Counter(
        str(record.get("exception_type"))
        for record in requests_payload
        if record.get("exception_type")
    )
    timeout_like_count = sum(1 for record in requests_payload if record.get("request_timeout_like"))
    slowest_requests = [
        {
            "instance_id": record.get("instance_id"),
            "request_index": record.get("request_index"),
            "duration_s": record.get("duration_s"),
            "status": record.get("status"),
            "input_tokens": record.get("input_tokens"),
            "message_chars": record.get("message_chars"),
            "exception_type": record.get("exception_type"),
        }
        for record in sorted(
            requests_payload,
            key=lambda record: float(record.get("duration_s") or 0.0),
            reverse=True,
        )[:10]
    ]

    return {
        "instance_count_with_lm_requests": len({record["instance_id"] for record in requests_payload}),
        "request_count": len(requests_payload),
        "status_counts": dict(status_counts),
        "exception_types": dict(exception_types),
        "request_timeout_like_count": timeout_like_count,
        "duration_seconds": _safe_stats(duration_values),
        "input_tokens": _safe_stats(input_token_values),
        "message_chars": _safe_stats(message_char_values),
        "slowest_requests": slowest_requests,
    }


def _apply_filter_and_slice(
    instance_ids: list[str], filter_pattern: str, slice_expr: str
) -> list[str]:
    filtered = [iid for iid in instance_ids if re.fullmatch(filter_pattern, iid)]
    if not slice_expr:
        return filtered
    parts = slice_expr.split(":")
    if len(parts) > 3:
        msg = f"Invalid slice expression: {slice_expr}"
        raise ValueError(msg)
    values = [int(part) if part else None for part in parts]
    values += [None] * (3 - len(values))
    return filtered[slice(*values)]


def _load_dataset_instance_ids(
    dataset_path: str, split: str, shuffle_seed: int | None
) -> list[str]:
    from datasets import load_dataset

    dataset = load_dataset(dataset_path, split=split)  # type: ignore[arg-type]
    if shuffle_seed is not None:
        dataset = dataset.shuffle(shuffle_seed)
    return [str(row["instance_id"]) for row in dataset]


def _derive_instance_ids_without_dataset(
    input_predictions: dict[str, dict[str, Any]],
    status_by_instance: dict[str, str],
    eval_outputs: dict[str, dict[str, Any]],
) -> list[str]:
    instance_ids = set(input_predictions)
    instance_ids.update(status_by_instance)
    instance_ids.update(eval_outputs)
    return sorted(instance_ids)


def _summarize_instance_rows(
    *,
    dataset_ids: list[str],
    input_predictions: dict[str, dict[str, Any]],
    eval_outputs: dict[str, dict[str, Any]],
    traj_infos: dict[str, dict[str, Any]],
    status_by_instance: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    submitted_ids: set[str] = set()
    scored_ids: set[str] = set()
    completed_ids: set[str] = set()
    scored_without_submission_ids: set[str] = set()
    resolved_ids: set[str] = set()
    unresolved_ids: set[str] = set()
    empty_patch_ids: set[str] = set()
    error_ids: set[str] = set()
    incomplete_ids: set[str] = set()
    instance_rows: list[dict[str, Any]] = []

    for instance_id in dataset_ids:
        pred = input_predictions.get(instance_id)
        eval_pred = eval_outputs.get(instance_id)
        traj_info = traj_infos.get(instance_id, {})
        exit_diagnostics = traj_info.get("exit_diagnostics") if isinstance(traj_info, dict) else None
        exit_status = status_by_instance.get(instance_id)

        submitted = pred is not None
        model_patch = "" if pred is None else str(pred.get("model_patch") or "")
        empty_patch = submitted and not model_patch.strip()
        if submitted:
            submitted_ids.add(instance_id)
        else:
            incomplete_ids.add(instance_id)
        if empty_patch:
            empty_patch_ids.add(instance_id)

        scored = eval_pred is not None
        if scored:
            scored_ids.add(instance_id)
        scored_without_submission = scored and not submitted
        if scored_without_submission:
            scored_without_submission_ids.add(instance_id)

        reward = None if eval_pred is None else eval_pred.get("reward")
        completed = submitted and reward is not None
        if completed:
            completed_ids.add(instance_id)

        resolved = False
        unresolved = False
        error = False
        if reward is None:
            if exit_status is not None:
                error = True
                error_ids.add(instance_id)
        else:
            try:
                resolved = float(reward) > 0
            except Exception:
                error = True
                error_ids.add(instance_id)
            else:
                if submitted:
                    if resolved:
                        resolved_ids.add(instance_id)
                    else:
                        unresolved = True
                        unresolved_ids.add(instance_id)

        instance_rows.append(
            {
                "instance_id": instance_id,
                "submitted": submitted,
                "scored": scored,
                "completed": completed,
                "scored_without_submission": scored_without_submission,
                "resolved": resolved,
                "unresolved": unresolved,
                "empty_patch": empty_patch,
                "error": error,
                "exit_status": exit_status,
                "exit_diagnostics": exit_diagnostics,
                "reward": reward,
                "input_patch_chars": len(model_patch),
            }
        )

    id_sets = {
        "submitted_ids": submitted_ids,
        "scored_ids": scored_ids,
        "completed_ids": completed_ids,
        "scored_without_submission_ids": scored_without_submission_ids,
        "resolved_ids": resolved_ids,
        "unresolved_ids": unresolved_ids,
        "empty_patch_ids": empty_patch_ids,
        "error_ids": error_ids,
        "incomplete_ids": incomplete_ids,
    }
    return instance_rows, id_sets


def _build_results_payload(
    *,
    dataset_ids: list[str],
    dataset_size: int | None,
    id_sets: dict[str, set[str]],
    traj_infos: dict[str, dict[str, Any]],
    diagnostics_output_dirs: list[Path],
    predictions_path: Path,
) -> dict[str, Any]:
    total_instances = dataset_size or len(dataset_ids)
    submitted_ids = id_sets["submitted_ids"]
    scored_ids = id_sets["scored_ids"]
    completed_ids = id_sets["completed_ids"]
    scored_without_submission_ids = id_sets["scored_without_submission_ids"]
    resolved_ids = id_sets["resolved_ids"]
    unresolved_ids = id_sets["unresolved_ids"]
    empty_patch_ids = id_sets["empty_patch_ids"]
    error_ids = id_sets["error_ids"]
    incomplete_ids = id_sets["incomplete_ids"]

    return {
        "total_instances": total_instances,
        "submitted_instances": len(submitted_ids),
        "scored_instances": len(scored_ids),
        "completed_instances": len(completed_ids),
        "resolved_instances": len(resolved_ids),
        "unresolved_instances": len(unresolved_ids),
        "empty_patch_instances": len(empty_patch_ids),
        "error_instances": len(error_ids),
        "scored_without_submission_instances": len(scored_without_submission_ids),
        "submitted_rate": (len(submitted_ids) / total_instances) if total_instances else 0.0,
        "resolved_rate": (len(resolved_ids) / total_instances) if total_instances else 0.0,
        "completed_ids": sorted(completed_ids),
        "scored_ids": sorted(scored_ids),
        "incomplete_ids": sorted(incomplete_ids),
        "empty_patch_ids": sorted(empty_patch_ids),
        "submitted_ids": sorted(submitted_ids),
        "scored_without_submission_ids": sorted(scored_without_submission_ids),
        "resolved_ids": sorted(resolved_ids),
        "unresolved_ids": sorted(unresolved_ids),
        "error_ids": sorted(error_ids),
        "exit_diagnostics_summary": _build_exit_diagnostics_summary(traj_infos),
        "lm_request_summary": _build_lm_request_summary(diagnostics_output_dirs),
        "schema_version": 3,
        "scoring_mode": "sandbox_swebench_harness_grading",
        "source_predictions_path": str(predictions_path.resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an official-style results report from docker-free patch scoring outputs."
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--predictions-path", type=Path, required=True)
    parser.add_argument("--dataset-path", type=str, default=None)
    parser.add_argument("--dataset-split", type=str, default="test")
    parser.add_argument("--dataset-shuffle-seed", type=int, default=42)
    parser.add_argument("--dataset-size", type=int, default=None)
    parser.add_argument("--instances-filter", type=str, default=".*")
    parser.add_argument("--instances-slice", type=str, default="")
    parser.add_argument("--output-dir", type=Path, action="append", default=[])
    parser.add_argument(
        "--diagnostics-output-dir",
        type=Path,
        action="append",
        default=[],
        help="Optional output dir(s) from the generation run used to read traj/LM diagnostics.",
    )
    parser.add_argument("--results-output", type=Path)
    parser.add_argument("--instance-results-output", type=Path)
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    output_dirs = _collect_output_dirs(runtime_root, [p.resolve() for p in args.output_dir])
    diagnostics_output_dirs = [p.resolve() for p in args.diagnostics_output_dir] or output_dirs
    input_predictions = _load_predictions(args.predictions_path.resolve())
    eval_outputs = _load_eval_outputs(output_dirs)
    traj_infos = _load_traj_infos(diagnostics_output_dirs)
    status_paths = [output_dir / "run_batch_exit_statuses.yaml" for output_dir in output_dirs]
    status_by_instance = _load_statuses(status_paths)

    if args.dataset_path:
        dataset_ids = _load_dataset_instance_ids(
            args.dataset_path, args.dataset_split, args.dataset_shuffle_seed
        )
    else:
        dataset_ids = _derive_instance_ids_without_dataset(
            input_predictions=input_predictions,
            status_by_instance=status_by_instance,
            eval_outputs=eval_outputs,
        )
    dataset_ids = _apply_filter_and_slice(dataset_ids, args.instances_filter, args.instances_slice)
    instance_rows, id_sets = _summarize_instance_rows(
        dataset_ids=dataset_ids,
        input_predictions=input_predictions,
        eval_outputs=eval_outputs,
        traj_infos=traj_infos,
        status_by_instance=status_by_instance,
    )
    results = _build_results_payload(
        dataset_ids=dataset_ids,
        dataset_size=args.dataset_size,
        id_sets=id_sets,
        traj_infos=traj_infos,
        diagnostics_output_dirs=diagnostics_output_dirs,
        predictions_path=args.predictions_path,
    )

    results_output = (args.results_output or (runtime_root / "results.json")).resolve()
    instance_results_output = (
        args.instance_results_output or (runtime_root / "instance_results.jsonl")
    ).resolve()
    results_output.write_text(json.dumps(results, indent=2, sort_keys=False))
    instance_results_output.write_text(
        "\n".join(json.dumps(row, sort_keys=False) for row in instance_rows) + "\n"
    )

    print(json.dumps(results, indent=2, sort_keys=False))
    print(f"results_output={results_output}")
    print(f"instance_results_output={instance_results_output}")


if __name__ == "__main__":
    main()
