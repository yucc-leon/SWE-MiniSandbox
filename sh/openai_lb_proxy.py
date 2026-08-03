#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from typing import Any
from urllib.parse import urlsplit

import requests


def _strip_hop_by_hop(headers: requests.structures.CaseInsensitiveDict[str]) -> dict[str, str]:
    blocked = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "host",
    }
    return {k: v for k, v in headers.items() if k.lower() not in blocked}


def _extract_request_metadata(body: bytes) -> dict[str, Any]:
    metadata: dict[str, Any] = {"body_bytes": len(body)}
    if not body:
        return metadata
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return metadata
    if not isinstance(payload, dict):
        return metadata
    metadata["model"] = payload.get("model")
    messages = payload.get("messages")
    if isinstance(messages, list):
        metadata["message_count"] = len(messages)
        metadata["message_chars"] = sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)
    tools = payload.get("tools")
    if isinstance(tools, list):
        metadata["tool_count"] = len(tools)
    return metadata


@dataclass
class Backend:
    name: str
    base_url: str
    active: int = 0
    served: int = 0
    failures: int = 0
    last_error: str = ""
    last_started_at: float = 0.0
    total_latency_s: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        with self.lock:
            self.active += 1
            self.served += 1
            self.last_started_at = time.time()

    def finish(self, *, ok: bool, error: str = "", latency_s: float = 0.0) -> None:
        with self.lock:
            self.active = max(0, self.active - 1)
            self.total_latency_s += max(0.0, latency_s)
            if not ok:
                self.failures += 1
                self.last_error = error

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            avg_latency = self.total_latency_s / self.served if self.served else 0.0
            return {
                "name": self.name,
                "base_url": self.base_url,
                "active": self.active,
                "served": self.served,
                "failures": self.failures,
                "last_error": self.last_error,
                "avg_latency_s": round(avg_latency, 3),
            }


