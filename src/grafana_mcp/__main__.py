# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Entry point: ``python -m grafana_mcp``."""

import logging
import sys

from .config import get_settings
from .server import create_app, create_mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if settings.mcp_transport == "stdio":
        # Local debugging only: there are no request headers on stdio, so tools
        # report not_configured.
        create_mcp(settings).run(transport="stdio")
        return

    import uvicorn

    uvicorn.run(create_app(settings), host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
