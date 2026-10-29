const $ = (id) => document.getElementById(id);
let playlists = [];
let dragFrom = null;
let activePane = null;
// 삭제 직후에도 서버 목록에 잠시 남아 있을 수 있어 화면에서는 바로 뺀다.
const deleted = new Set();
const PLAYLIST_GROUPS = [["mine", "내 재생목록"], ["others", "저장한 남의 재생목록 (읽기 전용)"], ["auto", "자동 목록"]];
// 이 개수 이상을 이동/삭제할 때만 확인 창을 띄운다. 한 곡씩 빠르게 처리할 때는 묻지 않는다.
const BULK_CONFIRM = 10;
const queue = [];
const progress = { done: 0, total: 0, failed: 0, lastError: "" };
let draining = false;
let currentLabel = "";
// 큐에서 이동/삭제를 기다리는 곡은 처리 전이라도 화면에서 숨겨 같은 곡을 다시 고르지 않게 한다.
const hidden = new Set();
const hideKey = (playlistId, t) => `${playlistId}|${t.setVideoId}`;

// 서버가 시작할 때 URL 조각(#token=...)으로 넘긴 접속 토큰. 조각은 HTTP 요청에 실리지 않는다.
// 새로 고침과 다른 탭에서도 쓰도록 저장하고, 주소창과 방문 기록에서는 바로 지운다.
const tokenFromUrl = new URLSearchParams(location.hash.slice(1)).get("token");
let token = tokenFromUrl || "";
try {
  if (tokenFromUrl) localStorage.setItem("token", tokenFromUrl);
  else token = localStorage.getItem("token") || "";
} catch {}
if (location.hash) history.replaceState(null, "", location.pathname);

async function api(path, body) {
  const headers = { "X-Token": token };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, body ? { method: "POST", headers, body: JSON.stringify(body) } : { headers });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (res.ok) return data;
  const error = new Error(data.error);
  error.auth = res.status === 401 && data.auth;
  if (error.auth) {
    renderAuth({ kind: "expired" });
    openAuth(data.error);
  }
  throw error;
}

// 인증이 풀려 멈춘 큐 작업은 다시 연결될 때까지 기다렸다가 이어서 처리한다.
let authWaiters = [];
const waitForAuth = () => new Promise((resolve) => authWaiters.push(resolve));

const AUTH_LABELS = {
  checking: () => "인증 확인 중…",
  ok: (account) => `${account || "YouTube Music"} 연결됨`,
  none: () => "연결 안 됨",
  expired: () => "인증 만료",
  unknown: () => "인증 확인 실패",
};
let authState = { kind: "checking", account: "" };

// 위쪽 상태 버튼과 연결 창의 계정 안내, 인증 해제 버튼을 같은 상태로 맞춘다.
function renderAuth(state) {
  if (state) authState = state;
  const { kind, account } = authState;
  const pill = $("authOpen");
  pill.className = `auth-pill ${kind}`;
  pill.lastChild.textContent = AUTH_LABELS[kind](account);
  pill.title = kind === "ok" ? "연결 정보 보기, 인증 해제" : "눌러서 YouTube Music에 연결";
  const saved = kind === "ok" || kind === "expired";
  $("authAccount").hidden = !saved;
  $("authAccount").textContent = kind === "ok"
    ? `현재 ${account || "YouTube Music"} 계정으로 연결되어 있습니다.`
    : "저장된 인증이 만료됐습니다. 새로 붙여넣으세요.";
  $("authLogout").hidden = !saved;
}

function openAuth(message) {
  $("authError").textContent = message || "";
  renderAuth();
  if ($("auth").open) return;
  $("authText").value = "";
  $("auth").showModal();
}

// 닫힌 창의 입력칸에 쿠키가 남지 않게 지운다. Esc로 닫는 경우는 아래 close 이벤트가 맡는다.
function closeAuth() {
  $("authText").value = "";
  $("auth").close();
}

