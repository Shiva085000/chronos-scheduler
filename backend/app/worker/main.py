"""Worker process entrypoint: `python -m app.worker.main`."""

import asyncio
import os
import signal
import socket

from app.core.config import settings
from app.core.logging import configure_logging
from app.worker.runner import WorkerRunner


async def _run() -> None:
    runner = WorkerRunner(settings, name=f"{socket.gethostname()}:{os.getpid()}")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runner.request_shutdown)
        except NotImplementedError:
            # Windows: no loop signal handlers; fall back to sync handlers.
            signal.signal(sig, lambda *_: runner.request_shutdown())

    await runner.run()


def main() -> None:
    configure_logging(debug=settings.debug, service="worker")
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
