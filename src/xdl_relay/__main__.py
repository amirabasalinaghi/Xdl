from __future__ import annotations

import logging

from xdl_relay.config import Settings
from xdl_relay.service import RelayService


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    RelayService(settings).run_forever()


if __name__ == "__main__":
    main()
