"""Everything that talks to Modal sandboxes. All `_experimental_` use stays here."""

import sys
import threading
from typing import Callable

import modal
import modal.exception

from .config import lifetime, parse_duration
from .errors import RnixError

SYSTEM = "/nix/var/nix/profiles/system"
SW = f"{SYSTEM}/sw"
READY = "/run/rnix/ready"
CERTS = "/etc/ssl/certs/ca-certificates.crt"

# `nixos/nix` sets these in the image config and they point at its profile, which
# the base removes. The NixOS guard stops /etc/bashrc replacing a dev shell's PATH.
SANDBOX_ENV = {
    "PATH": f"{SW}/bin",
    "NIX_SSL_CERT_FILE": CERTS,
    "SSL_CERT_FILE": CERTS,
    "GIT_SSL_CAINFO": CERTS,
    "CURL_CA_BUNDLE": CERTS,
    "LOCALE_ARCHIVE": f"{SW}/lib/locale/locale-archive",
    "__NIXOS_SET_ENVIRONMENT_DONE": "1",
}

Log = Callable[[str], None]


def stderr_log(message: str) -> None:
    print(f"rnix: {message}", file=sys.stderr, flush=True)


def app_for(cfg: dict) -> "modal.App":
    return modal.App.lookup(cfg["app"], create_if_missing=True, environment_name=cfg["modalEnvironment"])


def secrets_for(cfg: dict, names: list[str]) -> list:
    return [modal.Secret.from_name(name, environment_name=cfg["modalEnvironment"]) for name in names]


def build_kwargs(cfg: dict) -> dict:
    image = cfg["image"]
    return {
        "cpu": image["cpu"],
        "memory": image["memory"],
        "timeout": lifetime(image["timeout"]),
        # Nix cannot run local builds under gVisor, and images restore on either runtime.
        "runtime": "vm",
        "secrets": secrets_for(cfg, image["secrets"]),
    }


def session_kwargs(cfg: dict) -> dict:
    network = cfg["network"]
    environment = cfg["modalEnvironment"]
    kwargs = {
        "cpu": cfg["cpu"],
        "memory": cfg["memory"],
        "gpu": cfg["gpu"],
        "cloud": cfg["cloud"],
        "region": cfg["region"],
        "runtime": cfg["runtime"],
        "timeout": lifetime(cfg["timeout"]),
        "idle_timeout": parse_duration(cfg["idleTimeout"]),
        "secrets": secrets_for(cfg, cfg["secrets"]),
        "block_network": network["block"],
        "outbound_domain_allowlist": network["domainAllowlist"],
        "outbound_cidr_allowlist": network["cidrAllowlist"],
        "volumes": {
            path: modal.Volume.from_name(name, environment_name=environment)
            for path, name in cfg["volumes"].items()
        },
    }
    if cfg["exitSnapshot"]["enable"]:
        kwargs["experimental_options"] = {"enable_exit_snapshot": True}
    return {**kwargs, **cfg["extraCreateArgs"]}


def recovery_kwargs(cfg: dict) -> dict:
    """A short-lived, offline, secret-free sandbox for reading a dead session's snapshot."""
    return {"cpu": 1, "memory": 2048, "runtime": cfg["runtime"], "timeout": 900, "block_network": True}


def boot(cfg: dict, image, tags: dict[str, str], env: dict[str, str] | None = None, **kwargs):
    """Start a sandbox on a NixOS image and wait for activation."""
    sandbox = modal.Sandbox.create(
        f"{SW}/bin/rnix-entry",
        app=app_for(cfg),
        image=image,
        env={**SANDBOX_ENV, **(env or {})},
        tags=tags,
        readiness_probe=modal.Probe.with_exec(f"{SW}/bin/test", "-f", READY),
        environment_name=cfg["modalEnvironment"],
        **kwargs,
    )
    try:
        sandbox.wait_until_ready(timeout=180)
    except Exception:
        sandbox.terminate()
        raise
    return sandbox


def create_raw(cfg: dict, image, tags: dict[str, str], **kwargs):
    """Start a sandbox on an image that is not NixOS yet."""
    return modal.Sandbox.create(
        app=app_for(cfg),
        image=image,
        tags=tags,
        environment_name=cfg["modalEnvironment"],
        **kwargs,
    )


def attach(sandbox_id: str):
    """Return the sandbox if it is still running."""
    try:
        sandbox = modal.Sandbox.from_id(sandbox_id)
    except modal.exception.NotFoundError:
        return None
    return sandbox if sandbox.poll() is None else None


def exit_snapshot(sandbox_id: str, timeout: float) -> str | None:
    """Fetch a stopped sandbox's exit snapshot, returning None when there is none."""
    try:
        sandbox = modal.Sandbox.from_id(sandbox_id)
        return sandbox._experimental_get_exit_snapshot(timeout=timeout).object_id
    except (
        modal.exception.InvalidError,
        modal.exception.NotFoundError,
        modal.exception.SnapshotCreationError,
        modal.exception.TimeoutError,
    ):
        return None


def stream(process, stdout, stderr) -> int:
    """Forward a process's output and return its exit code."""

    def pump(source, sink):
        for chunk in source:
            sink.write(chunk)
            sink.flush()

    threads = [
        threading.Thread(target=pump, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    code = process.wait()
    for thread in threads:
        thread.join(timeout=5)
    return code


def run(sandbox, script: str, timeout: int | None = None) -> str:
    """Run a bash script, raising when it fails, and return its stdout."""
    process = sandbox.exec("bash", "-c", script, timeout=timeout)
    output = process.stdout.read()
    errors = process.stderr.read()
    code = process.wait()
    if code != 0:
        raise RnixError(f"remote command failed with exit code {code}:\n{output[-2000:]}{errors[-2000:]}")
    return output


def run_shell_command(sandbox, workspace: str, argv: list[str]) -> int:
    process = sandbox.exec(f"{SW}/bin/rnix-run", workspace, *argv)
    return stream(process, sys.stdout, sys.stderr)
