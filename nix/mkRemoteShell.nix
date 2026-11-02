{ self }:
{
  pkgs,
  remote ? { },
  ...
}@args:
let
  inherit (pkgs) lib;

  cfg =
    (lib.evalModules {
      modules = [
        ./options.nix
        remote
      ];
    }).config;

  checked =
    assert lib.assertMsg (cfg.systemd.enable -> cfg.runtime == "vm")
      "rnix: remote.systemd.enable requires remote.runtime = \"vm\"";
    assert lib.assertMsg (cfg.gpu != null -> cfg.runtime != "vm")
      "rnix: GPU sandboxes require remote.runtime = \"gvisor\"";
    cfg;

  nixos = import (pkgs.path + "/nixos/lib/eval-config.nix") {
    system = "x86_64-linux";
    inherit (checked.nixos) specialArgs;
    modules = [ (import ./base-nixos.nix { systemd = checked.systemd.enable; }) ] ++ checked.nixos.modules;
  };

  cli =
    pkgs.runCommand "rnix"
      {
        nativeBuildInputs = [ pkgs.makeWrapper ];
        meta.mainProgram = "rnix";
      }
      ''
        makeWrapper ${lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.rnix} $out/bin/rnix \
          --set-default RNIX_ATTR ${lib.escapeShellArg checked.attr}
      '';
in
pkgs.mkShell (
  removeAttrs args [
    "pkgs"
    "remote"
  ]
  // {
    passthru = (args.passthru or { }) // {
      inherit cli;
      nixosToplevel = nixos.config.system.build.toplevel;
      rnixConfig = removeAttrs checked [
        "nixos"
        "_module"
      ];
    };
  }
)
