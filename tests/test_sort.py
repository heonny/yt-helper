import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "server"))
import auth
import playlists


def item(title, playlist_id="PLx", selected=False):
    endpoint = {"playlistId": playlist_id, "actions": [{"action": "ACTION_SET_PLAYLIST_DYNAMIC_SORT_PREFERENCE", "playlistDynamicSortPreference": 1}]}
    return {"title": title, "selected": selected, "serviceEndpoint": {"playlistEditEndpoint": endpoint}}


def response(items):
    shelf = {"header": {"musicSideAlignedItemRenderer": {"startItems": [{"sortFilterSubMenuRenderer": {"subMenuItems": items}}]}}}
    section = {"sectionListRenderer": {"contents": [{"musicPlaylistShelfRenderer": shelf}]}}
    return {"contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": section}}}


class FakeYT:
    def __init__(self, items):
        self.items, self.sent = items, []

    def _send_request(self, endpoint, body):
        if endpoint == "browse":
            return response(self.items)
        self.sent.append(body)
        return {"status": "STATUS_SUCCEEDED"}


def test_sort_menu():
    assert playlists.sort_menu({}) == []
    assert [i["title"] for i in playlists.sort_menu(response([item("제목"), {"title": "모양 바뀜"}, {"serviceEndpoint": {}}]))] == ["제목"]


def test_set_sort():
    auth.yt = FakeYT([item("제목"), item("남의 것", playlist_id="PLother")])
    playlists.set_sort({"playlistId": "PLx", "title": "제목"})
    assert auth.yt.sent == [{"playlistId": "PLx", "actions": item("제목")["serviceEndpoint"]["playlistEditEndpoint"]["actions"]}]
    for title in ("없는 항목", "남의 것"):
        try:
            playlists.set_sort({"playlistId": "PLx", "title": title})
            raise AssertionError(title)
        except RuntimeError:
            pass
    assert len(auth.yt.sent) == 1


if __name__ == "__main__":
    test_sort_menu()
    test_set_sort()
    print("ok")
