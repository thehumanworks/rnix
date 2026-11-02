"""Command line interface. Progress goes to stderr and results to stdout."""

import argparse
import json
import sys
from pathlib import Path

import modal.exception

from . import context as contexts
from . import images, session
from .errors import RnixError
from .state import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rnix", description="Remote NixOS dev shells on Modal.")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("--attr", help="devShell attribute (default: RNIX_ATTR or remote)")
    commands = parser.add_subparsers(dest="command", required=True)

    up = commands.add_parser("up", help="create a sandbox on a new branch")
    up.add_argument("name", nargs="?")
    up.add_argument(
        "--worktree",
        type=lambda path: Path(path).resolve(),
        help="also add a local worktree for the branch",
    )

    for name, description in (
        ("shell", "open an interactive shell"),
        ("pull", "fetch commits into the local branch"),
        ("checkpoint", "snapshot a running session"),
        ("stop", "pull, terminate, and keep the exit snapshot"),
        ("resume", "start a stopped session from its latest snapshot"),
        ("status", "show one session"),
        ("rm", "terminate and forget a session"),
    ):
        commands.add_parser(name, help=description).add_argument("name", nargs="?")

    run = commands.add_parser("exec", help="run a command and return its exit code")
    run.add_argument("-s", "--session", help="session name")
    run.add_argument("argv", nargs=argparse.REMAINDER, metavar="-- COMMAND")

    commands.add_parser("list", help="list this project's sessions")

    image = commands.add_parser("image", help="manage the project image")
    image_commands = image.add_subparsers(dest="image_command", required=True)
    build = image_commands.add_parser("build", help="build and publish the project image")
    build.add_argument("--fresh", action="store_true", help="rebuild from the base")
    image_commands.add_parser("list", help="list published tags")
    return parser


def dispatch(args: argparse.Namespace):
    """Run a command and return its result, or an int to use as the exit code."""
    if args.command == "image" and args.image_command == "list":
        return images.list_images(contexts.cached(args.attr))
    if args.command == "image":
        context = contexts.evaluate(args.attr)
        store = Store(context.cfg["modalEnvironment"])
        return {"image": images.ensure_project(context, store, fresh=args.fresh)}
    if args.command == "up":
        context = contexts.evaluate(args.attr)
        return session.up(context, Store(context.cfg["modalEnvironment"]), args.name, args.worktree)

    context = contexts.cached(args.attr)
    store = Store(context.cfg["modalEnvironment"])
    if args.command == "exec":
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not argv:
            raise RnixError("exec needs a command: rnix exec -- make test")
        return session.exec_command(context, store, args.session, argv)
    if args.command == "shell":
        return session.shell(context, store, args.name)
    if args.command == "pull":
        return session.pull(context, store, args.name)
    if args.command == "checkpoint":
        return session.describe(session.checkpoint(context, store, args.name))
    if args.command == "stop":
        return session.describe(session.stop(context, store, args.name))
    if args.command == "resume":
        return session.describe(session.resume(context, store, args.name))
    if args.command == "status":
        return session.describe(session.load(context, store, args.name))
    if args.command == "rm":
        return session.remove(context, store, args.name)
    if args.command == "list":
        return session.list_sessions(context, store)
    raise AssertionError(args.command)


def emit(result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
    elif isinstance(result, list):
        for item in result:
            print("  ".join(str(value) for value in item.values()))
    else:
        for key, value in result.items():
            if key != "cfg":
                print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = dispatch(args)
    except (RnixError, modal.exception.Error) as error:
        if args.json:
            print(json.dumps({"error": str(error)}))
        else:
            print(f"rnix: error: {error}", file=sys.stderr)
        return 1
    if isinstance(result, int):
        return result
    if result is not None:
        emit(result, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
