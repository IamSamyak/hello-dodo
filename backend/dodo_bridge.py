
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 8766

CLAIM_LEASE_SECONDS = 240
REQUEST_RETENTION_SECONDS = 900

class DodoBridgeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.requests = {}
        self.queue = deque()

    def create_request(self, prompt: str) -> str:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("Prompt is required.")

        request_id = str(uuid.uuid4())
        now = time.time()

        with self.lock:
            self.cleanup(now)
            self.requests[request_id] = {
                "id": request_id,
                "prompt": prompt,
                "status": "pending",
                "created_at": now,
                "claimed_at": None,
                "completed_at": None,
                "response": None,
            }
            self.queue.append(request_id)

        return request_id

    def claim_request(self):
        now = time.time()
        with self.lock:
            self.cleanup(now)

            while self.queue:
                request_id = self.queue.popleft()
                item = self.requests.get(request_id)

                if not item or item["status"] != "pending":
                    continue

                item["status"] = "claimed"
                item["claimed_at"] = now
                return {
                    "id": item["id"],
                    "prompt": item["prompt"],
                }

        return None

    def set_response(self, request_id: str, response):
        if not isinstance(response, dict):
            raise ValueError("Response must be a JSON object.")

        with self.lock:
            self.cleanup(time.time())
            item = self.requests.get(request_id)

            if not item or item["status"] != "claimed":
                return False

            item["response"] = response
            item["status"] = "completed"
            item["completed_at"] = time.time()
            return True

    def get_response(self, request_id: str):
        with self.lock:
            self.cleanup(time.time())
            item = self.requests.get(request_id)

            if not item or item["status"] != "completed":
                return None

            return item["response"]

    def cleanup(self, now: float):
        for request_id, item in list(self.requests.items()):
            if item["status"] == "claimed":
                claimed_at = item["claimed_at"] or now

                if now - claimed_at >= CLAIM_LEASE_SECONDS:
                    item["status"] = "pending"
                    item["claimed_at"] = None
                    self.queue.append(request_id)

            elif item["status"] == "completed":
                completed_at = item["completed_at"] or now

                if now - completed_at >= REQUEST_RETENTION_SECONDS:
                    del self.requests[request_id]


STATE = DodoBridgeState()


class DodoBridgeHandler(BaseHTTPRequestHandler):
    def send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}

        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data

    def query_value(self, name: str):
        values = parse_qs(urlparse(self.path).query).get(name)
        if not values or not values[0].strip():
            raise ValueError(f"Missing {name}.")
        return values[0].strip()

    def do_GET(self):
        path = urlparse(self.path).path

        try:
            if path == "/health":
                self.send_json(200, {
                    "ok": True,
                    "assistant": "Hello Dodo",
                })
                return

            if path == "/next":
                self.send_json(200, {
                    "request": STATE.claim_request(),
                })
                return

            if path == "/result":
                request_id = self.query_value("id")
                self.send_json(200, {
                    "response": STATE.get_response(request_id),
                })
                return

            self.send_json(404, {"error": "Not found."})

        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Bridge request failed."})

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            data = self.read_json()

            if path == "/request":
                request_id = STATE.create_request(data.get("prompt", ""))
                self.send_json(201, {"request_id": request_id})
                return

            if path == "/result":
                request_id = self.query_value("id")
                accepted = STATE.set_response(
                    request_id,
                    data.get("result"),
                )

                if not accepted:
                    self.send_json(409, {
                        "ok": False,
                        "error": "Unknown request or request is not claimed.",
                    })
                    return

                self.send_json(200, {"ok": True})
                return

            self.send_json(404, {"error": "Not found."})

        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Bridge request failed."})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), DodoBridgeHandler)
    print(f"Hello Dodo bridge running at http://{HOST}:{PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Hello Dodo bridge stopped.")
    finally:
        server.server_close()