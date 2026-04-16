import asyncio
import os
import shutil
import subprocess
import tempfile
import tarfile
from pathlib import Path
from typing import Any, Literal, Protocol

from git import InvalidGitRepositoryError
from git import Repo as GitRepo
from pydantic import BaseModel, ConfigDict, Field
from swerex.deployment.abstract import AbstractDeployment
from swerex.runtime.abstract import Command, UploadRequest,BashAction
from typing_extensions import Self

from sweagent.utils.github import _parse_gh_repo_url
from sweagent.utils.log import get_logger

logger = get_logger("swea-config", emoji="🔧")

from swesandbox.utils import copytree_via_tar,tar_extract


def _cache_repo_dir_from_archive(archive_path: str) -> str:
    archive = Path(archive_path)
    stem = archive.name[:-4] if archive.name.endswith(".tar") else archive.stem
    return str(archive.with_name(f"{stem}.repo"))

class Repo(Protocol):
    """Protocol for repository configurations."""
    git_folder: str
    base_commit: str
    repo_name: str

    def copy_repo(self, deployment: AbstractDeployment): ...

    def copy2(self,deployment,**kwargs: Any): ...

    def get_reset_commands(self) -> list[str]: ...


def _get_git_reset_commands(base_commit: str) -> list[str]:
    return [
        "git fetch",
        "git status",
        "git restore .",
        "git reset --hard",
        f"git checkout {base_commit}",
        "git clean -fdq",
    ]


class PreExistingRepoConfig(BaseModel):
    """Use this to specify a repository that already exists on the deployment.
    This is important because we need to cd to the repo before running the agent.

    Note: The repository must be at the root of the deployment.
    """
    repo_name: str
    """The repo name (the repository must be located at the root of the deployment)."""
    base_commit: str = Field(default="HEAD")
    """The commit to reset the repository to. The default is HEAD,
    i.e., the latest commit. You can also set this to a branch name (e.g., `dev`),
    a tag (e.g., `v0.1.0`), or a commit hash (e.g., `a4464baca1f`).
    SWE-agent will then start from this commit when trying to solve the problem.
    """

    type: Literal["preexisting"] = "preexisting"
    """Discriminator for (de)serialization/CLI. Do not change."""

    reset: bool = True
    """If True, reset the repository to the base commit after the copy operation."""

    model_config = ConfigDict(extra="forbid")

    def copy_repo(self, deployment: AbstractDeployment):
        """Does nothing."""
        pass
    def copy2(self,deployment,**kwargs: Any):
        pass
    def get_reset_commands(self) -> list[str]:
        """Issued after the copy operation or when the environment is reset."""
        if self.reset:
            return _get_git_reset_commands(self.base_commit)
        return []


class LocalRepoConfig(BaseModel):
    path: Path
    base_commit: str = Field(default="HEAD")
    """The commit to reset the repository to. The default is HEAD,
    i.e., the latest commit. You can also set this to a branch name (e.g., `dev`),
    a tag (e.g., `v0.1.0`), or a commit hash (e.g., `a4464baca1f`).
    SWE-agent will then start from this commit when trying to solve the problem.
    """
    git_folder: str
    type: Literal["local"] = "local"
    """Discriminator for (de)serialization/CLI. Do not change."""

    model_config = ConfigDict(extra="forbid")
    def copy2(self,deployment,**kwargs: Any):
        pass
    @property
    def repo_name(self) -> str:
        """Set automatically based on the repository name. Cannot be set."""
        return Path(self.path).resolve().name.replace(" ", "-").replace("'", "")

    # Let's not make this a model validator, because it leads to cryptic errors.
    # Let's just check during copy instead.
    def check_valid_repo(self) -> Self:
        try:
            repo = GitRepo(self.path, search_parent_directories=True)
        except InvalidGitRepositoryError as e:
            msg = f"Could not find git repository at {self.path=}."
            raise ValueError(msg) from e
        if repo.is_dirty() and "PYTEST_CURRENT_TEST" not in os.environ:
            msg = f"Local git repository {self.path} is dirty. Please commit or stash changes."
            raise ValueError(msg)
        return self

    def copy_repo(self, deployment: AbstractDeployment):
        self.check_valid_repo()
        asyncio.run(
            deployment.runtime.upload(UploadRequest(source_path=str(self.path), target_path=f"/{self.repo_name}"))
        )
        r = asyncio.run(
            deployment.runtime.execute(Command(command=f"chown -R root:root /{self.repo_name}", shell=True))
        )
        if r.exit_code != 0:
            msg = f"Failed to change permissions on copied repository (exit code: {r.exit_code}, stdout: {r.stdout}, stderr: {r.stderr})"
            raise RuntimeError(msg)

    def get_reset_commands(self) -> list[str]:
        """Issued after the copy operation or when the environment is reset."""
        return _get_git_reset_commands(self.base_commit)


