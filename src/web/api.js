// 서버가 시작할 때 URL 조각(#token=...)으로 넘긴 접속 토큰. 조각은 HTTP 요청에 실리지 않는다.
// 새로 고침과 다른 탭에서도 쓰도록 저장하고, 주소창과 방문 기록에서는 바로 지운다.
const tokenFromUrl = new URLSearchParams(location.hash.slice(1)).get("token");
let token = tokenFromUrl || "";
try {
  if (tokenFromUrl) localStorage.setItem("token", tokenFromUrl);
  else token = localStorage.getItem("token") || "";
} catch {}
if (location.hash) history.replaceState(null, "", location.pathname);

export async function api(path, body) {
  const headers = { "X-Token": token };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, body ? { method: "POST", headers, body: JSON.stringify(body) } : { headers });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (res.ok) return data;
  const error = new Error(data.error);
  error.auth = res.status === 401 && data.auth;
  // 인증 창은 auth-dialog.js가 맡는다. 서로 import하지 않도록 이벤트로 알린다.
  if (error.auth) dispatchEvent(new CustomEvent("authexpired", { detail: data.error }));
  throw error;
}

// 인증이 풀려 멈춘 큐 작업은 다시 연결될 때까지 기다렸다가 이어서 처리한다.
let authWaiters = [];
export const waitForAuth = () => new Promise((resolve) => authWaiters.push(resolve));
export const authRestored = () => authWaiters.splice(0).forEach((resolve) => resolve());
