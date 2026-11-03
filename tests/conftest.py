# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from grafana_mcp import credentials
from grafana_mcp.config import Settings
from grafana_mcp.server import create_mcp

BASE = "https://grafana.example.com"


@pytest.fixture
def settings() -> Settings:
    # Keep retries fast in tests.
    return Settings(max_retries=2, call_budget_seconds=10)


@pytest.fixture
def mcp(settings):
    return create_mcp(settings)


@pytest.fixture
def creds():
    token = credentials._current.set(credentials.Credentials(token="test-token", base_url=BASE))
    yield
    credentials._current.reset(token)


@pytest.fixture
def call(mcp):
    async def _call(tool: str, /, **args):
        result = await mcp.call_tool(tool, args)
        blocks = result[0] if isinstance(result, tuple) else result
        return json.loads(blocks[0].text)

    return _call
