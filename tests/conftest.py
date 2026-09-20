import socket

import pytest


@pytest.fixture(autouse=True)
def disable_network_calls(monkeypatch):
    """Ensure no tests accidentally make network calls."""
    def guarded(*args, **kwargs):
        raise RuntimeError("Network calls are blocked in unit tests!")
    monkeypatch.setattr(socket, "socket", guarded)
