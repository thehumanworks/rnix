from rnix import remote


def config(**overrides):
    return {
        "modalEnvironment": None,
        "cpu": 2,
        "memory": 1024,
        "gpu": None,
        "cloud": None,
        "region": None,
        "runtime": "gvisor",
        "timeout": "1h",
        "idleTimeout": "30m",
        "secrets": [],
        "volumes": {},
        "extraCreateArgs": {},
        "network": {"block": False, "domainAllowlist": None, "cidrAllowlist": None},
        "exitSnapshot": {"enable": True, "timeout": 1},
        "image": {"cpu": 4, "memory": 8192, "timeout": "1h", "secrets": []},
        **overrides,
    }


def test_builds_always_use_vm():
    assert remote.build_kwargs(config())["runtime"] == "vm"


def test_sessions_use_the_configured_runtime():
    assert remote.session_kwargs(config())["runtime"] == "gvisor"


def test_exit_snapshots_are_on_by_default():
    assert remote.session_kwargs(config())["experimental_options"] == {"enable_exit_snapshot": True}


def test_exit_snapshots_can_be_disabled():
    cfg = config(exitSnapshot={"enable": False, "timeout": 1})
    assert "experimental_options" not in remote.session_kwargs(cfg)


def test_extra_create_args_override():
    assert remote.session_kwargs(config(extraCreateArgs={"cpu": 8}))["cpu"] == 8


def test_durations_become_seconds():
    kwargs = remote.session_kwargs(config())
    assert (kwargs["timeout"], kwargs["idle_timeout"]) == (3600, 1800)


def test_recovery_sandboxes_are_offline():
    assert remote.recovery_kwargs(config())["block_network"] is True


def test_environment_overrides_bootstrap_paths():
    assert remote.SANDBOX_ENV["GIT_SSL_CAINFO"] == remote.CERTS
    assert "default" not in remote.SANDBOX_ENV["PATH"]
