const GOOGLE_CLIENT_ID =
  "818713072122-g3aln31415tecrea2hv2nj72htd1ckna.apps.googleusercontent.com";

const RECAPTCHA_SITE_KEY =
  "6Le6UVgtAAAAAFsyBML3xCvwmV_KidonmaQqfsA5";

window.__recaptchaLoaded = false;

window.onRecaptchaLoaded = function () {
  window.__recaptchaLoaded = true;
  window.dispatchEvent(new Event("recaptcha-loaded"));
};

window.APP_CONFIG = {
  API_BASE_URL:
    "https://smart-inventory-sales-monitoring-system.onrender.com",

  GOOGLE_AUTH_API_BASE_URL:
    "https://smart-inventory-sales-monitoring-system.onrender.com",

  GOOGLE_CLIENT_ID,
  RECAPTCHA_SITE_KEY,
  RECAPTCHA_ENABLED: true
};
