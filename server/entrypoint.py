"""Entry point for the frozen (PyInstaller) desktop backend.

`python3 -m server.app` works from a source checkout, but a frozen build cannot
import the app by module string, so pass the app object to uvicorn directly.
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Keysight Connect desktop backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    from server.app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    # See server/app.py: pyvisa's socket threads outlive a graceful shutdown.
    os._exit(0)


if __name__ == "__main__":
    main()
