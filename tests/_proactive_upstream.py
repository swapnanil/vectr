"""Local mock of the Anthropic upstream for proxy tests.

Never touches the real API. Simulates streaming SSE (incl. tool_use blocks),
non-streaming JSON, error statuses, and records every request it receives so a
test can assert exactly what the proxy forwarded (body + headers). A companion
`unreachable_client_factory` simulates the upstream being down.
"""
from __future__ import annotations

import json

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

# A realistic streaming SSE body with a tool_use block, as the Messages API
# emits it. Bytes here are what a byte-exact relay must reproduce verbatim.
SSE_CHUNKS: list[bytes] = [
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","role":"assistant"}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_1","name":"get_weather","input":{}}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"city\\":\\"SF\\"}"}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]


class MockUpstream:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.mode = "json"          # json | sse | error
        self.status = 200
        self.response_json = {"id": "msg_1", "type": "message", "role": "assistant",
                              "content": [{"type": "text", "text": "ok"}]}
        self.sse_chunks = list(SSE_CHUNKS)
        self.delay_s = 0.0

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> dict | None:
        return self.requests[-1] if self.requests else None

    async def _handle(self, request: Request) -> Response:
        body = await request.body()
        parsed = None
        try:
            parsed = json.loads(body) if body else None
        except Exception:
            parsed = None
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "raw": body,
                "json": parsed,
            }
        )
        if self.delay_s:
            import asyncio
            await asyncio.sleep(self.delay_s)

        if self.mode == "error":
            return JSONResponse(
                status_code=self.status,
                content={"type": "error", "error": {"type": "overloaded_error", "message": "boom"}},
            )
        if self.mode == "sse":
            chunks = list(self.sse_chunks)

            async def _gen():
                for ch in chunks:
                    yield ch

            return StreamingResponse(
                _gen(), status_code=self.status, media_type="text/event-stream"
            )
        return JSONResponse(status_code=self.status, content=self.response_json)

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/{path:path}", self._handle,
                                       methods=["GET", "POST", "PUT", "DELETE", "PATCH"])])

    def client_factory(self):
        app = self.app()

        def _factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://upstream"
            )

        return _factory


def unreachable_client_factory():
    """A client whose every send raises ConnectError — simulates upstream down."""

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("upstream down", request=request)

    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=_Transport(), base_url="http://upstream")

    return _factory


def full_sse_bytes() -> bytes:
    return b"".join(SSE_CHUNKS)
