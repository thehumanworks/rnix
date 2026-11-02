{
  lib,
  buildPythonApplication,
  setuptools,
  modal,
  pytestCheckHook,
  gitMinimal,
}:
buildPythonApplication {
  pname = "rnix";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../src
      ../tests
    ];
  };

  build-system = [ setuptools ];
  dependencies = [ modal ];

  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    (lib.makeBinPath [ modal ])
  ];

  nativeCheckInputs = [
    pytestCheckHook
    gitMinimal
  ];

  meta = {
    description = "Remote NixOS dev shells on Modal sandboxes";
    mainProgram = "rnix";
  };
}
