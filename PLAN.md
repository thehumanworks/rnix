# rnix plan

`rnix` is a Nix library and CLI. A flake adds `mkRemoteShell` and gets a remote
Modal sandbox running its devShell on a NixOS base, on its own git branch.

## Goals

- Add to any flake with a few lines, flake-parts not required.
- Usable by humans (interactive shell) and agents (`--json`, exit codes).
- No registry, no local Docker, no Linux builder. A Modal token is enough.
- Flake dependencies are cached in a published Modal image per project.
- Everything configurable. Exit snapshots on by default.

## Public API

```nix
devShells.x86_64-linux.remote = rnix.lib.mkRemoteShell {
  inherit pkgs;                 # x86_64-linux
  inputsFrom = [ shell ];       # other args pass through to pkgs.mkShell
  packages = [ pkgs.ripgrep ];
  remote = {
    project = "myrepo";         # default: derived from the git top-level name
    base = "nixos/nix:2.35.4";  # any public registry ref
    nixos.modules = [ ];
    runtime = "vm";             # "vm" | "gvisor" | null
    cpu = 4; memory = 8192; gpu = null;
    timeout = "24h"; idleTimeout = "30m";
    workspace = "/workspace";
    exitSnapshot.enable = true;
    secrets = [ ];              # Modal Secret names, never values
    network = { block = false; domainAllowlist = null; cidrAllowlist = null; };
    env = { };
  };
}
```

Returns an ordinary mkShell derivation (`nix develop .#remote` works locally)
with `passthru.cli` (wrapped CLI carrying the rendered JSON config) and
`passthru.remote` (resolved settings). `flakeModules.default` is a thin
flake-parts wrapper around the same function.

## Image chain

All images after the first are Modal named images. No registry push.

```text
Image.from_registry(base)                                  public and pinned
  -> vm sandbox builds the NixOS toplevel, activates it, removes the bootstrap
     profile, GCs
  -> snapshot_filesystem(ttl=None); publish rnix-base:<basehash>

rnix-base:<basehash>, or rnix-<project>:latest-<basehash> when it exists
  -> vm sandbox runs `rnix-build-shell`, which saves the shell with
     `nix print-dev-env --profile`, roots flake inputs, deletes the checkout, GCs
  -> snapshot_filesystem(ttl=None); publish rnix-<project>:<key>,
     :latest-<basehash>, and :latest

rnix-<project>:<key> -> sessions
```

- `key` = hash of the shell drvPath and the NixOS toplevel drvPath, from one
  `nix eval --json`, so a base change invalidates it.
- Keys are computed from `git+file://$PWD?rev=$(git rev-parse HEAD)`, because
  only HEAD ships. A dirty tree cannot change the key.
- Warn when the key changes on commits that touch no Nix files. That means the
  shell depends on repo source, and every commit would publish a tag.
- Both builds upload a clean `git archive` of HEAD. `nixos.modules` are Nix
  functions and cannot go through JSON, so the base build evaluates the user's
  flake in the sandbox via `.#devShells.x86_64-linux.<attr>.nixosToplevel`.
- Sessions source `/nix/var/nix/rnix-shell.sh`. They never run `nix develop`,
  which looks up bashInteractive through the registry on each new session.
- Build sandboxes get only `image.secrets`. The checkout is deleted first.
- Builds always run on `vm`. Nix cannot run local builds under gVisor.
- `rnix image build` runs the same path for CI. `rnix image list` is read-only.

### Base module requirements

- NixOS activation replaces `/etc/nix/nix.conf`, so the base module sets
  `nix-command flakes`, `sandbox = false`, `keep-outputs`, `keep-derivations`.
- `flake-registry = ""` and `nixpkgs` pinned to `pkgs.path`, so the registry
  works with no network and matches the project.
- `/run` is empty after restore. `rnix-entry` re-activates on every start, then
  blocks, since a sandbox exits with its command.

## Session lifecycle

- `up [name]`: ensure image; `git bundle` HEAD; upload; clone to the workspace
  on branch `rnix/<name>`.
- Identity: `project` defaults to the sanitised repo name plus a short hash of
  the root commit, so same-named repos never share images or sandboxes.
- State: Modal Volume `rnix-state`, one JSON file per session, holding sandbox
  ID, synced commit, and latest snapshot image IDs. Not a Dict, whose entries
  expire after 7 days of inactivity. Not tags on dead sandboxes, since
  `from_name(include_terminated=True)` retention is unverified.
- `shell`: `modal shell <sb-id> --cmd …` if `--cmd` works with a sandbox ref.
- `exec -- cmd`: same, non-interactive, returns the remote exit code.
- `pull`: bundle `<base>..rnix/<name>`, fetch into `refs/rnix-pull/<name>`, then
  fast-forward, so a branch open in a worktree does not fail the fetch.
- `checkpoint`: `snapshot_filesystem` while running. Records the image ID in
  the session Dict.
- `stop`: pull, terminate, exit snapshot.
- `resume`: latest of exit snapshot or checkpoint, new sandbox, exit snapshot on.
- `list`, `rm`, `status`. All support `--json`.

Git is a clone, not a worktree. Sandbox has no git credentials. `pull` is the
durable path; a dead sandbox can be pulled by booting its snapshot.

## Implementation

- `flake.nix`: `lib.mkRemoteShell`, `flakeModules.default`, `packages.rnix`,
  `checks`, `templates.default`.
- `nix/mkRemoteShell.nix`, `nix/options.nix`, `nix/base-nixos.nix`.
- `src/rnix/` Python: `cli.py`, `config.py`, `images.py`, `session.py`,
  `gitops.py`. Remote scripts live in `nix/base-nixos.nix`.
