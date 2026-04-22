from __future__ import annotations

import argparse
import logging

from xdl_relay.config import Settings
from xdl_relay.service import RelayService
from xdl_relay.webui import DashboardServer


def main() -> None:
    parser = argparse.ArgumentParser(description="XDL Relay runner")
    parser.add_argument("--webui", action="store_true", help="Run the modern dashboard Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Web UI bind host")
    parser.add_argument("--port", type=int, default=8080, help="Web UI bind port")
    parser.add_argument(
        "--no-poller",
        action="store_true",
        help="Disable background polling while running the dashboard",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    service = RelayService(settings)

    if args.webui:
        DashboardServer(
            relay_service=service,
            host=args.host,
            port=args.port,
            enable_poller=not args.no_poller,
        ).run()
        return

    service.run_forever()


if __name__ == "__main__":
    main()
