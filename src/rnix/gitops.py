"""Local git operations. Sandboxes never get credentials, so all sync is by bundle."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import sanitize
from .errors import RnixError


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RnixError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def branch_for(name: str) -> str:
    return f"rnix/{name}"


def pull_ref(name: str) -> str:
    # Not refs/rnix/: git resolves that before refs/heads/, hijacking `rnix/<name>`.
    return f"refs/rnix-pull/{name}"


@dataclass(frozen=True)
class Repo:
    top: Path
    common_dir: Path
    rev: str
    flake_subdir: str

    @classmethod
    def discover(cls, cwd: Path | None = None) -> "Repo":
        cwd = (cwd or Path.cwd()).resolve()
        top = Path(git(cwd, "rev-parse", "--show-toplevel"))
        common = Path(git(top, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        rev = git(top, "rev-parse", "HEAD")
        return cls(top, common, rev, _flake_subdir(cwd, top))

    @property
    def default_project(self) -> str:
        # The common dir is stable across worktrees of one repo.
        name = self.common_dir.parent.name
        root = sorted(git(self.top, "rev-list", "--max-parents=0", "HEAD").split())[0]
        return f"{sanitize(name)}-{root[:8]}"

    @property
    def flake_ref(self) -> str:
        ref = f"git+file://{self.top}?rev={self.rev}"
        return f"{ref}&dir={self.flake_subdir}" if self.flake_subdir else ref

    @property
    def dirty(self) -> bool:
        return bool(git(self.top, "status", "--porcelain"))

    def archive(self, path: Path) -> None:
        git(self.top, "archive", "--format=tar.gz", "-o", str(path), self.rev)

    def create_branch(self, name: str, path: Path | None = None) -> bool:
        """Create the session branch, returning False when it was already at HEAD."""
        branch = branch_for(name)
        current = git(self.top, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
        if current and current != self.rev:
            raise RnixError(
                f"branch {branch} exists at another commit. Merge what you need, "
                f"then run `git branch -D {branch}`."
            )
        if not current:
            git(self.top, "branch", branch, self.rev)
        if path:
            git(self.top, "worktree", "add", str(path), branch)
        return not current

    def delete_branch(self, name: str, path: Path | None = None) -> None:
        if path:
            git(self.top, "worktree", "remove", "--force", str(path))
        git(self.top, "branch", "-D", branch_for(name))

    def bundle(self, branch: str, path: Path) -> None:
        git(self.top, "bundle", "create", str(path), f"refs/heads/{branch}")

    def identity(self) -> tuple[str, str]:
        name = git(self.top, "config", "user.name", check=False) or "rnix"
        email = git(self.top, "config", "user.email", check=False) or "rnix@localhost"
        return name, email

    def apply_bundle(self, bundle: Path, name: str) -> str:
        """Fetch into a private ref, then fast-forward the session branch if possible."""
        branch = branch_for(name)
        ref = pull_ref(name)
        git(self.top, "fetch", "--quiet", str(bundle), f"+refs/heads/{branch}:{ref}")
        new = git(self.top, "rev-parse", ref)
        current = git(self.top, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
        if not current:
            git(self.top, "update-ref", f"refs/heads/{branch}", new)
            return "created"
        if self._is_ancestor(new, current):
            return "up-to-date"
        if not self._is_ancestor(current, new):
            return "diverged"
        worktree = self._worktree_of(branch)
        if worktree:
            git(worktree, "merge", "--ff-only", "--quiet", ref)
        else:
            git(self.top, "update-ref", f"refs/heads/{branch}", new, current)
        return "fast-forward"

    def _is_ancestor(self, older: str, newer: str) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.top), "merge-base", "--is-ancestor", older, newer],
            capture_output=True,
        )
        return proc.returncode == 0

    def _worktree_of(self, branch: str) -> Path | None:
        path = None
        for line in git(self.top, "worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line == f"branch refs/heads/{branch}":
                return path
        return None


def _flake_subdir(cwd: Path, top: Path) -> str:
    for directory in (cwd, *cwd.parents):
        if (directory / "flake.nix").exists():
            relative = directory.relative_to(top)
            return "" if relative == Path(".") else str(relative)
        if directory == top:
            break
    raise RnixError(f"no flake.nix between {cwd} and {top}")
