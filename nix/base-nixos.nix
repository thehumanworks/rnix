{ systemd }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  system = "/nix/var/nix/profiles/system";
  shellProfile = "/nix/var/nix/profiles/rnix-shell";
  # `nix develop` looks up bashInteractive through the flake registry on every new
  # session, even with --offline, so sessions source a saved environment instead.
  shellEnv = "/nix/var/nix/rnix-shell.sh";
  inputRoots = "/nix/var/nix/gcroots/rnix-inputs";

  bootSystemd = ''
    container=rnix setsid unshare --pid --fork --mount --mount-proc \
      --kill-child=SIGRTMIN+3 ${system}/init \
      >/tmp/rnix-systemd.log 2>&1 </dev/null &

    for _ in $(seq 1 600); do
      pid=$(pgrep -x systemd | head -n 1 || true)
      if [ -n "$pid" ] && nsenter -t "$pid" -m -p -- systemctl is-system-running 2>/dev/null \
        | grep -Eq '^(running|degraded)$'; then
        echo "$pid" >/run/rnix/systemd.pid
        break
      fi
      sleep 0.1
    done
  '';

  entry = pkgs.writeShellScriptBin "rnix-entry" ''
    rm -f /run/rnix/ready
    ${system}/activate >/tmp/rnix-activate.log 2>&1
    mkdir -p /run/rnix
    ${lib.optionalString systemd bootSystemd}
    touch /run/rnix/ready
    exec sleep infinity
  '';

  run = pkgs.writeShellScriptBin "rnix-run" ''
    set -euo pipefail
    dir=$1
    shift
    [ $# -gt 0 ] || set -- bash

    if [ -z "''${RNIX_INNER:-}" ] && [ -f /run/rnix/systemd.pid ]; then
      RNIX_INNER=1 exec nsenter -t "$(cat /run/rnix/systemd.pid)" -m -p -- "$0" "$dir" "$@"
    fi

    cd "$dir"
    set +eu
    source ${shellEnv}
    exec "$@"
  '';

  buildShell = pkgs.writeShellScriptBin "rnix-build-shell" ''
    set -euo pipefail
    flake=$1
    attr=$2

    rm -f ${shellProfile} ${shellProfile}-*-link
    rm -rf ${inputRoots}
    mkdir -p ${inputRoots}

    nix print-dev-env --profile ${shellProfile} "path:$flake#$attr" >${shellEnv}

    # Keep flake inputs so sessions and later builds evaluate without fetching.
    archive=$(nix flake archive --json "path:$flake")
    source=$(${lib.getExe pkgs.jq} -r .path <<<"$archive")
    ${lib.getExe pkgs.jq} -r '.inputs | .. | .path? // empty' <<<"$archive" | while read -r path; do
      ln -sfn "$path" "${inputRoots}/$(basename "$path")"
    done

    rm -rf /tmp/rnix-src /tmp/rnix-src.tgz
    rm -f /nix/var/nix/gcroots/auto/*
    nix-collect-garbage

    if [ -e "$source" ]; then
      echo "rnix: warning: the repo source is still in the image because the shell depends on it" >&2
    fi
  '';
in
{
  boot.isContainer = true;
  documentation.enable = lib.mkDefault false;
  networking.firewall.enable = lib.mkDefault false;
  system.stateVersion = lib.mkDefault lib.trivial.release;

  nix.settings = {
    experimental-features = [
      "nix-command"
      "flakes"
    ];
    sandbox = lib.mkDefault false;
    keep-outputs = true;
    keep-derivations = true;
    # The default is a download, which fails in sandboxes with blocked networking.
    flake-registry = lib.mkDefault "";
  };

  # Tools like `nix shell nixpkgs#foo` should use the project's nixpkgs, not fetch one.
  nix.registry.nixpkgs.to = lib.mkDefault {
    type = "path";
    path = pkgs.path;
  };

  environment.systemPackages = [
    entry
    run
    buildShell
    pkgs.git
  ];
}
