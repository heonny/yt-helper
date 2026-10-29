#!/usr/bin/env python3
"""YouTube Music 재생목록 두 개를 나란히 띄워 곡을 드래그로 복사/이동하는 로컬 웹 GUI.

설치: python3 -m pip install --user -r requirements.txt
실행: ./start.sh 또는 python3 src/server/app.py  (브라우저가 열리고, 처음이면 인증 화면에서 Copy as cURL 내용을 붙여넣는다)
"""
import hmac
import html
import json
import secrets
import signal
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import auth
import playlists

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
PORT = 8765
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# 실행할 때마다 새로 만든다. 브라우저에는 URL 조각(#)으로만 넘겨, 같은 컴퓨터의 다른 계정이나 프로그램이 API를 부르지 못하게 한다.
TOKEN = secrets.token_urlsafe(32)
OPEN_PATH = auth.AUTH_PATH.with_name("open.html")
MAX_BODY = 5_000_000


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_api(self, action, needs_auth=True):
        # Host 검사는 DNS rebinding을, JSON Content-Type 강제는 다른 사이트의 POST(CSRF)를 막는다.
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return self.send_json(403, {"error": "forbidden host"})
        if not hmac.compare_digest(self.headers.get("X-Token", "").encode(), TOKEN.encode()):
            return self.send_json(403, {"error": "접속 토큰이 맞지 않습니다. 서버를 시작할 때 터미널에 나온 주소로 다시 여세요.", "token": True})
        if needs_auth and auth.yt is None:
            return self.send_json(401, {"error": "YouTube Music 인증이 필요합니다.", "auth": True})
        try:
            self.send_json(200, action())
        except Exception as e:
            # 인증이 풀려 YouTube가 HTTP 401을 돌려주면 화면이 인증 창을 다시 띄운다.
            status = 401 if "HTTP 401" in str(e) else 500
            self.send_json(status, {"error": str(e), "auth": status == 401})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in WEB_FILES:
            name, content_type = WEB_FILES[url.path]
            page = (WEB_DIR / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        elif url.path == "/api/auth":
            self.handle_api(auth.auth_status, needs_auth=False)
        elif url.path == "/api/playlists":
            self.handle_api(playlists.list_playlists)
        elif url.path == "/api/tracks":
            playlist_id = parse_qs(url.query).get("id", [""])[0]
            self.handle_api(lambda: playlists.list_tracks(playlist_id))
        else:
            self.send_error(404)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 <= length <= MAX_BODY:
            raise ValueError("요청 크기가 잘못됐습니다.")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("JSON 객체만 받습니다.")
        return body

    def do_POST(self):
        actions = {"/api/auth": auth.save_auth, "/api/logout": auth.logout, "/api/tracks/more": playlists.more_tracks, "/api/create": playlists.create, "/api/add": playlists.add, "/api/remove": playlists.remove, "/api/delete": playlists.delete, "/api/shuffle": playlists.shuffle}
        action = actions.get(self.path)
        if not action:
            return self.send_error(404)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send_json(415, {"error": "JSON only"})
        # 본문은 handle_api 안에서 읽어 Host 검사가 먼저 돌고, 잘못된 본문도 오류 응답으로 돌아가게 한다.
        self.handle_api(lambda: action(self.read_json()), needs_auth=action not in (auth.save_auth, auth.logout))

    def log_message(self, *args):
        pass


def open_browser(url):
    """토큰이 든 주소를 600 권한 파일에 넣고 그 파일을 연다."""
    # Linux의 xdg-open처럼 주소를 명령줄 인자로 받는 경우 다른 계정이 ps로 토큰을 볼 수 있다.
    try:
        auth.write_private(OPEN_PATH, f'<!doctype html><meta http-equiv="refresh" content="0;url={html.escape(url)}">')
    except OSError as e:
        print(f"브라우저를 자동으로 열지 못했습니다. 위 주소를 직접 여세요. ({e})")
        return
    webbrowser.open(OPEN_PATH.as_uri())


def main():
    auth.load_saved_auth()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/#token={TOKEN}"
    print(f"실행 중입니다. 브라우저가 열리지 않으면 이 주소를 여세요. 종료는 Ctrl+C.\n{url}")
    # 터미널 창을 닫거나(SIGHUP) kill로 꺼도 Ctrl+C처럼 아래 finally가 돌아 토큰이 든 파일을 지우게 한다.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.default_int_handler)
    try:
        open_browser(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        OPEN_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
