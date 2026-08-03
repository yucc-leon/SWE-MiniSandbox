#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> tuple[dict[str, list[str]], float]:
    if not path.exists():
        return {}, 0.0
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        return {}, 0.0
    status_map = data.get("instances_by_exit_status", data)
    if not isinstance(status_map, dict):
        status_map = {}
    total_cost = float(data.get("total_cost", 0) or 0)
    return {str(k): list(v or []) for k, v in status_map.items()}, total_cost


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
        "sum_seconds": total,
        "avg_seconds": total / len(ordered),
        "min_seconds": ordered[0],
        "p50_seconds": percentile(0.5),
        "p95_seconds": percentile(0.95),
        "max_seconds": ordered[-1],
    }


def _merge_status_maps(status_paths: list[Path]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    seen: set[str] = set()
    total_cost = 0.0
    for path in status_paths:
        status_map, path_cost = _load_yaml(path)
        total_cost += path_cost
        for status, instance_ids in status_map.items():
            bucket = merged.setdefault(status, [])
            for instance_id in instance_ids:
                if instance_id in seen:
                    continue
                bucket.append(instance_id)
                seen.add(instance_id)
    return {
        "instances_by_exit_status": merged,
        "total_cost": total_cost,
    }


def _collect_output_dirs(runtime_root: Path, explicit: list[Path]) -> list[Path]:
    if explicit:
        return explicit
    single = runtime_root / "output"
    if single.exists():
        return [single]
    shard_dirs = sorted(
        p for p in runtime_root.glob("shard*/output") if p.is_dir()
    )
    if shard_dirs:
        return shard_dirs
    msg = f"No output directories found under {runtime_root}"
    raise FileNotFoundError(msg)


def _merge_predictions(output_dirs: list[Path], merged_preds_path: Path) -> dict[str, Any]:
    preds: dict[str, Any] = {}
    for output_dir in output_dirs:
        for pred_path in sorted(output_dir.rglob("*.pred")):
            datum = json.loads(pred_path.read_text())
            instance_id = datum["instance_id"]
            datum["model_patch"] = str(datum.get("model_patch") or "")
            if instance_id in preds:
                msg = f"Duplicate prediction for instance_id={instance_id}"
                raise ValueError(msg)
            preds[instance_id] = datum
    merged_preds_path.parent.mkdir(parents=True, exist_ok=True)
    merged_preds_path.write_text(json.dumps(preds, indent=2))
    return preds


_RUNNING_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+swea-run\s+-\s+INFO\s+-\s+Running on instance (?P<instance>\S+)$"
)
_ENV_INIT_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+swea-env-(?P<instance>\S+)\s+-\s+INFO\s+-\s+Environment Initialized$"
)
_STEP_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+swea-agent-(?P<instance>\S+)\s+-\s+INFO\s+-\s+=+ STEP (?P<step>\d+) =+$"
)
_DURATION_PATTERNS = {
    "fresh_shared_venv_seconds": re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+rex-deploy-(?P<instance>\S+)\s+-\s+INFO\s+-\s+Fresh shared venv created in (?P<seconds>\d+(?:\.\d+)?)s"
    ),
    "shared_venv_extract_seconds": re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+rex-deploy-(?P<instance>\S+)\s+-\s+INFO\s+-\s+Shared venv extracted in (?P<seconds>\d+(?:\.\d+)?)s"
    ),
    "install_env_seconds": re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+rex-deploy-(?P<instance>\S+)\s+-\s+INFO\s+-\s+Environment install script completed in (?P<seconds>\d+(?:\.\d+)?)s"
    ),
    "shared_venv_pack_seconds": re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+rex-deploy-(?P<instance>\S+)\s+-\s+INFO\s+-\s+Shared venv cache packed in (?P<seconds>\d+(?:\.\d+)?)s"
    ),
    "git_cache_pack_seconds": re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+rex-deploy-(?P<instance>\S+)\s+-\s+INFO\s+-\s+Git cache packed in (?P<seconds>\d+(?:\.\d+)?)s"
    ),
}


def _parse_ts(value: str) -> float:
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()


