import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_postprocess_distinguishes_scored_vs_completed_for_missing_submission(tmp_path):
    mod = _load_module("postprocess_official_scoring", "sh/postprocess_official_scoring.py")

    dataset_ids = [
        "submitted-ok",
        "missing-submission",
        "submitted-error",
        "submitted-resolved",
    ]
    predictions = {
        "submitted-ok": {"model_patch": "diff --git a/a b/a"},
        "submitted-error": {"model_patch": "diff --git a/b b/b"},
        "submitted-resolved": {"model_patch": "diff --git a/c b/c"},
    }
    eval_outputs = {
        "submitted-ok": {"instance_id": "submitted-ok", "reward": 0},
        "missing-submission": {"instance_id": "missing-submission", "reward": 0},
        "submitted-error": {"instance_id": "submitted-error", "reward": None},
        "submitted-resolved": {"instance_id": "submitted-resolved", "reward": 1},
    }
    status_by_instance = {
        "submitted-ok": "failed",
        "missing-submission": "failed",
        "submitted-error": "Uncaught RuntimeError",
        "submitted-resolved": "passed",
    }

    instance_rows, id_sets = mod._summarize_instance_rows(
        dataset_ids=dataset_ids,
        input_predictions=predictions,
        eval_outputs=eval_outputs,
        traj_infos={},
        status_by_instance=status_by_instance,
    )
    results = mod._build_results_payload(
        dataset_ids=dataset_ids,
        dataset_size=None,
        id_sets=id_sets,
        traj_infos={},
        diagnostics_output_dirs=[],
        predictions_path=tmp_path / "preds.json",
    )

    assert results["submitted_instances"] == 3
    assert results["scored_instances"] == 4
    assert results["completed_instances"] == 2
    assert results["resolved_instances"] == 1
    assert results["unresolved_instances"] == 1
    assert results["error_instances"] == 1
    assert results["scored_without_submission_instances"] == 1
    assert results["scored_without_submission_ids"] == ["missing-submission"]
    assert results["schema_version"] == 3

    rows_by_id = {row["instance_id"]: row for row in instance_rows}
    assert rows_by_id["missing-submission"]["submitted"] is False
    assert rows_by_id["missing-submission"]["scored"] is True
    assert rows_by_id["missing-submission"]["completed"] is False
    assert rows_by_id["missing-submission"]["scored_without_submission"] is True
    assert rows_by_id["missing-submission"]["unresolved"] is False

    assert rows_by_id["submitted-error"]["submitted"] is True
    assert rows_by_id["submitted-error"]["scored"] is True
    assert rows_by_id["submitted-error"]["completed"] is False
    assert rows_by_id["submitted-error"]["error"] is True


def test_remote_inference_render_supports_served_model_name(tmp_path):
    mod = _load_module("run_remote_inference_task", "sh/run_remote_inference_task.py")

    runtime_root = tmp_path / "remote-runtime"
    parser = mod.build_parser()
    args = parser.parse_args(
        [
            "render",
            "--runtime-root",
            str(runtime_root),
            "--image",
            "registry/internal/vllm-ascend:0.11.0-cann8.3",
            "--resource-profile",
            "Ascend-1xNPU-96G",
            "--model-path",
            "/models/sweagent-7b",
            "--served-model-name",
            "/sharedata/liyuchen/models/sweagent-7b",
            "--public-base-url",
            "http://task-host:8000/v1",
        ]
    )
    rc = args.func(args)
    assert rc == 0

    request_path = runtime_root / "inference_task.request.json"
    script_path = runtime_root / "remote-inference" / "launch_vllm_task_ascend.generated.sh"
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    script_text = script_path.read_text(encoding="utf-8")

    assert request_payload["model_name"] == "/sharedata/liyuchen/models/sweagent-7b"
    assert request_payload["environment"]["MODEL_PATH"] == "/models/sweagent-7b"
    assert (
        request_payload["environment"]["SERVED_MODEL_NAME"]
        == "/sharedata/liyuchen/models/sweagent-7b"
    )
    assert '--served-model-name "${SERVED_MODEL_NAME}"' in script_text


