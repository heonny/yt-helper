#!/usr/bin/env python3
"""YouTube Music 재생목록 두 개를 나란히 띄워 곡을 드래그로 복사/이동하는 로컬 웹 GUI.

설치: python3 -m pip install --user -r requirements.txt
실행: python3 app.py  (브라우저가 열리고, 처음이면 인증 화면에서 Copy as cURL 내용을 붙여넣는다)
"""
import codecs
import json
import os
import re
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ytmusicapi
from ytmusicapi import YTMusic

HERE = Path(__file__).parent
PORT = 8765
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# 프로젝트 폴더 밖에 두어 git이나 폴더 공유로 쿠키가 새지 않게 한다.
AUTH_PATH = Path.home() / ".config" / "yt-helper" / "browser.json"
LEGACY_AUTH_PATH = HERE / "browser.json"
MAX_BODY = 5_000_000
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
        raise RuntimeError("인증 파일을 저장하지 못했습니다. 디스크 공간과 ~/.config 폴더 권한을 확인하세요.") from e
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


def load_saved_auth():
    migrate_legacy_auth()
    if not AUTH_PATH.exists():
        return
    try:
        os.chmod(AUTH_PATH, 0o600)
        use(*login(json.loads(AUTH_PATH.read_text())))
    except Exception as e:
        print(f"저장된 인증으로 로그인하지 못했습니다. 브라우저의 인증 화면에서 다시 붙여넣으세요. ({e})")


def list_playlists():
    library = yt.get_library_playlists(limit=None)
    # 보관함 목록에는 소유 여부가 없어 PL 재생목록마다 상세를 받아 owned를 확인한다. LM·리캡 같은 자동 목록은 건너뛴다.
    user_ids = [p["playlistId"] for p in library if p["playlistId"].startswith("PL")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        details = dict(zip(user_ids, pool.map(lambda pid: fetch_playlist(pid, limit=1), user_ids)))
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


def list_tracks(playlist_id):
    playlist = fetch_playlist(playlist_id)
    # 남의 재생목록을 보관함에 저장한 경우 owned가 False이고, 수정/삭제는 403으로 거부된다.
    return {"owned": bool(playlist.get("owned")), "tracks": [
        {
            "videoId": t.get("videoId"),
            "setVideoId": t.get("setVideoId"),
            "title": t["title"],
            "artists": ", ".join(a["name"] for a in t.get("artists") or []),
            "album": (t.get("album") or {}).get("name", ""),
            "thumb": (t.get("thumbnails") or [{}])[0].get("url", ""),
        }
        for t in playlist["tracks"]
    ]}


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
        if url.path == "/":
            page = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
        actions = {"/api/auth": save_auth, "/api/logout": logout, "/api/create": create, "/api/add": add, "/api/remove": remove, "/api/delete": delete}
        action = actions.get(self.path)
        if not action:
            return self.send_error(404)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send_json(415, {"error": "JSON only"})
        # 본문은 handle_api 안에서 읽어 Host 검사가 먼저 돌고, 잘못된 본문도 오류 응답으로 돌아가게 한다.
        self.handle_api(lambda: action(self.read_json()), needs_auth=action not in (save_auth, logout))

    def log_message(self, *args):
        pass


def main():
    load_saved_auth()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"{url} 에서 실행 중 (종료: Ctrl+C)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
