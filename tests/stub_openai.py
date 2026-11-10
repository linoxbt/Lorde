"""A tiny OpenAI-compatible server that plays teacher, student and judge in tests.

Judge prompts are recognised with the template matcher and answered with a
plausible score; QA-generation prompts get a JSON array; anything else is echoed.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lorde_forge import contract


def judge_reply(prompt: str, style: str) -> str | None:
    m = contract.match_template(prompt)
    if not m:
        return None
    kind, slots = m
    if kind == "grounding":
        ok = slots["answer"].strip()[:30].lower() in slots["source_text"].lower()
        score = 0.9 if ok else 0.2
        body = {"grounding_score": score, "supported_claims": [slots["answer"][:50]] if ok else [],
                "unsupported_claims": [] if ok else [slots["answer"][:50]],
                "verdict": "grounded" if ok else "hallucinated"}
    else:
        score = 0.8 if len(slots["response"]) > 40 else 0.3
        body = {"overall_score": score, "dimension_scores": {"helpfulness": score, "honesty": score,
                "instruction_following": score}, "strengths": "clear", "weaknesses": "none"}
    text = json.dumps(body)
    if style == "fenced":
        return f"```json\n{text}\n```"
    if style == "chatty":
        return f"Here is my evaluation: {text}"
    return text


class Handler(BaseHTTPRequestHandler):
    style = "plain"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "stub", "object": "model"}]})

    def do_POST(self):
        if self.style == "slow":
            import time

            time.sleep(1.0)
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        prompt = req["messages"][-1]["content"]
        style = "plain" if req.get("response_format") or self.style == "slow" else self.style
        text = judge_reply(prompt, style)
        if text is None and "question-answer pair" in prompt:
            src = prompt.split("---")[1].strip()
            first = re.split(r"(?<=[.!?])\s+", src)[0][:200]
            text = json.dumps([{"question": f"What does the text say first? ({i})", "answer": first} for i in range(3)])
        elif text is None:
            text = "Echo: " + prompt[-120:]
        self._send({
            "id": "cmpl-stub", "object": "chat.completion", "created": 0, "model": req.get("model", "stub"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4,
                      "total_tokens": (len(prompt) + len(text)) // 4},
        })


def serve(style: str = "plain") -> tuple[ThreadingHTTPServer, str]:
    handler = type("H", (Handler,), {"style": style})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
