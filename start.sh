#!/bin/sh
# yt-helper 서버를 켠다. 의존성이 없으면 먼저 설치한다.
cd "$(dirname "$0")" || exit 1
python3 -c "import ytmusicapi" 2>/dev/null || python3 -m pip install --user -r requirements.txt || exit 1
# macOS 기본 Python의 NotOpenSSLWarning만 숨긴다.
exec python3 -W "ignore:::urllib3" src/server/app.py
