"""Session lifecycle: one sandbox on one branch."""

import os
import secrets
import shlex
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import modal
import modal.exception

from . import images, remote
from .config import parse_duration, validate_session_name
from .context import Context
from .errors import RnixError
from .gitops import branch_for, pull_ref
from .state import Store, session_path

BUNDLE = "/tmp/rnix.bundle"


def _tags(context: Context, name: str) -> dict[str, str]:
    return {**context.cfg["tags"], "rnix-project": context.project, "rnix-session": name}


def _save(context: Context, store: Store, record: dict) -> dict:
    store.write(session_path(context.project, record["name"]), record)
    return record


def load(context: Context, store: Store, name: str | None) -> dict:
    if name is None:
        names = store.names(f"sessions/{context.project}")
        if len(names) != 1:
            found = ", ".join(names) or "none"
            raise RnixError(f"name a session (found: {found})")
        name = names[0]
    record = store.read(session_path(context.project, name))
    if record is None:
        raise RnixError(f"no session named {name}")
    return record


def _running(record: dict):
    sandbox = remote.attach(record["sandbox_id"])
    if sandbox is None:
        raise RnixError(f"session {record['name']} is not running. Run `rnix resume {record['name']}`.")
    return sandbox


def up(
    context: Context,
    store: Store,
    name: str | None = None,
    worktree: Path | None = None,
    log=remote.stderr_log,
) -> dict:
    repo = context.repo
    cfg = context.cfg
    name = validate_session_name(name or f"{repo.rev[:7]}-{secrets.token_hex(2)}")
    if store.read(session_path(context.project, name)):
        raise RnixError(f"session {name} already exists. Use `rnix resume {name}` or `rnix rm {name}`.")
    if repo.dirty:
        log("warning: uncommitted changes are not copied. The sandbox starts from HEAD.")

    image = images.ensure_project(context, store, log)
    branch = branch_for(name)
    created = repo.create_branch(name, worktree)
    sandbox = None
    try:
        sandbox = _boot_session(context, store, image, name, log)
        _clone(context, sandbox, branch)
    except Exception:
        if sandbox is not None:
            sandbox.terminate()
        if created:
            repo.delete_branch(name, worktree)
        raise
    return _save(
        context,
        store,
        {
            "name": name,
            "project": context.project,
            "branch": branch,
            "sandbox_id": sandbox.object_id,
            "image": image,
            "synced": repo.rev,
            "exit_image": None,
            "checkpoint_image": None,
            "cfg": cfg,
            "created_at": time.time(),
        },
    )


def _boot_session(context: Context, store: Store, image: str, name: str, log):
    def start():
        cfg = context.cfg
        return remote.boot(
            cfg,
            images.named(image, cfg["modalEnvironment"]),
            _tags(context, name),
            env=cfg["env"],
            **remote.session_kwargs(cfg),
        )

    try:
        return start()
    except modal.exception.NotFoundError as error:
        if not images.is_missing_image(error):
            raise
    log(f"{image} was deleted, so rebuilding it")
    images.ensure_project(context, store, log, fresh=True)
    return start()


def _clone(context: Context, sandbox, branch: str) -> None:
    repo = context.repo
    workspace = shlex.quote(context.cfg["workspace"])
    user, email = repo.identity()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "repo.bundle"
        repo.bundle(branch, path)
        sandbox.filesystem.copy_from_local(str(path), BUNDLE)
    remote.run(
        sandbox,
        f"""set -euo pipefail
git clone --quiet --branch {shlex.quote(branch)} {BUNDLE} {workspace}
git -C {workspace} remote remove origin
git -C {workspace} config user.name {shlex.quote(user)}
git -C {workspace} config user.email {shlex.quote(email)}
rm -f {BUNDLE}
""",
    )


def shell(context: Context, store: Store, name: str | None) -> None:
    record = load(context, store, name)
    sandbox = _running(record)
    cfg = record["cfg"]
    command = [f"{remote.SW}/bin/rnix-run", cfg["workspace"], "bash"]
    idle = parse_duration(cfg["idleTimeout"])
    if idle:
        # An open shell counts as activity, so log out at an idle prompt.
        command = [f"{remote.SW}/bin/env", f"TMOUT={idle}", *command]
    arguments = ["modal", "shell", sandbox.object_id, "--cmd", shlex.join(command)]
    if cfg["modalEnvironment"]:
        arguments += ["--env", cfg["modalEnvironment"]]
    os.execvp("modal", arguments)


def exec_command(context: Context, store: Store, name: str | None, argv: list[str]) -> int:
    record = load(context, store, name)
    return remote.run_shell_command(_running(record), record["cfg"]["workspace"], argv)


@contextmanager
def _readable(record: dict) -> Iterator:
    """Yield the running sandbox, or boot its latest snapshot to read from it."""
    sandbox = remote.attach(record["sandbox_id"])
    if sandbox is not None:
        yield sandbox
        return
    image = _latest_snapshot(record)
    cfg = record["cfg"]
    recovery = remote.boot(
        cfg,
        modal.Image.from_id(image),
        {"rnix-role": "recovery", "rnix-session": record["name"]},
        **remote.recovery_kwargs(cfg),
    )
    try:
        yield recovery
    finally:
        recovery.terminate()


