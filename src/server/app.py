#!/usr/bin/env python3
"""YouTube Music 재생목록 두 개를 나란히 띄워 곡을 드래그로 복사/이동하는 로컬 웹 GUI.

설치: python3 -m pip install --user -r requirements.txt
실행: ./start.sh 또는 python3 src/server/app.py  (브라우저가 열리고, 처음이면 인증 화면에서 Copy as cURL 내용을 붙여넣는다)
"""
import codecs
import hmac
import html
import json
import os
import random
import re
import secrets
import stat
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ytmusicapi
from ytmusicapi import YTMusic
# 아래 셋은 공개 API가 아니다. get_playlist가 다음 페이지 토큰을 돌려주지 않아 직접 쪽 단위로 받으려고 쓴다.
# requirements.txt가 ytmusicapi를 2 미만으로 묶어 두었으니, 올릴 때는 list_tracks와 more_tracks를 다시 확인한다.
from ytmusicapi.continuations import CONTINUATION_ITEMS, get_continuation_token
from ytmusicapi.navigation import CONTENT, SECTION, TWO_COLUMN_RENDERER, nav
from ytmusicapi.parsers.playlists import parse_playlist_items

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "src" / "web"
WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
PORT = 8765
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# 실행할 때마다 새로 만든다. 브라우저에는 URL 조각(#)으로만 넘겨, 같은 컴퓨터의 다른 계정이나 프로그램이 API를 부르지 못하게 한다.
TOKEN = secrets.token_urlsafe(32)
# 프로젝트 폴더 밖에 두어 git이나 폴더 공유로 쿠키가 새지 않게 한다.
AUTH_PATH = Path.home() / ".config" / "yt-helper" / "browser.json"
LEGACY_AUTH_PATH = ROOT / "browser.json"
OPEN_PATH = AUTH_PATH.with_name("open.html")
MAX_BODY = 5_000_000
PLAYLIST_SHELF = [*TWO_COLUMN_RENDERER, "secondaryContents", *SECTION, *CONTENT, "musicPlaylistShelfRenderer", "contents"]
# -H/-b 인자의 값. bash 형식은 '...'이나 $'...', Windows cmd 형식은 ^ 이스케이프를 푼 뒤 "..."로 남는다.
CURL_ARG = re.compile(r"""(-H|--header|-b|--cookie)\s+(?:\$'((?:[^'\\]|\\.)*)'|'([^']*)'|"((?:[^"\\]|\\.)*)")""")

yt = None
account = ""


def login(headers):
    """계정 정보를 읽어 로그인이 살아 있는지 확인하고 클라이언트와 계정 이름을 돌려준다."""
    # 기본값(en)이면 한국 곡도 영문 표기로 온다.
    client = YTMusic(headers, language="ko", location="KR")
    try:
        return client, client.get_account_info().get("accountName", "")
    except Exception as e:
        raise RuntimeError("로그인을 확인하지 못했습니다. music.youtube.com에 로그인한 상태에서 복사했는지 확인하세요.") from e


def use(client, name):
    global yt, account
    yt, account = client, name


def headers_from_curl(text):
    """크롬 Copy as cURL 결과에서 헤더와 쿠키만 뽑아 헤더 원문 형식으로 바꾼다."""
    # 크롬은 요청 본문을 맨 끝에 붙인다. 본문 안의 -H 같은 글자를 헤더로 읽지 않게 잘라 낸다.
    text = re.split(r"\s--data(?:-raw|-binary)?\s", text, maxsplit=1)[0]
    if '^"' in text:
        # Windows cmd 형식은 ^가 바로 다음 글자를 이스케이프하고, 줄 끝의 ^는 줄바꿈을 이어 붙인다.
        text = re.sub(r"\^(.)", r"\1", text, flags=re.S)
    lines = []
    for flag, ansi, single, double in CURL_ARG.findall(text):
        if ansi:
            try:
                value = codecs.decode(ansi, "unicode_escape")
            except UnicodeError:
                value = ansi
        elif double:
            value = re.sub(r"\\(.)", r"\1", double)
        else:
            value = single
        lines.append(value if flag in ("-H", "--header") else f"cookie: {value}")
    return "\n".join(lines)


