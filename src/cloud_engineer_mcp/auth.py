"""Auth flows for remote MCP backends.

Currently provides one strategy: ``CommandTokenAuth``, which mints a Bearer
token by running a shell command (e.g. ``gcloud auth print-access-token``)
and re-mints it on 401 Unauthorized responses.

The class implements httpx.Auth so it slots directly into the MCP SDK's
``streamablehttp_client(auth=...)`` parameter — no patching, no restart.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncGenerator, Generator

import httpx

from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("auth")

_TOKEN_COMMAND_TIMEOUT_SECONDS = 10.0


class CommandTokenAuth(httpx.Auth):
    """httpx Auth that mints a Bearer token from a shell command.

    The token is cached after the first mint and re-minted whenever the
    server returns 401. Concurrent requests through the same Auth instance
    share the cached token; we don't add a lock because httpx serializes
    auth-flow generators per request internally.

    Example::

        auth = CommandTokenAuth(
            command=["gcloud", "auth", "print-access-token"],
            header_name="Authorization",
            template="Bearer {token}",
        )
        async with streamablehttp_client(url, auth=auth) as ...:
            ...
    """

    requires_request_body = False
    requires_response_body = False

    def __init__(
        self,
        command: list[str],
        header_name: str = "Authorization",
        template: str = "Bearer {token}",
    ) -> None:
        if not command:
            raise ValueError("CommandTokenAuth requires a non-empty command")
        self._command = command
        self._header_name = header_name
        self._template = template
        self._cached: str | None = None

    # ----- token minting ------------------------------------------------

    def _mint_sync(self) -> str | None:
        try:
            result = subprocess.run(
                self._command,
                capture_output=True,
                text=True,
                timeout=_TOKEN_COMMAND_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            log.warning("auth.mint_failed", error=str(exc), mode="sync")
            return None
        if result.returncode != 0:
            log.warning(
                "auth.mint_command_nonzero",
                returncode=result.returncode,
                stderr=result.stderr[:200],
            )
            return None
        token = result.stdout.strip()
        return token or None

    async def _mint_async(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_TOKEN_COMMAND_TIMEOUT_SECONDS
            )
        except Exception as exc:
            log.warning("auth.mint_failed", error=str(exc), mode="async")
            return None
        if proc.returncode != 0:
            return None
        token = stdout.decode().strip()
        return token or None

    def _apply_header(self, request: httpx.Request, token: str) -> None:
        request.headers[self._header_name] = self._template.format(token=token)

    # ----- httpx.Auth interface ----------------------------------------

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        if self._cached is None:
            self._cached = self._mint_sync()
        if self._cached is not None:
            self._apply_header(request, self._cached)
        response = yield request
        if response.status_code == 401:
            log.info("auth.refresh_on_401", mode="sync")
            self._cached = self._mint_sync()
            if self._cached is not None:
                self._apply_header(request, self._cached)
                yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        if self._cached is None:
            self._cached = await self._mint_async()
        if self._cached is not None:
            self._apply_header(request, self._cached)
        response = yield request
        if response.status_code == 401:
            log.info("auth.refresh_on_401", mode="async")
            self._cached = await self._mint_async()
            if self._cached is not None:
                self._apply_header(request, self._cached)
                yield request
