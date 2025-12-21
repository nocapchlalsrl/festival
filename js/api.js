// js/api.js
function joinUrl(base, path) {
  if (!base) throw new Error("API_BASE_URL 미설정");
  const b = base.endsWith("/") ? base.slice(0, -1) : base;
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${b}${p}`;
}

function getAdminKey() {
  return (localStorage.getItem("adminKey") || "").trim();
}

async function request(method, path, body) {
  const url = joinUrl(window.API_BASE_URL, path);

  const headers = {
    "Accept": "application/json",
    "ngrok-skip-browser-warning": "1",
  };

  const key = getAdminKey();
  if (key) headers["X-Admin-Key"] = key;

  const opts = {
    method,
    mode: "cors",
    headers,
    cache: "no-cache",
  };

  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body ?? {});
  }

  console.log(`[${method}]`, url, body ?? null);

  const res = await fetch(url, opts);
  const text = await res.text();

  if (!res.ok) {
    let detail = text;
    try {
      const j = JSON.parse(text);
      detail = j.error ? j.error : JSON.stringify(j);
    } catch {}
    throw new Error(`HTTP ${res.status} ${res.statusText}\n${detail}`);
  }

  if (!text) return null;
  try { return JSON.parse(text); }
  catch { throw new Error(`JSON 파싱 실패(첫200): ${text.slice(0,200)}`); }
}

async function apiGet(path) { return request("GET", path); }
async function apiPost(path, body) { return request("POST", path, body); }
async function apiPut(path, body) { return request("PUT", path, body); }

window.apiGet = apiGet;
window.apiPost = apiPost;
window.apiPut = apiPut;
