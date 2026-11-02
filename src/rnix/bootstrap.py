"""The first build step, which runs on the public image before NixOS exists.

This text is part of the base hash, so editing it rebuilds the base.
"""

BOOTSTRAP = """\
set -euo pipefail
export NIX_CONFIG='extra-experimental-features = nix-command flakes'
out=$(nix build --print-out-paths --no-link "path:{flake}#devShells.{system}.{attr}.nixosToplevel")
nix-env -p {profiles}/system --set "$out"
# Some snippets need mounts that gVisor denies. The rest still applies.
"$out/activate" || echo "rnix: warning: activation reported errors" >&2
export PATH="$out/sw/bin"
rm -f {profiles}/default {profiles}/default-*-link /root/.nix-profile
rm -rf /tmp/rnix-src {archive}
nix-collect-garbage
"""
