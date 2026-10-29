import { $, setStatus } from "./dom.js";
import { api, waitForAuth } from "./api.js";
import { deleted, hidden, hideKey, load, panes, playlists, refreshPlaylists, render, titleOf } from "./panes.js";

// 이 개수 이상을 이동/삭제할 때만 확인 창을 띄운다. 한 곡씩 빠르게 처리할 때는 묻지 않는다.
const BULK_CONFIRM = 10;
export const queue = [];
const progress = { done: 0, total: 0, failed: 0, lastError: "" };
let draining = false;
let currentLabel = "";

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

export function transfer(from, to) {
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

export function removeSelected(p) {
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

export function deletePlaylist(p) {
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

export function shufflePlaylist(p) {
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

export function createPlaylist(p) {
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
