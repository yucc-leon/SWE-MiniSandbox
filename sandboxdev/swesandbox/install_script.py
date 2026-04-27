import re
import shlex
from pathlib import Path

_NOCHROOT_SYSTEM_PACKAGE_LINE = re.compile(
    r"^\s*(?:sudo\s+)?(?:apt-get|apt|yum|dnf|microdnf|apk|zypper|dpkg|rpm)\b"
)


def strip_nochroot_system_package_commands(script_content: str) -> str:
    """Drop system package manager commands that do not work in no-chroot mode."""
    kept_lines: list[str] = []
    skipping_continuation = False
    for line in script_content.splitlines():
        stripped = line.rstrip()
        if skipping_continuation:
            skipping_continuation = stripped.endswith("\\")
            continue
        if _NOCHROOT_SYSTEM_PACKAGE_LINE.match(line):
            skipping_continuation = stripped.endswith("\\")
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines) + ("\n" if script_content.endswith("\n") else "")


def resolve_wheelhouse_find_links(wheelhouse_root: str, py_version: str) -> str:
    if not wheelhouse_root:
        return ""
    root = Path(wheelhouse_root)
    short_version = str(py_version).replace(".", "")
    candidates = [
        root / str(py_version),
        root / f"py{short_version}",
        root / f"python{py_version}",
        root,
    ]
    find_links: list[str] = []
    for path in candidates:
        if path.is_dir():
            find_links.append(str(path))

    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.is_dir() and str(path) not in find_links:
                find_links.append(str(path))

    return " ".join(find_links)


def _pip_find_links_args(wheelhouse_dirs: str) -> str:
    return " ".join(
        f"--find-links {shlex.quote(path)}"
        for path in shlex.split(wheelhouse_dirs)
    )


def build_run_tests_prepare_command(
    run_tests_path: str,
    *,
    python_cmd: str,
    use_chroot: bool,
    wheelhouse_dir: str = "",
) -> str:
    """Build the pre-run command for test scripts.

    In no-chroot mode we avoid network-dependent bootstrap work. If a local
    wheelhouse is available, try a local-only chardet install; otherwise only
    make the script executable.
    """
    quoted_path = shlex.quote(run_tests_path)
    commands = [f"chmod +x {quoted_path}"]
    if use_chroot:
        commands.append(f"{python_cmd} -m pip install chardet")
        return " && ".join(commands)

    if wheelhouse_dir:
        find_links_args = _pip_find_links_args(wheelhouse_dir)
        check_cmd = (
            f"{python_cmd} -c "
            + shlex.quote(
                'import importlib.util, sys; '
                'sys.exit(0 if importlib.util.find_spec("chardet") else 1)'
            )
        )
        commands.extend(
            [
                f"if ! {check_cmd}; then",
                (
                    f"  {python_cmd} -m pip install --no-index "
                    f"{find_links_args} chardet || true"
                ),
                "fi",
            ]
        )
    return "\n".join(commands)
