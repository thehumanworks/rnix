import subprocess
from pathlib import Path

import pytest

from rnix.errors import RnixError
from rnix.gitops import Repo, branch_for, git, pull_ref


def commit(path: Path, name: str, content: str = "x") -> str:
    (path / name).write_text(content)
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Repo:
    path = tmp_path / "My_Repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    commit(path, "flake.nix")
    return Repo.discover(path)


def sandbox_clone(repo: Repo, branch: str, tmp_path: Path) -> Path:
    bundle = tmp_path / "up.bundle"
    repo.bundle(branch, bundle)
    remote = tmp_path / "remote"
    subprocess.run(["git", "clone", "-q", "--branch", branch, str(bundle), str(remote)], check=True)
    return remote


def bundle_from(remote: Path, synced: str, branch: str, tmp_path: Path) -> Path:
    path = tmp_path / "pull.bundle"
    git(remote, "bundle", "create", "--quiet", str(path), f"{synced}..refs/heads/{branch}")
    return path


def test_project_is_stable_across_worktrees(repo, tmp_path):
    assert repo.create_branch("wt", tmp_path / "elsewhere")
    assert Repo.discover(tmp_path / "elsewhere").default_project == repo.default_project
    assert repo.default_project.startswith("my-repo-")


def test_flake_ref_pins_head(repo):
    assert repo.flake_ref == f"git+file://{repo.top}?rev={repo.rev}"


def test_flake_ref_includes_subdirectory(repo):
    sub = repo.top / "sub"
    sub.mkdir()
    (sub / "flake.nix").write_text("")
    assert Repo.discover(sub).flake_ref.endswith(f"&dir=sub")


def test_discover_needs_a_flake(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    commit(tmp_path, "readme")
    with pytest.raises(RnixError):
        Repo.discover(tmp_path)


def test_create_branch_reuses_one_at_head(repo):
    assert repo.create_branch("one")
    assert not repo.create_branch("one")


def test_create_branch_refuses_one_at_another_commit(repo):
    repo.create_branch("one")
    git(repo.top, "checkout", "-q", branch_for("one"))
    commit(repo.top, "work.txt")
    git(repo.top, "checkout", "-q", "main")
    with pytest.raises(RnixError, match="git branch -D rnix/one"):
        repo.create_branch("one")


def test_delete_branch_removes_worktree(repo, tmp_path):
    repo.create_branch("one", tmp_path / "wt")
    repo.delete_branch("one", tmp_path / "wt")
    assert not (tmp_path / "wt").exists()
    assert repo.create_branch("one")


def test_round_trip_fast_forwards_branch(repo, tmp_path):
    branch = branch_for("s")
    repo.create_branch("s")
    remote = sandbox_clone(repo, branch, tmp_path)
    tip = commit(remote, "work.txt")
    assert repo.apply_bundle(bundle_from(remote, repo.rev, branch, tmp_path), "s") == "fast-forward"
    assert git(repo.top, "rev-parse", branch) == tip
    assert git(repo.top, "rev-parse", pull_ref("s")) == tip


def test_round_trip_updates_a_checked_out_worktree(repo, tmp_path):
    worktree = tmp_path / "wt"
    branch = branch_for("s")
    repo.create_branch("s", worktree)
    remote = sandbox_clone(repo, branch, tmp_path)
    commit(remote, "work.txt")
    assert repo.apply_bundle(bundle_from(remote, repo.rev, branch, tmp_path), "s") == "fast-forward"
    assert (worktree / "work.txt").exists()


def test_diverged_branch_is_left_alone(repo, tmp_path):
    branch = branch_for("s")
    repo.create_branch("s")
    remote = sandbox_clone(repo, branch, tmp_path)
    tip = commit(remote, "work.txt")
    git(repo.top, "checkout", "-q", branch)
    local = commit(repo.top, "local.txt")
    assert repo.apply_bundle(bundle_from(remote, repo.rev, branch, tmp_path), "s") == "diverged"
    assert git(repo.top, "rev-parse", branch) == local
    assert git(repo.top, "rev-parse", pull_ref("s")) == tip


def test_merged_divergence_counts_as_up_to_date(repo, tmp_path):
    branch = branch_for("s")
    repo.create_branch("s")
    remote = sandbox_clone(repo, branch, tmp_path)
    commit(remote, "work.txt")
    bundle = bundle_from(remote, repo.rev, branch, tmp_path)
    git(repo.top, "checkout", "-q", branch)
    commit(repo.top, "local.txt")
    assert repo.apply_bundle(bundle, "s") == "diverged"
    git(repo.top, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "--no-edit", pull_ref("s"))
    assert repo.apply_bundle(bundle, "s") == "up-to-date"


def test_deleted_branch_is_recreated(repo, tmp_path):
    branch = branch_for("s")
    repo.create_branch("s")
    remote = sandbox_clone(repo, branch, tmp_path)
    tip = commit(remote, "work.txt")
    git(repo.top, "branch", "-D", branch)
    assert repo.apply_bundle(bundle_from(remote, repo.rev, branch, tmp_path), "s") == "created"
    assert git(repo.top, "rev-parse", branch) == tip