- SDK: nixpkgs `python3Packages.modal` (1.6.2). All `_experimental_*` calls are in
  `remote.py`.

## Verify first (spike, real Modal)

Short timeouts, one dedicated app, `try/finally` terminate. Record results in
the Spike results section below.

0. The SDK rnix pins has the exit snapshot, ttl, name, and tag APIs.
1. `nixos/nix` on `vm`: PID 1, `nix --version`, `nix.conf`.
2. Snapshot, publish, `Image.from_name`, and exit snapshot round trip on `vm`.
3. Bundle upload and download at multi-MB size. Read `sandbox-files.md` first.
4. NixOS activation, then restore: re-activation and flakes still work.
5. Key stability on a real flake, chained snapshot timing.

## Known risks

- No official NixOS image exists; the base is NixOS built on `nixos/nix`.
- `snapshot_filesystem` default timeout 55s; large closures need more.
- `_experimental_get_exit_snapshot` can change.
- An open shell blocks idle timeout.
- Exit snapshot failure loses session state.
- Domain allowlist is TLS-only and must include Nix caches.
- GPU needs gVisor, where uncached derivations cannot build.
- `image_delete` keeps the tag listed and booting that tag raises NotFoundError,
  so rnix rebuilds on boot. There is no public check that avoids a boot. A child
  booted after one parent was deleted, which is one data point.

## Spike results

Run 2026-11-02 against live Modal with SDK 1.6.2, `runtime="vm"`.

- SDK: nixpkgs `modal` 1.6.2 has the exit snapshot method and snapshot `ttl`.
- PID 1 is `/__modal/runch-agent`, so systemd cannot be PID 1 directly.
- `nixos/nix:2.35.4`: first pull 52s, kernel 7.2.14, 1 CPU by default, flakes
  off in `nix.conf`, `sandbox = false`, 504G root filesystem.
- `snapshot_filesystem` took 1.7s and the sandbox kept running afterwards.
- `Sandbox.create` from `Image.from_name` took 0.23s and the marker survived.
- Exit snapshot after `terminate(wait=True)` was available in 0.18s through
  `Sandbox.from_id` and held later writes.
- `sandbox-files.md`: reads up to 5GB, writes of any size.
- NixOS 26.11 toplevel built in 22-30s (substituted) and activates over the
  `nixos/nix` filesystem. DNS survives because `/etc/resolv.conf` is left alone.
- `/run` is empty after restore. Re-activation restores `/run/current-system`.
- Clean image: 2.7G store, 7.2s snapshot, create 0.25s, ready about 2.5s.
- `nix develop --offline <profile>` 0.7s. A fresh checkout also works offline.
- `modal shell <sb-id> -c <argv>` works, is not run through a shell, and gives a
  real PTY.
- Six chained snapshots of 200MB each: ready stayed about 2s. One cold read took
  5.6s, then back under 1s. No degradation with depth.
- systemd boots as PID 1 of a nested PID namespace on `vm`: units run, none
  failed. Commands need `nsenter` to reach it.
- gVisor: the `specialfs` activation step fails (mount denied), everything else
  works, including Nix. No systemd.
- Removing the bootstrap profile breaks `GIT_SSL_CAINFO` from the image env, so
  it must be overridden.
- `modal.Dict` entries expire after 7 days of inactivity, shorter than the 30-day
  snapshot retention. State moves to a Modal Volume.

- gVisor: local derivation builds fail with "unexpected EOF" while waiting for
  the build environment. `filter-syscalls = false` and an empty
  `build-users-group` did not help. Root cause unknown. Substitution works.
- `nix develop --offline <profile> -c` fetches nixpkgs through the registry on
  the first run of a session. First exec took 186s before the fix, 3.2s after.

## Live test results (final code)

All 47 lifecycle checks passed: exec, exit codes, real Nix builds, commit round
trip, checkpoint, stop, resume with uncommitted files, PTY shell, idle timeout
then recovery pull, image hit on a docs commit, incremental rebuild, dirty tree,
systemd with nginx including resume, gVisor, blocked network, Modal Secrets.

Separately checked: `up`/`rm`/`up`, failed `up` cleanup, diverged pull,
`image build --fresh`, `rnix shell` on systemd, `--worktree` from a subdirectory,
rebuild after deleting the project image and after deleting the whole chain.
60 unit tests and `nix flake check` pass on x86_64-linux.

Not tested: building or running on darwin, private flake inputs, GPU sandboxes.

| Step | Time |
|---|---|
| First `rnix up`, from `nixos/nix` | 86s |
| `rnix up`, image hit | 11.5s |
| `rnix up`, flake changed | 26.1s |
| `rnix exec` | 3.2s |
| `rnix stop` | 11.9s |
| `rnix resume` | 7.4s |
| Recovery `pull` from a dead sandbox | 8.0s |

## Decisions from the spike

- `remote.systemd.enable` is opt-in, `vm` only, and runs through
  `$SYSTEM/init` in a nested namespace so activation happens inside it.
- Remote scripts (`rnix-entry`, `rnix-run`, `rnix-build-shell`) live in the NixOS
  system as Nix derivations, so changing them changes the base hash.
- The CLI reads config by evaluating the flake at HEAD, then caches project and
  app details in `.git/rnix/`. Only `up` and `image build` need a fresh eval.
- `rnix-<project>:latest-<basehash>` is the incremental parent, so a base change
  never builds on a stale system.
- `image gc` is cut. `image_delete` is irreversible. `image list` is read-only.