def _parse_launcher_timing(launcher_log: Path) -> dict[str, Any]:
    if not launcher_log.exists():
        return {"instances": {}, "wall_time_seconds": None}

    instance_data: dict[str, dict[str, Any]] = {}
    first_ts: float | None = None
    last_ts: float | None = None

    for raw_line in launcher_log.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = False
        for name, pattern in _DURATION_PATTERNS.items():
            m = pattern.match(line)
            if not m:
                continue
            ts = _parse_ts(m.group("ts"))
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
            bucket = instance_data.setdefault(m.group("instance"), {})
            bucket[name] = float(m.group("seconds"))
            matched = True
            break
        if matched:
            continue

        for pattern_name, pattern in (
            ("running", _RUNNING_RE),
            ("env_init", _ENV_INIT_RE),
            ("step", _STEP_RE),
        ):
            m = pattern.match(line)
            if not m:
                continue
            ts = _parse_ts(m.group("ts"))
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
            bucket = instance_data.setdefault(m.group("instance"), {})
            if pattern_name == "running":
                bucket.setdefault("run_start_ts", ts)
            elif pattern_name == "env_init":
                bucket.setdefault("environment_initialized_ts", ts)
                run_start = bucket.get("run_start_ts")
                if run_start is not None and "environment_init_latency_seconds" not in bucket:
                    bucket["environment_init_latency_seconds"] = ts - run_start
            else:
                bucket.setdefault("first_step_ts", ts)
                run_start = bucket.get("run_start_ts")
                if run_start is not None and "time_to_first_step_seconds" not in bucket:
                    bucket["time_to_first_step_seconds"] = ts - run_start
            break

    wall_time = None
    if first_ts is not None and last_ts is not None:
        wall_time = max(0.0, last_ts - first_ts)
    return {"instances": instance_data, "wall_time_seconds": wall_time}


def _parse_agent_time_records(output_dirs: list[Path]) -> dict[str, dict[str, float]]:
    records: dict[str, dict[str, float]] = {}
    for output_dir in output_dirs:
        for record_path in output_dir.rglob("agent_time_records.json"):
            instance_id = record_path.parent.name
            try:
                payload = json.loads(record_path.read_text())
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            response_values: list[float] = []
            execution_values: list[float] = []
            for datum in payload.values():
                if not isinstance(datum, dict):
                    continue
                response = datum.get("response_time")
                execution = datum.get("execution_time")
                if isinstance(response, (int, float)):
                    response_values.append(float(response))
                if isinstance(execution, (int, float)):
                    execution_values.append(float(execution))
            records[instance_id] = {
                "model_response_seconds": sum(response_values),
                "tool_execution_seconds": sum(execution_values),
                "step_count": float(max(len(response_values), len(execution_values))),
            }
    return records


def _build_timing_summary(runtime_root: Path, output_dirs: list[Path]) -> dict[str, Any]:
    shard_logs = []
    single_launcher = runtime_root / "launcher.log"
    if single_launcher.exists():
        shard_logs.append(("single", single_launcher))
    for shard_dir in sorted(runtime_root.glob("shard*/launcher.log")):
        shard_logs.append((shard_dir.parent.name, shard_dir))

    instances: dict[str, dict[str, Any]] = {}
    shard_wall_times: dict[str, float] = {}
    for shard_name, launcher_log in shard_logs:
        parsed = _parse_launcher_timing(launcher_log)
        if parsed["wall_time_seconds"] is not None:
            shard_wall_times[shard_name] = parsed["wall_time_seconds"]
        for instance_id, datum in parsed["instances"].items():
            merged = instances.setdefault(instance_id, {})
            merged.update(datum)
            merged.setdefault("shard", shard_name)

    for instance_id, datum in _parse_agent_time_records(output_dirs).items():
        merged = instances.setdefault(instance_id, {})
        merged.update(datum)

    stage_keys = [
        "environment_init_latency_seconds",
        "time_to_first_step_seconds",
        "fresh_shared_venv_seconds",
        "shared_venv_extract_seconds",
        "install_env_seconds",
        "shared_venv_pack_seconds",
        "git_cache_pack_seconds",
        "model_response_seconds",
        "tool_execution_seconds",
        "step_count",
    ]
    stage_stats = {}
    for key in stage_keys:
        values = [
            float(datum[key])
            for datum in instances.values()
            if isinstance(datum.get(key), (int, float))
        ]
        stats = _safe_stats(values)
        if stats is not None:
            stage_stats[key] = stats

    slowest_by_install = sorted(
        (
            {"instance_id": instance_id, "install_env_seconds": datum["install_env_seconds"]}
            for instance_id, datum in instances.items()
            if isinstance(datum.get("install_env_seconds"), (int, float))
        ),
        key=lambda x: x["install_env_seconds"],
        reverse=True,
    )[:10]
    slowest_by_model = sorted(
        (
            {"instance_id": instance_id, "model_response_seconds": datum["model_response_seconds"]}
            for instance_id, datum in instances.items()
            if isinstance(datum.get("model_response_seconds"), (int, float))
        ),
        key=lambda x: x["model_response_seconds"],
        reverse=True,
    )[:10]

    overall_wall = max(shard_wall_times.values()) if shard_wall_times else None
    return {
        "instance_count_with_timing": len(instances),
        "overall_wall_time_seconds": overall_wall,
        "shard_wall_times_seconds": shard_wall_times,
        "stage_stats": stage_stats,
        "slowest_instances": {
            "install_env_seconds": slowest_by_install,
            "model_response_seconds": slowest_by_model,
        },
    }


