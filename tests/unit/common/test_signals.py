import asyncio

from labpilot.common.signals import install_shutdown_handlers


def test_shutdown_handler_registers_sigterm(monkeypatch) -> None:
    event = asyncio.Event()
    registered: list[tuple[object, object]] = []

    class FakeLoop:
        def add_signal_handler(self, signal_value, callback) -> None:
            registered.append((signal_value, callback))

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
    install_shutdown_handlers(event)
    assert len(registered) == 2
    registered[1][1]()
    assert event.is_set()
