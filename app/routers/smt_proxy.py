"""Reverse-proxy `/smt/*` to the SMT (Next.js) container.

The system nginx forwards everything to FastAPI on port 8000. By proxying
`/smt/*` from inside FastAPI, we avoid needing any host-level nginx config
or sudo to roll out the integrated SMT app — `docker compose up` is enough.

The SMT container runs Next.js with `basePath: '/smt'`, so the original
path is forwarded verbatim (no rewrite needed).
"""
from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()
logger = logging.getLogger(__name__)

SMT_BACKEND_URL = os.getenv("SMT_BACKEND_URL", "http://smt:3000").rstrip("/")

# Hop-by-hop headers must not be forwarded (RFC 7230 § 6.1). Content-Encoding
# and Content-Length are stripped so the downstream gzip middleware and the
# streaming response length aren't double-counted.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length",
}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # Generous timeouts: SMT does file uploads and exports that can be large.
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=5.0),
            follow_redirects=False,
        )
    return _client


async def _proxy(request: Request, target_path: str) -> StreamingResponse:
    url = f"{SMT_BACKEND_URL}{target_path}"

    # Strip Host (httpx sets its own) and Accept-Encoding (let the upstream
    # send identity-encoded bytes; FastAPI's GZipMiddleware compresses the
    # response on the way out).
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {"host", "accept-encoding"}
    }

    client = _get_client()
    upstream_req = client.build_request(
        request.method,
        url,
        headers=fwd_headers,
        params=request.query_params,
        content=request.stream(),
    )

    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.ConnectError as exc:
        logger.warning("SMT backend unreachable at %s: %s", url, exc)
        return StreamingResponse(
            iter([b"SMT service is starting up. Please retry in a moment."]),
            status_code=502,
            media_type="text/plain",
        )

    out_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )


_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]


@router.api_route("/smt", methods=_METHODS, include_in_schema=False)
async def smt_root(request: Request) -> StreamingResponse:
    return await _proxy(request, "/smt")


@router.api_route("/smt/{path:path}", methods=_METHODS, include_in_schema=False)
async def smt_subpath(request: Request, path: str) -> StreamingResponse:
    return await _proxy(request, f"/smt/{path}")
