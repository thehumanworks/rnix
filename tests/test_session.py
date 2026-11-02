import modal.exception
import pytest

from rnix import images, session
from rnix.context import Context
from rnix.errors import RnixError
from rnix.state import session_path


class FakeStore:
    def __init__(self):
        self.files = {}

    def read(self, path):
        return self.files.get(path)

    def write(self, path, record):
        self.files[path] = record

    def remove(self, path):
        del self.files[path]

    def names(self, directory):
        prefix = f"{directory}/"
        return sorted(p[len(prefix):-5] for p in self.files if p.startswith(prefix))


def record(**overrides):
    return {
        "name": "one",
        "sandbox_id": "sb-1",
        "exit_image": None,
        "checkpoint_image": None,
        "cfg": {"exitSnapshot": {"enable": True, "timeout": 1}},
        **overrides,
    }


@pytest.fixture
def context():
    return Context(repo=None, attr="remote", cfg={}, project="p")


def test_load_picks_the_only_session(context):
    store = FakeStore()
    store.write(session_path("p", "one"), record())
    assert session.load(context, store, None)["name"] == "one"


def test_load_needs_a_name_when_ambiguous(context):
    store = FakeStore()
    store.write(session_path("p", "one"), record())
    store.write(session_path("p", "two"), record(name="two"))
    with pytest.raises(RnixError, match="one, two"):
        session.load(context, store, None)


def test_latest_snapshot_prefers_known_exit_image():
    assert session._latest_snapshot(record(exit_image="im-e", checkpoint_image="im-c")) == "im-e"


def test_latest_snapshot_fetches_exit_image(monkeypatch):
    monkeypatch.setattr(session.remote, "exit_snapshot", lambda _id, _timeout: "im-e")
    assert session._latest_snapshot(record(checkpoint_image="im-c")) == "im-e"


def test_latest_snapshot_falls_back_to_checkpoint(monkeypatch):
    monkeypatch.setattr(session.remote, "exit_snapshot", lambda _id, _timeout: None)
    assert session._latest_snapshot(record(checkpoint_image="im-c")) == "im-c"


def test_latest_snapshot_fails_without_any(monkeypatch):
    monkeypatch.setattr(session.remote, "exit_snapshot", lambda _id, _timeout: None)
    with pytest.raises(RnixError, match="no snapshot"):
        session._latest_snapshot(record())


def test_missing_image_is_told_apart_from_a_missing_secret():
    assert images.is_missing_image(modal.exception.NotFoundError("image im-1 not found"))
    assert not images.is_missing_image(modal.exception.NotFoundError("Secret 'x' not found"))


@pytest.fixture
def boot_context():
    cfg = {"modalEnvironment": None, "env": {}, "tags": {}}
    return Context(repo=None, attr="remote", cfg=cfg, project="p")


def test_deleted_image_is_rebuilt_once(boot_context, monkeypatch):
    attempts = []

    def boot(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise modal.exception.NotFoundError("image im-1 not found")
        return "sandbox"

    rebuilt = []
    monkeypatch.setattr(session.remote, "boot", boot)
    monkeypatch.setattr(session.remote, "session_kwargs", lambda _cfg: {})
    monkeypatch.setattr(session.images, "named", lambda name, _env: name)
    monkeypatch.setattr(
        session.images, "ensure_project", lambda _context, _store, _log, fresh: rebuilt.append(fresh)
    )
    result = session._boot_session(boot_context, FakeStore(), "image", "one", lambda _: None)
    assert result == "sandbox"
    assert rebuilt == [True]


def test_missing_secret_does_not_rebuild(boot_context, monkeypatch):
    def boot(*_args, **_kwargs):
        raise modal.exception.NotFoundError("Secret 'x' not found")

    monkeypatch.setattr(session.remote, "boot", boot)
    monkeypatch.setattr(session.remote, "session_kwargs", lambda _cfg: {})
    monkeypatch.setattr(session.images, "named", lambda name, _env: name)
    with pytest.raises(modal.exception.NotFoundError):
        session._boot_session(boot_context, FakeStore(), "image", "one", lambda _: None)
