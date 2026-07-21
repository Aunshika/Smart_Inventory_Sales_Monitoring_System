const GOOGLE_CLIENT_ID = "818713072122-g3aln31415tecrea2hv2nj72htd1ckna.apps.googleusercontent.com";
const RECAPTCHA_SITE_KEY = "PASTE_YOUR_RECAPTCHA_SITE_KEY_HERE";

window.__recaptchaLoaded = false;
window.onRecaptchaLoaded = function () {
  window.__recaptchaLoaded = true;
  window.dispatchEvent(new Event("recaptcha-loaded"));
};

window.APP_CONFIG = {
  API_BASE_URL: "http://localhost:8080",
  GOOGLE_AUTH_API_BASE_URL: "http://localhost:8080",
  GOOGLE_CLIENT_ID,
  RECAPTCHA_SITE_KEY,
  RECAPTCHA_ENABLED: true
};
