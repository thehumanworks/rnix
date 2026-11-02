{ self }:
{ flake-parts-lib, lib, ... }:
{
  options.perSystem = flake-parts-lib.mkPerSystemOption (
    { config, pkgs, ... }:
    let
      cfg = config.rnix;
    in
    {
      options.rnix = {
        enable = lib.mkEnableOption "the rnix remote shell" // {
          default = true;
        };
        devShell = lib.mkOption {
          type = lib.types.str;
          default = "default";
          description = "Existing devShell the remote shell inherits from.";
        };
        packages = lib.mkOption {
          type = lib.types.listOf lib.types.package;
          default = [ ];
          description = "Packages only the remote shell gets.";
        };
        remote = lib.mkOption {
          type = lib.types.deferredModule;
          default = { };
          description = "Settings for `mkRemoteShell`.";
        };
      };

      config = lib.mkIf cfg.enable {
        devShells.remote = self.lib.mkRemoteShell {
          inherit pkgs;
          inherit (cfg) packages remote;
          inputsFrom = [ config.devShells.${cfg.devShell} ];
        };
        packages.rnix = config.devShells.remote.cli;
      };
    }
  );
}