class GithubRepoConfig(BaseModel):
    github_url: str
    git_folder: str
    base_commit: str = Field(default="HEAD")
    """The commit to reset the repository to. The default is HEAD,
    i.e., the latest commit. You can also set this to a branch name (e.g., `dev`),
    a tag (e.g., `v0.1.0`), or a commit hash (e.g., `a4464baca1f`).
    SWE-agent will then start from this commit when trying to solve the problem.
    """

    clone_timeout: float = 500
    """Timeout for git clone operation."""

    type: Literal["github"] = "github"
    """Discriminator for (de)serialization/CLI. Do not change."""

    model_config = ConfigDict(extra="forbid")
    def copy2(self,deployment,**kwargs: Any):
        pass
    def model_post_init(self, __context: Any) -> None:
        if self.github_url.count("/") == 1:
            self.github_url = f"https://github.com/{self.github_url}"

    @property
    def repo_name(self) -> str:
        org, repo = _parse_gh_repo_url(self.github_url)
        return f"{org}__{repo}"

    def _get_url_with_token(self, token: str) -> str:
        """Prepend github token to URL"""
        if not token:
            return self.github_url
        if "@" in self.github_url:
            logger.warning("Cannot prepend token to URL. '@' found in URL")
            return self.github_url
        _, _, url_no_protocol = self.github_url.partition("://")
        return f"https://{token}@{url_no_protocol}"

    def copy_repo(self, deployment: AbstractDeployment):
        """Clones the repository to the sandbox."""
        base_commit = self.base_commit
        github_token = os.getenv("GITHUB_TOKEN", "")
        url = self._get_url_with_token(github_token)
        asyncio.run(
            deployment.runtime.execute(
                Command(
                    command=" && ".join(
                        (
                            f"mkdir /{self.repo_name}",
                            f"cd /{self.repo_name}",
                            "git init",
                            f"git remote add origin {url}",
                            f"git fetch --depth 1 origin {base_commit}",
                            "git checkout FETCH_HEAD",
                            "cd ..",
                        )
                    ),
                    timeout=self.clone_timeout,
                    shell=True,
                    check=True,
                )
            ),
        )

    def get_reset_commands(self) -> list[str]:
        """Issued after the copy operation or when the environment is reset."""
        return _get_git_reset_commands(self.base_commit)


