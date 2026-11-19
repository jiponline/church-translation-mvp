"""Church Translation — Phase A server (stdlib only, asyncio).

Changes vs MVP 0.1:
  * asyncio: SSE listeners are cheap coroutines, not one thread each
  * publisher auth (Bearer token) on every write endpoint
  * translation runs on the server behind a provider adapter (retry + circuit breaker)
  * per-language SSE streams with event ids and Last-Event-ID replay
  * static files served from an allow-list only
"""
import asyncio
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
PORT = int(os.environ.get("PORT", "8000"))
LANGS = {"th", "en", "zh", "ko", "ja"}
MAX_BODY = 64 * 1024
MAX_TEXT = 1500
MAX_LISTENERS = int(os.environ.get("MAX_LISTENERS", "2000"))
MAX_WRITE_BUFFER = 256 * 1024  # drop slow consumers beyond this
REPLAY_SIZE = 300
HEARTBEAT_S = 15

PUBLISH_TOKEN = os.environ.get("PUBLISH_TOKEN") or secrets.token_urlsafe(18)
TOKEN_WAS_GENERATED = "PUBLISH_TOKEN" not in os.environ

STATIC = {
    "/": ("public/index.html", "text/html; charset=utf-8"),
    "/admin": ("admin.html", "text/html; charset=utf-8"),
    "/gateway": ("gateway.html", "text/html; charset=utf-8"),
    "/screen": ("public/screen.html", "text/html; charset=utf-8"),
    "/app.css":("public/app.css", "text/css; charset=utf-8"),
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- translation
def chunk_text(text, limit=450):
    """Split on sentence/space boundaries so providers with a char limit work."""
    text = text.strip()
    parts = []
    while len(text) > limit:
        cut = max(text.rfind(p, 0, limit) for p in ("。", ".", "!", "?", "！", "？", " "))
        cut = cut + 1 if cut > 0 else limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


class TranslationError(Exception):
    pass


class EchoProvider:
    """Deterministic provider for tests/offline use."""
    name = "echo"

    def translate(self, text, src, dst):
        return f"[{dst}] {text}"


class MyMemoryProvider:
    """Free, keyless, quota-limited. For development only."""
    name = "mymemory"

    def _one(self, part, src, dst):
        qs = urllib.parse.urlencode({"q": part, "langpair": f"{src}|{dst}"})
        req = urllib.request.Request(
            f"https://api.mymemory.translated.net/get?{qs}",
            headers={"User-Agent": "church-translation/0.2"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("responseStatus") not in (200, "200"):
            raise TranslationError(str(data.get("responseDetails", "provider error"))[:120])
        return data["responseData"]["translatedText"]

    def translate(self, text, src, dst):
        parts = chunk_text(text)
        if len(parts) == 1:
            return self._one(parts[0], src, dst)
        # long text: translate chunks in parallel (separate pool avoids nested-pool deadlock)
        return " ".join(chunk_pool.map(lambda p: self._one(p, src, dst), parts))


class GoogleProvider:
    """Google Cloud Translation v2. Key comes from GOOGLE_TRANSLATE_API_KEY (env only, never logged).
    The key is sent in a header, not the URL, so it cannot leak into access logs."""
    name = "google"
    URL = os.environ.get("GOOGLE_TRANSLATE_URL", "https://translation.googleapis.com/language/translate/v2")

    def __init__(self):
        self.key = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")
        if not self.key:
            raise SystemExit("GOOGLE_TRANSLATE_API_KEY is not set")

    def translate(self, text, src, dst):
        body = json.dumps({"q": text, "source": src, "target": dst, "format": "text"}).encode("utf-8")
        req = urllib.request.Request(self.URL, data=body, headers={
            "Content-Type": "application/json; charset=utf-8", "X-Goog-Api-Key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # keep status only; body may echo request details
            raise TranslationError(f"http_{exc.code}")
        return data["data"]["translations"][0]["translatedText"]


PROVIDERS = {"echo": EchoProvider, "mymemory": MyMemoryProvider, "google": GoogleProvider}
_default = "google" if os.environ.get("GOOGLE_TRANSLATE_API_KEY") else "mymemory"
provider = PROVIDERS[os.environ.get("TRANSLATION_PROVIDER", _default)]()


class Breaker:
    """Per-language circuit breaker: 5 consecutive failures -> skip for 30s."""

    def __init__(self):
        self.fails = {}
        self.open_until = {}

    def allow(self, lang):
        return time.time() >= self.open_until.get(lang, 0)

    def ok(self, lang):
        self.fails[lang] = 0

    def bad(self, lang):
        self.fails[lang] = self.fails.get(lang, 0) + 1
        if self.fails[lang] >= 5:
            self.open_until[lang] = time.time() + 30
            self.fails[lang] = 0


breaker = Breaker()
pool = ThreadPoolExecutor(max_workers=16)
chunk_pool = ThreadPoolExecutor(max_workers=16)


def translate_one(text, src, dst):
    """Runs in a worker thread. Returns (text, error_code|None)."""
    if not breaker.allow(dst):
        return None, "circuit_open"
    last = "unknown"
    for attempt in range(2):
        try:
            result = provider.translate(text, src, dst)
            breaker.ok(dst)
            return result, None
        except Exception as exc:  # network, quota, parse
            last = type(exc).__name__
            time.sleep(0.3 * (attempt + 1))
    breaker.bad(dst)
    return None, last


# ------------------------------------------------------------------ state
class Hub:
    def __init__(self):
        self.listeners = set()  # Listener objects
        self.history = deque(maxlen=REPLAY_SIZE)
        self.event_id = 0
        self.sequence = 0
        self.gateway = {"last_seen": 0.0, "name": None, "source_lang": None}
        self.stats = {"segments": 0, "translation_errors": 0, "dropped_slow": 0}
        self.latency = {}  # lang -> deque of recent translation latencies (ms)

    def latency_summary(self):
        out = {}
        for lang, d in self.latency.items():
            v = sorted(d)
            out[lang] = {"n": len(v), "p50_ms": v[len(v) // 2], "p95_ms": v[min(len(v) - 1, int(len(v) * 0.95))]}
        return out

    def publish(self, event):
        self.event_id += 1
        event["id"] = self.event_id
        self.history.append(event)
        frame = sse_frame(event)
        for lst in list(self.listeners):
            if lst.lang == event["language"]:
                lst.send(frame)


class Listener:
    def __init__(self, writer, lang):
        self.writer, self.lang = writer, lang

    def send(self, frame):
        t = self.writer.transport
        if t.is_closing():
            return
        if t.get_write_buffer_size() > MAX_WRITE_BUFFER:
            hub.stats["dropped_slow"] += 1
            t.abort()
            return
        self.writer.write(frame)


def sse_frame(event):
    return f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


hub = Hub()


async def process_segment(source_text, src, targets, pre=None):
    """pre: translations already produced on the gateway (Chrome on-device); only the rest hit the provider."""
    pre = pre or {}
    hub.sequence += 1
    seq = hub.sequence
    hub.stats["segments"] += 1
    loop = asyncio.get_running_loop()

    async def one(dst):
        started = time.time()
        engine = provider.name
        if dst == src:
            text, err, engine = source_text, None, "source"
        elif dst in pre:
            text, err, engine = pre[dst], None, "gateway"
        else:
            text, err = await loop.run_in_executor(pool, translate_one, source_text, src, dst)
        if err:
            hub.stats["translation_errors"] += 1
        elif engine == provider.name:
            hub.latency.setdefault(dst, deque(maxlen=200)).append(int((time.time() - started) * 1000))
        hub.publish({
            "type": "text.segment", "session_id": "live", "sequence": seq,
            "language": dst, "source_language": src, "final": True,
            "text": text, "error": err, "engine": engine,
            "latency_ms": int((time.time() - started) * 1000),
            "generated_at": now_iso(),
        })

    await asyncio.gather(*(one(d) for d in targets))
    return seq


# ------------------------------------------------------------------- HTTP
REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
           405: "Method Not Allowed", 413: "Payload Too Large", 429: "Too Many Requests",
           503: "Service Unavailable"}
COMMON = [("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
          ("Cache-Control", "no-store")]


async def respond(writer, status, body=b"", ctype="application/json; charset=utf-8", extra=()):
    head = [f"HTTP/1.1 {status} {REASONS.get(status, '')}", f"Content-Type: {ctype}",
            f"Content-Length: {len(body)}", "Connection: close"]
    head += [f"{k}: {v}" for k, v in list(COMMON) + list(extra)]
    writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + body)
    await writer.drain()


async def respond_json(writer, status, payload):
    await respond(writer, status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def authorized(headers):
    auth = headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    return hmac.compare_digest(token.encode(), PUBLISH_TOKEN.encode())


rate = {}


def rate_ok(key, limit=120, per=60.0):
    now = time.time()
    q = rate.setdefault(key, deque())
    while q and now - q[0] > per:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


async def read_request(reader):
    line = await asyncio.wait_for(reader.readline(), 10)
    if not line:
        return None
    try:
        method, target, _ = line.decode("latin-1").split(" ", 2)
    except ValueError:
        return None
    headers, size = {}, 0
    while True:
        h = await asyncio.wait_for(reader.readline(), 10)
        size += len(h)
        if size > 16 * 1024:
            return None
        if h in (b"\r\n", b"\n", b""):
            break
        k, _, v = h.decode("latin-1").partition(":")
        headers[k.strip().lower()] = v.strip()
    body = b""
    n = int(headers.get("content-length", "0") or 0)
    if n > MAX_BODY:
        return method, target, headers, None
    if n:
        body = await asyncio.wait_for(reader.readexactly(n), 10)
    return method, target, headers, body


async def handle_events(reader, writer, query, headers):
    lang = (query.get("lang") or ["en"])[0]
    if lang not in LANGS:
        return await respond_json(writer, 400, {"error": "unsupported language"})
    if len(hub.listeners) >= MAX_LISTENERS:
        return await respond_json(writer, 503, {"error": "listener limit reached"})
    writer.write(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
                  "Cache-Control: no-cache\r\nConnection: keep-alive\r\nX-Accel-Buffering: no\r\n\r\n"
                  "retry: 3000\n\n").encode())
    last = headers.get("last-event-id") or (query.get("last") or [""])[0]
    past = [e for e in hub.history if e["language"] == lang]
    past = [e for e in past if e["id"] > int(last)] if last.isdigit() else past[-10:]
    for e in past:
        writer.write(sse_frame(dict(e, replay=True)))  # lets clients skip speaking/showing old lines
    lst = Listener(writer, lang)
    hub.listeners.add(lst)
    try:
        await writer.drain()
        while True:
            try:
                data = await asyncio.wait_for(reader.read(1), HEARTBEAT_S)
                if not data:
                    break  # client closed
            except asyncio.TimeoutError:
                if writer.transport.is_closing():
                    break
                writer.write(b": hb\n\n")
    finally:
        hub.listeners.discard(lst)


async def handle(reader, writer):
    try:
        req = await read_request(reader)
        if req is None:
            return
        method, target, headers, body = req
        url = urllib.parse.urlsplit(target)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        if body is None:
            return await respond_json(writer, 413, {"error": "payload too large"})

        if method == "GET" and path == "/events":
            return await handle_events(reader, writer, query, headers)
        if method == "GET" and path in STATIC:
            f, ctype = STATIC[path]
            return await respond(writer, 200, (ROOT / f).read_bytes(), ctype)
        if method == "GET" and path == "/health":
            return await respond_json(writer, 200, {"ok": True})
        if method == "GET" and path == "/api/status":
            age = time.time() - hub.gateway["last_seen"]
            return await respond_json(writer, 200, {
                "gateway_connected": age < 15, "listeners": len(hub.listeners),
                "provider": provider.name, "stats": hub.stats,
                "translation_latency": hub.latency_summary()})

        if path.startswith("/api/") and method == "POST":
            ip = (headers.get("x-forwarded-for") or "").split(",")[0].strip() or "direct"
            if not rate_ok(ip, 300):
                return await respond_json(writer, 429, {"error": "rate limited"})
            if not authorized(headers):
                return await respond_json(writer, 401, {"error": "invalid token"})
            try:
                data = json.loads(body.decode("utf-8") or "{}")
                assert isinstance(data, dict)
            except (ValueError, AssertionError):
                return await respond_json(writer, 400, {"error": "invalid json"})

            if path == "/api/gateway/heartbeat":
                hub.gateway.update(last_seen=time.time(), name=str(data.get("name", ""))[:60],
                                   source_lang=data.get("source_lang"))
                return await respond_json(writer, 200, {"ok": True})

            if path == "/api/segment":
                text = str(data.get("source_text", "")).strip()[:MAX_TEXT]
                src = data.get("source_lang")
                targets = [t for t in data.get("targets", []) if t in LANGS]
                if not text or src not in LANGS or not targets:
                    return await respond_json(writer, 400, {"error": "need source_text, source_lang, targets"})
                raw = data.get("translations") if isinstance(data.get("translations"), dict) else {}
                pre = {k: v.strip()[:MAX_TEXT * 2] for k, v in raw.items()
                       if k in targets and k != src and isinstance(v, str) and v.strip()}
                hub.gateway["last_seen"] = time.time()
                seq = hub.sequence + 1
                asyncio.ensure_future(process_segment(text, src, sorted(set(targets)), pre))
                return await respond_json(writer, 202, {"accepted": True, "sequence": seq})

        if path.startswith("/api/"):
            return await respond_json(writer, 404 if method in ("GET", "POST") else 405, {"error": "not found"})
        await respond(writer, 404, b"not found", "text/plain")
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=64 * 1024, backlog=1024)
    print(f"Church Translation Phase A on :{PORT} provider={provider.name}", flush=True)
    if TOKEN_WAS_GENERATED:
        print(f"PUBLISH_TOKEN not set; generated for this run: {PUBLISH_TOKEN}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
