"""Regression tests for no-chroot install script sanitization."""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("sandboxdev", "SWE-agent", "SWE-ReX/src"):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

from swesandbox.install_script import (
    build_run_tests_prepare_command,
    resolve_wheelhouse_find_links,
    strip_nochroot_system_package_commands,
)


def test_strip_nochroot_system_package_commands():
    script = "\n".join(
        [
            "#!/bin/bash",
            "source /env/bin/activate",
            "yum install -y libjpeg-turbo-devel",
            "  apt-get update",
            "sudo dnf install -y foo",
            "python -m pip install -r /requirements.txt",
            "",
        ]
    )

    sanitized = strip_nochroot_system_package_commands(script)

    assert "yum install" not in sanitized
    assert "apt-get update" not in sanitized
    assert "dnf install" not in sanitized
    assert "source /env/bin/activate" in sanitized
    assert "python -m pip install -r /requirements.txt" in sanitized


def test_strip_nochroot_system_package_commands_drops_continuations():
    script = "\n".join(
        [
            "#!/bin/bash",
            "apt-get update && apt-get install -y \\",
            "  libfreetype6-dev \\",
            "  pkg-config",
            "dpkg --configure -a",
            "python -m pip install -e .",
            "",
        ]
    )

    sanitized = strip_nochroot_system_package_commands(script)

    assert "apt-get" not in sanitized
    assert "libfreetype6-dev" not in sanitized
    assert "pkg-config" not in sanitized
    assert "dpkg" not in sanitized
    assert "python -m pip install -e ." in sanitized


def test_build_run_tests_prepare_command_nochroot_skips_network_install_without_wheelhouse():
    command = build_run_tests_prepare_command(
        "/sandbox/run_tests.sh",
        python_cmd="python3",
        use_chroot=False,
    )

    assert command == "chmod +x /sandbox/run_tests.sh"
    assert "chardet" not in command


def test_build_run_tests_prepare_command_nochroot_uses_local_wheelhouse_only():
    command = build_run_tests_prepare_command(
        "/sandbox/run_tests.sh",
        python_cmd="python3",
        use_chroot=False,
        wheelhouse_dir="/wheelhouse/py39",
    )

    assert "chmod +x /sandbox/run_tests.sh" in command
    assert "python3 -m pip install --no-index --find-links /wheelhouse/py39 chardet || true" in command
    assert 'find_spec("chardet")' in command
    assert "if !" in command


def test_build_run_tests_prepare_command_supports_multiple_wheelhouse_dirs():
    command = build_run_tests_prepare_command(
        "/sandbox/run_tests.sh",
        python_cmd="python3",
        use_chroot=False,
        wheelhouse_dir="/wheelhouse/3.8 /wheelhouse/3.6",
    )

    assert "--find-links /wheelhouse/3.8 --find-links /wheelhouse/3.6 chardet" in command


def test_build_run_tests_prepare_command_chroot_keeps_pip_install():
    command = build_run_tests_prepare_command(
        "/run_tests.sh",
        python_cmd="python",
        use_chroot=True,
    )

    assert command == "chmod +x /run_tests.sh && python -m pip install chardet"


def _assert_resolve_wheelhouse_dir_includes_sibling_version_dirs(root: Path):
    for name in ("3.6", "3.8", "py39"):
        (root / name).mkdir(parents=True)

    resolved = resolve_wheelhouse_find_links(str(root), "3.8")

    assert str(root / "3.8") in resolved
    assert str(root / "3.6") in resolved
    assert str(root / "py39") in resolved


def test_resolve_wheelhouse_dir_includes_sibling_version_dirs(tmp_path):
    _assert_resolve_wheelhouse_dir_includes_sibling_version_dirs(tmp_path / "wheelhouse")


if __name__ == "__main__":
    test_strip_nochroot_system_package_commands()
    test_strip_nochroot_system_package_commands_drops_continuations()
    test_build_run_tests_prepare_command_nochroot_skips_network_install_without_wheelhouse()
    test_build_run_tests_prepare_command_nochroot_uses_local_wheelhouse_only()
    test_build_run_tests_prepare_command_supports_multiple_wheelhouse_dirs()
    test_build_run_tests_prepare_command_chroot_keeps_pip_install()
    with tempfile.TemporaryDirectory() as tmpdir:
        _assert_resolve_wheelhouse_dir_includes_sibling_version_dirs(Path(tmpdir) / "wheelhouse")
    print("ok")
