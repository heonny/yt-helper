#!/bin/sh
# macOS Finder에서 더블클릭하면 터미널 창에서 실행된다. 창을 닫으면 서버도 꺼진다.
exec "$(dirname "$0")/start.sh"
