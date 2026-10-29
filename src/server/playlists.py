"""재생목록과 곡을 YouTube Music에서 읽고 바꾼다. 로그인된 클라이언트는 auth.yt를 쓴다."""
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

# 아래 셋은 공개 API가 아니다. get_playlist가 다음 페이지 토큰을 돌려주지 않아 직접 쪽 단위로 받으려고 쓴다.
# requirements.txt가 ytmusicapi를 2 미만으로 묶어 두었으니, 올릴 때는 list_tracks와 more_tracks를 다시 확인한다.
from ytmusicapi.continuations import CONTINUATION_ITEMS, get_continuation_token
from ytmusicapi.navigation import CONTENT, SECTION, TWO_COLUMN_RENDERER, nav
from ytmusicapi.parsers.playlists import parse_playlist_items

import auth

PLAYLIST_SHELF = [*TWO_COLUMN_RENDERER, "secondaryContents", *SECTION, *CONTENT, "musicPlaylistShelfRenderer", "contents"]


def list_playlists():
    library = auth.yt.get_library_playlists(limit=None)
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
            return auth.yt.get_playlist(playlist_id, limit=limit)
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
        first = pool.submit(auth.yt._send_request, "browse", {"browseId": "VL" + playlist_id})
        playlist, response = meta.result(), first.result()
    # 남의 재생목록을 보관함에 저장한 경우 owned가 False이고, 수정/삭제는 403으로 거부된다.
    return {"owned": bool(playlist.get("owned")), "total": playlist.get("trackCount"), **track_page(nav(response, PLAYLIST_SHELF, True))}


def more_tracks(body):
    response = auth.yt._send_request("browse", {"continuation": str(body["token"])})
    return track_page(nav(response, CONTINUATION_ITEMS, True))


def ensure_ok(result, what):
    status = result.get("status", "") if isinstance(result, dict) else result
    if "SUCCEEDED" not in str(status):
        raise RuntimeError(f"{what} 실패: {result}")


def create(body):
    new_id = auth.yt.create_playlist(body["title"], "")
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
        ensure_ok(auth.yt.add_playlist_items(body["targetId"], new_ids), "추가")
    if body.get("move"):
        # 추가가 성공한 뒤에만 원본에서 지워 곡을 잃지 않는다.
        ensure_ok(auth.yt.remove_playlist_items(body["sourceId"], tracks), "원본에서 제거")
    return {"added": len(new_ids), "skipped": len(tracks) - len(new_ids)}


def remove(body):
    tracks = [t for t in body["tracks"] if t.get("setVideoId")]
    ensure_ok(auth.yt.remove_playlist_items(body["playlistId"], tracks), "곡 삭제")
    return {"removed": len(tracks)}


def delete(body):
    result = auth.yt.delete_playlist(body["playlistId"])
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
        ensure_ok(auth.yt._send_request("browse/edit_playlist", {"playlistId": playlist_id, "actions": moves[i:i + 100]}), "셔플")
    return {"shuffled": len(current)}
