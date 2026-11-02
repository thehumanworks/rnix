{ self, pkgs }:
let
  shell = self.lib.mkRemoteShell {
    inherit pkgs;
    packages = [ pkgs.hello ];
    remote = {
      project = "check";
      timeout = "1h";
      nixos.modules = [ { environment.variables.RNIX_CHECK = "1"; } ];
    };
  };
in
assert shell.rnixConfig.project == "check";
assert shell.rnixConfig.exitSnapshot.enable;
assert shell.rnixConfig ? nixos == false;
pkgs.runCommand "rnix-eval-check" { } ''
  echo ${builtins.unsafeDiscardStringContext shell.drvPath}
  echo ${builtins.unsafeDiscardStringContext shell.nixosToplevel.drvPath}
  touch $out
''
