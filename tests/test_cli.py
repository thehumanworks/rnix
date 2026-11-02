import pytest

from rnix.cli import build_parser


def test_exec_keeps_command_flags():
    args = build_parser().parse_args(["exec", "-s", "one", "--", "make", "--jobs", "4"])
    assert args.session == "one"
    assert args.argv == ["--", "make", "--jobs", "4"]


@pytest.mark.parametrize("command", ["shell", "pull", "checkpoint", "stop", "resume", "status", "rm"])
def test_session_name_is_optional(command):
    assert build_parser().parse_args([command]).name is None


def test_worktree_path_is_relative_to_the_caller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["up", "--worktree", "../wt"])
    assert args.worktree == tmp_path.parent / "wt"


def test_json_flag_is_global():
    assert build_parser().parse_args(["--json", "list"]).json
