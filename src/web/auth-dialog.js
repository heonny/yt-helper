import { $, setStatus } from "./dom.js";
import { api, authRestored } from "./api.js";

const AUTH_LABELS = {
  checking: () => "인증 확인 중…",
  ok: (account) => `${account || "YouTube Music"} 연결됨`,
  none: () => "연결 안 됨",
  expired: () => "인증 만료",
  unknown: () => "인증 확인 실패",
};
export let authState = { kind: "checking", account: "" };

// 위쪽 상태 버튼과 연결 창의 계정 안내, 인증 해제 버튼을 같은 상태로 맞춘다.
export function renderAuth(state) {
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

export function openAuth(message) {
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

// 로그아웃 뒤와 다시 연결된 뒤에 화면을 어떻게 바꿀지는 main.js가 정한다.
export function initAuthDialog({ onLogout, onConnected }) {
  addEventListener("authexpired", (e) => {
    renderAuth({ kind: "expired" });
    openAuth(e.detail);
  });
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
    await onLogout();
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
    authRestored();
    onConnected();
  };
}
