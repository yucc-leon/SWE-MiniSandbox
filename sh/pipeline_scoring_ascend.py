#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def build_instance_filter(instance_ids: list[str]) -> str:
    if not instance_ids:
        raise ValueError("cannot build an instance filter for an empty batch")
    return "^(?:" + "|".join(re.escape(instance_id) for instance_id in instance_ids) + ")$"


def discover_predictions(
    eval_runtime_root: Path, *, stable_seconds: float, now: float | None = None
) -> dict[str, dict[str, Any]]:
    output_root = eval_runtime_root / "output"
    if not output_root.is_dir():
        return {}

    current_time = time.time() if now is None else now
    predictions: dict[str, dict[str, Any]] = {}
    mtimes: dict[str, float] = {}

    for pred_path in sorted(output_root.rglob("*.pred")):
        try:
            stat = pred_path.stat()
        except OSError:
            continue
        if current_time - stat.st_mtime < stable_seconds:
            continue
        try:
            payload = _json_load(pred_path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if "model_patch" not in payload:
            continue
        instance_id = str(payload.get("instance_id") or pred_path.stem)
        if not instance_id:
            continue
        if stat.st_mtime >= mtimes.get(instance_id, -1):
            predictions[instance_id] = payload
            mtimes[instance_id] = stat.st_mtime

    return predictions


def load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    payload = _json_load(path)
    if not isinstance(payload, dict):
        raise ValueError(f"unsupported predictions format in {path}")
    return {str(instance_id): dict(prediction or {}) for instance_id, prediction in payload.items()}


def write_predictions(path: Path, predictions: dict[str, dict[str, Any]]) -> None:
    ordered = {instance_id: predictions[instance_id] for instance_id in sorted(predictions)}
    _json_dump(path, ordered)


def _empty_state() -> dict[str, Any]:
    return {
        "next_shard_index": 0,
        "scored_ids": [],
        "running_ids": [],
        "failed_ids": [],
        "shards": [],
    }


def load_state(score_runtime_root: Path) -> dict[str, Any]:
    state_path = score_runtime_root / "pipeline_state.json"
    state = _empty_state()
    if state_path.is_file():
        loaded = _json_load(state_path)
        if isinstance(loaded, dict):
            state.update(loaded)

    # Recompute shard-derived state from metadata on every load.
    # Persisted running/scored/failed IDs can go stale across crashes or manual
    # metadata repair, which would otherwise cause pending instances to be
    # skipped forever.
    scored_ids: set[str] = set()
    running_ids: set[str] = set()
    failed_ids: set[str] = set()
    next_shard_index = int(state.get("next_shard_index") or 0)
    shards: list[dict[str, Any]] = []

    for metadata_path in sorted((score_runtime_root / "shards").glob("shard-*/metadata.json")):
        try:
            metadata = _json_load(metadata_path)
        except Exception:
            continue
        if not isinstance(metadata, dict):
            continue
        shards.append(metadata)
        shard_index = int(metadata.get("shard_index") or 0)
        next_shard_index = max(next_shard_index, shard_index + 1)
        shard_ids = [str(instance_id) for instance_id in metadata.get("instance_ids", [])]
        if metadata.get("status") == "completed":
            scored_ids.update(shard_ids)
        elif metadata.get("status") == "running":
            running_ids.update(shard_ids)
        elif metadata.get("status") == "failed":
            failed_ids.update(shard_ids)

    state["next_shard_index"] = next_shard_index
    state["scored_ids"] = sorted(scored_ids)
    state["running_ids"] = sorted(running_ids - scored_ids - failed_ids)
    state["failed_ids"] = sorted(failed_ids)
    state["shards"] = shards
    return state


def save_state(score_runtime_root: Path, state: dict[str, Any]) -> None:
    _json_dump(score_runtime_root / "pipeline_state.json", state)


def refresh_state(score_runtime_root: Path) -> dict[str, Any]:
    state = load_state(score_runtime_root)
    save_state(score_runtime_root, state)
    return state


def _completed_shard_output_dirs(score_runtime_root: Path) -> list[Path]:
    output_dirs: list[Path] = []
    for metadata_path in sorted((score_runtime_root / "shards").glob("shard-*/metadata.json")):
        try:
            metadata = _json_load(metadata_path)
        except Exception:
            continue
        if metadata.get("status") != "completed":
            continue
        output_dir = metadata_path.parent / "output"
        if output_dir.is_dir():
            output_dirs.append(output_dir)
    return output_dirs


def failed_shard_summary(score_runtime_root: Path) -> dict[str, Any]:
    state = load_state(score_runtime_root)
    failed_shards = [
        shard
        for shard in state.get("shards", [])
        if isinstance(shard, dict) and shard.get("status") == "failed"
    ]
    failed_ids = sorted(
        {
            str(instance_id)
            for shard in failed_shards
            for instance_id in shard.get("instance_ids", [])
        }
    )
    return {
        "failed_shard_count": len(failed_shards),
        "failed_ids": failed_ids,
        "failed_shards": failed_shards,
    }


def write_failed_shard_summary(score_runtime_root: Path) -> dict[str, Any]:
    summary = failed_shard_summary(score_runtime_root)
    if summary["failed_shard_count"]:
        _json_dump(score_runtime_root / "failed_shards.json", summary)
    return summary


def _prepare_scoring_shard(
    *,
    args: argparse.Namespace,
    shard_index: int,
    shard_predictions: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, str], Path, Path, dict[str, Any]]:
    instance_ids = sorted(shard_predictions)
    shard_root = args.score_runtime_root / "shards" / f"shard-{shard_index:06d}"
    shard_root.mkdir(parents=True, exist_ok=True)
    shard_predictions_path = shard_root / "preds.json"
    shard_log_path = shard_root / "scoring.log"
    metadata_path = shard_root / "metadata.json"

    write_predictions(shard_predictions_path, shard_predictions)
    metadata = {
        "shard_index": shard_index,
        "status": "running",
        "instance_ids": instance_ids,
        "predictions_path": str(shard_predictions_path),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _json_dump(metadata_path, metadata)

    env = os.environ.copy()
    env.update(
        {
            "CONFIG_PATH": str(args.config_path),
            "RUNTIME_ROOT": str(shard_root),
            "PREDICTIONS_PATH": str(shard_predictions_path),
            "INSTANCE_FILTER": build_instance_filter(instance_ids),
            # The filter is already exact. Reapplying the source slice after filtering can
            # drop all selected IDs for non-zero slices such as 200:500.
            "INSTANCE_SLICE": "",
            "NUM_WORKERS": str(args.num_workers),
            "DATASET_DIR": str(args.dataset_dir),
            "DATASET_SPLIT": args.dataset_split,
            "SANDBOX_ROOT": str(shard_root / "sandbox"),
            "GITCACHE_ROOT": str(args.score_runtime_root / "gitcache"),
            "SHARED_VENV_ROOT": str(args.score_runtime_root / "shared_venv"),
            "POSTPROCESS_SCORING": "0",
        }
    )

    command = ["bash", str(ROOT_DIR / "sh" / "run_swebench_scoring_ascend.sh")]
    return command, env, shard_root, shard_log_path, metadata


def _finish_scoring_shard(*, metadata: dict[str, Any], metadata_path: Path, returncode: int) -> None:
    metadata["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metadata["returncode"] = returncode
    metadata["status"] = "completed" if returncode == 0 else "failed"
    _json_dump(metadata_path, metadata)


def run_scoring_shard(
    *,
    args: argparse.Namespace,
    shard_index: int,
    shard_predictions: dict[str, dict[str, Any]],
) -> tuple[int, Path]:
    command, env, shard_root, shard_log_path, metadata = _prepare_scoring_shard(
        args=args, shard_index=shard_index, shard_predictions=shard_predictions
    )
    with shard_log_path.open("ab") as log_file:
        log_file.write(
            (
                f"[pipeline-scoring] command={' '.join(command)}\n"
                f"[pipeline-scoring] ids={','.join(metadata['instance_ids'])}\n"
            ).encode("utf-8")
        )
        completed = subprocess.run(command, cwd=ROOT_DIR, env=env, stdout=log_file, stderr=log_file)

    _finish_scoring_shard(
        metadata=metadata,
        metadata_path=shard_root / "metadata.json",
        returncode=completed.returncode,
    )
    return completed.returncode, shard_root


def start_scoring_shard(
    *,
    args: argparse.Namespace,
    shard_index: int,
    shard_predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    command, env, shard_root, shard_log_path, metadata = _prepare_scoring_shard(
        args=args, shard_index=shard_index, shard_predictions=shard_predictions
    )
    log_file = shard_log_path.open("ab")
    log_file.write(
        (
            f"[pipeline-scoring] command={' '.join(command)}\n"
            f"[pipeline-scoring] ids={','.join(metadata['instance_ids'])}\n"
        ).encode("utf-8")
    )
    log_file.flush()
    process = subprocess.Popen(command, cwd=ROOT_DIR, env=env, stdout=log_file, stderr=log_file)
    return {
        "process": process,
        "log_file": log_file,
        "shard_root": shard_root,
        "metadata": metadata,
    }


def poll_scoring_shards(active_shards: list[dict[str, Any]]) -> bool:
    completed_any = False
    still_active: list[dict[str, Any]] = []
    for shard in active_shards:
        process = shard["process"]
        returncode = process.poll()
        if returncode is None:
            still_active.append(shard)
            continue
        completed_any = True
        shard["log_file"].close()
        _finish_scoring_shard(
            metadata=shard["metadata"],
            metadata_path=shard["shard_root"] / "metadata.json",
            returncode=returncode,
        )
    active_shards[:] = still_active
    return completed_any


def merge_pipeline_results(
    *,
    args: argparse.Namespace,
    predictions: dict[str, dict[str, Any]],
) -> None:
    output_dirs = _completed_shard_output_dirs(args.score_runtime_root)
    if not output_dirs:
        return

    if args.final_predictions_path and args.final_predictions_path.is_file():
        predictions_path = args.final_predictions_path
    else:
        predictions_path = args.score_runtime_root / "pipeline_preds.json"
        write_predictions(predictions_path, predictions)

    command = [
        str(args.python_bin),
        str(ROOT_DIR / "sh" / "postprocess_official_scoring.py"),
        "--runtime-root",
        str(args.score_runtime_root),
        "--predictions-path",
        str(predictions_path),
        "--dataset-path",
        str(args.dataset_dir),
        "--dataset-split",
        args.dataset_split,
        "--instances-filter",
        args.instance_filter,
        "--instances-slice",
        args.instance_slice,
        "--diagnostics-output-dir",
        str(args.eval_runtime_root / "output"),
        "--results-output",
        str(args.score_runtime_root / "results.json"),
        "--instance-results-output",
        str(args.score_runtime_root / "instance_results.jsonl"),
    ]
    for output_dir in output_dirs:
        command.extend(["--output-dir", str(output_dir)])

    merge_log_path = args.score_runtime_root / "merge.log"
    with merge_log_path.open("ab") as log_file:
        log_file.write(("[pipeline-scoring] merge " + " ".join(command) + "\n").encode("utf-8"))
        subprocess.run(command, cwd=ROOT_DIR, check=True, stdout=log_file, stderr=log_file)


def _select_pending_batch(
    *,
    predictions: dict[str, dict[str, Any]],
    state: dict[str, Any],
    batch_size: int,
    force: bool,
) -> dict[str, dict[str, Any]]:
    scored_ids = set(str(instance_id) for instance_id in state.get("scored_ids", []))
    running_ids = set(str(instance_id) for instance_id in state.get("running_ids", []))
    failed_ids = set(str(instance_id) for instance_id in state.get("failed_ids", []))
    pending_ids = [
        instance_id
        for instance_id in sorted(predictions)
        if instance_id not in scored_ids
        and instance_id not in running_ids
        and instance_id not in failed_ids
    ]
    if not force and len(pending_ids) < batch_size:
        return {}
    return {instance_id: predictions[instance_id] for instance_id in pending_ids[:batch_size]}


def run_once(args: argparse.Namespace, *, force: bool) -> bool:
    state = load_state(args.score_runtime_root)
    predictions = discover_predictions(
        args.eval_runtime_root, stable_seconds=args.stable_seconds
    )
    if args.final_predictions_path and args.final_predictions_path.is_file():
        predictions.update(load_predictions(args.final_predictions_path))

    batch = _select_pending_batch(
        predictions=predictions,
        state=state,
        batch_size=args.batch_size,
        force=force,
    )
    if not batch:
        merge_pipeline_results(args=args, predictions=predictions)
        return False

    shard_index = int(state.get("next_shard_index") or 0)
    returncode, _ = run_scoring_shard(args=args, shard_index=shard_index, shard_predictions=batch)
    state = load_state(args.score_runtime_root)
    state["next_shard_index"] = max(int(state.get("next_shard_index") or 0), shard_index + 1)
    if returncode == 0:
        scored_ids = set(str(instance_id) for instance_id in state.get("scored_ids", []))
        scored_ids.update(batch)
        state["scored_ids"] = sorted(scored_ids)
    else:
        failed_ids = set(str(instance_id) for instance_id in state.get("failed_ids", []))
        failed_ids.update(batch)
        state["failed_ids"] = sorted(failed_ids)
    save_state(args.score_runtime_root, state)
    merge_pipeline_results(args=args, predictions=predictions)
    return True


def run_watch(args: argparse.Namespace) -> int:
    args.score_runtime_root.mkdir(parents=True, exist_ok=True)
    save_state(args.score_runtime_root, load_state(args.score_runtime_root))

    if args.merge_only:
        predictions = discover_predictions(
            args.eval_runtime_root, stable_seconds=args.stable_seconds
        )
        if args.final_predictions_path and args.final_predictions_path.is_file():
            predictions.update(load_predictions(args.final_predictions_path))
        merge_pipeline_results(args=args, predictions=predictions)
        summary = write_failed_shard_summary(args.score_runtime_root)
        if summary["failed_shard_count"]:
            print(
                f"[pipeline-scoring] failed shards present: {summary['failed_shard_count']}",
                file=sys.stderr,
            )
            return 1
        return 0

    shards_started = 0
    active_shards: list[dict[str, Any]] = []
    while True:
        completed_any = poll_scoring_shards(active_shards)
        if completed_any:
            predictions = discover_predictions(
                args.eval_runtime_root, stable_seconds=args.stable_seconds
            )
            if args.final_predictions_path and args.final_predictions_path.is_file():
                predictions.update(load_predictions(args.final_predictions_path))
            merge_pipeline_results(args=args, predictions=predictions)
            refresh_state(args.score_runtime_root)

        if (args.score_runtime_root / "STOP").exists():
            for shard in active_shards:
                shard["process"].terminate()
                shard["log_file"].close()
            predictions = discover_predictions(
                args.eval_runtime_root, stable_seconds=args.stable_seconds
            )
            merge_pipeline_results(args=args, predictions=predictions)
            summary = write_failed_shard_summary(args.score_runtime_root)
            if summary["failed_shard_count"]:
                print(
                    f"[pipeline-scoring] STOP found with failed shards: {summary['failed_shard_count']}",
                    file=sys.stderr,
                )
                return 1
            print("[pipeline-scoring] STOP found; exiting")
            return 0

        final_available = bool(args.final_predictions_path and args.final_predictions_path.is_file())
        ran_shard = False
        if args.max_concurrent_shards <= 1:
            ran_shard = run_once(args, force=final_available or args.once)
        else:
            while len(active_shards) < args.max_concurrent_shards:
                state = load_state(args.score_runtime_root)
                predictions = discover_predictions(
                    args.eval_runtime_root, stable_seconds=args.stable_seconds
                )
                if args.final_predictions_path and args.final_predictions_path.is_file():
                    predictions.update(load_predictions(args.final_predictions_path))
                batch = _select_pending_batch(
                    predictions=predictions,
                    state=state,
                    batch_size=args.batch_size,
                    force=final_available or args.once,
                )
                if not batch:
                    break
                shard_index = int(state.get("next_shard_index") or 0)
                active_shards.append(
                    start_scoring_shard(
                        args=args,
                        shard_index=shard_index,
                        shard_predictions=batch,
                    )
                )
                state = load_state(args.score_runtime_root)
                state["next_shard_index"] = max(
                    int(state.get("next_shard_index") or 0), shard_index + 1
                )
                save_state(args.score_runtime_root, state)
                ran_shard = True
                shards_started += 1
                if args.max_shards is not None and shards_started >= args.max_shards:
                    break
        if ran_shard:
            if args.max_shards is not None and shards_started >= args.max_shards:
                while active_shards:
                    completed_any = poll_scoring_shards(active_shards)
                    if completed_any:
                        predictions = discover_predictions(
                            args.eval_runtime_root, stable_seconds=args.stable_seconds
                        )
                        if args.final_predictions_path and args.final_predictions_path.is_file():
                            predictions.update(load_predictions(args.final_predictions_path))
                        merge_pipeline_results(args=args, predictions=predictions)
                        refresh_state(args.score_runtime_root)
                    time.sleep(min(args.poll_seconds, 5))
                print(f"[pipeline-scoring] max_shards={args.max_shards} reached")
                return 0
            if args.once:
                continue
        elif args.once and not active_shards:
            return 0

        state = load_state(args.score_runtime_root)
        predictions = discover_predictions(
            args.eval_runtime_root, stable_seconds=args.stable_seconds
        )
        if args.final_predictions_path and args.final_predictions_path.is_file():
            predictions.update(load_predictions(args.final_predictions_path))
        pending = _select_pending_batch(
            predictions=predictions,
            state=state,
            batch_size=1,
            force=True,
        )
        if final_available and not pending and not active_shards:
            merge_pipeline_results(args=args, predictions=predictions)
            refresh_state(args.score_runtime_root)
            summary = write_failed_shard_summary(args.score_runtime_root)
            if summary["failed_shard_count"]:
                print(
                    f"[pipeline-scoring] final predictions drained with failed shards: {summary['failed_shard_count']}",
                    file=sys.stderr,
                )
                return 1
            print("[pipeline-scoring] final predictions drained; exiting")
            return 0

        time.sleep(args.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run official-like SWE-bench scoring as generation emits per-instance .pred files."
    )
    parser.add_argument("--eval-runtime-root", type=Path, required=True)
    parser.add_argument("--score-runtime-root", type=Path, required=True)
    parser.add_argument(
        "--final-predictions-path",
        type=Path,
        default=None,
        help="Complete preds.json from the generation wrapper; used to decide when to drain and exit.",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=ROOT_DIR / "config" / "sweagent_score_ascend.yaml",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=ROOT_DIR / "dataset" / "SWE-bench" / "SWE-bench_Verified" / "data",
    )
    parser.add_argument("--dataset-split", default="test")
    parser.add_argument("--instance-filter", default=".*")
    parser.add_argument("--instance-slice", default=":500")
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--stable-seconds", type=float, default=5.0)
    parser.add_argument(
        "--python-bin",
        type=Path,
        default=Path(sys.executable),
        help="Python interpreter used for final report merging.",
    )
    parser.add_argument("--merge-only", action="store_true", help="Only merge existing completed shards.")
    parser.add_argument("--once", action="store_true", help="Drain currently available predictions, then exit.")
    parser.add_argument("--max-shards", type=int, default=None)
    parser.add_argument("--max-concurrent-shards", type=int, default=1)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.eval_runtime_root = args.eval_runtime_root.resolve()
    args.score_runtime_root = args.score_runtime_root.resolve()
    args.config_path = args.config_path.resolve()
    args.dataset_dir = args.dataset_dir.resolve()
    args.python_bin = args.python_bin.resolve()
    if args.final_predictions_path:
        args.final_predictions_path = args.final_predictions_path.resolve()

    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")
    if args.num_workers < 1:
        parser.error("--num-workers must be >= 1")
    if args.max_concurrent_shards < 1:
        parser.error("--max-concurrent-shards must be >= 1")
    return run_watch(args)


if __name__ == "__main__":
    raise SystemExit(main())