$("authOpen").onclick = () => openAuth();
$("authCancel").onclick = closeAuth;
$("authLogout").onclick = async () => {
  if (!confirm("인증을 해제할까요? 저장된 로그인 쿠키 파일을 지웁니다.")) return;
  try {
    await api("/api/logout", {});
  } catch (e) {
    $("authError").textContent = e.message;
    return;
  }
  renderAuth({ kind: "none" });
  playlists = [];
  fillSelects();
  await Promise.all(panes.map((p) => load(p, "")));
  $("authError").textContent = "인증을 해제했습니다. 다시 쓰려면 새로 붙여넣으세요.";
  setStatus("인증 해제됨");
};
$("auth").addEventListener("close", () => ($("authText").value = ""));
$("authForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("authText").value.trim();
  if (!text) return $("authText").focus();
  $("authSubmit").disabled = true;
  $("authError").textContent = "확인 중…";
  let account;
  try {
    ({ account } = await api("/api/auth", { text }));
  } catch (err) {
    $("authError").textContent = err.message;
    return;
  } finally {
    $("authSubmit").disabled = false;
  }
  renderAuth({ kind: "ok", account });
  closeAuth();
  setStatus(`${account || "YouTube Music"} 계정으로 연결됨`);
  authWaiters.splice(0).forEach((resolve) => resolve());
  refreshPlaylists().then(restoreView).catch((e) => setStatus("오류: " + e.message));
};

const setStatus = (text) => ($("status").textContent = text);
const el = (tag, props) => Object.assign(document.createElement(tag), props);
const titleOf = (id) => (playlists.find((p) => p.id === id) || {}).title || id;
const collator = new Intl.Collator("ko", { numeric: true, sensitivity: "base" });

function makePane(root) {
  root.append($("paneTpl").content.cloneNode(true));
  const q = (s) => root.querySelector(s);
  const p = { id: "", owned: false, tracks: [], selected: new Set(), visible: [], rows: [], last: null, sort: { key: "", dir: 1 }, loadSeq: 0, loading: null, q };
  const list = q(".list");
  p.root = root;
  root.addEventListener("mousedown", () => (activePane = p));

  // 정렬은 화면 표시 순서만 바꾼다. 클릭할 때마다 오름차순, 내림차순, 원래 순서로 돈다.
  root.querySelectorAll(".sort").forEach((button) => (button.onclick = () => {
    const { key, dir } = p.sort;
    p.sort = key !== button.dataset.key ? { key: button.dataset.key, dir: 1 } : dir === 1 ? { key, dir: -1 } : { key: "", dir: 1 };
    p.last = null;
    render(p);
  }));
  q(".pl").onchange = () => load(p, q(".pl").value).catch((e) => setStatus("오류: " + e.message));
  q(".search").oninput = () => { p.last = null; render(p); };
  q(".only").onchange = () => { p.last = null; render(p); };
  q(".all").onclick = () => { p.visible.forEach((t) => p.selected.add(t.i)); paint(p); };
  q(".clear").onclick = () => { p.selected.clear(); paint(p); };
  q(".new").onclick = () => createPlaylist(p);
  q(".send").onclick = () => transfer(p, p.other);
  q(".rm").onclick = () => removeSelected(p);
  q(".del").onclick = () => deletePlaylist(p);
  q(".shuffle").onclick = () => shufflePlaylist(p);

  list.ondragover = (e) => {
    if (!dragFrom || dragFrom === p) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = $("move").checked ? "move" : "copy";
    list.classList.add("drop");
  };
  list.ondragleave = (e) => { if (!list.contains(e.relatedTarget)) list.classList.remove("drop"); };
  list.ondrop = (e) => { e.preventDefault(); list.classList.remove("drop"); transfer(dragFrom, p); };
  return p;
}

const panes = [...document.querySelectorAll(".pane")].map(makePane);
panes[0].other = panes[1];
panes[1].other = panes[0];

function fillSelects() {
  for (const p of panes) {
    const sel = p.q(".pl");
    sel.replaceChildren(el("option", { value: "", textContent: "재생목록 선택" }));
    for (const [group, label] of PLAYLIST_GROUPS) {
      const items = playlists.filter((pl) => (pl.group || "mine") === group);
      if (!items.length) continue;
      const optgroup = el("optgroup", { label });
      for (const pl of items) {
        optgroup.append(el("option", { value: pl.id, textContent: pl.count != null ? `${pl.title} (${pl.count})` : pl.title }));
      }
      sel.append(optgroup);
    }
    sel.value = p.id;
  }
}

async function refreshPlaylists() {
  const fresh = (await api("/api/playlists")).filter((pl) => !deleted.has(pl.id));
  // 방금 만든 재생목록은 서버 목록에 늦게 나타날 수 있어, 열려 있는 것은 유지한다.
  for (const p of panes) {
    if (p.id && !fresh.some((pl) => pl.id === p.id)) fresh.push(playlists.find((pl) => pl.id === p.id) || { id: p.id, title: p.id });
  }
  playlists = fresh;
  fillSelects();
}

