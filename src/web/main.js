import { $, setStatus } from "./dom.js";
import { api } from "./api.js";
import { authState, initAuthDialog, openAuth, renderAuth } from "./auth-dialog.js";
import { activePane, dragFrom, forgetPlaylists, load, panes, playlists, refreshPlaylists } from "./panes.js";
import { createPlaylist, deletePlaylist, queue, removeSelected, shufflePlaylist, transfer } from "./queue.js";

// panes.js와 queue.js가 서로 import하지 않도록 재생목록을 바꾸는 버튼과 드롭은 여기서 잇는다.
for (const p of panes) {
  p.q(".new").onclick = () => createPlaylist(p);
  p.q(".send").onclick = () => transfer(p, p.other);
  p.q(".rm").onclick = () => removeSelected(p);
  p.q(".del").onclick = () => deletePlaylist(p);
  p.q(".shuffle").onclick = () => shufflePlaylist(p);
  const list = p.q(".list");
  list.ondrop = (e) => { e.preventDefault(); list.classList.remove("drop"); transfer(dragFrom, p); };
}

initAuthDialog({
  onLogout: async () => {
    forgetPlaylists();
    await Promise.all(panes.map((p) => load(p, "")));
  },
  onConnected: () => refreshPlaylists().then(restoreView).catch((e) => setStatus("오류: " + e.message)),
});

function setLayout(col) {
  $("panes").classList.toggle("col", col);
  $("layout").textContent = col ? "좌우로 보기" : "위아래로 보기";
  try { localStorage.setItem("layout", col ? "col" : "row"); } catch {}
}
async function refreshAll() {
  setStatus("갱신 중…");
  try {
    // 같은 재생목록을 다시 불러오는 load는 스크롤과 선택을 유지한다.
    await Promise.all(panes.filter((p) => p.id).map((p) => load(p, p.id)));
    await refreshPlaylists();
    setStatus("갱신 완료");
  } catch (e) {
    setStatus("오류: " + e.message);
  }
}
$("refresh").onclick = refreshAll;

// Cmd+R로 새로 고쳐도 보던 재생목록, 검색어, 정렬, 스크롤 위치로 돌아오도록 탭 단위로 저장한다.
addEventListener("pagehide", () => {
  const view = panes.map((p) => ({ id: p.id, search: p.q(".search").value, scroll: p.q(".list").scrollTop, sort: p.sort }));
  try { sessionStorage.setItem("view", JSON.stringify(view)); } catch {}
});

async function restoreView() {
  let saved = [];
  try { saved = JSON.parse(sessionStorage.getItem("view")) || []; } catch {}
  await Promise.all(panes.map(async (p, k) => {
    const view = saved[k];
    if (!view || !playlists.some((pl) => pl.id === view.id)) return;
    p.q(".search").value = view.search || "";
    if (view.sort) p.sort = view.sort;
    p.q(".pl").value = view.id;
    await load(p, view.id);
    p.q(".list").scrollTop = view.scroll || 0;
  }));
}

// 큐에 남은 작업은 이 페이지 메모리에만 있어서 새로 고치면 보내지 못하고 사라진다.
addEventListener("beforeunload", (e) => {
  if (!queue.length) return;
  e.preventDefault();
  e.returnValue = "";
});

// 맥 키보드의 delete 키는 Backspace로 들어온다. 검색창 등에서 글자를 지울 때는 무시한다.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Delete" && e.key !== "Backspace") return;
  if (e.target.closest("input, select, textarea") || !activePane) return;
  e.preventDefault();
  removeSelected(activePane);
});

$("layout").onclick = () => setLayout(!$("panes").classList.contains("col"));
try { setLayout(localStorage.getItem("layout") === "col"); } catch {}

api("/api/auth")
  .then((auth) => {
    renderAuth(auth.ok ? { kind: "ok", account: auth.account } : { kind: "none" });
    if (!auth.ok) return openAuth();
    return refreshPlaylists().then(restoreView);
  })
  .catch((e) => {
    if (authState.kind === "checking") renderAuth({ kind: "unknown" });
    setStatus("오류: " + e.message);
  });
