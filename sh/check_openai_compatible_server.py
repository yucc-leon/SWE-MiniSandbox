#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from run_remote_inference_task import probe_openai_server


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe an OpenAI-compatible inference endpoint for /health, /v1/models, and optional chat readiness."
    )
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--check-chat", action="store_true")
    parser.add_argument("--chat-model")
    args = parser.parse_args()

    chat_model = args.chat_model if args.check_chat else None
    result = probe_openai_server(
        api_base=args.api_base,
        timeout_s=args.timeout,
        poll_s=args.poll_interval,
        request_timeout_s=args.request_timeout,
        chat_model=chat_model,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