def pull(context: Context, store: Store, name: str | None) -> dict:
    record = load(context, store, name)
    branch = record["branch"]
    workspace = shlex.quote(record["cfg"]["workspace"])
    with _readable(record) as sandbox:
        tip, ahead, dirty = remote.run(
            sandbox,
            f"""set -euo pipefail
cd {workspace}
git rev-parse refs/heads/{shlex.quote(branch)}
git rev-list --count {record['synced']}..refs/heads/{shlex.quote(branch)}
git status --porcelain | wc -l
""",
        ).split()
        result = {"name": record["name"], "branch": branch, "tip": tip, "uncommitted": int(dirty)}
        if int(ahead) == 0:
            return {**result, "status": "up-to-date"}
        status = _transfer(context, sandbox, record, branch)
    if status == "diverged":
        # Stay at the old commit, so the next pull still reports what is missing.
        return {**result, "status": status, "ref": pull_ref(record["name"])}
    record["synced"] = tip
    _save(context, store, record)
    return {**result, "status": status}


def _transfer(context: Context, sandbox, record: dict, branch: str) -> str:
    workspace = shlex.quote(record["cfg"]["workspace"])
    revisions = f"{record['synced']}..refs/heads/{branch}"
    remote.run(sandbox, f"git -C {workspace} bundle create --quiet {BUNDLE} {shlex.quote(revisions)}")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pull.bundle"
        sandbox.filesystem.copy_to_local(BUNDLE, str(path))
        remote.run(sandbox, f"rm -f {BUNDLE}")
        return context.repo.apply_bundle(path, record["name"])


def checkpoint(context: Context, store: Store, name: str | None) -> dict:
    record = load(context, store, name)
    cfg = record["cfg"]
    image = _running(record).snapshot_filesystem(
        timeout=cfg["image"]["snapshotTimeout"],
        ttl=parse_duration(cfg["checkpoint"]["ttl"]),
    )
    record["checkpoint_image"] = image.object_id
    return _save(context, store, record)


def stop(context: Context, store: Store, name: str | None, log=remote.stderr_log) -> dict:
    record = load(context, store, name)
    sandbox = remote.attach(record["sandbox_id"])
    if sandbox is not None:
        try:
            pulled = pull(context, store, record["name"])
            log(f"pulled {pulled['branch']}: {pulled['status']}")
            if pulled["uncommitted"]:
                log(f"{pulled['uncommitted']} uncommitted files stay in the snapshot only")
            record = load(context, store, record["name"])
        except RnixError as error:
            log(f"warning: pull failed, relying on the snapshot: {error}")
        sandbox.terminate(wait=True)
    return _record_exit(context, store, record, log)


def _record_exit(context: Context, store: Store, record: dict, log) -> dict:
    exit_config = record["cfg"]["exitSnapshot"]
    if exit_config["enable"] and not record["exit_image"]:
        record["exit_image"] = remote.exit_snapshot(record["sandbox_id"], exit_config["timeout"])
        if not record["exit_image"]:
            log("warning: no exit snapshot is available for this session")
    return _save(context, store, record)


def _latest_snapshot(record: dict) -> str:
    """Pick the newest state: the exit snapshot, then the last checkpoint."""
    image = record["exit_image"]
    exit_config = record["cfg"]["exitSnapshot"]
    if not image and exit_config["enable"]:
        image = remote.exit_snapshot(record["sandbox_id"], exit_config["timeout"])
    image = image or record["checkpoint_image"]
    if not image:
        raise RnixError(f"session {record['name']} has no snapshot to restore")
    return image


def resume(context: Context, store: Store, name: str | None, log=remote.stderr_log) -> dict:
    record = load(context, store, name)
    if remote.attach(record["sandbox_id"]) is not None:
        return record
    image = _latest_snapshot(record)
    if image == record["checkpoint_image"]:
        log("warning: restoring the last checkpoint, so later work is lost")
    cfg = record["cfg"]
    sandbox = remote.boot(
        cfg,
        modal.Image.from_id(image),
        _tags(context, record["name"]),
        env=cfg["env"],
        **remote.session_kwargs(cfg),
    )
    record["sandbox_id"] = sandbox.object_id
    record["exit_image"] = None
    return _save(context, store, record)


def remove(context: Context, store: Store, name: str | None) -> dict:
    record = load(context, store, name)
    sandbox = remote.attach(record["sandbox_id"])
    if sandbox is not None:
        sandbox.terminate()
    store.remove(session_path(context.project, record["name"]))
    return {"name": record["name"], "removed": True, "branch": branch_for(record["name"])}


def describe(record: dict) -> dict:
    sandbox = remote.attach(record["sandbox_id"])
    summary = {key: record[key] for key in ("name", "branch", "sandbox_id", "image", "synced")}
    summary["running"] = sandbox is not None
    summary["snapshot"] = record["exit_image"] or record["checkpoint_image"]
    return summary


def list_sessions(context: Context, store: Store) -> list[dict]:
    names = store.names(f"sessions/{context.project}")
    return [describe(load(context, store, name)) for name in names]
