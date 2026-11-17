import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = int(os.environ.get("PORT", "8000"))
listeners = []
lock = threading.Lock()
sequence = 0
gateway_state = {
    "connected": False,
    "gateway_id": None,
    "name": None,
    "source": None,
    "last_seen": None,
    "chunks": 0,
    "bytes_received": 0,
}

translations = {
    "th": "ยินดีต้อนรับทุกท่าน นี่คือการสาธิตระบบแปลภาษาแบบสด",
    "en": "Welcome everyone. This is a live translation demo.",
    "zh": "欢迎参加聚会。这是实时翻译演示。",
    "ko": "예배에 오신 것을 환영합니다. 실시간 번역 데모입니다.",
    "ja": "礼拝へようこそ。これはライブ翻訳のデモです.",
}


def send_event(event):
    payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
    with lock:
        active = []
        for stream in listeners:
            try:
                stream.write(payload)
                stream.flush()
                active.append(stream)
            except Exception:
                pass
        listeners[:] = active


def demo_loop():
    global sequence
    while True:
        time.sleep(5)
        sequence += 1
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for language, text in translations.items():
            send_event({
                "type": "text.segment",
                "session_id": "demo-session",
                "sequence": sequence,
                "language": language,
                "final": True,
                "text": text,
                "generated_at": now,
            })


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_POST(self):
        global gateway_state, sequence
        if self.path in ("/api/gateway/register", "/api/gateway/heartbeat"):
            payload = self._read_json()
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with lock:
                gateway_state.update({
                    "connected": True,
                    "gateway_id": payload.get("gateway_id") or gateway_state["gateway_id"] or "browser-gateway",
                    "name": payload.get("name") or gateway_state["name"] or "Church Audio Gateway 1",
                    "source": payload.get("source") or gateway_state["source"] or "browser",
                    "last_seen": now,
                })
                result = dict(gateway_state)
            self._json(200, result)
            return
        if self.path == "/api/gateway/audio":
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with lock:
                gateway_state["connected"] = True
                gateway_state["last_seen"] = now
                gateway_state["chunks"] += 1
                gateway_state["bytes_received"] += length
            self._json(202, {"accepted": True, "bytes": length})
            return
        if self.path == "/api/translation":
            payload = self._read_json()
            sequence += 1
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for language, text in (payload.get("translations") or {}).items():
                send_event({
                    "type": "text.segment",
                    "session_id": "live-session",
                    "sequence": sequence,
                    "language": language,
                    "source_text": payload.get("source_text", ""),
                    "final": True,
                    "text": text,
                    "generated_at": now,
                })
            self._json(202, {"accepted": True, "sequence": sequence})
            return
        self.send_error(404)

    def do_GET(self):
        if self.path == "/api/gateway/status":
            with lock:
                result = dict(gateway_state)
                if result["last_seen"]:
                    last = time.mktime(time.strptime(result["last_seen"], "%Y-%m-%dT%H:%M:%SZ"))
                    result["connected"] = time.time() - last < 15
            self._json(200, result)
            return
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            with lock:
                listeners.append(self.wfile)
            try:
                while True:
                    time.sleep(30)
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            return
        path = "/index.html" if self.path == "/" else ("/admin.html" if self.path == "/admin" else ("/gateway.html" if self.path == "/gateway" else self.path))
        file_path = ROOT / "public" / path.lstrip("/")
        if not file_path.exists():
            file_path = ROOT / path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            content = file_path.read_bytes()
            content_type = "text/html; charset=utf-8" if file_path.suffix == ".html" else "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_error(404)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    # Keep the public service quiet until a real gateway publishes a translation.
    # Set DEMO_MODE=1 only when intentionally running the sample feed locally.
    if os.environ.get("DEMO_MODE") == "1":
        threading.Thread(target=demo_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Church Translation MVP running on port {PORT}")
    server.serve_forever()
