"""After a Wi-Fi drop the connection often goes silent without being closed.
The viewer and the soak must notice the silence and connect again."""

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from connection import protocol
from tools import soak
from ui import viewer

SHORT_TIMEOUT_S = 1


class SilentBoard(threading.Thread):
    """Accepts connections, sends one METRICS record to each, then stays
    silent with the connection left open, like a board behind a dead router."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.connections: list[socket.socket] = []

    def run(self) -> None:
        while True:
            try:
                client, address = self.server.accept()
            except OSError:
                return
            record = protocol.encode(protocol.METRICS, 0, 0, 0, b"{}")
            client.sendall(record)
            self.connections.append(client)

    def wait_for_connections(self, count: int, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if len(self.connections) >= count:
                return True
            time.sleep(0.05)
        return False

    def close(self) -> None:
        self.server.close()
        for connection in self.connections:
            connection.close()


def test_viewer_reconnects_after_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(viewer, "NO_DATA_TIMEOUT_S", SHORT_TIMEOUT_S)
    board = SilentBoard()
    board.start()
    source = viewer.LiveSource("127.0.0.1", board.port, viewer.queue.Queue())
    source.start()
    reconnected = board.wait_for_connections(2, seconds=5)
    source.running = False
    board.close()
    assert reconnected


def test_soak_reconnects_after_silence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(soak, "NO_DATA_TIMEOUT_S", SHORT_TIMEOUT_S)
    board = SilentBoard()
    board.start()
    serial_logger = SimpleNamespace(ip=None, boots=0, reset_reasons=[])
    receiver = soak.StreamReceiver(
        "127.0.0.1", board.port, tmp_path, 600, serial_logger  # type: ignore
    )
    receiver.run(minutes=4 / 60)
    board.close()
    assert receiver.stats["reconnects"] >= 1
