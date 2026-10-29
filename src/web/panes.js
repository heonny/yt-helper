import { $, el, setStatus } from "./dom.js";
import { api } from "./api.js";

export let playlists = [];
export let dragFrom = null;
export let activePane = null;
// 삭제 직후에도 서버 목록에 잠시 남아 있을 수 있어 화면에서는 바로 뺀다.
export const deleted = new Set();
const PLAYLIST_GROUPS = [["mine", "내 재생목록"], ["others", "저장한 남의 재생목록 (읽기 전용)"], ["auto", "자동 목록"]];
// 큐에서 이동/삭제를 기다리는 곡은 처리 전이라도 화면에서 숨겨 같은 곡을 다시 고르지 않게 한다.
export const hidden = new Set();
export const hideKey = (playlistId, t) => `${playlistId}|${t.setVideoId}`;
export const titleOf = (id) => (playlists.find((p) => p.id === id) || {}).title || id;
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

  list.ondragover = (e) => {
    if (!dragFrom || dragFrom === p) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = $("move").checked ? "move" : "copy";
    list.classList.add("drop");
  };
  list.ondragleave = (e) => { if (!list.contains(e.relatedTarget)) list.classList.remove("drop"); };
  return p;
}

export const panes = [...document.querySelectorAll(".pane")].map(makePane);
panes[0].other = panes[1];
panes[1].other = panes[0];

export function forgetPlaylists() {
  playlists = [];
  fillSelects();
}

export function fillSelects() {
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

export async function refreshPlaylists() {
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

export async function load(p, id) {
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

export function render(p) {
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