function setLoading(p, done, total) {
  p.loading = done == null ? null : { done, total };
  const bar = p.q(".loadbar");
  bar.hidden = !p.loading;
  if (p.loading && total) {
    bar.max = total;
    bar.value = done;
  } else {
    bar.removeAttribute("value");
  }
  paint(p);
}

async function load(p, id) {
  const sameList = id === p.id;
  const scroll = sameList ? p.q(".list").scrollTop : 0;
  // 불러오는 도중 다른 재생목록을 고르거나 다시 불러오면, 앞선 불러오기는 결과를 버리고 멈춘다.
  const seq = ++p.loadSeq;
  p.id = id;
  p.last = null;
  if (!sameList) { p.selected.clear(); p.tracks = []; p.owned = false; render(p); p.q(".list").scrollTop = 0; }
  if (!id) { setLoading(p, null); return render(p.other); }
  setLoading(p, 0, null);
  try {
    const first = await api("/api/tracks?id=" + encodeURIComponent(id));
    if (p.loadSeq !== seq) return;
    const tracks = first.tracks;
    const apply = () => {
      // 큐 처리 중에도 다음 곡을 고르고 있을 수 있어, 다시 불러와도 선택은 setVideoId 기준으로 유지한다. 인덱스는 바뀔 수 있다.
      const keep = new Set([...p.selected].map((i) => p.tracks[i] && p.tracks[i].setVideoId));
      tracks.forEach((t, i) => (t.i = i));
      p.selected = new Set(tracks.filter((t) => keep.has(t.setVideoId)).map((t) => t.i));
      p.tracks = tracks;
      p.owned = first.owned;
      render(p);
    };
    // 처음 여는 목록은 100곡씩 받는 대로 보여 주고, 보던 목록을 다시 받을 때는 다 받은 뒤 한 번에 바꿔 화면이 비지 않게 한다.
    if (!sameList) apply();
    setLoading(p, tracks.length, first.total);
    for (let next = first.next; next; ) {
      const page = await api("/api/tracks/more", { token: next });
      if (p.loadSeq !== seq) return;
      if (!page.tracks.length) break;
      tracks.push(...page.tracks);
      next = page.next;
      if (!sameList) apply();
      setLoading(p, tracks.length, first.total);
    }
    apply();
    render(p.other);
    if (sameList) p.q(".list").scrollTop = scroll;
  } finally {
    if (p.loadSeq === seq) setLoading(p, null);
  }
}

function render(p) {
  // 드래그 중에 노드를 바꾸면 드래그가 끊기므로 dragend 때 그린다.
  if (dragFrom) { p.stale = true; return; }
  const q = p.q(".search").value.trim().toLowerCase();
  const only = p.q(".only").checked;
  const otherIds = new Set(p.other.tracks.map((t) => t.videoId));
  p.shown = p.tracks.filter((t) => !hidden.has(hideKey(p.id, t)));
  p.visible = p.shown.filter((t) =>
    (!only || p.selected.has(t.i)) &&
    (!q || `${t.title} ${t.artists} ${t.album}`.toLowerCase().includes(q)));
  const { key, dir } = p.sort;
  if (key) p.visible.sort((a, b) => collator.compare(a[key], b[key]) * dir);
  p.root.querySelectorAll(".sort").forEach((button) => {
    const on = button.dataset.key === key;
    button.classList.toggle("on", on);
    button.textContent = button.dataset.label + (on ? (dir === 1 ? " ▲" : " ▼") : "");
  });

  p.rows = p.visible.map((t, pos) => {
    const dup = otherIds.has(t.videoId);
    const name = el("div", { className: "name", textContent: t.title });
    if (dup) name.append(el("span", { className: "tag", textContent: "반대쪽에 있음" }));
    const row = el("div", { className: "row" + (dup ? " dup" : ""), draggable: true });
    row.append(
      el("input", { type: "checkbox", tabIndex: -1 }),
      el("img", { src: t.thumb, loading: "lazy", alt: "" }),
      name,
      el("div", { className: "artist", textContent: t.artists }));
    row.onclick = (e) => toggle(p, pos, e.shiftKey);
    row.ondragstart = (e) => startDrag(p, t, e);
    row.ondragend = endDrag;
    return row;
  });

  const list = p.q(".list");
  if (p.rows.length) list.replaceChildren(...p.rows);
  else list.replaceChildren(el("div", { className: "empty", textContent: !p.id ? "재생목록을 고르세요" : p.shown.length ? "검색 결과 없음" : "빈 재생목록 (반대쪽에서 곡을 드래그하세요)" }));
  paint(p);
}

