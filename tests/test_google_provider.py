"""GoogleProvider against a local fake endpoint — no real key or network needed."""
import importlib
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
seen = {}


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.update(key=self.headers.get("X-Goog-Api-Key"), path=self.path, body=body)
        if body["target"] == "ko":
            self.send_response(403)
            self.end_headers()
            return
        out = json.dumps({"data": {"translations": [{"translatedText": f"<{body['target']}>{body['q']}"}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_):
        pass


class GoogleProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        os.environ.update(GOOGLE_TRANSLATE_API_KEY="fake-key-123", PUBLISH_TOKEN="t",
                          GOOGLE_TRANSLATE_URL=f"http://127.0.0.1:{cls.httpd.server_port}/v2")
        os.environ.pop("TRANSLATION_PROVIDER", None)
        sys.path.insert(0, str(ROOT))
        cls.server = importlib.import_module("server")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_google_is_default_when_key_present(self):
        self.assertEqual(self.server.provider.name, "google")

    def test_request_shape_and_header_key(self):
        out = self.server.provider.translate("สวัสดี", "th", "en")
        self.assertEqual(out, "<en>สวัสดี")
        self.assertEqual(seen["key"], "fake-key-123")
        self.assertNotIn("key=", seen["path"])  # key must not be in the URL
        self.assertEqual((seen["body"]["source"], seen["body"]["target"]), ("th", "en"))

    def test_http_error_becomes_translation_error_without_key(self):
        with self.assertRaises(self.server.TranslationError) as c:
            self.server.provider.translate("hi", "en", "ko")
        self.assertNotIn("fake-key", str(c.exception))


if __name__ == "__main__":
    unittest.main()
