#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import yaml


def _load_postprocess_module(repo_root: Path):
    script_path = repo_root / "sh" / "postprocess_official_scoring.py"
    spec = importlib.util.spec_from_file_location("postprocess_official_scoring", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load scoring postprocess module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_run_batch_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError(f"unsupported run_batch config format in {path}")
    return payload


def _derive_dataset_size(slice_expr: str | None) -> int | None:
    if not slice_expr:
        return None
    parts = slice_expr.split(":")
    if len(parts) not in {2, 3}:
        return None
    start = int(parts[0]) if parts[0] else 0
    stop = int(parts[1]) if parts[1] else None
    step = int(parts[2]) if len(parts) == 3 and parts[2] else 1
    if stop is None or step <= 0:
        return None
    if stop <= start:
        return 0
    return len(range(start, stop, step))


def _refresh_runtime(runtime_root: Path, module: Any) -> dict[str, Any]:
    config_path = runtime_root / "output" / "run_batch.config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"missing run_batch config: {config_path}")
    config = _load_run_batch_config(config_path)
    instances = dict(config.get("instances") or {})
    predictions_path = Path(str(instances.get("model_patch_file") or ""))
    if not predictions_path.is_file():
        raise FileNotFoundError(f"missing predictions path for {runtime_root}: {predictions_path}")

    dataset_path = instances.get("database")
    dataset_split = str(instances.get("split") or "test")
    instances_filter = str(instances.get("filter") or ".*")
    instances_slice = str(instances.get("slice") or "")
    dataset_size = _derive_dataset_size(instances_slice)

    output_dirs = module._collect_output_dirs(runtime_root, [])
    input_predictions = module._load_predictions(predictions_path.resolve())
    eval_outputs = module._load_eval_outputs(output_dirs)
    traj_infos = module._load_traj_infos(output_dirs)
    status_paths = [output_dir / "run_batch_exit_statuses.yaml" for output_dir in output_dirs]
    status_by_instance = module._load_statuses(status_paths)

    if dataset_path:
        dataset_ids = module._load_dataset_instance_ids(dataset_path, dataset_split, 42)
    else:
        dataset_ids = module._derive_instance_ids_without_dataset(
            input_predictions=input_predictions,
            status_by_instance=status_by_instance,
            eval_outputs=eval_outputs,
        )
    dataset_ids = module._apply_filter_and_slice(dataset_ids, instances_filter, instances_slice)

    instance_rows, id_sets = module._summarize_instance_rows(
        dataset_ids=dataset_ids,
        input_predictions=input_predictions,
        eval_outputs=eval_outputs,
        traj_infos=traj_infos,
        status_by_instance=status_by_instance,
    )
    results = module._build_results_payload(
        dataset_ids=dataset_ids,
        dataset_size=dataset_size,
        id_sets=id_sets,
        traj_infos=traj_infos,
        diagnostics_output_dirs=output_dirs,
        predictions_path=predictions_path,
    )

    results_path = runtime_root / "results.json"
    instance_results_path = runtime_root / "instance_results.jsonl"
    results_path.write_text(json.dumps(results, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    instance_results_path.write_text(
        "\n".join(json.dumps(row, sort_keys=False) for row in instance_rows) + "\n",
        encoding="utf-8",
    )

    return {
        "runtime_root": str(runtime_root),
        "results_path": str(results_path),
        "instance_results_path": str(instance_results_path),
        "schema_version": results["schema_version"],
        "submitted_instances": results["submitted_instances"],
        "scored_instances": results["scored_instances"],
        "completed_instances": results["completed_instances"],
        "resolved_instances": results["resolved_instances"],
        "scored_without_submission_instances": results["scored_without_submission_instances"],
    }


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _migrate_legacy_runtime(runtime_root: Path) -> dict[str, Any]:
    results_path = runtime_root / "results.json"
    instance_results_path = runtime_root / "instance_results.jsonl"
    if not results_path.is_file():
        raise FileNotFoundError(f"missing legacy results: {results_path}")
    if not instance_results_path.is_file():
        raise FileNotFoundError(f"missing legacy instance results: {instance_results_path}")

    legacy_results = json.loads(results_path.read_text(encoding="utf-8"))
    legacy_rows = _load_jsonl_rows(instance_results_path)
    if not legacy_rows:
        raise ValueError(f"no rows found in {instance_results_path}")

    migrated_rows: list[dict[str, Any]] = []
    submitted_ids: set[str] = set()
    scored_ids: set[str] = set()
    completed_ids: set[str] = set()
    scored_without_submission_ids: set[str] = set()
    resolved_ids: set[str] = set()
    unresolved_ids: set[str] = set()
    empty_patch_ids: set[str] = set()
    error_ids: set[str] = set()
    incomplete_ids: set[str] = set()

    for row in legacy_rows:
        instance_id = str(row["instance_id"])
        submitted = bool(row.get("submitted"))
        scored = bool(row.get("completed"))
        reward = row.get("reward")
        completed = submitted and reward is not None
        scored_without_submission = scored and not submitted
        resolved = bool(row.get("resolved"))
        unresolved = completed and not resolved and reward is not None and not bool(row.get("error"))
        empty_patch = bool(row.get("empty_patch"))
        error = bool(row.get("error"))

        if submitted:
            submitted_ids.add(instance_id)
        else:
            incomplete_ids.add(instance_id)
        if scored:
            scored_ids.add(instance_id)
        if completed:
            completed_ids.add(instance_id)
        if scored_without_submission:
            scored_without_submission_ids.add(instance_id)
        if resolved:
            resolved_ids.add(instance_id)
        if unresolved:
            unresolved_ids.add(instance_id)
        if empty_patch:
            empty_patch_ids.add(instance_id)
        if error:
            error_ids.add(instance_id)

        migrated_rows.append(
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
                "exit_status": row.get("exit_status"),
                "exit_diagnostics": row.get("exit_diagnostics"),
                "reward": reward,
                "input_patch_chars": int(row.get("input_patch_chars") or 0),
            }
        )

    total_instances = int(legacy_results.get("total_instances") or len(migrated_rows))
    migrated_results = {
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
        "exit_diagnostics_summary": legacy_results.get("exit_diagnostics_summary", {}),
        "lm_request_summary": legacy_results.get("lm_request_summary", {}),
        "schema_version": 3,
        "scoring_mode": legacy_results.get("scoring_mode", "sandbox_swebench_harness_grading"),
        "source_predictions_path": legacy_results.get("source_predictions_path", ""),
    }

    results_path.write_text(json.dumps(migrated_results, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    instance_results_path.write_text(
        "\n".join(json.dumps(row, sort_keys=False) for row in migrated_rows) + "\n",
        encoding="utf-8",
    )

    return {
        "runtime_root": str(runtime_root),
        "results_path": str(results_path),
        "instance_results_path": str(instance_results_path),
        "schema_version": migrated_results["schema_version"],
        "submitted_instances": migrated_results["submitted_instances"],
        "scored_instances": migrated_results["scored_instances"],
        "completed_instances": migrated_results["completed_instances"],
        "resolved_instances": migrated_results["resolved_instances"],
        "scored_without_submission_instances": migrated_results["scored_without_submission_instances"],
        "migration_mode": "legacy_results_only",
    }


def build_parser() -> argparse.ArgumentParser:
    runtime_root_base = os.environ.get("RUNTIME_ROOT_BASE", ".runtime").rstrip("/") or ".runtime"
    parser = argparse.ArgumentParser(
        description="Refresh official-style scoring reports using each runtime's saved run_batch config."
    )
    parser.add_argument(
        "--runtime-root",
        action="append",
        default=[],
        help=f"Specific scoring runtime root(s) to refresh. Defaults to {runtime_root_base}/ascend-score-*",
    )
    parser.add_argument(
        "--glob",
        default=f"{runtime_root_base}/ascend-score-*",
        help="Glob used when --runtime-root is not provided.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    module = _load_postprocess_module(repo_root)

    runtime_roots = [Path(path).expanduser().resolve() for path in args.runtime_root]
    if not runtime_roots:
        runtime_roots = sorted(repo_root.glob(args.glob))

    refreshed: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for runtime_root in runtime_roots:
        try:
            refreshed.append(_refresh_runtime(runtime_root.resolve(), module))
        except Exception as exc:  # noqa: BLE001
            try:
                refreshed.append(_migrate_legacy_runtime(runtime_root.resolve()))
            except Exception as fallback_exc:  # noqa: BLE001
                skipped.append(
                    {
                        "runtime_root": str(runtime_root),
                        "reason": f"{type(exc).__name__}: {exc}",
                        "fallback_reason": f"{type(fallback_exc).__name__}: {fallback_exc}",
                    }
                )

    print(json.dumps({"refreshed": refreshed, "skipped": skipped}, indent=2, ensure_ascii=False))
    return 0 if refreshed else 1


if __name__ == "__main__":
    raise SystemExit(main())
