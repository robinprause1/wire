"""HTTP edge: static pages and a JSON API over desks. No business rules live here."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from intel import accounts, clock, connectors, desks, settings, subjects
from intel.board import board, summary
from intel.cycle import Runner
from intel.desk import STEPS
from intel.index import WireIndex, embedder_name
from intel.store import Store
from intel.templates import TEMPLATES

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = Path(__file__).resolve().parent / "data"
STATIC = Path(__file__).resolve().parent / "static"
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}
_DESK = r"(?P<desk>[a-z0-9-]{1,40})"
_BODY_LIMIT = 200_000

Response = tuple[int, dict[str, object]]


def _static_target(name: str) -> Path | None:
    """A file in static/, or one file under static/fonts/. Nothing else."""
    if not name or ".." in name or "\\" in name or name.startswith(("/", ".")):
        return None
    parts = [part for part in name.split("/") if part and part != "."]
    if any(part.startswith(".") for part in parts):
        return None
    if len(parts) == 1:
        return STATIC / parts[0]
    if len(parts) == 2 and parts[0] == "fonts":
        return STATIC / "fonts" / parts[1]
    return None


class Api:
    """Every route is a method returning (status, payload). The handler only parses and serialises."""

    def __init__(self, store: Store, index: WireIndex, runner: Runner) -> None:
        self.store = store
        self.index = index
        self.runner = runner
        self.routes: list[tuple[str, re.Pattern[str], Callable[..., Response]]] = [
            ("GET", re.compile(r"^/api/desks$"), self.list_desks),
            ("POST", re.compile(r"^/api/desks$"), self.create_desk),
            ("GET", re.compile(rf"^/api/desks/{_DESK}$"), self.get_desk),
            ("PUT", re.compile(rf"^/api/desks/{_DESK}$"), self.put_desk),
            ("DELETE", re.compile(rf"^/api/desks/{_DESK}$"), self.delete_desk),
            ("GET", re.compile(rf"^/api/desks/{_DESK}/board$"), self.get_board),
            ("POST", re.compile(rf"^/api/desks/{_DESK}/run$"), self.run_desk),
            ("POST", re.compile(rf"^/api/desks/{_DESK}/ask$"), self.ask),
            ("GET", re.compile(r"^/api/options$"), self.options),
            ("GET", re.compile(r"^/api/settings$"), self.get_settings),
            ("POST", re.compile(r"^/api/settings$"), self.post_settings),
        ]

    def dispatch(self, method: str, path: str, payload: object) -> Response:
        path_known = False
        for verb, pattern, handler in self.routes:
            match = pattern.match(path)
            if match is None:
                continue
            path_known = True
            if verb != method:
                continue
            try:
                return handler(payload=payload, **match.groupdict())
            except LookupError:
                return 404, {"error": "There is no such desk."}
            except ValueError as error:
                return 400, {"error": str(error)}
        if path_known:
            return 405, {"error": "method not allowed"}
        return 404, {"error": "not found"}

    def list_desks(self, payload: object) -> Response:
        return 200, {"desks": [summary(self.store, self.runner, desk) for desk in desks.all_desks(self.store)]}

    def create_desk(self, payload: object) -> Response:
        data = payload if isinstance(payload, dict) else {}
        desk = desks.create(self.store, str(data.get("template") or "blank"), str(data.get("name") or ""))
        clock.retarget(self.store, desk)
        return 201, {"desk": desk.to_json(), "status": summary(self.store, self.runner, desk)}

    def get_desk(self, payload: object, desk: str) -> Response:
        found = desks.get(self.store, desk)
        return 200, {"desk": found.to_json(), "status": summary(self.store, self.runner, found)}

    def put_desk(self, payload: object, desk: str) -> Response:
        saved = desks.replace(self.store, desk, payload)
        clock.retarget(self.store, saved)
        return 200, {"desk": saved.to_json(), "status": summary(self.store, self.runner, saved)}

    def delete_desk(self, payload: object, desk: str) -> Response:
        if self.runner.busy(desk):
            return 409, {"error": "The desk is running. Wait until the run has finished."}
        desks.delete(self.store, desk)
        return 200, {"ok": True}

    def get_board(self, payload: object, desk: str) -> Response:
        return 200, board(self.store, self.index, self.runner, desks.get(self.store, desk))

    def run_desk(self, payload: object, desk: str) -> Response:
        data = payload if isinstance(payload, dict) else {}
        found = desks.get(self.store, desk)
        started = self.runner.start(found, force_jev=bool(data.get("forceJev")), fetch=data.get("fetch", True) is not False)
        if not started:
            return 409, {"ok": False, "reason": "running"}
        return 202, {"ok": True}

    def ask(self, payload: object, desk: str) -> Response:
        question = payload.get("question") if isinstance(payload, dict) else None
        if not isinstance(question, str) or not question.strip():
            return 400, {"error": "question required"}
        found = desks.get(self.store, desk)
        hits = self.index.search(question.strip(), desk=found.id, limit=8)
        return 200, {
            "question": question.strip(),
            "hits": [
                {"id": hit.id, "source": hit.source, "title": hit.title, "snippet": hit.text[:220], "url": hit.url, "score": round(hit.score, 4)}
                for hit in hits
            ],
        }

    def options(self, payload: object) -> Response:
        return 200, {
            "connectors": connectors.describe(),
            "pickers": subjects.describe(),
            "templates": [{"id": item.id, "name": item.name, "summary": item.summary} for item in TEMPLATES],
            "steps": [{"id": step_id, "name": name, "summary": text} for step_id, name, text in STEPS],
            "embedder": embedder_name(),
        }

    def get_settings(self, payload: object) -> Response:
        return 200, settings.view(self.store)

    def post_settings(self, payload: object) -> Response:
        data = payload if isinstance(payload, dict) else {}
        op = data.get("op")
        if op == "secret":
            settings.save_secret(self.store, data.get("key"), data.get("value"))
        elif op == "model":
            settings.save_model(self.store, data.get("value"))
        else:
            raise ValueError("Unknown action.")
        return 200, settings.view(self.store)


class WireHandler(BaseHTTPRequestHandler):
    api: Api

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"wire {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/login":
            self._file(STATIC / "login.html")
            return
        if path.startswith("/static/"):
            target = _static_target(path.removeprefix("/static/"))
            if target is not None:
                self._file(target)
                return
        if path in ("/", "/manage"):
            self._page(STATIC / ("index.html" if path == "/" else "manage.html"))
            return
        self._guarded("GET", path)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/session":
            self._login()
            return
        if path == "/api/register":
            self._register()
            return
        if path == "/api/setup":
            self._setup()
            return
        self._guarded("POST", path)

    def do_PUT(self) -> None:
        self._guarded("PUT", self.path.split("?", 1)[0])

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/session":
            self._logout()
            return
        self._guarded("DELETE", path)

    def _user(self) -> dict[str, object] | None:
        return accounts.user_from_token(self.api.store, accounts.read_cookie(self.headers.get("Cookie", "")))

    def _page(self, path: Path) -> None:
        user = self._user()
        decision = accounts.clearance(user, "GET", self.path.split("?", 1)[0])
        if decision == "login":
            self._redirect("/login?next=" + accounts.safe_next(self.path))
            return
        if decision == "pending":
            self._redirect("/login?pending=1")
            return
        if decision == "forbidden":
            self._redirect("/")
            return
        self._file(path)

    def _guarded(self, method: str, path: str) -> None:
        user = self._user()
        if path == "/api/me":
            if user is None:
                self._json(401, {"setup": accounts.needs_setup(self.api.store)})
            else:
                self._json(200, {"user": user})
            return
        decision = accounts.clearance(user, method, path)
        if decision != "ok":
            self._deny(decision)
            return
        if path == "/api/users" and method == "GET":
            self._json(200, {"users": accounts.list_users(self.api.store)})
            return
        if path == "/api/users" and method == "POST":
            self._change_user(user)
            return
        payload = None if method in ("GET", "DELETE") else self._body()
        self._json(*self.api.dispatch(method, path, payload))

    def _login(self) -> None:
        data = self._body()
        fields = data if isinstance(data, dict) else {}
        try:
            user, token = accounts.open_session(self.api.store, fields.get("email"), fields.get("password"))
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return
        self._json(200, {"user": user}, accounts.cookie_header(token))

    def _register(self) -> None:
        data = self._body()
        fields = data if isinstance(data, dict) else {}
        try:
            accounts.register(self.api.store, fields.get("name"), fields.get("email"), fields.get("password"))
            user, token = accounts.open_session(self.api.store, fields.get("email"), fields.get("password"))
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return
        self._json(201, {"user": user}, accounts.cookie_header(token))

    def _setup(self) -> None:
        data = self._body()
        fields = data if isinstance(data, dict) else {}
        try:
            accounts.setup_admin(self.api.store, fields.get("name"), fields.get("email"), fields.get("password"))
            user, token = accounts.open_session(self.api.store, fields.get("email"), fields.get("password"))
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return
        self._json(201, {"user": user}, accounts.cookie_header(token))

    def _logout(self) -> None:
        accounts.close_session(self.api.store, accounts.read_cookie(self.headers.get("Cookie", "")))
        self._json(200, {"ok": True}, accounts.cookie_header(""))

    def _change_user(self, actor: dict[str, object] | None) -> None:
        data = self._body()
        fields = data if isinstance(data, dict) else {}
        if actor is None:
            self._deny("login")
            return
        try:
            if fields.get("op") == "delete":
                accounts.remove_user(self.api.store, str(actor["id"]), fields.get("id"))
            else:
                accounts.set_status(self.api.store, str(actor["id"]), fields.get("id"), fields.get("status"))
        except LookupError:
            self._json(404, {"error": "There is no such user."})
            return
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return
        self._json(200, {"users": accounts.list_users(self.api.store)})

    def _deny(self, decision: str) -> None:
        if decision == "login":
            self._json(401, {"error": "Please sign in."})
            return
        if decision == "pending":
            self._json(403, {"error": "Your access is waiting for approval."})
            return
        self._json(403, {"error": "Admins only."})

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _body(self) -> object:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, _BODY_LIMIT))
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return {}

    def _json(self, status: int, payload: dict[str, object], cookie: str | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> None:
    load_env(ROOT / ".env")
    data = Path(os.environ.get("WIRE_DATA", str(DEFAULT_DATA)))
    store = Store(data / "wire.sqlite")
    settings.install_secrets(store)
    accounts.ensure_admin(store)
    index = WireIndex.open(data / "qdrant")
    runner = Runner(store, index)
    desks.bootstrap(store)
    clock.start(store, runner)
    host = os.environ.get("WIRE_HOST", "127.0.0.1")
    port = int(os.environ.get("WIRE_PORT", "8787"))
    handler = type("BoundHandler", (WireHandler,), {"api": Api(store, index, runner)})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Wire on http://{host}:{port}  embedder {embedder_name()}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
