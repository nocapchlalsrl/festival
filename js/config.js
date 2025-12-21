// js/config.js
// ✅ Vultr + Nginx + Gunicorn 환경에서 가장 안전한 설정:
// - 현재 접속한 도메인(origin)을 기준으로 API를 자동으로 잡음
// - 즉, https://your-domain.com 접속이면 API는 https://your-domain.com/api/v1

(() => {
  const origin = window.location.origin.replace(/\/$/, "");
  window.API_BASE_URL = origin + "/api/v1";
})();
