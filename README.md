# rnix

Remote NixOS dev shells on [Modal](https://modal.com) sandboxes. Add a few lines to
a flake, and humans or agents can start a sandbox on its own git branch, with the
project's dev shell already cached.

```console
$ rnix up feature-x          # first run about 90 s, then about 12 s
$ rnix exec -- pnpm test     # returns the remote exit code
$ rnix shell                 # interactive shell in the workspace
$ rnix pull                  # fetch sandbox commits into rnix/feature-x
$ rnix stop                  # pull, terminate, keep an exit snapshot
$ rnix resume                # continue, including uncommitted files
```

You need a Modal account (`modal token new`). No registry, Docker, or Linux builder.

## Setup

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rnix.url = "github:thehumanworks/rnix";
  };

  outputs = { nixpkgs, rnix, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      shell = pkgs.mkShell { packages = [ pkgs.nodejs ]; };
      remote = rnix.lib.mkRemoteShell {
        inherit pkgs;
        inputsFrom = [ shell ];
      };
    in {
      devShells.${system} = {
        default = pkgs.mkShell {
          inputsFrom = [ shell ];
          packages = [ remote.cli ];
        };
        inherit remote;
      };
    };
}
```

`mkRemoteShell` takes the same arguments as `pkgs.mkShell`, plus `remote`. It returns
a normal dev shell, so `nix develop .#remote` tries the same environment locally.

`devShells.x86_64-linux` must exist on every machine, since sandboxes are x86_64.
A Mac only evaluates it, but `remote.cli` above is a Linux binary. On a Mac, run the
CLI with `nix run github:thehumanworks/rnix -- up`. Darwin builds are untested. With flake-parts, import `rnix.flakeModules.default`, and set
`rnix.devShell`, `rnix.packages`, and `rnix.remote` under `perSystem`.

## Configuration

Everything in `remote` is optional. See `nix/options.nix` for the full list.

```nix
remote = {
  project = "myrepo";              # default: repo name + root commit hash
  runtime = "vm";                  # or "gvisor"
  cpu = 4;
  memory = 8192;
  timeout = "24h";
  idleTimeout = "30m";
  secrets = [ "anthropic" ];       # Modal Secret names, never values
  env.FOO = "bar";
  volumes."/data" = "my-volume";
  network.domainAllowlist = [ "cache.nixos.org" "*.github.com" ];
  exitSnapshot.enable = true;      # experimental, on by default
  systemd.enable = true;           # run NixOS services, vm only
  nixos.modules = [ { services.postgresql.enable = true; } ];
  image.secrets = [ "nix-tokens" ]; # for private flake inputs
};
```

## How it works

Images are a chain, and all but the first are Modal named images:

```text
nixos/nix (public)                 pulled once
  rnix-base:<hash>                 NixOS built in a sandbox from your modules
    rnix-<project>:<key>           your dev shell and flake inputs, pre-built
      session snapshots            repo, uncommitted work, session state
```

`<key>` hashes the shell and NixOS derivations at HEAD, so a commit that changes
neither reuses the image. When the flake changes, the next `rnix up` builds on the
last image, which only fetches what changed. `rnix image build` does the same from CI.

A session is a clone on branch `rnix/<name>`, not a worktree, because a worktree
cannot exist remotely. Commits travel by `git bundle`, so sandboxes hold no git
credentials and cannot push anywhere. `rnix up --worktree PATH` adds a local worktree
that `rnix pull` keeps up to date.

Snapshots come in two forms. Exit snapshots happen on any exit, including idle
timeout. `rnix checkpoint` takes one while the sandbox runs. `rnix pull` works on a
stopped session by booting its snapshot in a temporary offline sandbox.

## Limits

- **Exit snapshots are experimental.** Modal says they can fail, and a failure loses
  that session's files. `pull` is the durable path. Snapshots expire after 30 days.
- **All flake inputs must be reachable from a sandbox.** Local `path:` inputs outside
  the repo fail. For private inputs, put `NIX_CONFIG` with `access-tokens` in a Modal
  Secret and list it in `image.secrets`.
- **gVisor sessions cannot build uncached derivations.** They can still substitute,
  and images always build on `vm`. GPUs require gVisor.
- **systemd runs in a nested PID namespace**, because Modal's agent is PID 1. `rnix`
  commands enter it automatically, but `modal shell` on its own does not.
- **Published images are kept forever and rnix does not delete them.** You can delete
  old ones with `modal.experimental.image_delete`. In testing, a child image still
  booted after its parent was deleted, and `rnix up` rebuilt a deleted image. Sessions
  still need their own snapshots, and a deleted image's tag stays listed.
- **`flake-registry` is cleared in sandboxes**, so `nixpkgs` resolves to your pin and
  short names like `flake-utils#` do not. Set `nix.settings.flake-registry` to restore it.
- `rnix exec` takes about 3 s per call, mostly client overhead.
- Darwin, GPU sandboxes, and private flake inputs are untested.

## Development

```console
$ nix flake check path:.
$ nix develop path:. -c pytest
```