def _build_exit_diagnostics_summary(output_dirs: list[Path]) -> dict[str, Any]:
    categories = Counter()
    format_failure_types = Counter()
    exception_types = Counter()
    timeout_like_instances = 0
    instance_count = 0

    for output_dir in output_dirs:
        for traj_path in sorted(output_dir.rglob("*.traj")):
            try:
                payload = json.loads(traj_path.read_text())
            except Exception:
                continue
            info = payload.get("info") or {}
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
    slowest_requests: list[dict[str, Any]] = []

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
    for record in sorted(
        requests_payload,
        key=lambda record: float(record.get("duration_s") or 0.0),
        reverse=True,
    )[:10]:
        slowest_requests.append(
            {
                "instance_id": record.get("instance_id"),
                "request_index": record.get("request_index"),
                "duration_s": record.get("duration_s"),
                "status": record.get("status"),
                "input_tokens": record.get("input_tokens"),
                "message_chars": record.get("message_chars"),
                "exception_type": record.get("exception_type"),
            }
        )

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


def _build_report(
    preds: dict[str, Any],
    merged_statuses_payload: dict[str, Any],
    dataset_size: int | None,
    output_dirs: list[Path],
) -> dict[str, Any]:
    merged_statuses = merged_statuses_payload.get("instances_by_exit_status", {})
    total_cost = float(merged_statuses_payload.get("total_cost", 0) or 0)
    total_instances = dataset_size
    if total_instances is None:
        status_instance_count = sum(len(v) for v in merged_statuses.values())
        total_instances = max(status_instance_count, len(preds))

    reward_counter = Counter()
    empty_patch_count = 0
    nonempty_patch_count = 0
    for pred in preds.values():
        reward = pred.get("reward")
        if reward is None:
            reward_counter["missing"] += 1
        elif reward > 0:
            reward_counter["positive"] += 1
        else:
            reward_counter["non_positive"] += 1
        if (pred.get("model_patch") or "").strip():
            nonempty_patch_count += 1
        else:
            empty_patch_count += 1

    pass_count = reward_counter["positive"]
    submission_count = len(preds)
    status_counts = {status: len(instance_ids) for status, instance_ids in merged_statuses.items()}

    return {
        "evaluation_mode": "reward_based_local",
        "total_instances": total_instances,
        "submission_count": submission_count,
        "submission_rate": submission_count / total_instances if total_instances else 0.0,
        "pass_count": pass_count,
        "pass_rate": pass_count / total_instances if total_instances else 0.0,
        "nonempty_patch_count": nonempty_patch_count,
        "empty_patch_count": empty_patch_count,
        "reward_counts": dict(reward_counter),
        "exit_status_counts": status_counts,
        "exit_diagnostics_summary": _build_exit_diagnostics_summary(output_dirs),
        "lm_request_summary": _build_lm_request_summary(output_dirs),
        "total_cost": total_cost,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge predictions/statuses and emit a local reward-based eval report.")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, action="append", default=[])
    parser.add_argument("--dataset-size", type=int, default=None)
    parser.add_argument("--preds-output", type=Path, default=None)
    parser.add_argument("--status-output", type=Path, default=None)
    parser.add_argument("--report-output", type=Path, default=None)
    parser.add_argument("--timing-output", type=Path, default=None)
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    output_dirs = [p.resolve() for p in _collect_output_dirs(runtime_root, args.output_dir)]

    preds_output = (args.preds_output or (runtime_root / "output.merged.preds.json")).resolve()
    status_output = (args.status_output or (runtime_root / "run_batch_exit_statuses.merged.yaml")).resolve()
    report_output = (args.report_output or (runtime_root / "local_eval_report.json")).resolve()
    timing_output = (args.timing_output or (runtime_root / "timing_summary.json")).resolve()

    preds = _merge_predictions(output_dirs, preds_output)
    merged_statuses = _merge_status_maps([p / "run_batch_exit_statuses.yaml" for p in output_dirs])
    status_output.write_text(yaml.safe_dump(merged_statuses, sort_keys=False))
    report = _build_report(preds, merged_statuses, args.dataset_size, output_dirs)
    report_output.write_text(json.dumps(report, indent=2))
    timing_summary = _build_timing_summary(runtime_root, output_dirs)
    timing_output.write_text(json.dumps(timing_summary, indent=2))

    print(f"merged_predictions={preds_output}")
    print(f"merged_statuses={status_output}")
    print(f"local_report={report_output}")
    print(f"timing_summary={timing_output}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
