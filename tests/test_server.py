import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "test-token"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, PORT=str(cls.port), PUBLISH_TOKEN=TOKEN, TRANSLATION_PROVIDER="echo")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                cls.get("/health")
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)

    @classmethod
    def get(cls, path):
        return urllib.request.urlopen(f"http://127.0.0.1:{cls.port}{path}", timeout=5)

    @classmethod
    def post(cls, path, body, token=TOKEN):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", json.dumps(body).encode(), headers)
        return urllib.request.urlopen(req, timeout=5)

    def sse(self, lang, extra_headers=None):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        hdr = "".join(f"{k}: {v}\r\n" for k, v in (extra_headers or {}).items())
        s.sendall(f"GET /events?lang={lang} HTTP/1.1\r\nHost: x\r\n{hdr}\r\n".encode())
        return s

    def read_events(self, s, n):
        buf, out = b"", []
        deadline = time.time() + 5
        while len(out) < n and time.time() < deadline:
            try:
                buf += s.recv(4096)
            except socket.timeout:
                break
            for block in buf.decode().split("\n\n"):
                for line in block.splitlines():
                    if line.startswith("data: "):
                        e = json.loads(line[6:])
                        if e not in out:
                            out.append(e)
        return out

    def test_pages_and_traversal(self):
        self.assertEqual(self.get("/").status, 200)
        self.assertEqual(self.get("/gateway").status, 200)
        self.assertEqual(self.get("/screen?lang=th&pos=top").status, 200)
        self.assertEqual(self.get("/app.css").status, 200)
        for bad in ("/server.py", "/../server.py", "/public/index.html"):
            with self.assertRaises(urllib.error.HTTPError) as c:
                self.get(bad)
            self.assertEqual(c.exception.code, 404)

    def test_auth_required(self):
        for tok in (None, "wrong"):
            with self.assertRaises(urllib.error.HTTPError) as c:
                self.post("/api/segment", {"source_text": "hi", "source_lang": "en", "targets": ["th"]}, tok)
            self.assertEqual(c.exception.code, 401)

    def test_validation(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/api/segment", {"source_text": "hi", "source_lang": "xx", "targets": ["th"]})
        self.assertEqual(c.exception.code, 400)

    def test_publish_filtered_by_language(self):
        th, ko = self.sse("th"), self.sse("ko")
        time.sleep(0.3)
        r = self.post("/api/segment", {"source_text": "hello", "source_lang": "en", "targets": ["th", "en"]})
        self.assertEqual(r.status, 202)
        got = self.read_events(th, 1)
        self.assertEqual(got[0]["language"], "th")
        self.assertEqual(got[0]["text"], "[th] hello")
        ko.settimeout(0.5)
        live_ko = [e for e in self.read_events(ko, 1) if not e.get("replay")]
        self.assertEqual(live_ko, [])  # ko not requested in this segment
        th.close(); ko.close()

    def test_source_language_passthrough(self):
        en = self.sse("en")
        time.sleep(0.3)
        self.post("/api/segment", {"source_text": "same", "source_lang": "en", "targets": ["en"]})
        texts = [e["text"] for e in self.read_events(en, 5)]  # may include replayed history
        self.assertIn("same", texts)
        self.assertNotIn("[en] same", texts)  # source language is not machine-translated
        en.close()

    def test_gateway_translations_used_and_rest_filled_by_provider(self):
        zh, ko = self.sse("zh"), self.sse("ko")
        time.sleep(0.3)
        self.post("/api/segment", {"source_text": "pre", "source_lang": "en", "targets": ["zh", "ko"],
                                   "translations": {"zh": "预翻译", "ja": "ignored: not a target", "ko": 5}})
        z = [e for e in self.read_events(zh, 5) if e.get("sequence") and e["text"] in ("预翻译", "[zh] pre")]
        k = [e for e in self.read_events(ko, 5) if e["text"] == "[ko] pre"]
        self.assertEqual(z[-1]["text"], "预翻译")
        self.assertEqual(z[-1]["engine"], "gateway")
        self.assertEqual(k[-1]["engine"], "echo")  # non-string value ignored -> provider fills it
        zh.close(); ko.close()

    def test_replay_after_last_event_id(self):
        self.post("/api/segment", {"source_text": "one", "source_lang": "en", "targets": ["ja"]})
        self.post("/api/segment", {"source_text": "two", "source_lang": "en", "targets": ["ja"]})
        time.sleep(0.5)
        s = self.sse("ja")
        first = self.read_events(s, 2)
        s.close()
        self.assertGreaterEqual(len(first), 2)
        s = self.sse("ja", {"Last-Event-ID": str(first[-2]["id"])})
        again = self.read_events(s, 1)
        s.close()
        self.assertEqual(again[0]["id"], first[-1]["id"])


if __name__ == "__main__":
    unittest.main()
