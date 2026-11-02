{
  description = "Remote NixOS dev shells on Modal sandboxes";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      lib.mkRemoteShell = import ./nix/mkRemoteShell.nix { inherit self; };

      flakeModules.default = import ./nix/flake-module.nix { inherit self; };

      packages = forAllSystems (pkgs: rec {
        rnix = pkgs.python3Packages.callPackage ./nix/package.nix { };
        default = rnix;
      });

      checks = forAllSystems (pkgs: {
        rnix = self.packages.${pkgs.stdenv.hostPlatform.system}.rnix;
        eval = import ./nix/eval-check.nix { inherit self pkgs; };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (p: [
              p.modal
              p.pytest
            ]))
            pkgs.nixfmt
          ];
        };
      });

      templates.default = {
        path = ./templates/default;
        description = "A flake with a remote dev shell on Modal";
      };
    };
}