class ProxyState:
    def __init__(
        self,
        backends: list[Backend],
        timeout_s: float,
        model_name: str,
        rewrite_model_to: str | None = None,
    ) -> None:
        self.backends = backends
        self.timeout_s = timeout_s
        self.model_name = model_name
        self.rewrite_model_to = rewrite_model_to
        self._counter = count()
        self.session = requests.Session()
        self.session.trust_env = False

    def choose_backend(self) -> Backend:
        ordered = list(self.backends)
        offset = next(self._counter) % len(ordered)
        ordered = ordered[offset:] + ordered[:offset]
        ordered.sort(key=lambda b: (b.active, b.failures))
        return ordered[0]

    def fallback_backend(self, current: Backend) -> Backend | None:
        others = [b for b in self.backends if b is not current]
        if not others:
            return None
        others.sort(key=lambda b: (b.active, b.failures))
        return others[0]

    def stats(self) -> dict[str, Any]:
        return {
            "timeout_s": self.timeout_s,
            "model_name": self.model_name,
            "rewrite_model_to": self.rewrite_model_to,
            "backends": [backend.snapshot() for backend in self.backends],
        }

    def rewrite_request_body(self, body: bytes) -> bytes:
        """Rewrite the public model alias to the backend model name when requested."""
        if not body or not self.rewrite_model_to or self.rewrite_model_to == self.model_name:
            return body
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return body
        if not isinstance(payload, dict):
            return body
        if payload.get("model") != self.model_name:
            return body
        payload["model"] = self.rewrite_model_to
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class LoadBalancingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: ProxyState

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._handle_with_disconnect_logging()

    def do_POST(self) -> None:
        self._handle_with_disconnect_logging()

    def _handle_with_disconnect_logging(self) -> None:
        try:
            self._handle()
        except (BrokenPipeError, ConnectionResetError) as exc:
            print(
                f"proxy client disconnected {self.command} {self.path}"
                f" error={type(exc).__name__}",
                flush=True,
            )
            self.close_connection = True

    def _handle(self) -> None:
        if self.path == "/__stats":
            payload = json.dumps(self.state.stats(), ensure_ascii=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            self.close_connection = True
            return
        if self.path == "/health":
            payload = b'{"status":"healthy"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            self.close_connection = True
            return
        if self.path == "/v1/models":
            payload = json.dumps(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": self.state.model_name,
                            "object": "model",
                            "created": 0,
                            "owned_by": "local",
                        }
                    ],
                },
                ensure_ascii=True,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            self.close_connection = True
            return

        body = self._read_body()
        prefer_lb = self.command == "POST" and self.path.startswith("/v1/chat/completions")
        backend = self.state.choose_backend()
        request_meta = _extract_request_metadata(body)
        print(
            f"proxy {self.command} {self.path} -> {backend.name}"
            f" active={backend.active} bytes={request_meta['body_bytes']}"
            f" model={request_meta.get('model')}"
            f" messages={request_meta.get('message_count')}"
            f" chars={request_meta.get('message_chars')}"
            f" tools={request_meta.get('tool_count')}",
            flush=True,
        )
        body = self.state.rewrite_request_body(body)
        response = self._forward(backend, body)

        if (response is None or response.status_code >= 500) and prefer_lb:
            fallback = self.state.fallback_backend(backend)
            if fallback is not None:
                print(
                    f"proxy fallback {self.command} {self.path} {backend.name} -> {fallback.name}"
                    f" reason={'no_response' if response is None else response.status_code}",
                    flush=True,
                )
                response = self._forward(fallback, body)
        elif response is None or response.status_code >= 500:
            fallback = self.state.fallback_backend(backend)
            if fallback is not None:
                print(
                    f"proxy fallback {self.command} {self.path} {backend.name} -> {fallback.name}"
                    f" reason={'no_response' if response is None else response.status_code}",
                    flush=True,
                )
                response = self._forward(fallback, body)

        if response is None:
            payload = b'{"error":"all backends unavailable"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(response.status_code)
        for key, value in _strip_hop_by_hop(response.headers).items():
            self.send_header(key, value)
        if "Content-Length" not in response.headers:
            self.send_header("Content-Length", str(len(response.content)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(response.content)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError) as exc:
            print(
                f"proxy client disconnected {self.command} {self.path}"
                f" status={response.status_code} error={type(exc).__name__}",
                flush=True,
            )
        self.close_connection = True

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _forward(self, backend: Backend, body: bytes) -> requests.Response | None:
        start = time.time()
        backend.start()
        try:
            url = backend.base_url.rstrip("/") + self.path
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in {"host", "connection", "keep-alive", "proxy-connection"}
            }
            headers["Connection"] = "close"
            response = self.state.session.request(
                method=self.command,
                url=url,
                headers=headers,
                data=body,
                timeout=self.state.timeout_s,
            )
            latency_s = time.time() - start
            backend.finish(
                ok=response.status_code < 500,
                error="" if response.status_code < 500 else f"http {response.status_code}",
                latency_s=latency_s,
            )
            print(
                f"proxy response {self.command} {self.path} <- {backend.name}"
                f" status={response.status_code} latency={latency_s:.2f}s"
                f" req_bytes={len(body)}",
                flush=True,
            )
            return response
        except requests.RequestException as exc:
            latency_s = time.time() - start
            backend.finish(ok=False, error=str(exc), latency_s=latency_s)
            print(
                f"proxy error {self.command} {self.path} <- {backend.name}"
                f" latency={latency_s:.2f}s error={exc}",
                flush=True,
            )
            return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Least-inflight OpenAI-compatible proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model-name", default="local-model")
    parser.add_argument(
        "--rewrite-model-to",
        help="Rewrite request JSON bodies from --model-name to this backend model name.",
    )
    parser.add_argument(
        "--backend",
        action="append",
        required=True,
        help="Backend base URL, e.g. http://127.0.0.1:8001",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    backends = []
    for idx, backend in enumerate(args.backend):
        parsed = urlsplit(backend)
        if not parsed.scheme or not parsed.netloc:
            raise SystemExit(f"invalid backend url: {backend}")
        backends.append(Backend(name=f"backend{idx}", base_url=backend.rstrip("/")))
    state = ProxyState(
        backends=backends,
        timeout_s=args.timeout,
        model_name=args.model_name,
        rewrite_model_to=args.rewrite_model_to,
    )
    handler_cls = type("ProxyHandler", (LoadBalancingHandler,), {"state": state})
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    print(f"load-balancing proxy listening on http://{args.host}:{args.port}", flush=True)
    print(f"public model name: {args.model_name}", flush=True)
    if args.rewrite_model_to:
        print(f"rewrite model to: {args.rewrite_model_to}", flush=True)
    for backend in backends:
        print(f"backend: {backend.base_url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
