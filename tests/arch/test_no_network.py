"""K. Static + runtime network policy (test plan F.0)."""
import socket

import pytest

from tests.conftest import _guarded_connect


def test_runtime_guard_active():
    assert socket.socket.connect is _guarded_connect
    s = socket.socket()
    with pytest.raises(RuntimeError):
        s.connect(("93.184.216.34", 80))
    s.close()


@pytest.mark.network
def test_marker_opt_out_is_deselected_by_default():
    # Excluded by addopts (-m 'not network'); present so the marker mechanism is exercised in Phase 4.
    assert True
