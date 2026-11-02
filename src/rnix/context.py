"""Resolves the flake's rnix settings at HEAD."""

import json
import os
import subprocess
from dataclasses import dataclass

from .bootstrap import BOOTSTRAP
from .config import base_hash, project_key
from .errors import RnixError
from .gitops import Repo

NIX = ["nix", "--extra-experimental-features", "nix-command flakes"]
APPLY = "s: { config = s.rnixConfig; shell = s.drvPath; toplevel = s.nixosToplevel.drvPath; }"
SYSTEM = "x86_64-linux"


def attr_name(override: str | None = None) -> str:
    return override or os.environ.get("RNIX_ATTR", "remote")


@dataclass
class Context:
    repo: Repo
    attr: str
    cfg: dict
    project: str
    shell_drv: str = ""
    toplevel_drv: str = ""

    @property
    def key(self) -> str:
        return project_key(self.shell_drv, self.base_hash)

    @property
    def base_hash(self) -> str:
        return base_hash(self.cfg["base"], self.toplevel_drv, BOOTSTRAP)

    @property
    def image_repo(self) -> str:
        return f"rnix-{self.project}"

    @property
    def flake_path(self) -> str:
        path = "/tmp/rnix-src"
        return f"{path}/{self.repo.flake_subdir}" if self.repo.flake_subdir else path


def evaluate(attr: str | None = None) -> Context:
    """Evaluate the flake at HEAD. HEAD is what ships, so a dirty tree is ignored."""
    repo = Repo.discover()
    attr = attr_name(attr)
    installable = f"{repo.flake_ref}#devShells.{SYSTEM}.{attr}"
    proc = subprocess.run(
        [*NIX, "eval", "--json", "--no-write-lock-file", installable, "--apply", APPLY],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RnixError(f"could not evaluate {installable}:\n{proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    cfg = data["config"]
    context = Context(
        repo,
        attr,
        cfg,
        cfg["project"] or repo.default_project,
        data["shell"],
        data["toplevel"],
    )
    _write_cache(context)
    return context


def cached(attr: str | None = None) -> Context:
    """Session commands only need names, so they skip a slow evaluation when they can."""
    repo = Repo.discover()
    attr = attr_name(attr)
    path = _cache_path(repo, attr)
    if path.exists():
        data = json.loads(path.read_text())
        return Context(repo, attr, data["cfg"], data["project"])
    return evaluate(attr)


def _cache_path(repo: Repo, attr: str):
    return repo.common_dir / "rnix" / f"context-{attr}.json"


def _write_cache(context: Context) -> None:
    path = _cache_path(context.repo, context.attr)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"cfg": context.cfg, "project": context.project}))
