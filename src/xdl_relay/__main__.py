from __future__ import annotations

import argparse
import logging

from xdl_relay.config import Settings
from xdl_relay.service import RelayService
from xdl_relay.webui import DashboardServer


def main() -> None:
    parser = argparse.ArgumentParser(description="XDL Relay runner")
    parser.add_argument("--x-login", action="store_true", help="Run interactive X OAuth login and save token")
    parser.add_argument("--token-path", default=None, help="Optional override for token output path")
    parser.add_argument("--client-id", default=None, help="Optional override for X OAuth client ID")
    parser.add_argument("--redirect-uri", default=None, help="Optional override for X OAuth redirect URI")
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
    if args.x_login:
        if args.client_id:
            service.x_client.client_id = args.client_id
            service.x_client.oauth.client_id = args.client_id
        if args.redirect_uri:
            service.x_client.redirect_uri = args.redirect_uri
            service.x_client.oauth.redirect_uri = args.redirect_uri
        if args.token_path:
            from xdl_relay.x_auth import OAuthTokenStore

            service.x_client.token_store = OAuthTokenStore(args.token_path)
        service.x_client.interactive_login()
        print(f"Saved OAuth token to: {service.x_client.token_store.path}")
        return

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
