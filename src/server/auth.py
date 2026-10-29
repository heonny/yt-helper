"""YouTube Music 로그인 쿠키를 붙여넣은 cURL에서 뽑아 내 계정만 읽을 수 있게 저장하고, 로그인 상태를 들고 있는다."""
import codecs
import json
import os
import re
import stat
from pathlib import Path

import ytmusicapi
from ytmusicapi import YTMusic

# 프로젝트 폴더 밖에 두어 git이나 폴더 공유로 쿠키가 새지 않게 한다.
AUTH_PATH = Path.home() / ".config" / "yt-helper" / "browser.json"
LEGACY_AUTH_PATH = Path(__file__).resolve().parents[2] / "browser.json"
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