class GithubRepoRetryConfig(BaseModel):
    """Configuration for cloning a GitHub repository with retry logic."""
    git_folder: str
    """The folder name where the repository will be cloned."""

    github_url: str
    """The URL of the GitHub repository to clone."""
    base_commit: str = Field(default="HEAD")
    """The commit to reset the repository to. The default is HEAD,
    i.e., the latest commit. You can also set this to a branch name (e.g., `dev`),
    a tag (e.g., `v0.1.0`), or a commit hash (e.g., `a4464baca1f`).
    SWE-agent will then start from this commit when trying to solve the problem.
    """

    clone_timeout: float = 180
    """Timeout for git clone operation."""

    type: Literal["github"] = "github"
    """Discriminator for (de)serialization/CLI. Do not change."""

    model_config = ConfigDict(extra="forbid")

    def model_post_init(self, __context: Any) -> None:
        if self.github_url.count("/") == 1:
            self.github_url = f"https://github.com/{self.github_url}"

    @property
    def repo_name(self) -> str:
        """Set automatically based on the repository name. Cannot be set."""
        return self.git_folder

    def _get_url_with_token(self, token: str) -> str:
        """Prepend github token to URL"""
        if not token:
            return self.github_url
        if "@" in self.github_url:
            logger.warning("Cannot prepend token to URL. '@' found in URL")
            return self.github_url
        _, _, url_no_protocol = self.github_url.partition("://")
        return f"https://{token}@{url_no_protocol}"

    def copy_repo(self, deployment: AbstractDeployment):
        """Clones the repository to the sandbox."""
        base_commit = self.base_commit
        github_token = os.getenv("GITHUB_TOKEN", "")
        url = self._get_url_with_token(github_token)
        asyncio.run(
            deployment.runtime.execute(
                Command(
                    command=" && ".join(
                        (
                            f"mkdir /{self.repo_name}",
                            f"cd /{self.repo_name}",
                            "git init",
                            f"git remote add origin {url}",
                            f"git fetch --depth 1 origin {base_commit}",
                            "git checkout FETCH_HEAD",
                            "cd ..",
                        )
                    ),
                    timeout=self.clone_timeout,
                    shell=True,
                    check=True,
                )
            ),
        )

    def copy2(self, deployment: AbstractDeployment,try_count=3,local_path=None):
        """This function copies the repository to the deployment.
        It first checks if a local path is provided and exists. If so, it extracts
        the repository from the local path to the deployment. If not, it clones
        the repository from GitHub with retry logic.
        For repo matplotlib/matplotlib, it also extracts freetype and qhull tarballs to /testbed/build.
        This is due to the network limitations in our experimental environment, its original implementation is to directly download from the internet.

        Attributes:
            deployment (AbstractDeployment): The deployment to copy the repository to.
            try_count (int): The number of retry attempts for cloning the repository.
            local_path (str|None): The local path to the repository archive. If provided and exists, the repository will be extracted from this path instead of cloning from GitHub.

        """
        current_file = os.path.abspath(__file__)
        current_dir = os.path.dirname(current_file)
        zip_dir = os.path.abspath(os.path.join(current_dir, "../../../zip"))

        if (
            local_path is not None
            and not os.path.exists(local_path)
            and deployment._config.cache_git
            and not deployment._config.force_rebuild
        ):
            try:
                self._prefetch_repo_archive(local_path)
            except Exception as e:
                logger.warning(f"Outer cache prefetch failed for {self.repo_name}@{self.base_commit[:12]}: {e}")

        # if local_path exist
        if deployment._config.force_rebuild:
            if local_path is not None and os.path.exists(local_path):
                os.remove(local_path)
        if local_path is not None and os.path.exists(local_path):
            target_repo_dir = os.path.join(deployment.root_dir, self.git_folder)
            if not deployment._config.use_chroot:
                try:
                    cache_repo_dir = self._ensure_local_repo_cache(local_path)
                    self._clone_from_local_cache(cache_repo_dir, target_repo_dir)
                except Exception as e:
                    logger.warning(
                        "Local shared clone fallback failed for %s@%s, falling back to tar extract: %s",
                        self.repo_name,
                        self.base_commit[:12],
                        e,
                    )
                    tar_extract(local_path, target_repo_dir, threads=2)
            else:
                #directly copy to deployment.root_dir/deployment.git_folder
                tar_extract(local_path, target_repo_dir, threads=2)
            if deployment.ds['repo']=='matplotlib/matplotlib':
                # cp /home/zeta/SWE/SWE/zip/freetype-2.6.1.tar.gz to sandbox /testbed/build

                deployment.extract_freetype_tarball(f"{zip_dir}/freetype-2.6.1.tar.gz",deployment.sandbox_path('/testbed/build'))
                deployment.extract_freetype_tarball(f"{zip_dir}/qhull-2020-src-8.0.2.tgz",deployment.sandbox_path('/testbed/build'))
            return True


        base_commit = self.base_commit
        github_token = os.getenv("GITHUB_TOKEN", "")
        url = self._get_url_with_token(github_token)
        git_dir = deployment.sandbox_path(f"/{self.git_folder}")
        asyncio.run(
                deployment.runtime.run_in_session(
                    BashAction(
                        command=" && ".join(
                            (

                                f"mkdir -p {git_dir}" ,
                                f"cd {git_dir}",
                                "git init",
                                f"git remote add origin {url}"
                            )
                        ),
                        timeout=self.clone_timeout,
                        check='raise',
                    )
                ),
        )
        #impelement retry logic
        count=0
        while count<try_count:
            try:
                asyncio.run(
                    deployment.runtime.run_in_session(
                        BashAction(
                            command=" && ".join(
                                (

                                    f"git fetch --depth 1 origin {base_commit}",
                                    "git checkout FETCH_HEAD",
                                    "cd ..",
                                )
                            ),
                            timeout=self.clone_timeout,
                            check='raise',
                        )
                    ),
                )
                break
            except Exception as e:
                logger.warning(f"Shallow git fetch failed for {base_commit[:12]}, trying partial full-ref fetch...")
                try:
                    asyncio.run(
                        deployment.runtime.run_in_session(
                            BashAction(
                                command=" && ".join(
                                    (
                                        "git fetch --filter=tree:0 --no-tags origin '+refs/heads/*:refs/remotes/origin/*'",
                                        f"git checkout {base_commit}",
                                        "cd ..",
                                    )
                                ),
                                timeout=self.clone_timeout,
                                check='raise',
                            )
                        ),
                    )
                    break
                except Exception:
                    logger.warning(
                        f"Head-ref fetch failed for {base_commit[:12]}, trying exact ref lookup via git ls-remote..."
                    )
                    try:
                        asyncio.run(
                            deployment.runtime.run_in_session(
                                BashAction(
                                    command=" && ".join(
                                        (
                                            f"""ref=$(git ls-remote --refs origin 'refs/heads/*' 'refs/tags/*' 'refs/pull/*/head' 'refs/pull/*/merge' | grep '^{base_commit}[[:space:]]' | head -n1 | cut -f2)""",
                                            """test -n "$ref" """,
                                            """printf 'Resolved hidden ref: %s\n' "$ref" """,
                                            """git fetch --depth 1 origin "$ref" """,
                                            """git checkout -q FETCH_HEAD""",
                                            "cd ..",
                                        )
                                    ),
                                    timeout=self.clone_timeout,
                                    check='raise',
                                )
                            ),
                        )
                        break
                    except Exception:
                        count+=1
                        if count==try_count:
                            raise e
                        logger.warning(f"Git clone failed, retrying {count}/{try_count}...")
        if deployment.ds['repo']=='matplotlib/matplotlib':
            # cp /home/zeta/SWE/SWE/zip/freetype-2.6.1.tar.gz to sandbox /testbed/build
            deployment.extract_freetype_tarball(f"{zip_dir}/freetype-2.6.1.tar.gz",deployment.sandbox_path('/testbed/build'))
            deployment.extract_freetype_tarball(f"{zip_dir}/qhull-2020-src-8.0.2.tgz",deployment.sandbox_path('/testbed/build'))
        return False

    def _run_local_git(self, cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=self.clone_timeout,
            check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

    def _prefetch_repo_archive(self, archive_path: str) -> None:
        github_token = os.getenv("GITHUB_TOKEN", "")
        url = self._get_url_with_token(github_token)
        base_commit = self.base_commit
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="swe_gitprefetch_") as tmpdir:
            repo_dir = os.path.join(tmpdir, self.git_folder)
            os.makedirs(repo_dir, exist_ok=True)
            self._run_local_git(repo_dir, "init", "-q")
            self._run_local_git(repo_dir, "remote", "add", "origin", url)
            checked_out = False
            try:
                self._run_local_git(repo_dir, "fetch", "--depth", "1", "origin", base_commit)
            except Exception:
                try:
                    self._run_local_git(
                        repo_dir,
                        "fetch",
                        "--filter=tree:0",
                        "--no-tags",
                        "origin",
                        "+refs/heads/*:refs/remotes/origin/*",
                    )
                    self._run_local_git(repo_dir, "checkout", base_commit)
                    checked_out = True
                except Exception:
                    ref = self._resolve_hidden_ref(url, base_commit)
                    if not ref:
                        raise RuntimeError(f"unable to resolve hidden ref for {base_commit}")
                    self._run_local_git(repo_dir, "fetch", "--depth", "1", "origin", ref)
            if not checked_out:
                self._run_local_git(repo_dir, "checkout", "-q", "FETCH_HEAD")
            with tarfile.open(archive_path, "w") as tar:
                for entry in os.listdir(repo_dir):
                    tar.add(os.path.join(repo_dir, entry), arcname=entry)

    def _ensure_local_repo_cache(self, archive_path: str) -> str:
        cache_repo_dir = _cache_repo_dir_from_archive(archive_path)
        git_dir = os.path.join(cache_repo_dir, ".git")
        if os.path.isdir(git_dir):
            return cache_repo_dir
        if os.path.exists(cache_repo_dir):
            shutil.rmtree(cache_repo_dir, ignore_errors=True)
        os.makedirs(cache_repo_dir, exist_ok=True)
        tar_extract(archive_path, cache_repo_dir, threads=2)
        if not os.path.isdir(git_dir):
            raise RuntimeError(f"archive {archive_path} did not extract into a git repo cache")
        return cache_repo_dir

    def _clone_from_local_cache(self, source_repo_dir: str, target_repo_dir: str) -> None:
        target_path = Path(target_repo_dir)
        local_timeout = max(self.clone_timeout, 300)
        if target_path.exists():
            shutil.rmtree(target_path, ignore_errors=True)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--local",
                "--shared",
                "--no-checkout",
                source_repo_dir,
                target_repo_dir,
            ],
            text=True,
            capture_output=True,
            timeout=local_timeout,
            check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        subprocess.run(
            ["git", "checkout", "-q", self.base_commit],
            cwd=target_repo_dir,
            text=True,
            capture_output=True,
            timeout=local_timeout,
            check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        self._run_local_git(target_repo_dir, "clean", "-fdq")

    def _resolve_hidden_ref(self, url: str, commit: str) -> str:
        out = subprocess.run(
            [
                "git",
                "ls-remote",
                "--refs",
                url,
                "refs/heads/*",
                "refs/tags/*",
                "refs/pull/*/head",
                "refs/pull/*/merge",
            ],
            text=True,
            capture_output=True,
            timeout=self.clone_timeout,
            check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        ).stdout
        for line in out.splitlines():
            sha, _, ref = line.partition("\t")
            if sha == commit and ref:
                logger.warning(f"Resolved hidden ref for {commit[:12]} -> {ref}")
                return ref
        return ""
    def get_reset_commands(self) -> list[str]:

        return _get_git_reset_commands(self.base_commit)


RepoConfig = LocalRepoConfig | GithubRepoConfig | PreExistingRepoConfig | GithubRepoRetryConfig


# def repo_from_simplified_input(
#     *, input: str, base_commit: str = "HEAD", type: Literal["local", "github", "preexisting", "auto"] = "auto"
# ) -> RepoConfig:
#     """Get repo config from a simplified input.

#     Args:
#         input: Local path or GitHub URL
#         type: The type of repo. Set to "auto" to automatically detect the type
#             (does not work for preexisting repos).
#     """
#     if type == "local":
#         return LocalRepoConfig(path=Path(input), base_commit=base_commit)
#     if type == "github":
#         return GithubRepoConfig(github_url=input, base_commit=base_commit)
#     if type == "preexisting":
#         return PreExistingRepoConfig(repo_name=input, base_commit=base_commit)
#     if type == "auto":
#         if input.startswith("https://github.com/"):
#             return GithubRepoConfig(github_url=input, base_commit=base_commit)
#         else:
#             return LocalRepoConfig(path=Path(input), base_commit=base_commit)
#     msg = f"Unknown repo type: {type}"
#     raise ValueError(msg)

