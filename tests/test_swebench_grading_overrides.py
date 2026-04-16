from pathlib import Path

from swebench.harness.constants import FAIL_TO_PASS, PASS_TO_PASS

from swesandbox.swebench_grading_overrides import apply_local_swebench_report_overrides
from swesandbox.swebench_grading_overrides import maybe_relax_pytest_minversion
from swesandbox.swebench_grading_overrides import should_skip_swebench_instance


def test_should_skip_swebench_instance_respects_flag():
    assert should_skip_swebench_instance("django__django-14771", apply_local_overrides=True)
    assert not should_skip_swebench_instance("django__django-14771", apply_local_overrides=False)
    assert not should_skip_swebench_instance("unknown__instance-1", apply_local_overrides=True)


def test_apply_local_swebench_report_overrides_can_be_disabled():
    report = {
        PASS_TO_PASS: {"failure": ["test_shufflesplit_errors[None-train_size3]", "keep-pass"]},
        FAIL_TO_PASS: {"failure": ["test_shufflesplit_errors[None-train_size3]", "keep-fail"]},
    }
    ds = {
        "instance_id": "scikit-learn__scikit-learn-14983",
        "repo": "scikit-learn/scikit-learn",
    }

    unchanged = apply_local_swebench_report_overrides(report, ds, apply_local_overrides=False)
    assert unchanged == report

    adjusted = apply_local_swebench_report_overrides(report, ds, apply_local_overrides=True)
    assert adjusted[PASS_TO_PASS]["failure"] == ["keep-pass"]
    assert adjusted[FAIL_TO_PASS]["failure"] == ["keep-fail"]


def test_apply_local_swebench_report_overrides_handles_pylint_special_case():
    report = {
        PASS_TO_PASS: {"failure": [f"keep-{idx}" for idx in range(20)]},
        FAIL_TO_PASS: {"failure": []},
    }
    ds = {
        "instance_id": "pylint-dev__pylint-1",
        "repo": "pylint-dev/pylint",
    }

    adjusted = apply_local_swebench_report_overrides(report, ds, apply_local_overrides=True)
    assert adjusted[PASS_TO_PASS]["failure"] == [f"keep-{idx}" for idx in range(16, 20)]

    unchanged = apply_local_swebench_report_overrides(report, ds, apply_local_overrides=False)
    assert unchanged[PASS_TO_PASS]["failure"] == [f"keep-{idx}" for idx in range(20)]


def test_maybe_relax_pytest_minversion_can_be_disabled(tmp_path: Path):
    repo_root = tmp_path / "testbed"
    repo_root.mkdir()
    tox_ini = repo_root / "tox.ini"
    tox_ini.write_text("[pytest]\nminversion = 8.0\n", encoding="utf-8")

    maybe_relax_pytest_minversion(
        str(tmp_path),
        "testbed",
        "pytest-dev/pytest",
        apply_local_overrides=False,
    )
    assert "minversion = 8.0" in tox_ini.read_text(encoding="utf-8")

    maybe_relax_pytest_minversion(
        str(tmp_path),
        "testbed",
        "pytest-dev/pytest",
        apply_local_overrides=True,
    )
    assert "minversion = 0.0" in tox_ini.read_text(encoding="utf-8")
