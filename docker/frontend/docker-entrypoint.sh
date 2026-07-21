#!/bin/sh
set -eu

cat > /usr/share/nginx/html/assets/js/config.js <<EOF
const GOOGLE_CLIENT_ID = "${GOOGLE_CLIENT_ID:-}";
const RECAPTCHA_SITE_KEY = "${RECAPTCHA_SITE_KEY:-}";

window.__recaptchaLoaded = false;
window.onRecaptchaLoaded = function () {
  window.__recaptchaLoaded = true;
  window.dispatchEvent(new Event("recaptcha-loaded"));
};

window.APP_CONFIG = {
  API_BASE_URL: "${API_BASE_URL:-http://localhost:8080}",
  GOOGLE_AUTH_API_BASE_URL: "${GOOGLE_AUTH_API_BASE_URL:-${API_BASE_URL:-http://localhost:8080}}",
  GOOGLE_CLIENT_ID,
  RECAPTCHA_SITE_KEY,
  RECAPTCHA_ENABLED: ${RECAPTCHA_ENABLED:-true}
};
EOF
