"""Tiny HTTP client with caching, retry/backoff and per-host rate limiting.

Standard library only so the app runs on a bare Python 3.9 install.
"""
import hashlib
import http.client
import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("fundwatch.http")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class HttpError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, cache_dir, timeout=15, min_interval=0.35, retries=3, use_proxy=False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = float(timeout)
        self.min_interval = float(min_interval)
        self.retries = max(1, int(retries))
        self._last_call = {}
        handlers = [urllib.request.ProxyHandler({})] if not use_proxy else []
        self._opener = urllib.request.build_opener(*handlers)
        self.calls = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ utils
    def _cache_path(self, key):
        return self.cache_dir / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def _read_cache(self, key, ttl):
        if ttl <= 0:
            return None
        path = self._cache_path(key)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                item = json.load(fh)
            if time.time() - float(item.get("ts", 0)) <= ttl:
                self.cache_hits += 1
                return item.get("body")
        except (OSError, ValueError):
            return None
        return None

    def _write_cache(self, key, body):
        path = self._cache_path(key)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"ts": time.time(), "body": body}, fh, ensure_ascii=False)
            tmp.replace(path)
        except OSError:
            pass

    def _throttle(self, host):
        last = self._last_call.get(host, 0.0)
        wait = self.min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_call[host] = time.time()

    # ------------------------------------------------------------------- main
    def get(self, url, headers=None, encoding="utf-8", ttl=0, retries=None, cache_key=None):
        """Fetch a URL, optionally serving from a TTL cache. Raises HttpError."""
        key = cache_key or url
        cached = self._read_cache(key, ttl)
        if cached is not None:
            return cached
        attempts = self.retries if retries is None else max(1, int(retries))
        hdrs = {"User-Agent": DEFAULT_UA, "Accept": "*/*", "Accept-Encoding": "identity"}
        hdrs.update(headers or {})
        host = urlparse(url).netloc
        last_err = None
        for attempt in range(attempts):
            self._throttle(host)
            self.calls += 1
            req = urllib.request.Request(url, headers=hdrs, method="GET")
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                body = raw.decode(encoding, "replace")
                if ttl > 0:
                    self._write_cache(key, body)
                return body
            except urllib.error.HTTPError as exc:
                last_err = exc
                if 400 <= exc.code < 500:
                    raise HttpError("HTTP %s for %s" % (exc.code, url))
                log.warning("HTTP %s, retry %s/%s: %s", exc.code, attempt + 1, attempts, url)
            except (urllib.error.URLError, http.client.RemoteDisconnected, ConnectionError,
                    socket.timeout, ssl.SSLError, TimeoutError) as exc:
                last_err = exc
                log.warning("network error, retry %s/%s: %s (%s)", attempt + 1, attempts, url, exc)
            if attempt < attempts - 1:
                time.sleep(min(2.0, 0.5 * (2 ** attempt)))
        raise HttpError("request failed after %s attempts: %s (%s)" % (attempts, url, last_err))

    def post_json(self, url, payload, headers=None, timeout=None):
        hdrs = {"User-Agent": DEFAULT_UA, "Content-Type": "application/json; charset=utf-8"}
        hdrs.update(headers or {})
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        self._throttle(urlparse(url).netloc)
        with self._opener.open(req, timeout=timeout or self.timeout) as resp:
            return resp.read().decode("utf-8", "replace")