def write_private(path, text):
    """임시 파일에 600으로 쓴 뒤 바꿔 끼워, 쓰다 실패해도 기존 파일이 남고 권한이 열린 순간이 없게 한다."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # os.open의 0o600은 새로 만드는 파일에만 적용된다. Windows의 Python 3.9에는 fchmod가 없다.
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def auth_status():
    return {"ok": yt is not None, "account": account}


def save_auth(body):
    text = str(body.get("text", "")).strip()
    raw = headers_from_curl(text) if text.startswith("curl") else text
    headers = json.loads(ytmusicapi.setup(headers_raw=raw))
    client, name = login(headers)
    try:
        write_private(AUTH_PATH, json.dumps(headers))
    except OSError as e:
        raise RuntimeError(f"인증 파일을 저장하지 못했습니다. 디스크 공간과 폴더 소유자, 권한을 확인하세요. {e}") from e
    # 저장까지 성공한 뒤에만 바꿔서, 다음 실행 때 읽을 파일과 지금 쓰는 계정이 어긋나지 않게 한다.
    use(client, name)
    return auth_status()


def logout(body):
    """저장된 쿠키 파일을 지우고 연결을 끊는다. Google 쪽 세션은 YouTube에서 로그아웃해야 끝난다."""
    use(None, "")
    AUTH_PATH.unlink(missing_ok=True)
    return auth_status()


def migrate_legacy_auth():
    if not LEGACY_AUTH_PATH.exists():
        return
    try:
        if AUTH_PATH.exists():
            print(f"{AUTH_PATH}가 이미 있어 프로젝트 폴더의 {LEGACY_AUTH_PATH.name}을 지웁니다.")
        else:
            write_private(AUTH_PATH, LEGACY_AUTH_PATH.read_text())
            print(f"인증 파일을 {AUTH_PATH}로 옮겼습니다.")
        LEGACY_AUTH_PATH.unlink()
    except OSError as e:
        print(f"프로젝트 폴더의 {LEGACY_AUTH_PATH.name}을 옮기지 못했습니다. 직접 지워 주세요. ({e})")


def check_private(path, mode):
    """내 소유가 아니면 거부하고, 권한이 mode보다 열려 있으면 경고한 뒤 고친다."""
    # Windows에는 POSIX 소유자와 권한 비트가 없어 사용자 폴더의 기본 접근 권한에 맡긴다.
    if os.name == "nt":
        return
    info = path.stat()
    if info.st_uid != os.getuid():
        raise PermissionError(f"{path}의 소유자가 현재 사용자가 아닙니다. 이 파일은 쓰지 않습니다.")
    current = stat.S_IMODE(info.st_mode)
    if current & ~mode:
        os.chmod(path, mode)
        print(f"경고: {path} 권한이 {oct(current)}여서 {oct(mode)}로 고쳤습니다. "
              "다른 계정이 쿠키를 읽었을 수 있으니, 걱정되면 YouTube에서 로그아웃한 뒤 다시 연결하세요.")


def load_saved_auth():
    migrate_legacy_auth()
    if not AUTH_PATH.exists():
        return
    try:
        check_private(AUTH_PATH.parent, 0o700)
        check_private(AUTH_PATH, 0o600)
        use(*login(json.loads(AUTH_PATH.read_text())))
    except Exception as e:
        print(f"저장된 인증으로 로그인하지 못했습니다. 브라우저의 인증 화면에서 다시 붙여넣으세요. ({e})")


def list_playlists():
    library = yt.get_library_playlists(limit=None)
    # 보관함 목록에는 소유 여부가 없어 PL 재생목록마다 상세를 받아 owned를 확인한다. LM·리캡 같은 자동 목록은 건너뛴다.
    user_ids = [p["playlistId"] for p in library if p["playlistId"].startswith("PL")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        # limit=0이면 첫 페이지만 받는다. limit=1은 첫 페이지 다음 쪽까지 한 번 더 요청한다.
        details = dict(zip(user_ids, pool.map(lambda pid: fetch_playlist(pid, limit=0), user_ids)))
    result = []
    for p in library:
        detail = details.get(p["playlistId"])
        group = "auto" if detail is None else "mine" if detail.get("owned") else "others"
        count = detail.get("trackCount") if detail else p.get("count")
        result.append({"id": p["playlistId"], "title": p["title"], "count": count, "group": group})
    return result


def fetch_playlist(playlist_id, limit=None, attempts=4):
    # 만든 직후 몇 초간은 YouTube가 'contents' 없는 응답을 돌려줘 ytmusicapi가 예외를 던진다.
    for attempt in range(attempts):
        try:
            return yt.get_playlist(playlist_id, limit=limit)
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(1.5)


def simplify(track):
    return {
        "videoId": track.get("videoId"),
        "setVideoId": track.get("setVideoId"),
        "title": track["title"],
        "artists": ", ".join(a["name"] for a in track.get("artists") or []),
        "album": (track.get("album") or {}).get("name", ""),
        "thumb": (track.get("thumbnails") or [{}])[0].get("url", ""),
    }


def track_page(items):
    """한 쪽(약 100곡)의 곡과, 마지막 항목에 붙은 다음 쪽 토큰을 돌려준다."""
    items = items or []
    return {"tracks": [simplify(t) for t in parse_playlist_items(items)], "next": get_continuation_token(items) if items else None}


def list_tracks(playlist_id):
    """첫 쪽과 다음 쪽 토큰, 소유 여부, 전체 곡 수를 돌려준다. 나머지 쪽은 화면이 more_tracks로 이어 받는다."""
    # owned와 trackCount는 get_playlist의 머리글 해석을 그대로 쓰고, 곡과 토큰은 같은 browse 요청을 직접 보내 얻는다.
    with ThreadPoolExecutor(max_workers=2) as pool:
        meta = pool.submit(fetch_playlist, playlist_id, 0)
        first = pool.submit(yt._send_request, "browse", {"browseId": "VL" + playlist_id})
        playlist, response = meta.result(), first.result()
    # 남의 재생목록을 보관함에 저장한 경우 owned가 False이고, 수정/삭제는 403으로 거부된다.
    return {"owned": bool(playlist.get("owned")), "total": playlist.get("trackCount"), **track_page(nav(response, PLAYLIST_SHELF, True))}


def more_tracks(body):
    response = yt._send_request("browse", {"continuation": str(body["token"])})
    return track_page(nav(response, CONTINUATION_ITEMS, True))


def ensure_ok(result, what):
    status = result.get("status", "") if isinstance(result, dict) else result
    if "SUCCEEDED" not in str(status):
        raise RuntimeError(f"{what} 실패: {result}")


def create(body):
    new_id = yt.create_playlist(body["title"], "")
    if not isinstance(new_id, str):
        raise RuntimeError(f"재생목록 생성 실패: {new_id}")
    return {"id": new_id}


def add(body):
    """대상에 이미 있는 곡과 선택 안의 중복을 걸러 추가하고, move면 원본에서 지운다."""
    tracks = [t for t in body["tracks"] if t.get("videoId")]
    existing = {t.get("videoId") for t in fetch_playlist(body["targetId"])["tracks"]}
    new_ids = []
    for t in tracks:
        if t["videoId"] not in existing:
            existing.add(t["videoId"])
            new_ids.append(t["videoId"])
    if new_ids:
        ensure_ok(yt.add_playlist_items(body["targetId"], new_ids), "추가")
    if body.get("move"):
        # 추가가 성공한 뒤에만 원본에서 지워 곡을 잃지 않는다.
        ensure_ok(yt.remove_playlist_items(body["sourceId"], tracks), "원본에서 제거")
    return {"added": len(new_ids), "skipped": len(tracks) - len(new_ids)}


def remove(body):
    tracks = [t for t in body["tracks"] if t.get("setVideoId")]
    ensure_ok(yt.remove_playlist_items(body["playlistId"], tracks), "곡 삭제")
    return {"removed": len(tracks)}


def delete(body):
    result = yt.delete_playlist(body["playlistId"])
    # 삭제 응답에는 status 대신 handlePlaylistDeletionCommand가 온다(2026-10 실계정 응답으로 확인).
    if "handlePlaylistDeletionCommand" not in json.dumps(result):
        ensure_ok(result, "재생목록 삭제")
    return {}


def reorder_moves(current, order):
    """setVideoId 목록 current를 order 순서로 바꾸는 (옮길 곡, 그 뒤에 올 곡) 이동 목록."""
    # 뒤에서부터 각 곡을 order에서 바로 다음 곡 앞으로 옮기면 전체가 order 순서로 맨 앞에 모인다.
    # 마지막 곡은 지금 맨 앞 곡 앞으로 옮기고, 이미 맨 앞이면 건너뛴다.
    successors = order[1:] + current[:1]
    return [(s, n) for s, n in reversed(list(zip(order, successors))) if s != n]


def shuffle(body):
    """곡 순서를 무작위로 섞어 재생목록 자체에 저장한다."""
    playlist_id = body["playlistId"]
    playlist = fetch_playlist(playlist_id)
    if not playlist.get("owned"):
        raise RuntimeError("내 재생목록만 섞을 수 있습니다.")
    current = [t["setVideoId"] for t in playlist["tracks"] if t.get("setVideoId")]
    moves = [{"action": "ACTION_MOVE_VIDEO_BEFORE", "setVideoId": s, "movedSetVideoIdSuccessor": n}
             for s, n in reorder_moves(current, random.sample(current, len(current)))]
    # edit_playlist는 요청 하나에 이동을 하나만 담아 직접 보낸다. 한 요청에 몇 개까지 받는지 몰라 100개씩 나눈다.
    for i in range(0, len(moves), 100):
        ensure_ok(yt._send_request("browse/edit_playlist", {"playlistId": playlist_id, "actions": moves[i:i + 100]}), "셔플")
    return {"shuffled": len(current)}


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
        if needs_auth and yt is None:
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
            self.handle_api(auth_status, needs_auth=False)
        elif url.path == "/api/playlists":
            self.handle_api(list_playlists)
        elif url.path == "/api/tracks":
            playlist_id = parse_qs(url.query).get("id", [""])[0]
            self.handle_api(lambda: list_tracks(playlist_id))
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
        actions = {"/api/auth": save_auth, "/api/logout": logout, "/api/tracks/more": more_tracks, "/api/create": create, "/api/add": add, "/api/remove": remove, "/api/delete": delete, "/api/shuffle": shuffle}
        action = actions.get(self.path)
        if not action:
            return self.send_error(404)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send_json(415, {"error": "JSON only"})
        # 본문은 handle_api 안에서 읽어 Host 검사가 먼저 돌고, 잘못된 본문도 오류 응답으로 돌아가게 한다.
        self.handle_api(lambda: action(self.read_json()), needs_auth=action not in (save_auth, logout))

    def log_message(self, *args):
        pass


def open_browser(url):
    """토큰이 든 주소를 600 권한 파일에 넣고 그 파일을 연다."""
    # Linux의 xdg-open처럼 주소를 명령줄 인자로 받는 경우 다른 계정이 ps로 토큰을 볼 수 있다.
    try:
        write_private(OPEN_PATH, f'<!doctype html><meta http-equiv="refresh" content="0;url={html.escape(url)}">')
    except OSError as e:
        print(f"브라우저를 자동으로 열지 못했습니다. 위 주소를 직접 여세요. ({e})")
        return
    webbrowser.open(OPEN_PATH.as_uri())


def main():
    load_saved_auth()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/#token={TOKEN}"
    print(f"실행 중입니다. 브라우저가 열리지 않으면 이 주소를 여세요. 종료는 Ctrl+C.\n{url}")
    open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        OPEN_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
