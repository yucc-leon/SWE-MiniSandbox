"""Regression tests for local repo cache fallback on cache-miss reruns."""

import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("SWE-agent", "sandboxdev", "SWE-ReX/src"):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

from sweagent.environment.repo import GithubRepoRetryConfig, _cache_repo_dir_from_archive


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _make_repo_archive(archive_path: Path) -> str:
    repo_dir = archive_path.parent / "source"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=repo_dir)
    _git("config", "user.email", "test@example.com", cwd=repo_dir)
    _git("config", "user.name", "Test User", cwd=repo_dir)
    (repo_dir / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=repo_dir)
    _git("commit", "-qm", "init", cwd=repo_dir)
    commit = _git("rev-parse", "HEAD", cwd=repo_dir)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w") as tar:
        for entry in repo_dir.iterdir():
            tar.add(entry, arcname=entry.name)
    return commit


def test_find_alternate_local_repo_cache_uses_sibling_tar():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        runtime_root = root / ".runtime"
        gitcache_root = runtime_root / "run-a" / "gitcache"
        missing_archive = gitcache_root / "django" / "django" / "4.0" / "django__missing" / "testbed.tar"
        sibling_archive = gitcache_root / "django" / "django" / "4.0" / "django__cached" / "testbed.tar"
        commit = _make_repo_archive(sibling_archive)

        config = GithubRepoRetryConfig(
            github_url="https://github.com/django/django",
            git_folder="testbed",
            base_commit=commit,
            clone_timeout=5,
        )

        repo_dir = config._find_alternate_local_repo_cache(str(missing_archive))

        assert repo_dir == _cache_repo_dir_from_archive(str(sibling_archive))
        assert Path(repo_dir, ".git").is_dir()


def test_find_alternate_local_repo_cache_searches_other_runtime_gitcache():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        runtime_root = root / ".runtime"
        missing_archive = (
            runtime_root
            / "run-a"
            / "gitcache"
            / "django"
            / "django"
            / "4.0"
            / "django__missing"
            / "testbed.tar"
        )
        other_archive = (
            runtime_root
            / "run-b"
            / "gitcache"
            / "django"
            / "django"
            / "4.0"
            / "django__cached"
            / "testbed.tar"
        )
        commit = _make_repo_archive(other_archive)

        config = GithubRepoRetryConfig(
            github_url="https://github.com/django/django",
            git_folder="testbed",
            base_commit=commit,
            clone_timeout=5,
        )

        repo_dir = config._find_alternate_local_repo_cache(str(missing_archive))

        assert repo_dir == _cache_repo_dir_from_archive(str(other_archive))
        assert Path(repo_dir, ".git").is_dir()


def test_find_alternate_local_repo_cache_returns_empty_when_commit_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        runtime_root = root / ".runtime"
        missing_archive = (
            runtime_root
            / "run-a"
            / "gitcache"
            / "django"
            / "django"
            / "4.0"
            / "django__missing"
            / "testbed.tar"
        )
        sibling_archive = (
            runtime_root
            / "run-a"
            / "gitcache"
            / "django"
            / "django"
            / "4.0"
            / "django__cached"
            / "testbed.tar"
        )
        _make_repo_archive(sibling_archive)

        config = GithubRepoRetryConfig(
            github_url="https://github.com/django/django",
            git_folder="testbed",
            base_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            clone_timeout=5,
        )

        repo_dir = config._find_alternate_local_repo_cache(str(missing_archive))

        assert repo_dir == ""


if __name__ == "__main__":
    test_find_alternate_local_repo_cache_uses_sibling_tar()
    test_find_alternate_local_repo_cache_searches_other_runtime_gitcache()
    test_find_alternate_local_repo_cache_returns_empty_when_commit_missing()
    print("ok")