def test_local_ascend_serve_script_supports_served_model_name():
    script_text = (ROOT / "sh/serve_qwen_ascend.sh").read_text(encoding="utf-8")

    assert "SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-${MODEL_NAME:-}}" in script_text
    assert 'EXTRA_ARGS+=(--served-model-name "${SERVED_MODEL_NAME}")' in script_text
    assert "SWE-agent-LM-32B*" in script_text


def test_remote_inference_doctor_accepts_consistent_runtime(tmp_path):
    mod = _load_module("run_remote_inference_task", "sh/run_remote_inference_task.py")

    runtime_root = tmp_path / "remote-runtime"
    parser = mod.build_parser()
    render_args = parser.parse_args(
        [
            "render",
            "--runtime-root",
            str(runtime_root),
            "--image",
            "registry/internal/vllm-ascend:0.11.0-cann8.3",
            "--resource-profile",
            "Ascend-1xNPU-96G",
            "--model-path",
            "/models/sweagent-7b",
            "--served-model-name",
            "sweagent-7b",
            "--public-base-url",
            "http://task-host:8000/v1",
        ]
    )
    assert render_args.func(render_args) == 0

    record_args = parser.parse_args(
        [
            "record",
            "--runtime-root",
            str(runtime_root),
            "--task-id",
            "task-123",
            "--api-base",
            "http://task-host:8000/v1",
        ]
    )
    assert record_args.func(record_args) == 0

    probe_path = runtime_root / "inference_probe.json"
    probe_path.write_text(
        json.dumps(
            {
                "api_base": "http://task-host:8000/v1",
                "ready": True,
                "attempt_count": 1,
                "attempts": [],
                "checked_chat": False,
            }
        ),
        encoding="utf-8",
    )

    result = mod.doctor_runtime(
        runtime_root=runtime_root,
        template_path=(ROOT / "sh/templates/launch_vllm_task_ascend.sh").resolve(),
    )
    assert result["ok"] is True
    assert result["errors"] == []


def test_remote_inference_doctor_flags_mismatched_request_fields(tmp_path):
    mod = _load_module("run_remote_inference_task", "sh/run_remote_inference_task.py")

    runtime_root = tmp_path / "remote-runtime"
    parser = mod.build_parser()
    render_args = parser.parse_args(
        [
            "render",
            "--runtime-root",
            str(runtime_root),
            "--image",
            "registry/internal/vllm-ascend:0.11.0-cann8.3",
            "--resource-profile",
            "Ascend-1xNPU-96G",
            "--model-path",
            "/models/sweagent-7b",
            "--served-model-name",
            "sweagent-7b",
            "--public-base-url",
            "http://task-host:8000/v1",
        ]
    )
    assert render_args.func(render_args) == 0

    request_path = runtime_root / "inference_task.request.json"
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    request_payload["model_name"] = "wrong-model-name"
    request_payload["environment"]["PUBLIC_BASE_URL"] = "http://other-host:8000/v1"
    request_path.write_text(json.dumps(request_payload), encoding="utf-8")

    result = mod.doctor_runtime(
        runtime_root=runtime_root,
        template_path=(ROOT / "sh/templates/launch_vllm_task_ascend.sh").resolve(),
    )
    assert result["ok"] is False
    assert any("PUBLIC_BASE_URL" in message for message in result["errors"])
    assert any("model_name" in message for message in result["errors"])


