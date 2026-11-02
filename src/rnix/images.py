"""The image chain: public base, then rnix-base, then the project image."""

import json
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

import modal
import modal.exception

from . import remote
from .bootstrap import BOOTSTRAP
from .context import SYSTEM as NIX_SYSTEM
from .context import Context
from .errors import RnixError
from .gitops import git
from .state import Store, image_path

SOURCE_ARCHIVE = "/tmp/rnix-src.tgz"
PROFILES = "/nix/var/nix/profiles"


def _tags(prefix: str, environment: str | None) -> list[dict]:
    # Named images cannot be hydrated, so the CLI is the public way to look one up.
    command = ["modal", "image", "names", "list", "--prefix", prefix, "--json"]
    if environment:
        command += ["--env", environment]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RnixError(f"could not list images: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def exists(name: str, environment: str | None) -> bool:
    """Check the tag. A deleted image keeps its tag, so booting can still miss."""
    return any(item["tag"] == name for item in _tags(name, environment))


def is_missing_image(error: Exception) -> bool:
    # Secrets raise the same class, and those must not trigger a rebuild.
    return isinstance(error, modal.exception.NotFoundError) and str(error).startswith("image ")


def named(name: str, environment: str | None):
    return modal.Image.from_name(name, environment_name=environment)


def _upload_source(context: Context, sandbox) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "src.tgz"
        context.repo.archive(path)
        sandbox.filesystem.copy_from_local(str(path), SOURCE_ARCHIVE)
    remote.run(sandbox, f"mkdir -p /tmp/rnix-src && tar -xzf {SOURCE_ARCHIVE} -C /tmp/rnix-src")


def _snapshot(context: Context, sandbox, *tags: str) -> str:
    image = sandbox.snapshot_filesystem(timeout=context.cfg["image"]["snapshotTimeout"], ttl=None)
    for tag in tags:
        image.publish(tag, environment_name=context.cfg["modalEnvironment"])
    return image.object_id


def ensure_base(context: Context, log=remote.stderr_log, fresh: bool = False) -> str:
    cfg = context.cfg
    name = f"rnix-base:{context.base_hash}"
    if not fresh and exists(name, cfg["modalEnvironment"]):
        return name

    log(f"building NixOS base {name} from {cfg['base']}")
    started = time.monotonic()
    sandbox = remote.create_raw(
        cfg,
        modal.Image.from_registry(cfg["base"]),
        {"rnix-role": "base-build"},
        **remote.build_kwargs(cfg),
    )
    try:
        _upload_source(context, sandbox)
        remote.run(
            sandbox,
            BOOTSTRAP.format(
                flake=context.flake_path,
                system=NIX_SYSTEM,
                attr=context.attr,
                profiles=PROFILES,
                archive=SOURCE_ARCHIVE,
            ),
        )
        _snapshot(context, sandbox, name)
    finally:
        sandbox.terminate()
    log(f"built {name} in {time.monotonic() - started:.0f}s")
    return name


def ensure_project(context: Context, store: Store, log=remote.stderr_log, fresh: bool = False) -> str:
    """Return the project image for this shell, building it when the flake changed."""
    cfg = context.cfg
    environment = cfg["modalEnvironment"]
    name = f"{context.image_repo}:{context.key}"
    if not fresh and exists(name, environment):
        return name

    _warn_if_key_follows_source(context, store, log)
    base = ensure_base(context, log)
    # This tag includes the base hash, so a parent always has the right system.
    latest = f"{context.image_repo}:latest-{context.base_hash}"
    parent = latest if not fresh and exists(latest, environment) else base

    try:
        _build_project(context, store, log, name, parent, latest)
    except modal.exception.NotFoundError as error:
        if not is_missing_image(error):
            raise
        log(f"{parent} was deleted, so rebuilding from the public base")
        _build_project(context, store, log, name, ensure_base(context, log, fresh=True), latest)
    return name


def _build_project(context: Context, store: Store, log, name: str, parent: str, latest: str) -> None:
    cfg = context.cfg
    log(f"building {name} from {parent}")
    started = time.monotonic()
    sandbox = remote.boot(
        cfg,
        named(parent, cfg["modalEnvironment"]),
        {"rnix-role": "project-build", "rnix-project": context.project},
        **remote.build_kwargs(cfg),
    )
    try:
        _upload_source(context, sandbox)
        arguments = shlex.join([context.flake_path, context.attr])
        remote.run(sandbox, f"rnix-build-shell {arguments}")
        image_id = _snapshot(context, sandbox, name, latest, f"{context.image_repo}:latest")
    finally:
        sandbox.terminate()
    store.write(image_path(context.project), {"key": context.key, "commit": context.repo.rev, "image": image_id})
    log(f"published {name} ({image_id}) in {time.monotonic() - started:.0f}s")


def _warn_if_key_follows_source(context: Context, store: Store, log) -> None:
    previous = store.read(image_path(context.project))
    if not previous or previous["key"] == context.key:
        return
    changed = git(context.repo.top, "diff", "--name-only", previous["commit"], context.repo.rev, check=False)
    if changed and not any(path.endswith((".nix", "flake.lock")) for path in changed.splitlines()):
        log(
            "warning: the image key changed without a Nix file changing. The shell probably "
            "depends on repo source (./. or self.packages), so each commit rebuilds the image."
        )


def list_images(context: Context) -> list[dict]:
    return _tags(context.image_repo, context.cfg["modalEnvironment"])
