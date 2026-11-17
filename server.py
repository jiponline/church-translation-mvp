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
    def do_GET(self):
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
        path = "/index.html" if self.path == "/" else self.path
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
    threading.Thread(target=demo_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Church Translation MVP running on port {PORT}")
    server.serve_forever()