def test_refresh_scoring_reports_rebuilds_results_from_saved_config(tmp_path):
    refresh_mod = _load_module("refresh_scoring_reports", "sh/refresh_scoring_reports.py")

    runtime_root = tmp_path / "ascend-score-demo"
    output_dir = runtime_root / "output"
    output_dir.mkdir(parents=True)
    predictions_path = tmp_path / "preds.json"
    predictions_path.write_text(
        json.dumps(
            {
                "demo-1": {"model_patch": "diff --git a/a b/a"},
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "demo-1.pred").write_text(
        json.dumps({"instance_id": "demo-1", "reward": 1}),
        encoding="utf-8",
    )
    (output_dir / "run_batch_exit_statuses.yaml").write_text(
        "instances_by_exit_status:\n  passed:\n    - demo-1\n",
        encoding="utf-8",
    )
    run_batch_config = {
        "instances": {
            "database": None,
            "split": "test",
            "filter": ".*",
            "slice": ":1",
            "model_patch_file": str(predictions_path),
        }
    }
    (output_dir / "run_batch.config.yaml").write_text(
        json.dumps(json.dumps(run_batch_config)),
        encoding="utf-8",
    )

    postprocess_mod = refresh_mod._load_postprocess_module(ROOT)
    summary = refresh_mod._refresh_runtime(runtime_root, postprocess_mod)
    results = json.loads((runtime_root / "results.json").read_text(encoding="utf-8"))

    assert summary["schema_version"] == 3
    assert summary["submitted_instances"] == 1
    assert summary["scored_instances"] == 1
    assert summary["completed_instances"] == 1
    assert summary["resolved_instances"] == 1
    assert summary["scored_without_submission_instances"] == 0
    assert results["source_predictions_path"] == str(predictions_path.resolve())
    assert results["schema_version"] == 3


def test_refresh_scoring_reports_can_migrate_legacy_results_only(tmp_path):
    refresh_mod = _load_module("refresh_scoring_reports", "sh/refresh_scoring_reports.py")

    runtime_root = tmp_path / "ascend-score-legacy"
    runtime_root.mkdir()
    (runtime_root / "results.json").write_text(
        json.dumps(
            {
                "total_instances": 2,
                "submitted_instances": 1,
                "completed_instances": 1,
                "resolved_instances": 0,
                "unresolved_instances": 1,
                "empty_patch_instances": 0,
                "error_instances": 0,
                "submitted_rate": 0.5,
                "resolved_rate": 0.0,
                "exit_diagnostics_summary": {"instance_count_with_exit_diagnostics": 1},
                "lm_request_summary": {"instance_count_with_lm_requests": 1},
                "schema_version": 2,
                "scoring_mode": "sandbox_swebench_harness_grading",
                "source_predictions_path": "/tmp/legacy-preds.json",
            }
        ),
        encoding="utf-8",
    )
    (runtime_root / "instance_results.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "instance_id": "submitted-ok",
                        "submitted": True,
                        "completed": True,
                        "resolved": False,
                        "empty_patch": False,
                        "error": False,
                        "exit_status": "failed",
                        "reward": 0,
                        "input_patch_chars": 10,
                    }
                ),
                json.dumps(
                    {
                        "instance_id": "missing-submission",
                        "submitted": False,
                        "completed": True,
                        "resolved": False,
                        "empty_patch": False,
                        "error": False,
                        "exit_status": "failed",
                        "reward": 0,
                        "input_patch_chars": 0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = refresh_mod._migrate_legacy_runtime(runtime_root)
    results = json.loads((runtime_root / "results.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (runtime_root / "instance_results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert summary["migration_mode"] == "legacy_results_only"
    assert results["schema_version"] == 3
    assert results["submitted_instances"] == 1
    assert results["scored_instances"] == 2
    assert results["completed_instances"] == 1
    assert results["scored_without_submission_instances"] == 1
    assert results["source_predictions_path"] == "/tmp/legacy-preds.json"
    row_by_id = {row["instance_id"]: row for row in rows}
    assert row_by_id["missing-submission"]["scored"] is True
    assert row_by_id["missing-submission"]["completed"] is False
    assert row_by_id["missing-submission"]["scored_without_submission"] is True