function paint(p) {
  p.rows.forEach((row, pos) => {
    const on = p.selected.has(p.visible[pos].i);
    row.classList.toggle("on", on);
    row.firstChild.checked = on;
  });
  const count = p.loading ? `${p.loading.done}/${p.loading.total || "?"}곡 불러오는 중` : `${(p.shown || p.tracks).length}곡`;
  p.q(".info").textContent = p.id ? `${count} · 선택 ${p.selected.size}` + (p.owned ? "" : " · 읽기 전용") : "";
  p.q(".send").disabled = !p.selected.size;
  p.q(".rm").disabled = !p.owned || !p.selected.size;
  // LM(좋아요), 리캡(LR…), 에피소드 목록 같은 시스템 목록은 지울 수 없게 PL로 시작하는 내 재생목록만 허용한다.
  p.q(".del").disabled = !p.owned || !p.id.startsWith("PL");
  p.q(".shuffle").disabled = !p.owned || !!p.loading || p.tracks.length < 2;
}

function toggle(p, pos, range) {
  const on = !p.selected.has(p.visible[pos].i);
  const [from, to] = range && p.last != null ? [Math.min(p.last, pos), Math.max(p.last, pos)] : [pos, pos];
  for (let k = from; k <= to; k++) on ? p.selected.add(p.visible[k].i) : p.selected.delete(p.visible[k].i);
  p.last = pos;
  paint(p);
}

function startDrag(p, track, e) {
  // 선택 안 된 곡을 잡으면 그 곡만 옮긴다. 드래그 중 노드를 바꾸면 드래그가 끊기므로 render 대신 paint.
  if (!p.selected.has(track.i)) { p.selected = new Set([track.i]); paint(p); }
  dragFrom = p;
  const badge = $("dragBadge");
  badge.textContent = `${p.selected.size}곡`;
  e.dataTransfer.setDragImage(badge, 0, 0);
  e.dataTransfer.effectAllowed = "copyMove";
  e.dataTransfer.setData("text/plain", badge.textContent);
}

function endDrag() {
  dragFrom = null;
  for (const p of panes) if (p.stale) { p.stale = false; render(p); }
}

function enqueue(job) {
  queue.push(job);
  (job.hide || []).forEach((k) => hidden.add(k));
  progress.total++;
  if (draining) showProgress();
  else drain();
}

async function drain() {
  if (draining) return;
  draining = true;
  while (queue.length) {
    const finished = [];
    const dirty = new Set();
    while (queue.length) {
      const job = queue[0];
      currentLabel = job.label;
      showProgress();
      try {
        setStatus(await job.work());
      } catch (e) {
        if (e.auth) {
          currentLabel = "인증 대기 중… 연결 창에서 다시 붙여넣으세요";
          showProgress();
          await waitForAuth();
          continue;
        }
        progress.failed++;
        progress.lastError = `오류 (${job.label}): ${e.message}`;
        setStatus(progress.lastError);
      }
      (job.dirty || []).forEach((id) => dirty.add(id));
      finished.push(job);
      queue.shift();
      progress.done++;
    }
    // 곡 목록과 재생목록 목록(약 2초)은 작업마다가 아니라 큐가 빌 때 한 번만 새로 받는다.
    currentLabel = "목록 새로 고치는 중…";
    showProgress();
    try {
      await Promise.all(panes.filter((p) => dirty.has(p.id)).map((p) => load(p, p.id)));
      await refreshPlaylists();
    } catch (e) {
      setStatus("오류 (목록 새로 고침): " + e.message);
    }
    finished.forEach((job) => (job.hide || []).forEach((k) => hidden.delete(k)));
    panes.forEach(render);
  }
  draining = false;
  showProgress();
}

function showProgress() {
  const { done, total, failed } = progress;
  $("queue").hidden = false;
  $("qbar").max = total;
  $("qbar").value = done;
  if (draining) {
    $("qtext").textContent = `${Math.min(done + 1, total)}/${total} · ${currentLabel}`;
    return;
  }
  $("qtext").textContent = `완료 ${total}건` + (failed ? ` (실패 ${failed})` : "");
  // 실패 메시지가 뒤이은 작업의 성공 메시지에 덮이지 않게 끝날 때 다시 보여준다.
  if (failed) setStatus(progress.lastError + (failed > 1 ? ` 외 ${failed - 1}건` : ""));
  Object.assign(progress, { done: 0, total: 0, failed: 0, lastError: "" });
  setTimeout(() => { if (!draining) $("queue").hidden = true; }, 4000);
}

const pickedOf = (p) => p.tracks.filter((t) => p.selected.has(t.i)).map(({ videoId, setVideoId }) => ({ videoId, setVideoId }));

