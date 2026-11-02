{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rnix.url = "github:thehumanworks/rnix";
  };

  outputs =
    { self, nixpkgs, rnix }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      shell = pkgs.mkShell { packages = [ pkgs.hello ]; };
      remote = rnix.lib.mkRemoteShell {
        inherit pkgs;
        inputsFrom = [ shell ];
      };
    in
    {
      devShells.${system} = {
        default = pkgs.mkShell {
          inputsFrom = [ shell ];
          packages = [ remote.cli ];
        };
        inherit remote;
      };
    };
}
