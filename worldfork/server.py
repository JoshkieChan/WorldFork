"""Bounded, single-user loopback HTTP server for WorldFork.

Loopback is a deployment boundary, not user authentication. Other programs and
users on the same machine can read local evidence and obtain a mutation token.
Do not expose this development server through a public interface or proxy.
"""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

from . import __version__
from .provider import OpenAIInterpreter, ProviderError, ProviderUnavailable

MAX_BODY_BYTES = 16384
MAX_RUN_BYTES = 1048576
MAX_HISTORY_BYTES = 16 * 1048576
MAX_HISTORY_RUNS = 100
MAX_CONNECTIONS = 8
RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
)


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class RequestError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class RunStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "runs.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Database path must not be a symbolic link.")
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
            connection.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, name TEXT NOT NULL, recommended_policy TEXT NOT NULL, service_rate REAL NOT NULL, payload BLOB NOT NULL)")
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=3)
        try:
            connection.execute("PRAGMA secure_delete=ON")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute("PRAGMA max_page_count=" + str((32 * 1048576) // page_size))
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, run):
        payload = encode_json(run)
        if len(payload) > MAX_RUN_BYTES or not RUN_ID.fullmatch(run["id"]):
            raise ValueError("Experiment result exceeds storage limits.")
        winner = next(policy for policy in run["policies"] if policy["id"] == run["recommended_policy"])
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                (run["id"], run["created_at"], run["scenario"]["name"],
                 run["recommended_policy"], winner["service_rate"], payload),
            )
            connection.execute("DELETE FROM runs WHERE rowid NOT IN (SELECT rowid FROM runs ORDER BY rowid DESC LIMIT ?)", (MAX_HISTORY_RUNS,))
            total = connection.execute("SELECT COALESCE(SUM(LENGTH(payload)), 0) FROM runs").fetchone()[0]
            while total > MAX_HISTORY_BYTES:
                oldest, size = connection.execute("SELECT rowid, LENGTH(payload) FROM runs ORDER BY rowid LIMIT 1").fetchone()
                connection.execute("DELETE FROM runs WHERE rowid = ?", (oldest,))
                total -= size

    def list(self):
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT id, created_at, name, recommended_policy, service_rate FROM runs ORDER BY rowid DESC LIMIT ?", (MAX_HISTORY_RUNS,)).fetchall()
            return [dict(row) for row in rows]

    def get(self, run_id):
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
            return row[0] if row else None


class WorldForkServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, address, *, data_dir, web_dir, engine_module, provider):
        if address[0] == "::1":
            self.address_family = socket.AF_INET6
        self.engine = engine_module
        self.provider = provider
        self.web_dir = Path(web_dir).resolve()
        self.store = RunStore(Path(data_dir))
        self.csrf_token = secrets.token_urlsafe(32)
        self.work_slot = threading.BoundedSemaphore(1)
        self.connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._rate_lock = threading.Lock()
        self._requests, self._mutations = deque(), deque()
        super().__init__(address, WorldForkHandler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            # Bound thread creation even for sockets that send no HTTP headers.
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, request, client_address):
        # Do not log body data, credentials, request paths, or exception details.
        print("WorldFork: a local connection failed.", file=sys.stderr)

    def allow_request(self, mutation=False):
        now = time.monotonic()
        with self._rate_lock:
            queue, limit = (self._mutations, 12) if mutation else (self._requests, 120)
            while queue and now - queue[0] >= 60:
                queue.popleft()
            if len(queue) >= limit:
                return False
            queue.append(now)
            return True


class WorldForkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def version_string(self):
        return "WorldFork"

    def log_message(self, format, *args):
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8", *, download=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Connection", "close")
        if status == 429:
            self.send_header("Retry-After", "60")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="' + download + '"')
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, value):
        self._send(status, encode_json(value))

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler can call this before dispatch for malformed input.
        try:
            self._json(code, {"error": "Invalid or unsupported HTTP request."})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def _guard(self, mutation=False):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if len(hosts) != 1 or hosts[0] not in allowed:
            raise RequestError(403, "Invalid local host.")
        expected_origin = "http://" + hosts[0]
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] != expected_origin):
            raise RequestError(403, "Cross-origin requests are not allowed.")
        if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            raise RequestError(403, "Cross-origin requests are not allowed.")
        if not self.server.allow_request():
            raise RequestError(429, "Local request limit reached. Retry in one minute.")
        if mutation:
            tokens = self.headers.get_all("X-WorldFork-Token", [])
            if len(tokens) != 1 or not tokens[0].isascii() or not hmac.compare_digest(tokens[0], self.server.csrf_token):
                raise RequestError(403, "Refresh the page to obtain a valid local request token.")
            if not self.server.allow_request(mutation=True):
                raise RequestError(429, "Experiment request limit reached. Retry in one minute.")

    def _path(self):
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise RequestError(400, "Invalid request target.")
        try:
            path = unquote(parsed.path, errors="strict")
        except UnicodeError:
            raise RequestError(400, "Invalid request path.") from None
        if "\\" in path or "\x00" in path or ".." in path.split("/"):
            raise RequestError(404, "Not found.")
        return path

    def _read_json(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            raise RequestError(400, "One Content-Length header is required.")
        if not re.fullmatch(r"[0-9]{1,8}", lengths[0]):
            raise RequestError(400, "Invalid request length.")
        length = int(lengths[0])
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "Request exceeds the 16 KiB limit.")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise RequestError(415, "Content-Type must be application/json.")
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            def invalid_constant(value):
                raise ValueError()
            data = json.loads(body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError, RecursionError):
            raise RequestError(400, "Request must be a valid JSON object without duplicate keys or non-finite numbers.") from None

    def _get(self):
        self._guard()
        path = self._path()
        if path == "/api/health":
            return self._json(200, {"status": "ok", "version": __version__})
        if path == "/api/bootstrap":
            result = dict(self.server.engine.bootstrap())
            result["capabilities"] = {
                "language_model": self.server.provider.configured,
                "model": self.server.provider.model if self.server.provider.configured else None,
            }
            result["csrf_token"] = self.server.csrf_token
            return self._json(200, result)
        if path == "/api/runs":
            return self._json(200, {"runs": self.server.store.list()})
        if path.startswith("/api/runs/"):
            parts = path.split("/")
            if len(parts) not in (4, 5) or not RUN_ID.fullmatch(parts[3]) or (len(parts) == 5 and parts[4] != "export"):
                raise RequestError(404, "Experiment not found.")
            payload = self.server.store.get(parts[3])
            if payload is None:
                raise RequestError(404, "Experiment not found.")
            filename = "worldfork-" + parts[3] + ".json" if len(parts) == 5 else None
            return self._send(200, payload, download=filename)
        if path not in STATIC_FILES:
            raise RequestError(404, "Not found.")
        filename, content_type = STATIC_FILES[path]
        target = (self.server.web_dir / filename).resolve()
        if not target.is_relative_to(self.server.web_dir) or not target.is_file():
            raise RequestError(404, "Not found.")
        if target.stat().st_size > MAX_RUN_BYTES:
            raise RequestError(500, "Static asset exceeds the allowed size.")
        return self._send(200, target.read_bytes(), content_type)

    def _post(self):
        self._guard(mutation=True)
        path = self._path()
        if path not in ("/api/runs", "/api/interpret"):
            raise RequestError(404, "Not found.")
        data = self._read_json()
        if not self.server.work_slot.acquire(blocking=False):
            raise RequestError(503, "Another experiment or interpretation is running. Try again shortly.")
        try:
            if path == "/api/runs":
                try:
                    scenario = self.server.engine.validate_scenario(data)
                except ValueError as error:
                    raise RequestError(422, str(error)) from None
                result = self.server.engine.run_experiment(scenario)
                self.server.store.save(result)
                return self._json(201, result)
            if set(data) != {"prompt"}:
                raise RequestError(422, "Interpretation requires exactly one prompt field.")
            try:
                result = self.server.provider.interpret(data["prompt"])
            except ValueError as error:
                raise RequestError(422, str(error)) from None
            except ProviderUnavailable as error:
                raise RequestError(503, str(error)) from None
            except ProviderError as error:
                raise RequestError(502, str(error)) from None
            return self._json(200, result)
        finally:
            self.server.work_slot.release()

    def _dispatch(self, action):
        try:
            action()
        except RequestError as error:
            self._json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self._json(500, {"error": "The local server could not complete this request."})

    def do_GET(self):
        self._dispatch(self._get)

    do_HEAD = do_GET

    def do_POST(self):
        self._dispatch(self._post)

    def _unsupported(self):
        self._json(405, {"error": "HTTP method not allowed."})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = _unsupported


def create_server(host="127.0.0.1", port=8787, *, data_dir=None, web_dir=None, engine_module=None, provider=None):
    """Construct a server without starting it; port 0 is useful for tests."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("WorldFork only supports loopback hosts.")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Port must be an integer between 0 and 65535.")
    if engine_module is None:
        from . import engine as engine_module
    root = Path(__file__).resolve().parent.parent
    return WorldForkServer(
        ("127.0.0.1" if host == "localhost" else host, port),
        data_dir=data_dir if data_dir is not None else root / ".worldfork",
        web_dir=web_dir if web_dir is not None else root / "web",
        engine_module=engine_module,
        provider=provider if provider is not None else OpenAIInterpreter.from_environment(),
    )


def main():
    parser = argparse.ArgumentParser(description="WorldFork local counterfactual lab")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()
    try:
        server = create_server(args.host, args.port, data_dir=args.data_dir)
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(1, "WorldFork could not start. Check the loopback port and local data directory.\n")
    display_host = "[::1]" if args.host == "::1" else "127.0.0.1"
    print(f"WorldFork {__version__}: http://{display_host}:{server.server_address[1]}", flush=True)
    print("Single-user local research server. Synthetic results are not real-world forecasts.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