function transfer(from, to) {
  if (!from || from === to || !from.selected.size) return;
  if (!to.id) return setStatus("반대쪽에서 넣을 재생목록을 먼저 고르세요.");
  if (from.id === to.id) return setStatus("같은 재생목록끼리는 보낼 수 없습니다.");
  const tracks = pickedOf(from);
  const move = $("move").checked;
  const [fromId, toId, fromName, toName] = [from.id, to.id, titleOf(from.id), titleOf(to.id)];
  if (!to.owned) return setStatus(`"${toName}"은 내 재생목록이 아니라 곡을 넣을 수 없습니다.`);
  if (move && !from.owned) return setStatus(`"${fromName}"은 내 재생목록이 아니라 곡을 뺄 수 없습니다. 이동 모드를 끄고 복사하세요.`);
  if (move && tracks.length >= BULK_CONFIRM && !confirm(`"${fromName}"에서 ${tracks.length}곡을 빼서 "${toName}"로 옮길까요?`)) return;
  from.selected.clear();
  enqueue({
    label: `"${toName}"에 ${tracks.length}곡 ${move ? "이동" : "추가"}`,
    dirty: move ? [toId, fromId] : [toId],
    hide: move ? tracks.map((t) => hideKey(fromId, t)) : [],
    work: async () => {
      const r = await api("/api/add", { targetId: toId, sourceId: fromId, move, tracks });
      return `"${toName}"에 ${r.added}곡 추가` + (r.skipped ? `, 이미 있는 ${r.skipped}곡 제외` : "") + (move ? `, "${fromName}"에서 ${tracks.length}곡 제거` : "");
    },
  });
  panes.forEach(render);
}

function removeSelected(p) {
  if (!p.selected.size) return;
  if (!p.owned) return setStatus(`"${titleOf(p.id)}"은 내 재생목록이 아니라 곡을 삭제할 수 없습니다.`);
  const tracks = pickedOf(p);
  const [id, name] = [p.id, titleOf(p.id)];
  if (tracks.length >= BULK_CONFIRM && !confirm(`"${name}"에서 선택한 ${tracks.length}곡을 삭제할까요?`)) return;
  p.selected.clear();
  enqueue({
    label: `"${name}"에서 ${tracks.length}곡 삭제`,
    dirty: [id],
    hide: tracks.map((t) => hideKey(id, t)),
    work: async () => `"${name}"에서 ${(await api("/api/remove", { playlistId: id, tracks })).removed}곡 삭제`,
  });
  panes.forEach(render);
}

function deletePlaylist(p) {
  if (!p.id) return;
  const [id, name] = [p.id, titleOf(p.id)];
  if (!confirm(`재생목록 "${name}" (${p.tracks.length}곡)을 삭제할까요?\n삭제하면 되돌릴 수 없습니다.`)) return;
  enqueue({
    label: `재생목록 "${name}" 삭제`,
    work: async () => {
      await api("/api/delete", { playlistId: id });
      deleted.add(id);
      await Promise.all(panes.filter((pane) => pane.id === id).map((pane) => load(pane, "")));
      return `재생목록 "${name}" 삭제 완료`;
    },
  });
}

function shufflePlaylist(p) {
  if (!p.owned) return setStatus(`"${titleOf(p.id)}"은 내 재생목록이 아니라 섞을 수 없습니다.`);
  const [id, name] = [p.id, titleOf(p.id)];
  if (!confirm(`재생목록 "${name}" (${(p.shown || p.tracks).length}곡)의 순서를 무작위로 섞을까요?\n지금 순서는 되돌릴 수 없습니다.`)) return;
  // 화면 정렬이 켜져 있으면 섞인 순서가 보이지 않아 원래 순서 보기로 돌린다.
  p.sort = { key: "", dir: 1 };
  render(p);
  enqueue({
    label: `"${name}" 셔플`,
    dirty: [id],
    work: async () => `"${name}" ${(await api("/api/shuffle", { playlistId: id })).shuffled}곡 셔플 완료`,
  });
}

function createPlaylist(p) {
  const title = (prompt("새 재생목록 이름") || "").trim();
  if (!title) return;
  enqueue({
    label: `"${title}" 만들기`,
    work: async () => {
      const { id } = await api("/api/create", { title });
      playlists.push({ id, title, count: 0, group: "mine" });
      p.id = "";
      await load(p, id);
      return `"${title}" 생성 완료. 반대쪽에서 곡을 드래그해 넣으세요.`;
    },
  });
}

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
