{ lib, ... }:
let
  inherit (lib) mkOption types;

  optionalStr = mkOption {
    type = types.nullOr types.str;
    default = null;
  };
  duration = types.either types.ints.positive types.str;
in
{
  options = {
    project = optionalStr // {
      description = "Project identifier. Defaults to the repo name plus a hash of its root commit.";
    };
    attr = mkOption {
      type = types.str;
      default = "remote";
      description = "Attribute under devShells.x86_64-linux that holds this shell.";
    };
    base = mkOption {
      type = types.str;
      default = "nixos/nix:2.35.4";
      description = "Public registry image the NixOS base is bootstrapped from.";
    };
    app = mkOption {
      type = types.str;
      default = "rnix";
      description = "Modal App that owns the sandboxes.";
    };
    modalEnvironment = optionalStr // {
      description = "Modal Environment. Defaults to the active profile's.";
    };

    runtime = mkOption {
      type = types.nullOr (
        types.enum [
          "vm"
          "gvisor"
        ]
      );
      default = "vm";
      description = "Session runtime. gVisor sessions cannot build uncached derivations. Images always build on vm.";
    };
    cpu = mkOption {
      type = types.nullOr types.number;
      default = 4;
    };
    memory = mkOption {
      type = types.nullOr types.ints.positive;
      default = 8192;
      description = "Memory in MiB.";
    };
    gpu = optionalStr;
    cloud = optionalStr;
    region = mkOption {
      type = types.nullOr (types.either types.str (types.listOf types.str));
      default = null;
    };
    timeout = mkOption {
      type = duration;
      default = "24h";
      description = "Maximum sandbox lifetime, up to 24h. Seconds, or a string like 30m.";
    };
    idleTimeout = mkOption {
      type = types.nullOr duration;
      default = "30m";
    };

    workspace = mkOption {
      type = types.str;
      default = "/workspace";
    };
    secrets = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Modal Secret names for sessions. Never put values here.";
    };
    env = mkOption {
      type = types.attrsOf types.str;
      default = { };
    };
    volumes = mkOption {
      type = types.attrsOf types.str;
      default = { };
      description = "Mount path to Modal Volume name. Snapshots do not include these.";
    };
    tags = mkOption {
      type = types.attrsOf types.str;
      default = { };
    };

    network = {
      block = mkOption {
        type = types.bool;
        default = false;
      };
      domainAllowlist = mkOption {
        type = types.nullOr (types.listOf types.str);
        default = null;
        description = "TLS-only. Include your Nix caches if sessions fetch from them.";
      };
      cidrAllowlist = mkOption {
        type = types.nullOr (types.listOf types.str);
        default = null;
      };
    };

    exitSnapshot = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Experimental Modal feature: snapshot the filesystem on any exit.";
      };
      timeout = mkOption {
        type = types.ints.positive;
        default = 120;
        description = "Seconds to wait when fetching an exit snapshot.";
      };
    };

    checkpoint.ttl = mkOption {
      type = types.nullOr duration;
      default = "30d";
      description = "Retention for `rnix checkpoint` images. Null keeps them forever.";
    };

    image = {
      cpu = mkOption {
        type = types.nullOr types.number;
        default = 4;
      };
      memory = mkOption {
        type = types.nullOr types.ints.positive;
        default = 8192;
      };
      timeout = mkOption {
        type = duration;
        default = "1h";
      };
      snapshotTimeout = mkOption {
        type = types.ints.positive;
        default = 600;
      };
      secrets = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Secrets for image builds, for example to fetch private flake inputs.";
      };
    };

    systemd.enable = mkOption {
      type = types.bool;
      default = false;
      description = "Boot systemd in a nested namespace so NixOS services run. Requires runtime = \"vm\".";
    };

    extraCreateArgs = mkOption {
      type = types.attrs;
      default = { };
      description = "Extra keyword arguments for modal.Sandbox.create in sessions.";
    };

    nixos = {
      modules = mkOption {
        type = types.listOf types.raw;
        default = [ ];
        description = "NixOS modules for the sandbox system.";
      };
      specialArgs = mkOption {
        type = types.attrs;
        default = { };
      };
    };
  };
}
