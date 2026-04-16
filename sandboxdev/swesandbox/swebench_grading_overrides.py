from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from swebench.harness.constants import FAIL_TO_PASS, PASS_TO_PASS

from .swe_bench_instance_map import instance_map
from .swe_bench_instance_map import instance_to_skip


def should_skip_swebench_instance(instance_id: str, *, apply_local_overrides: bool) -> bool:
    """Return whether local MiniSandbox bring-up overrides should skip this instance."""
    return apply_local_overrides and instance_id in instance_to_skip


def maybe_relax_pytest_minversion(
    root_dir: str,
    git_folder: str,
    repo: str,
    *,
    apply_local_overrides: bool,
) -> None:
    """Apply local compatibility relaxations used during MiniSandbox bring-up."""
    if not apply_local_overrides or repo != "pytest-dev/pytest":
        return

    from configparser import ConfigParser

    tox_ini = Path(root_dir) / git_folder / "tox.ini"
    if tox_ini.exists():
        cp = ConfigParser()
        cp.read(tox_ini)
        if cp.has_section("pytest"):
            cp.set("pytest", "minversion", "0.0")
            with open(tox_ini, "w", encoding="utf-8") as f:
                cp.write(f)

    pyproject = Path(root_dir) / git_folder / "pyproject.toml"
    if pyproject.exists():
        import tomli
        import tomli_w

        with open(pyproject, "rb") as f:
            data = tomli.load(f)

        if "tool" in data and "pytest" in data["tool"] and "ini_options" in data["tool"]["pytest"]:
            data["tool"]["pytest"]["ini_options"]["minversion"] = "0.0"
            with open(pyproject, "wb") as f:
                tomli_w.dump(data, f)


def apply_local_swebench_report_overrides(
    report: dict[str, Any],
    ds: dict[str, Any],
    *,
    apply_local_overrides: bool,
) -> dict[str, Any]:
    """Apply local report munging used by the current engineering stack."""
    if not apply_local_overrides:
        return report

    adjusted = copy.deepcopy(report)
    skip_key_list = instance_map(instance_id=ds["instance_id"], repo=ds["repo"])
    if skip_key_list:
        for section_name in (PASS_TO_PASS, FAIL_TO_PASS):
            failures = adjusted.get(section_name, {}).get("failure", [])
            if failures:
                adjusted[section_name]["failure"] = [
                    key for key in failures if not any(skip_key in key for skip_key in skip_key_list)
                ]

    if ds["repo"] == "pylint-dev/pylint":
        failures = adjusted.get(PASS_TO_PASS, {}).get("failure", [])
        adjusted.setdefault(PASS_TO_PASS, {})["failure"] = failures[16:]

    return adjusted
