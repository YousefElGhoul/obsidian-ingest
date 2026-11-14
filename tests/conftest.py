import pytest


@pytest.fixture(autouse=True)
def isolated_host_state(monkeypatch, tmp_path_factory):
    """Keep any default source cache, including in subprocesses, out of real host state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg-state")))
