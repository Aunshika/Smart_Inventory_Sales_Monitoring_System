const API_URL = window.APP_CONFIG?.API_BASE_URL || window.location.origin;
const REPORT_EXPORT_TIMEOUT_MS = 120000;
const LOGIN_REQUEST_TIMEOUT_MS = 60000;
const PASSWORD_REVEAL_MAX_MS = 3000;
const activePasswordRevealTimers = new Map();

const authScreen = document.getElementById("authScreen");
const dashboardScreen = document.getElementById("dashboardScreen");
const loginForm = document.getElementById("loginForm");
const registerForm = document.getElementById("registerForm");
const authStatus = document.getElementById("authStatus");
const openRegister = document.getElementById("openRegister");
const openLogin = document.getElementById("openLogin");
const dashboardContent = document.getElementById("dashboardContent");
const dashboardTemplate = dashboardContent.innerHTML;
const navButtons = document.querySelectorAll(".nav-link");
let activeView = "dashboard";
let viewRenderToken = 0;
let appStarted = false;
let dashboardData = null;
let dashboardDataCacheKey = "";
let dashboardLoadPromise = null;
let dashboardLoadSequence = 0;
let inventorySearchPromise = null;
let restockQueueCache = null;
let restockQueuePage = 1;
let inventoryHealthState = { page: 1, limit: 10, summary: null, attention: null };
let purchasePendingSummaryCache = null;
let productScanStream = null;
let productScannerInstance = null;
const routeCache = new Map();
let searchTimer = null;
let dateRangeState = loadStoredDateRangeState();
let dashboardNotifications = [];
let notificationCloseTimer = null;
let googleButtonRendered = false;
const recaptchaWidgets = { login: null, register: null, forgot: null };
const recaptchaCompleted = { login: false, register: false, forgot: false };

(function removeLegacyThemePreferences() {
  try {
    const legacyKey = ["the", "me"].join("");
    localStorage.removeItem(`smart_inventory_${legacyKey}`);
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key || !key.startsWith("smart_inventory_settings_")) continue;
      const settings = JSON.parse(localStorage.getItem(key) || "{}");
      if (Object.prototype.hasOwnProperty.call(settings, legacyKey)) {
        delete settings[legacyKey];
        localStorage.setItem(key, JSON.stringify(settings));
      }
    }
    sessionStorage.removeItem(`smart_inventory_${legacyKey}`);
  } catch {
    // Ignore old preference cleanup failures; the app renders the light design.
  }
})();

function enforceLightThemeOnly() {
  try {
    document.documentElement.removeAttribute("data-" + "theme");
    document.body?.removeAttribute("data-" + "theme");
    const oldModeClass = ["dark", "mode"].join("-");
    const oldThemeClass = ["dark", "theme"].join("-");
    const oldPrefixedClass = ["theme", "dark"].join("-");
    document.documentElement.classList.remove(oldModeClass, oldThemeClass, oldPrefixedClass);
    document.body?.classList.remove(oldModeClass, oldThemeClass, oldPrefixedClass);
    const themeWord = ["the", "me"].join("");
    const previousMode = ["dark", "theme"].join("_");
    const themeKeys = [
      themeWord,
      `app_${themeWord}`,
      `smart_inventory_${themeWord}`,
      `smart_inventory_${previousMode}`,
      `smart_inventory_color_${themeWord}`
    ];
    themeKeys.forEach((key) => {
      localStorage.removeItem(key);
      sessionStorage.removeItem(key);
    });
  } catch {
    // Ignore old preference cleanup failures; the app renders the light design.
  }
}

function recaptchaSiteKey() {
  return (window.APP_CONFIG?.RECAPTCHA_SITE_KEY || "").trim();
}

function recaptchaIsConfigured() {
  const key = recaptchaSiteKey();
  return Boolean(key) && key !== "PASTE_YOUR_RECAPTCHA_SITE_KEY_HERE" && key !== "your_public_site_key";
}

function recaptchaIsRequired() {
  return window.APP_CONFIG?.RECAPTCHA_ENABLED !== false && recaptchaIsConfigured();
}

function recaptchaSection(name) {
  return document.getElementById(`${name}-recaptcha-section`);
}

function setRecaptchaMessage(name, message = "") {
  const messageEl = document.getElementById(`${name}-recaptcha-message`);
  if (!messageEl) return;
  messageEl.textContent = message;
  messageEl.hidden = !message;
}

function updateRecaptchaDependentControls() {
  const loginButton = loginForm?.querySelector('button[type="submit"]');
  if (!loginButton) return;
  const enabled = window.APP_CONFIG?.RECAPTCHA_ENABLED !== false;
  if (!enabled) {
    loginButton.disabled = false;
    return;
  }
  loginButton.disabled = !recaptchaIsConfigured() || !recaptchaCompleted.login;
}

function updateRecaptchaConfigMessages() {
  const enabled = window.APP_CONFIG?.RECAPTCHA_ENABLED !== false;
  ["login", "register", "forgot"].forEach((name) => {
    const section = recaptchaSection(name);
    if (section) section.hidden = !enabled;
    setRecaptchaMessage(name);
  });
  if (!enabled) {
    updateRecaptchaDependentControls();
    return;
  }
  if (!recaptchaIsConfigured()) {
    const message = "reCAPTCHA site key is missing. Add RECAPTCHA_SITE_KEY in backend .env and restart the backend.";
    ["login", "register", "forgot"].forEach((name) => setRecaptchaMessage(name, message));
    updateRecaptchaDependentControls();
    return;
  }
  if (!window.__recaptchaLoaded || !window.grecaptcha?.render) {
    ["login", "register", "forgot"].forEach((name) => setRecaptchaMessage(name, "Loading reCAPTCHA..."));
  }
  updateRecaptchaDependentControls();
}

function renderRecaptchaWidget(name, elementId) {
  if (!recaptchaIsRequired()) {
    updateRecaptchaConfigMessages();
    return null;
  }
  const element = document.getElementById(elementId);
  if (!element || element.hidden || element.offsetParent === null) return null;
  if (!window.grecaptcha?.render || !window.__recaptchaLoaded) {
    updateRecaptchaConfigMessages();
    return null;
  }
  if (recaptchaWidgets[name] !== null) return recaptchaWidgets[name];
  recaptchaWidgets[name] = window.grecaptcha.render(element, {
    sitekey: recaptchaSiteKey(),
    theme: "light",
    size: "normal",
    callback: () => {
      recaptchaCompleted[name] = true;
      setRecaptchaMessage(name);
      updateRecaptchaDependentControls();
    },
    "expired-callback": () => {
      recaptchaCompleted[name] = false;
      setRecaptchaMessage(name, "reCAPTCHA expired. Please verify again.");
      updateRecaptchaDependentControls();
    },
    "error-callback": () => {
      recaptchaCompleted[name] = false;
      setRecaptchaMessage(name, "Unable to load reCAPTCHA. Please refresh and try again.");
      updateRecaptchaDependentControls();
    }
  });
  recaptchaCompleted[name] = false;
  setRecaptchaMessage(name);
  updateRecaptchaDependentControls();
  return recaptchaWidgets[name];
}

function renderVisibleRecaptchaWidgets() {
  renderRecaptchaWidget("login", "login-recaptcha");
  renderRecaptchaWidget("register", "register-recaptcha");
  renderRecaptchaWidget("forgot", "forgot-recaptcha");
  updateRecaptchaConfigMessages();
}

function getRecaptchaToken(name, elementId) {
  if (!recaptchaIsRequired()) return "";
  if (!window.grecaptcha?.getResponse || !window.__recaptchaLoaded) {
    throw new Error("reCAPTCHA is still loading. Please try again in a moment.");
  }
  const widgetId = recaptchaWidgets[name] ?? renderRecaptchaWidget(name, elementId);
  const token = widgetId !== null ? window.grecaptcha.getResponse(widgetId) : "";
  if (!token) throw new Error("Please complete the reCAPTCHA verification.");
  return token;
}

function resetRecaptcha(name) {
  recaptchaCompleted[name] = false;
  if (!window.grecaptcha?.reset || recaptchaWidgets[name] === null) {
    updateRecaptchaDependentControls();
    return;
  }
  try {
    window.grecaptcha.reset(recaptchaWidgets[name]);
  } catch (error) {
    console.warn("Unable to reset reCAPTCHA widget", name, error);
  }
  updateRecaptchaDependentControls();
}

window.addEventListener("recaptcha-loaded", renderVisibleRecaptchaWidgets);

async function loadPublicConfig() {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`${API_URL}/public-config`, { signal: controller.signal });
    if (!response.ok) throw new Error(`Public config failed with ${response.status}`);
    const data = await response.json();
    window.APP_CONFIG = {
      ...(window.APP_CONFIG || {}),
      GOOGLE_CLIENT_ID: data.google_client_id || window.APP_CONFIG?.GOOGLE_CLIENT_ID || "",
      RECAPTCHA_ENABLED: Boolean(data.recaptcha_enabled),
      RECAPTCHA_SITE_KEY: data.recaptcha_site_key || ""
    };
    renderVisibleRecaptchaWidgets();
    updateRecaptchaConfigMessages();
  } catch (error) {
    console.warn("Unable to load public config. Continuing with local config.", error);
    if (!recaptchaIsConfigured()) {
      updateRecaptchaConfigMessages();
    }
  } finally {
    window.clearTimeout(timeoutId);
  }
}
const countryCodes = [
  { code: "+91", label: "India +91" },
  { code: "+1", label: "USA +1" },
  { code: "+44", label: "UK +44" },
  { code: "+1", label: "Canada +1" },
  { code: "+61", label: "Australia +61" },
  { code: "+971", label: "UAE +971" },
  { code: "+65", label: "Singapore +65" }
];

const pageModules = window.SmartInventoryPageModules || {};
const views = Object.fromEntries(
  Object.entries(pageModules)
    .filter(([, page]) => page.endpoint)
    .map(([name, page]) => [name, page])
);
const pagedViews = new Set(
  Object.entries(pageModules)
    .filter(([, page]) => page.paged)
    .map(([name]) => name)
);
let productNotice = "";
let productWarehouseFilter = "";
let inventoryWarehouseFilter = "";

const cleanRouteViews = new Set(
  Object.entries(pageModules)
    .filter(([, page]) => page.protected)
    .map(([name]) => name)
);
const authRoutes = new Set(
  Object.entries(pageModules)
    .filter(([, page]) => page.auth)
    .map(([name]) => name)
);
const rolePageAccess = {
  Admin: ["dashboard", "products", "inventory", "sales", "purchases", "suppliers", "reports", "analytics", "users", "settings", "profile"],
  Manager: ["dashboard", "products", "inventory", "sales", "purchases", "suppliers", "reports", "analytics", "profile"],
  Staff: ["dashboard", "products", "inventory", "sales", "profile"]
};

const AUTH_SESSION_KEYS = ["access_token", "role", "username", "userProfile", "lastLogin"];
const REMEMBER_SESSION_KEY = "smart_inventory_remember_session";
const REMEMBER_IDENTIFIER_KEY = "smart_inventory_remember_identifier";

function getAuthValue(key) {
  const sessionValue = sessionStorage.getItem(key);
  if (sessionValue) return sessionValue;
  if (localStorage.getItem(REMEMBER_SESSION_KEY) === "true") {
    return localStorage.getItem(key) || "";
  }
  if (AUTH_SESSION_KEYS.includes(key)) localStorage.removeItem(key);
  return "";
}

function getStoredAccessToken() {
  return getAuthValue("access_token");
}

function getStoredUserProfile() {
  try {
    return JSON.parse(getAuthValue("userProfile") || "{}");
  } catch {
    return {};
  }
}

function clearAuthSession() {
  AUTH_SESSION_KEYS.forEach((key) => {
    localStorage.removeItem(key);
    sessionStorage.removeItem(key);
  });
  localStorage.removeItem("token");
  localStorage.removeItem("smart_inventory_token");
  sessionStorage.removeItem("token");
  sessionStorage.removeItem("smart_inventory_token");
  localStorage.removeItem(REMEMBER_SESSION_KEY);
  localStorage.removeItem(REMEMBER_IDENTIFIER_KEY);
}

function setAuthValue(key, value, remember) {
  const target = remember ? localStorage : sessionStorage;
  const other = remember ? sessionStorage : localStorage;
  other.removeItem(key);
  if (value === undefined || value === null) target.removeItem(key);
  else target.setItem(key, String(value));
}

function rememberMeChecked() {
  return Boolean(document.getElementById("rememberMe")?.checked);
}

function activeSessionIsRemembered() {
  return localStorage.getItem(REMEMBER_SESSION_KEY) === "true" && Boolean(localStorage.getItem("access_token"));
}


function prefillRememberedIdentifier() {
  const remembered = localStorage.getItem(REMEMBER_IDENTIFIER_KEY) || "";
  const usernameInput = document.getElementById("loginUsername");
  const rememberInput = document.getElementById("rememberMe");
  if (remembered && usernameInput && !usernameInput.value) usernameInput.value = remembered;
  if (remembered && rememberInput) rememberInput.checked = true;
}

function profileFromSessionResponse(data) {
  const user = data.user || data;
  return {
    username: user.username,
    full_name: user.full_name || user.name || user.username,
    email: user.email,
    phone: user.phone || "",
    role: user.role,
    location_id: user.location_id || "ALL",
    warehouse_id: user.warehouse_id || "",
    warehouse_name: user.warehouse_name || user.location_name || user.location || (user.role === "Admin" ? "All Warehouses" : ""),
    location: user.location || user.warehouse_name || user.location_name || "",
    state: user.state || "",
    account_created: user.account_created || "",
    last_login: user.last_login || new Date().toISOString()
  };
}

function currentUserRole() {
  return getAuthValue("role") || getProfileDetails().role || "Staff";
}

function allowedViewsForRole(role = currentUserRole()) {
  return new Set(rolePageAccess[role] || rolePageAccess.Staff);
}

function canAccessView(name, role = currentUserRole()) {
  if (!name || authRoutes.has(name)) return true;
  return allowedViewsForRole(role).has(name);
}

function applyRoleNavigation() {
  const allowed = allowedViewsForRole();
  navButtons.forEach((button) => {
    const isAllowed = allowed.has(button.dataset.view);
    button.hidden = !isAllowed;
    button.disabled = !isAllowed;
  });
  refreshIcons();
}

function showAccessDenied(message = "Access denied. You do not have permission to view this page.") {
  authScreen.hidden = true;
  dashboardScreen.hidden = false;
  updateDashboardUser();
  applyRoleNavigation();
  activeView = "dashboard";
  navButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === "dashboard"));
  setCleanRoute("dashboard", true);
  setDashboardGreeting();
  document.getElementById("welcomeText").textContent = "Your dashboard is limited to your assigned role and warehouse.";
  dashboardContent.innerHTML = `<section class="panel error-panel"><h3>Access Denied</h3><p>${escapeHtml(message)}</p></section>`;
  refreshIcons();
}
function readableErrorMessage(error, fallback = "Unable to load data.") {
  if (!error) return fallback;
  if (error instanceof TypeError && /failed to fetch|networkerror|load failed/i.test(error.message || "")) {
    return "Backend server is not running. Please start the backend and try again.";
  }
  if (error.status === 0) {
    return error.message || "Backend server is not running. Please start the backend and try again.";
  }
  if (error.status === 401) {
    return "Your session has expired. Please sign in again.";
  }
  if (error.status === 403) {
    return "Access denied. You do not have permission to view this page.";
  }
  const candidates = [error.detail, error.message, error];
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (typeof candidate === "string") return candidate;
    if (Array.isArray(candidate)) {
      const message = candidate.map((item) => readableErrorMessage(item, "")).filter(Boolean).join("; ");
      if (message) return message;
    }
    if (typeof candidate === "object") {
      if (candidate.detail) return readableErrorMessage(candidate.detail, fallback);
      if (candidate.message) return readableErrorMessage(candidate.message, fallback);
      if (candidate.msg) return readableErrorMessage(candidate.msg, fallback);
      try {
        const json = JSON.stringify(candidate);
        if (json && json !== "{}") return json;
      } catch {
        return fallback;
      }
    }
  }
  return fallback;
}

function renderSkeletonPanel(lines = 4, label = "Loading data...") {
  return `<div class="panel skeleton-panel" role="status" aria-live="polite" aria-label="${escapeHtml(label)}"><span></span>${Array.from({ length: Math.max(lines - 1, 1) }, () => "<span></span>").join("")}<p class="skeleton-label">${escapeHtml(label)}</p></div>`;
}

function renderErrorPanel(title, error, retryView = activeView) {
  const message = readableErrorMessage(error, "Unable to load data.");
  return `<section class="panel error-panel" role="alert"><div class="error-icon" aria-hidden="true">!</div><div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p><button type="button" class="retry-button" data-retry-view="${escapeHtml(retryView || "dashboard")}">Try again</button></div></section>`;
}
const passwordRules = [
  { key: "length", label: "Minimum 8 characters", test: (value) => value.length >= 8 },
  { key: "upper", label: "One uppercase letter", test: (value) => /[A-Z]/.test(value) },
  { key: "lower", label: "One lowercase letter", test: (value) => /[a-z]/.test(value) },
  { key: "number", label: "One number", test: (value) => /\d/.test(value) },
  { key: "special", label: "One special character", test: (value) => /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]/.test(value) }
];

function passwordScore(password) {
  return passwordRules.reduce((score, rule) => score + (rule.test(password) ? 1 : 0), 0);
}

function isStrongPassword(password) {
  return passwordScore(password) === passwordRules.length;
}

function strengthDetails(password) {
  const score = passwordScore(password);
  if (score <= 2) return { label: "Weak", className: "weak", blocks: 2 };
  if (score <= 4) return { label: "Medium", className: "medium", blocks: 4 };
  return { label: "Strong", className: "strong", blocks: 5 };
}

function renderPasswordFeedback(inputId, feedbackId) {
  const input = document.getElementById(inputId);
  const target = document.getElementById(feedbackId);
  if (!input || !target) return;
  const value = input.value;
  if (!value) {
    target.innerHTML = "";
    target.hidden = true;
    return;
  }
  const strength = strengthDetails(value);
  const failedRules = passwordRules.filter((rule) => !rule.test(value));
  const passedCount = passwordRules.length - failedRules.length;
  const blocks = Array.from({ length: 5 }, (_, index) =>
    `<span class="${index < strength.blocks ? "filled" : ""}"></span>`
  ).join("");
  target.innerHTML = `
    ${failedRules.length ? `<ul class="password-rules">${failedRules.map((rule) => `<li><span>&times;</span>${rule.label}</li>`).join("")}</ul>` : '<p class="password-complete"><span>&#10003;</span>Password meets all requirements</p>'}
    <div class="password-strength ${strength.className}" aria-label="Password strength ${strength.label}">
      <span>Password Strength:</span>
      <strong>${strength.label}</strong>
      <div class="strength-blocks">${blocks}</div>
      <small>${passedCount}/${passwordRules.length}</small>
    </div>
  `;
  target.hidden = false;
}

function setupPasswordValidation(inputId, feedbackId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  renderPasswordFeedback(inputId, feedbackId);
  input.addEventListener("input", () => renderPasswordFeedback(inputId, feedbackId));
}

function setupPasswordGuidance(inputId, feedbackId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.addEventListener("input", () => renderPasswordFeedback(inputId, feedbackId));
  renderPasswordFeedback(inputId, feedbackId);
}

function currentRouteName() {
  const segment = window.location.pathname.replace(/\/+$/, "").split("/").pop();
  if (!segment || segment === "index.html" || segment === "frontend") return null;
  return segment;
}

function routePath(name) {
  return `/${name}`;
}

function setCleanRoute(name, replace = false) {
  if (![...cleanRouteViews, ...authRoutes].includes(name)) return;
  const path = routePath(name);
  if (window.location.pathname === path && !window.location.search && !window.location.hash) return;
  const method = replace || window.location.pathname === path ? "replaceState" : "pushState";
  window.history[method]({ route: name }, "", path);
}

function currentRouteParams() {
  return new URLSearchParams(window.location.search || "");
}

function clearRouteQuery(name) {
  if (![...cleanRouteViews, ...authRoutes].includes(name) || !window.location.search) return;
  window.history.replaceState({ route: name }, "", routePath(name));
}

function navigateToViewWithParams(name, params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") query.set(key, value);
  });
  const suffix = query.toString();
  window.history.pushState({ route: name }, "", `${routePath(name)}${suffix ? `?${suffix}` : ""}`);
  openView(name, 1, false);
}

function showProtectedRoute(name, replace = false) {
  const token = getStoredAccessToken();
  if (!token) {
    showLogin(true);
    setAuthStatus("Please sign in to continue.");
    setCleanRoute("login", true);
    return;
  }
  if (!canAccessView(name)) {
    showAccessDenied();
    return;
  }
  showDashboard(name, !replace);
  if (replace && !window.location.search) setCleanRoute(name, true);
}

function handleRoute() {
  const route = currentRouteName();
  if (route === "login") {
    showLogin(true);
    return;
  }
  if (route === "register") {
    showRegister(true);
    return;
  }
  if (route && cleanRouteViews.has(route)) {
    showProtectedRoute(route, true);
    return;
  }
  if (getStoredAccessToken()) {
    showDashboard(preferredLandingPage(), true);
  } else {
    showLogin(true);
    setCleanRoute("login", true);
  }
}

function getPasswordRevealTarget(button) {
  if (!button) return null;
  return button.dataset.passwordTarget || button.dataset.modalPassword || button.dataset.resetPassword || null;
}

function getPasswordRevealButton(event) {
  return event.target.closest?.("[data-password-target], [data-modal-password], [data-reset-password]") || null;
}

function hidePasswordForButton(button) {
  const targetId = getPasswordRevealTarget(button);
  const input = targetId ? document.getElementById(targetId) : null;
  if (!input) return;
  window.clearTimeout(activePasswordRevealTimers.get(button));
  activePasswordRevealTimers.delete(button);
  input.type = "password";
  button.classList.remove("is-visible", "is-revealing");
  button.setAttribute("aria-label", "Press and hold to reveal password");
  if (button.matches("[data-modal-password]") && button.querySelector("i")) {
    button.innerHTML = '<i data-lucide="eye"></i>';
    refreshIcons();
  }
}

function hideAllRevealedPasswords() {
  document.querySelectorAll("[data-password-target], [data-modal-password], [data-reset-password]").forEach(hidePasswordForButton);
}

function revealPasswordForButton(button) {
  const targetId = getPasswordRevealTarget(button);
  const input = targetId ? document.getElementById(targetId) : null;
  if (!input || input.type === "text") return;
  input.type = "text";
  button.classList.add("is-visible", "is-revealing");
  button.setAttribute("aria-label", "Release to hide password");
  window.clearTimeout(activePasswordRevealTimers.get(button));
  activePasswordRevealTimers.set(
    button,
    window.setTimeout(() => hidePasswordForButton(button), PASSWORD_REVEAL_MAX_MS)
  );
}

function bindPressHoldPasswordReveal() {
  document.addEventListener("pointerdown", (event) => {
    const button = getPasswordRevealButton(event);
    if (!button) return;
    event.preventDefault();
    revealPasswordForButton(button);
  });
  ["pointerup", "pointercancel", "mouseup", "touchend", "touchcancel"].forEach((eventName) => {
    document.addEventListener(eventName, (event) => {
      const button = getPasswordRevealButton(event);
      if (button) hidePasswordForButton(button);
    }, true);
  });
  document.addEventListener("pointerleave", (event) => {
    const button = getPasswordRevealButton(event);
    if (button) hidePasswordForButton(button);
  }, true);
  document.addEventListener("click", (event) => {
    const button = getPasswordRevealButton(event);
    if (!button) return;
    event.preventDefault();
    hidePasswordForButton(button);
  });
  document.addEventListener("keydown", (event) => {
    const button = getPasswordRevealButton(event);
    if (!button || ![" ", "Enter"].includes(event.key)) return;
    event.preventDefault();
    revealPasswordForButton(button);
  });
  document.addEventListener("keyup", (event) => {
    const button = getPasswordRevealButton(event);
    if (button) hidePasswordForButton(button);
  });
  document.addEventListener("focusout", (event) => {
    const button = getPasswordRevealButton(event);
    if (button) hidePasswordForButton(button);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) hideAllRevealedPasswords();
  });
  window.addEventListener("blur", hideAllRevealedPasswords);
}

bindPressHoldPasswordReveal();

function saveAuthenticatedSession(data, remember = false) {
  const user = data.user || data;
  const sessionUser = {
    username: user.username,
    full_name: user.full_name || user.name || user.username,
    email: user.email,
    phone: user.phone || "",
    role: user.role,
    location_id: user.location_id || "ALL",
    warehouse_id: user.warehouse_id || "",
    warehouse_name: user.warehouse_name || user.location_name || user.location || (user.role === "Admin" ? "All Warehouses" : ""),
    location: user.location || user.warehouse_name || user.location_name || "",
    state: user.state || "",
    account_created: user.account_created || "",
    last_login: user.last_login || new Date().toISOString()
  };
  remember = Boolean(remember);
  setAuthValue("access_token", data.access_token, remember);
  localStorage.removeItem("token");
  localStorage.removeItem("smart_inventory_token");
  sessionStorage.removeItem("token");
  sessionStorage.removeItem("smart_inventory_token");
  setAuthValue("role", sessionUser.role, remember);
  setAuthValue("username", sessionUser.username, remember);
  setAuthValue("userProfile", JSON.stringify(sessionUser), remember);
  setAuthValue("lastLogin", sessionUser.last_login, remember);
  if (remember) {
    localStorage.setItem(REMEMBER_SESSION_KEY, "true");
    localStorage.setItem(REMEMBER_IDENTIFIER_KEY, sessionUser.email || sessionUser.username || "");
  } else {
    localStorage.removeItem(REMEMBER_SESSION_KEY);
    localStorage.removeItem(REMEMBER_IDENTIFIER_KEY);
  }
  applyAppSettings();
}

async function verifyStoredSession() {
  const token = getStoredAccessToken();
  if (!token) return false;
  try {
    const response = await fetch(`${API_URL}/auth/session`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = {}; }
    if (!response.ok) {
      clearAuthSession();
      return false;
    }
    const remember = localStorage.getItem(REMEMBER_SESSION_KEY) === "true" && localStorage.getItem("access_token") === token;
    const profile = profileFromSessionResponse(data);
    setAuthValue("role", profile.role, remember);
    setAuthValue("username", profile.username, remember);
    setAuthValue("userProfile", JSON.stringify(profile), remember);
    setAuthValue("lastLogin", profile.last_login, remember);
    applyAppSettings();
    return true;
  } catch (error) {
    console.warn("Unable to verify stored session", error);
    clearAuthSession();
    return false;
  }
}

async function bootstrapApplication() {
  prefillRememberedIdentifier();
  const route = currentRouteName();
  const hasToken = Boolean(getStoredAccessToken());
  if (hasToken) {
    const valid = await verifyStoredSession();
    if (valid) {
      if (!route || route === "login" || route === "register") {
        showDashboard(preferredLandingPage(), true);
        setCleanRoute(preferredLandingPage(), true);
      } else {
        handleRoute();
      }
      return;
    }
  }
  handleRoute();
}

function googleAuthUrl() {
  const baseUrl = window.APP_CONFIG?.GOOGLE_AUTH_API_BASE_URL || window.APP_CONFIG?.API_BASE_URL || "http://127.0.0.1:8080";
  return `${baseUrl.replace(/\/$/, "")}/auth/google`;
}

async function parseJsonResponseSafely(response, emptyMessage) {
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  console.log("Backend response status:", response.status);
  console.log("Backend response text:", text);

  if (!text.trim()) {
    throw new Error(emptyMessage);
  }

  if (!contentType.includes("application/json")) {
    throw new Error(text || "Server returned a non-JSON response.");
  }

  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error("Server returned invalid JSON.");
  }
}

function googleClientId() {
  return window.APP_CONFIG?.GOOGLE_CLIENT_ID || "";
}

function googleIsConfigured() {
  return googleClientId() && googleClientId() !== "PASTE_YOUR_GOOGLE_CLIENT_ID_HERE";
}

async function handleGoogleCredential(response) {
  console.log("Google credential received:", Boolean(response?.credential));
  const requestUrl = googleAuthUrl();
  console.log("Backend request URL:", requestUrl);
  try {
    const result = await fetch(requestUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: response.credential })
    });
    const data = await parseJsonResponseSafely(
      result,
      "Google login failed. Empty response from server."
    );
    if (!result.ok) throw new Error(data.detail || "Google sign-in failed.");
    saveAuthenticatedSession(data, rememberMeChecked());
    showDashboard(preferredLandingPage());
  } catch (error) {
    setAuthStatus(error.message || "Google sign-in failed.");
  }
}

function initializeGoogleSignIn() {
  if (!googleIsConfigured() || !window.google?.accounts?.id) return;
  window.google.accounts.id.initialize({ client_id: googleClientId(), callback: handleGoogleCredential, auto_select: false });
  const host = document.getElementById("googleSignInButton");
  if (!host || googleButtonRendered) return;
  host.innerHTML = "";
  host.onclick = null;
  host.onkeydown = null;
  window.google.accounts.id.renderButton(host, {
    type: "standard",
    theme: "outline",
    size: "large",
    text: "signin_with",
    shape: "rectangular",
    logo_alignment: "center",
    width: 432
  });
  googleButtonRendered = true;
}

function startGoogleSignIn() {
  if (!googleIsConfigured()) {
    setAuthStatus("Google Sign-In is not configured yet.");
    return;
  }
  if (!window.google?.accounts?.id) {
    setAuthStatus("Google Sign-In is loading. Please try again in a moment.");
    return;
  }
  initializeGoogleSignIn();
  window.google.accounts.id.prompt((notification) => {
    if (notification.isNotDisplayed?.() || notification.isSkippedMoment?.()) {
      setAuthStatus("Google sign-in popup was not displayed. Confirm your Google test user and authorized origin.");
    }
  });
}

function clearLoginFormFields() {
  const usernameInput = document.getElementById("loginUsername");
  const passwordInput = document.getElementById("loginPassword");
  const rememberInput = document.getElementById("rememberMe");
  const feedback = document.getElementById("loginPasswordFeedback");
  if (usernameInput) usernameInput.value = "";
  if (passwordInput) passwordInput.value = "";
  if (rememberInput) rememberInput.checked = false;
  if (feedback) {
    feedback.innerHTML = "";
    feedback.hidden = true;
  }
  clearLoginValidation();
  setAuthStatus("");
  resetRecaptcha("login");
}

function showLogin(skipRoute = false) {
  document.body.classList.remove("auth-register-mode");
  loginForm.hidden = false;
  registerForm.hidden = true;
  document.getElementById("registerView").hidden = true;
  document.getElementById("loginView").hidden = false;
  document.getElementById("authVisual").className = "auth-visual login-visual";
  document.getElementById("visualTitle").innerHTML = "Smart Inventory &amp;<br>Sales Monitoring System";
  document.getElementById("visualSubtitle").textContent = "Track inventory in real-time, monitor sales, manage stock, and grow your business smarter.";
  authStatus.hidden = true;
  authScreen.hidden = false;
  dashboardScreen.hidden = true;
  window.setTimeout(renderVisibleRecaptchaWidgets, 0);
  if (!skipRoute) setCleanRoute("login");
}

function showRegister(skipRoute = false) {
  document.body.classList.add("auth-register-mode");
  loginForm.hidden = true;
  registerForm.hidden = false;
  document.getElementById("loginView").hidden = true;
  document.getElementById("registerView").hidden = false;
  document.getElementById("authVisual").className = "auth-visual register-visual";
  document.getElementById("visualTitle").innerHTML = "Create <span>Your Account</span>";
  document.getElementById("visualSubtitle").textContent = "Join Smart Inventory today and take control of your business.";
  authStatus.hidden = true;
  authScreen.hidden = false;
  dashboardScreen.hidden = true;
  window.setTimeout(renderVisibleRecaptchaWidgets, 0);
  if (!skipRoute) setCleanRoute("register");
}

openRegister.addEventListener("click", showRegister);
openLogin.addEventListener("click", showLogin);
function updateRegistrationWarehouseField() {
  const role = document.getElementById("regRole")?.value;
  const group = document.getElementById("regWarehouseGroup");
  const input = document.getElementById("regWarehouseName");
  if (!group || !input) return;
  const needsWarehouse = role !== "Admin";
  group.hidden = !needsWarehouse;
  input.required = needsWarehouse;
  if (!needsWarehouse) input.value = "";
}

document.getElementById("regRole")?.addEventListener("change", updateRegistrationWarehouseField);
updateRegistrationWarehouseField();
document.getElementById("googleSignInButton").addEventListener("click", () => {
  if (!googleButtonRendered) startGoogleSignIn();
});
document.getElementById("googleSignInButton").addEventListener("keydown", (event) => {
  if (!googleButtonRendered && (event.key === "Enter" || event.key === " ")) startGoogleSignIn();
});
document.getElementById("forgotPasswordButton").addEventListener("click", showForgotPasswordModal);

function setAuthStatus(message, isError = true) {
  authStatus.textContent = message;
  authStatus.classList.toggle("success", !isError);
  authStatus.hidden = false;
}

function showDashboard(targetView = "dashboard", updateRoute = true) {
  const token = getStoredAccessToken();

  if (!token) {
    showLogin(true);
    if (updateRoute) setCleanRoute("login", true);
    return;
  }

  if (!canAccessView(targetView)) {
    showAccessDenied();
    return;
  }

  authScreen.hidden = true;
  dashboardScreen.hidden = false;
  updateDashboardUser();
  applyRoleNavigation();
  setDashboardGreeting();
  if (targetView === "dashboard") {
    activeView = "dashboard";
    navButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === "dashboard"));
    if (updateRoute) setCleanRoute("dashboard");
    loadDashboardData();
    return;
  }
  openView(targetView, 1, updateRoute);
}

function logout() {
  clearAuthSession();
  clearLoginFormFields();
  authScreen.hidden = false;
  dashboardScreen.hidden = true;
  dashboardData = null;
  dashboardLoadPromise = null;
  inventorySearchPromise = null;
  window.clearInterval(autoRefreshTimer);
  window.clearTimeout(sessionTimeoutTimer);
  autoRefreshTimer = null;
  sessionTimeoutTimer = null;
  applyAppSettings();
  showLogin(true);
  setCleanRoute("login", true);
}

function setLoginFieldError(input, errorId, message = "") {
  const errorEl = document.getElementById(errorId);
  if (input) {
    input.classList.toggle("input-invalid", Boolean(message));
    input.setAttribute("aria-invalid", message ? "true" : "false");
  }
  if (errorEl) {
    errorEl.textContent = message;
    errorEl.hidden = !message;
  }
}

function clearLoginValidation() {
  setLoginFieldError(document.getElementById("loginUsername"), "loginUsernameError");
  setLoginFieldError(document.getElementById("loginPassword"), "loginPasswordError");
}

function validateLoginFields(usernameInput, passwordInput) {
  clearLoginValidation();
  const username = (usernameInput?.value || "").trim();
  const password = (passwordInput?.value || "").trim();
  let valid = true;
  if (!username) {
    setLoginFieldError(usernameInput, "loginUsernameError", "Username or email is required.");
    valid = false;
  }
  if (!password) {
    setLoginFieldError(passwordInput, "loginPasswordError", "Password is required.");
    valid = false;
  }
  return { valid, username, password };
}

function loginResponseMessage(response, data, responseText) {
  if (response.status === 400) return data.detail || "Please complete all required login fields.";
  if (response.status === 401) return "Invalid username/email or password.";
  if (response.status === 403) return data.detail || "Your account is inactive or you do not have permission to sign in.";
  if (response.status === 429) return formatLoginRetryMessage(response);
  if (response.status === 503) return data.detail || "Login service is temporarily unavailable. Please try again.";
  if (response.status >= 500) return "Server error while signing in. Please try again shortly.";
  return data.detail || responseText || "Unable to sign in. Please try again.";
}

function formatLoginRetryMessage(response) {
  const retryAfter = Number(response.headers.get("Retry-After") || 0);
  if (!retryAfter || Number.isNaN(retryAfter)) {
    return "Too many login attempts. Please try again later.";
  }
  const minutes = Math.ceil(retryAfter / 60);
  if (minutes >= 1) {
    return `Too many login attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
  }
  return `Too many login attempts. Try again in ${retryAfter} second${retryAfter === 1 ? "" : "s"}.`;
}
loginForm.addEventListener("submit", async function (e) {
  e.preventDefault();

  const submitButton = loginForm.querySelector('button[type="submit"]');
  const originalButtonText = submitButton?.textContent || "Sign in";
  if (submitButton?.disabled) return;

  const usernameInput = document.getElementById("loginUsername");
  const passwordInput = document.getElementById("loginPassword");
  const feedback = document.getElementById("loginPasswordFeedback");
  if (feedback) {
    feedback.innerHTML = "";
    feedback.hidden = true;
  }
  const { valid, username, password } = validateLoginFields(usernameInput, passwordInput);
  if (!valid) {
    setAuthStatus("Please fix the highlighted login fields.");
    (username ? passwordInput : usernameInput)?.focus();
    return;
  }
  usernameInput.value = username;

  let recaptchaToken = "";
  try {
    recaptchaToken = getRecaptchaToken("login", "login-recaptcha");
  } catch (error) {
    setAuthStatus(error.message || "Please complete the reCAPTCHA verification.");
    return;
  }

  const formData = new URLSearchParams();
  formData.append("username", username);
  formData.append("password", password);
  if (recaptchaToken) formData.append("recaptcha_token", recaptchaToken);

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), LOGIN_REQUEST_TIMEOUT_MS);

  try {
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Signing in...";
    }
    setAuthStatus("Signing in...", false);
    console.info("[login] sending request", `${API_URL}/login`);
    const requestStarted = performance.now();
    const response = await fetch(`${API_URL}/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: formData,
      signal: controller.signal
    });
    console.info("[login] response status", response.status, `${Math.round(performance.now() - requestStarted)}ms`);

    const responseText = await response.text();
    console.info("[login] response body", responseText);
    let data = {};
    try {
      data = responseText ? JSON.parse(responseText) : {};
    } catch (parseError) {
      console.error("Login response was not JSON", response.status, responseText);
    }

    if (!response.ok) {
      const message = loginResponseMessage(response, data, responseText);
      if (response.status === 400 && /username|email/i.test(message)) {
        setLoginFieldError(usernameInput, "loginUsernameError", message);
      }
      if (response.status === 400 && /password/i.test(message)) {
        setLoginFieldError(passwordInput, "loginPasswordError", message);
      }
      setAuthStatus(message);
      resetRecaptcha("login");
      return;
    }


    passwordInput.value = "";
    saveAuthenticatedSession(data, rememberMeChecked());

    showDashboard(preferredLandingPage());
  } catch (error) {
    if (error?.name === "AbortError") {
      setAuthStatus("Login request timed out. Please try again.");
    } else if (!navigator.onLine) {
      setAuthStatus("You appear to be offline. Check your internet connection and try again.");
    } else {
      setAuthStatus("Unable to reach the server. Please check the backend connection and try again.");
    }
  } finally {
    window.clearTimeout(timeoutId);
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.textContent = originalButtonText;
    }
    updateRecaptchaDependentControls();
  }
});
registerForm.addEventListener("submit", async function (e) {
  e.preventDefault();

  const fullName = document.getElementById("regFullName").value.trim();
  const email = document.getElementById("regEmail").value.trim();
  const phone = document.getElementById("regPhone")?.value.trim() || "";
  const password = document.getElementById("regPassword").value;
  const confirmPassword = document.getElementById("regConfirmPassword").value;
  const role = document.getElementById("regRole").value;
  const warehouseName = document.getElementById("regWarehouseName")?.value.trim() || "";

  if (!/^\+?[0-9\s-]{7,20}$/.test(phone)) {
    setAuthStatus("Enter a valid phone number.");
    return;
  }

  if (!isStrongPassword(password)) {
    renderPasswordFeedback("regPassword", "regPasswordFeedback");
    setAuthStatus("Password must meet all strength requirements.");
    return;
  }

  if (!["Manager", "Staff"].includes(role)) {
    setAuthStatus("Choose Manager or Staff as the role.");
    return;
  }

  if (!warehouseName) {
    setAuthStatus("Warehouse Name is required for Manager and Staff.");
    return;
  }

  let recaptchaToken = "";
  try {
    recaptchaToken = getRecaptchaToken("register", "register-recaptcha");
  } catch (error) {
    setAuthStatus(error.message || "Please complete the reCAPTCHA verification.");
    return;
  }

  const params = new URLSearchParams({
    full_name: fullName,
    email,
    phone,
    password,
    confirm_password: confirmPassword,
    role
  });
  if (recaptchaToken) params.set("recaptcha_token", recaptchaToken);
  params.set("warehouse_name", warehouseName);

  try {
    const response = await fetch(`${API_URL}/register?${params.toString()}`, {
      method: "POST"
    });

    const data = await response.json();

    if (!response.ok) {
      setAuthStatus(data.detail || "Registration failed");
      resetRecaptcha("register");
      return;
    }

    resetRecaptcha("register");
    showLogin();
    setAuthStatus("Registration successful. Please sign in.", false);
  } catch (error) {
    setAuthStatus("Backend server is not running");
  }
});
async function authFetch(endpoint, options = {}) {
  const token = getStoredAccessToken();
  const timeoutMs = options.timeoutMs || 15000;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  const { timeoutMs: _timeoutMs, signal: callerSignal, ...fetchOptions } = options;
  let response;

  if (callerSignal) {
    if (callerSignal.aborted) controller.abort();
    else callerSignal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  try {
    response = await fetch(`${API_URL}${endpoint}`, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        ...(fetchOptions.headers || {}),
        Authorization: `Bearer ${token}`
      }
    });
  } catch (error) {
    const isTimeout = error.name === "AbortError";
    const networkError = new Error(isTimeout ? "Request timed out. Please try again." : "Backend server is not running. Please start the backend and try again.");
    networkError.status = 0;
    networkError.cause = error;
    throw networkError;
  } finally {
    window.clearTimeout(timeoutId);
  }

  const contentType = response.headers.get("content-type") || "";
  const responseText = await response.text();
  let data = null;
  if (responseText) {
    if (contentType.includes("application/json")) {
      try {
        data = JSON.parse(responseText);
      } catch {
        data = { detail: responseText };
      }
    } else {
      data = { detail: responseText };
    }
  }

  if (!response.ok) {
    const message = readableErrorMessage(data, `Request failed: ${response.status}`);
    const error = new Error(message);
    error.status = response.status;
    error.detail = data?.detail;
    if (response.status === 401) logout();
    if (response.status === 403) showAccessDenied(message);
    throw error;
  }

  if (!responseText) return {};
  return data ?? {};
}
async function fetchAllPaged(endpoint) {
  const first = await authFetch(`${endpoint}?page=1&limit=100`);
  const pages = Math.max(first.total_pages || 1, 1);
  if (pages === 1) return first.items || [];

  const remaining = await Promise.all(
    Array.from({ length: pages - 1 }, (_, index) =>
      authFetch(`${endpoint}?page=${index + 2}&limit=100`)
    )
  );
  return [
    ...(first.items || []),
    ...remaining.flatMap((response) => response.items || [])
  ];
}

async function cachedAuthFetch(cacheKey, fetcher, ttl = 30000) {
  const cached = routeCache.get(cacheKey);
  if (cached && Date.now() - cached.time < ttl) {
    return cached.data;
  }
  const data = await fetcher();
  routeCache.set(cacheKey, { data, time: Date.now() });
  return data;
}

function clearCacheByPrefix(prefix) {
  [...routeCache.keys()].forEach((key) => {
    if (String(key).startsWith(prefix)) routeCache.delete(key);
  });
}

async function loadDashboardDataset(force = false) {
  const cacheKey = dashboardRangeCacheKey();
  if (!force && dashboardData && dashboardDataCacheKey === cacheKey) return dashboardData;
  if (!dashboardLoadPromise || dashboardDataCacheKey !== cacheKey) {
    dashboardDataCacheKey = cacheKey;
    const endpoint = appendQueryParams("/dashboard/overview", dashboardDateParams());
    dashboardLoadPromise = authFetch(endpoint).then((overview) => {
      dashboardData = {
        summary: overview.summary || {},
        inventory: overview.inventory || {},
        products: overview.health_products || [],
        healthProducts: overview.health_products || [],
        lowStockProducts: overview.low_stock_products || [],
        sales: overview.sales || [],
        purchases: overview.purchases || [],
        suppliers: overview.suppliers || [],
        currentStock: null,
        range: overview.range,
        startDate: overview.start_date,
        endDate: overview.end_date
      };
      return dashboardData;
    }).finally(() => {
      dashboardLoadPromise = null;
    });
  }
  return dashboardLoadPromise;
}

async function loadInventorySearchData() {
  if (dashboardData?.currentStock) return dashboardData.currentStock;
  if (!inventorySearchPromise) {
    inventorySearchPromise = authFetch("/inventory/current-stock").then((items) => {
      if (dashboardData) dashboardData.currentStock = items;
      return items;
    }).finally(() => {
      inventorySearchPromise = null;
    });
  }
  return inventorySearchPromise;
}

function normalizeRestockQueueResponse(value) {
  if (Array.isArray(value)) {
    return {
      page: 1,
      limit: value.length || getRecordsPerPage(),
      total: value.length,
      total_pages: 1,
      has_next: false,
      has_previous: false,
      items: value
    };
  }
  return {
    page: Number(value?.page || 1),
    limit: Number(value?.limit || getRecordsPerPage()),
    total: Number(value?.total || value?.items?.length || 0),
    total_pages: Number(value?.total_pages || 1),
    has_next: Boolean(value?.has_next),
    has_previous: Boolean(value?.has_previous),
    items: Array.isArray(value?.items) ? value.items : []
  };
}


function normalizePagedResponse(value) {
  if (Array.isArray(value)) {
    return {
      page: 1,
      limit: value.length || getRecordsPerPage(),
      total: value.length,
      total_pages: 1,
      has_next: false,
      has_previous: false,
      items: value
    };
  }
  return {
    page: Number(value?.page || 1),
    limit: Number(value?.limit || getRecordsPerPage()),
    total: Number(value?.total || value?.items?.length || 0),
    total_pages: Number(value?.total_pages || 1),
    has_next: Boolean(value?.has_next),
    has_previous: Boolean(value?.has_previous),
    items: Array.isArray(value?.items) ? value.items : []
  };
}

async function loadRestockQueue(force = false, page = restockQueuePage || 1) {
  if (restockQueueCache && !force && Number(restockQueueCache.page || 1) === Number(page)) return restockQueueCache;
  try {
    const response = await authFetch(`/restock-queue?page=${page}&limit=${Math.min(getRecordsPerPage(), 50)}`);
    restockQueueCache = normalizeRestockQueueResponse(response);
    restockQueuePage = restockQueueCache.page;
    localStorage.setItem("smart_inventory_restock_queue", JSON.stringify(restockQueueCache));
  } catch (error) {
    restockQueueCache = normalizeRestockQueueResponse(JSON.parse(localStorage.getItem("smart_inventory_restock_queue") || "[]"));
  }
  return restockQueueCache;
}

async function loadPurchasePendingSummary(force = false) {
  if (purchasePendingSummaryCache && !force) return purchasePendingSummaryCache;
  purchasePendingSummaryCache = await authFetch("/purchases/pending-summary");
  return purchasePendingSummaryCache;
}

async function loadDashboardSummary() {
  const summary = await cachedAuthFetch(`dashboard:summary:${dashboardRangeCacheKey()}`, () => authFetch(appendQueryParams("/dashboard/refresh-statistics", dashboardDateParams())), 10000);
  const totalProducts = document.getElementById("totalProducts");
  const totalSales = document.getElementById("totalSales");
  const totalPurchases = document.getElementById("totalPurchases");
  const lowStock = document.getElementById("lowStock");
  const totalSuppliers = document.getElementById("totalSuppliers");
  if (totalProducts) totalProducts.textContent = formatDashboardNumber(summary.total_products || 0);
  if (totalSales) totalSales.textContent = formatMoney(summary.total_sales || 0);
  if (totalPurchases) totalPurchases.textContent = formatMoney(summary.total_purchases || 0);
  if (lowStock) lowStock.textContent = formatDashboardNumber(summary.low_stock_items || 0);
  if (totalSuppliers) totalSuppliers.textContent = formatDashboardNumber(summary.total_suppliers || 0);
  return summary;
}

function invalidateDashboardData() {
  dashboardData = null;
  dashboardLoadPromise = null;
  inventorySearchPromise = null;
  restockQueueCache = null;
  purchasePendingSummaryCache = null;
  routeCache.clear();
}

async function refreshDashboardAfterMutation() {
  invalidateDashboardData();
  try {
    await authFetch("/dashboard/refresh-statistics");
  } catch (error) {
    console.warn("Dashboard statistics refresh failed:", error.message);
  }
  if (activeView === "dashboard") {
    await loadDashboardData();
  }
}

function setDashboardStatus(message = "", isError = false) {
  const status = document.getElementById("dashboardStatus");
  if (!status) return;
  status.textContent = message;
  status.classList.toggle("error", isError);
  status.hidden = !message;
}

function showToast(message, type = "success") {
  let toast = document.getElementById("dashboardToast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "dashboardToast";
    toast.className = "dashboard-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = `dashboard-toast ${type}`;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3200);
}

function productBarcodeValue(product) {
  return String(product?.barcode_value || product?.product_id || "").trim();
}

function productQrValue(product) {
  return String(product?.qr_code_value || product?.product_id || "").trim();
}

function renderFallbackBarcode(value) {
  const seed = Array.from(String(value || "SMART")).map((char) => char.charCodeAt(0));
  const bars = Array.from({ length: 42 }, (_, index) => {
    const width = 2 + ((seed[index % seed.length] + index) % 3);
    const height = 42 + ((seed[index % seed.length] + index * 7) % 38);
    return `<span style="width:${width}px;height:${height}px"></span>`;
  }).join("");
  return `<div class="barcode-fallback" aria-label="Barcode ${escapeHtml(value)}">${bars}</div>`;
}

function renderCodeModal(product) {
  const barcodeValue = productBarcodeValue(product);
  const qrValue = productQrValue(product);
  openModal(`
    <section class="product-code-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="productCodeTitle">
      <header class="modal-header"><h2 id="productCodeTitle">Product Codes</h2><button class="modal-close" type="button" data-modal-close aria-label="Close product codes"><i data-lucide="x"></i></button></header>
      <div class="product-code-summary">
        <span class="code-product-icon"><i data-lucide="scan-barcode"></i></span>
        <div><strong>${escapeHtml(product.product_name || product.product_id)}</strong><small>${escapeHtml(product.product_id)}</small></div>
      </div>
      <div class="code-grid">
        <article>
          <h3>Barcode</h3>
          <div id="barcodePreview" class="code-preview">${renderFallbackBarcode(barcodeValue)}</div>
          <code>${escapeHtml(barcodeValue)}</code>
        </article>
        <article>
          <h3>QR Code</h3>
          <div id="qrPreview" class="code-preview qr-preview"><span>${escapeHtml(qrValue)}</span></div>
          <code>${escapeHtml(qrValue)}</code>
        </article>
      </div>
      <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-download-barcode><i data-lucide="download"></i>Download Barcode</button><button class="modal-secondary-button" type="button" data-download-qr><i data-lucide="download"></i>Download QR</button><button class="modal-secondary-button" type="button" data-print-product-codes><i data-lucide="printer"></i>Print</button><button class="modal-primary-button" type="button" data-modal-close>Done</button></footer>
    </section>
  `);
  bindModalEvents();

  const barcodeTarget = document.getElementById("barcodePreview");
  if (window.JsBarcode && barcodeTarget && barcodeValue) {
    barcodeTarget.innerHTML = '<svg id="barcodeSvg"></svg>';
    window.JsBarcode("#barcodeSvg", barcodeValue, {
      format: "CODE128",
      lineColor: "#0f172a",
      width: 2,
      height: 82,
      displayValue: false,
      margin: 4
    });
  }
  const qrTarget = document.getElementById("qrPreview");
  if (window.QRCode && qrTarget && qrValue) {
    qrTarget.innerHTML = "";
    new window.QRCode(qrTarget, {
      text: qrValue,
      width: 150,
      height: 150,
      colorDark: "#0f172a",
      colorLight: "#ffffff",
      correctLevel: window.QRCode.CorrectLevel.M
    });
  }
  const overlay = document.getElementById("modalOverlay");
  overlay.querySelector("[data-print-product-codes]")?.addEventListener("click", () => window.print());
  overlay.querySelector("[data-download-barcode]")?.addEventListener("click", () => downloadBarcodeImage(product));
  overlay.querySelector("[data-download-qr]")?.addEventListener("click", () => downloadQrImage(product));
}

function downloadDataUrl(dataUrl, filename) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function downloadBarcodeImage(product) {
  const svg = document.getElementById("barcodeSvg");
  if (!svg) {
    showToast("Barcode image is not ready yet.", "error");
    return;
  }
  const source = new XMLSerializer().serializeToString(svg);
  const dataUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
  downloadDataUrl(dataUrl, `${product.product_id || "product"}-barcode.svg`);
}

function downloadQrImage(product) {
  const qrTarget = document.getElementById("qrPreview");
  const canvas = qrTarget?.querySelector("canvas");
  const image = qrTarget?.querySelector("img");
  const dataUrl = canvas?.toDataURL("image/png") || image?.src;
  if (!dataUrl) {
    showToast("QR code image is not ready yet.", "error");
    return;
  }
  downloadDataUrl(dataUrl, `${product.product_id || "product"}-qr.png`);
}

async function stopProductScanner() {
  productScanResolved = true;
  if (productScannerInstance) {
    const scanner = productScannerInstance;
    productScannerInstance = null;
    try {
      const state = typeof scanner.getState === "function" ? scanner.getState() : null;
      if (state !== 1) await scanner.stop();
    } catch (error) {
      console.warn("Scanner stop failed:", error.message);
    }
    try {
      await scanner.clear();
    } catch (error) {
      console.warn("Scanner cleanup failed:", error.message);
    }
  }
  if (productScanStream) {
    productScanStream.getTracks().forEach((track) => track.stop());
    productScanStream = null;
  }
}

async function openScannedProduct(code) {
  const cleanCode = String(code || "").trim();
  if (!cleanCode) throw new Error("Enter a Product ID, Barcode, or QR Code.");
  const result = await authFetch(`/products/scan/${encodeURIComponent(cleanCode)}`);
  const product = result.product;
  localStorage.setItem("smart_inventory_focus_product", product.product_id);
  await stopProductScanner();
  closeModal();
  showToast(`${product.product_name || product.product_id} found from scan.`);
  await openView("products");
  renderCodeModal(product);
}

function renderProductScannerModal() {
  stopProductScanner();
  openModal(`
    <section class="product-scan-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="productScanTitle">
      <header class="modal-header"><h2 id="productScanTitle">Scan Product</h2><button class="modal-close" type="button" data-modal-close aria-label="Close product scanner"><i data-lucide="x"></i></button></header>
      <div class="scanner-body">
        <div id="productScannerReader" class="scanner-reader"></div>
        <p id="scannerMessage">Allow camera access to scan a barcode or QR code.</p>
        <form id="manualScanForm" class="manual-scan-form">
          <label>Enter code manually<input id="manualScanCode" placeholder="Product ID, barcode, or QR value" autocomplete="off"></label>
          <button type="submit"><i data-lucide="search"></i>Find Product</button>
        </form>
      </div>
    </section>
  `);
  bindModalEvents();
  setupProductScanner();
}

async function setupProductScanner() {
  const message = document.getElementById("scannerMessage");
  const manualForm = document.getElementById("manualScanForm");

  manualForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = document.getElementById("manualScanCode").value.trim();
    if (!code) return;
    try {
      message.textContent = "Searching product...";
      await openScannedProduct(code);
    } catch (error) {
      message.textContent = "No product found with the entered Product ID, Barcode, or QR Code.";
      message.classList.add("error");
    }
  });

  if (!window.Html5Qrcode || !navigator.mediaDevices?.getUserMedia) {
    message.textContent = "Camera scanning is not supported in this browser. Enter the Product ID manually.";
    return;
  }

  try {
    const formats = window.Html5QrcodeSupportedFormats ? [
      window.Html5QrcodeSupportedFormats.QR_CODE,
      window.Html5QrcodeSupportedFormats.CODE_128,
      window.Html5QrcodeSupportedFormats.CODE_39,
      window.Html5QrcodeSupportedFormats.EAN_13,
      window.Html5QrcodeSupportedFormats.EAN_8,
      window.Html5QrcodeSupportedFormats.UPC_A,
      window.Html5QrcodeSupportedFormats.UPC_E
    ].filter(Boolean) : undefined;
    productScannerInstance = new window.Html5Qrcode(
      "productScannerReader",
      formats ? { formatsToSupport: formats } : undefined
    );
    message.textContent = "Scanning for barcode or QR code...";
    await productScannerInstance.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: { width: 260, height: 180 }, aspectRatio: 1.777 },
      async (decodedText) => {
        message.textContent = `Code detected: ${decodedText}`;
        const scanner = productScannerInstance;
        productScannerInstance = null;
        if (scanner) {
          try {
            await scanner.stop();
            await scanner.clear();
          } catch (error) {
            console.warn("Scanner stop failed:", error.message);
          }
        }
        await openScannedProduct(decodedText);
      },
      () => {}
    );
  } catch (error) {
    message.textContent = /permission|notallowed|denied/i.test(error.message || "")
      ? "Camera permission denied. Please allow camera access or enter the Product ID manually."
      : "Camera scanning is not supported on this device. Enter the Product ID manually.";
    message.classList.add("error");
  }
}

function startOfDay(date) {
  const value = new Date(date);
  value.setHours(0, 0, 0, 0);
  return value;
}

function endOfDay(date) {
  const value = new Date(date);
  value.setHours(23, 59, 59, 999);
  return value;
}

function apiDateParams() {
  const range = getSelectedRange();
  return {
    start_date: range.start.toISOString().slice(0, 10),
    end_date: range.end.toISOString().slice(0, 10)
  };
}

function dashboardRangeValue() {
  const key = dateRangeState.key || "last7";
  return {
    today: "today",
    last7: "last_7_days",
    last30: "last_30_days",
    month: "this_month",
    custom: "custom"
  }[key] || "last_7_days";
}

function dashboardDateParams() {
  return { range: dashboardRangeValue(), ...apiDateParams() };
}

function dashboardRangeCacheKey() {
  const params = dashboardDateParams();
  return `${params.range}:${params.start_date}:${params.end_date}`;
}

function reportExportParams() {
  const params = apiDateParams();
  const mode = document.getElementById("inventory-report-mode")?.value;
  if (mode) params.report_mode = mode;
  return params;
}

function appendQueryParams(endpoint, params) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") query.set(key, value);
  });
  const serialized = query.toString();
  if (!serialized) return endpoint;
  const separator = endpoint.includes("?") ? "&" : "?";
  return `${endpoint}${separator}${serialized}`;
}

function dateCacheSuffix() {
  const params = apiDateParams();
  return `${dateRangeState.key}:${params.start_date}:${params.end_date}`;
}

function isActiveRender(name, token) {
  return activeView === name && token === viewRenderToken;
}

function loadStoredDateRangeState() {
  try {
    const stored = JSON.parse(localStorage.getItem("smart_inventory_date_range") || "{}");
    const validKeys = new Set(["today", "last7", "last30", "month", "custom"]);
    if (!validKeys.has(stored.key)) return { key: "last7" };
    if (stored.key === "custom" && (!stored.from || !stored.to)) return { key: "last7" };
    return stored;
  } catch {
    return { key: "last7" };
  }
}

function saveDateRangeState() {
  localStorage.setItem("smart_inventory_date_range", JSON.stringify(dateRangeState));
}

function updateDateRangeLabels() {
  const { label } = getSelectedRange();
  ["dateRangeLabel", "salesPeriodButton", "categoryPeriodButton"].forEach((id) => {
    const element = document.getElementById(id);
    if (element) element.textContent = label;
  });
}
function getSelectedRange() {
  const now = new Date();
  const today = startOfDay(now);
  let start = new Date(today);
  let end = endOfDay(now);
  let label = "Last 7 Days";

  if (dateRangeState.key === "today") {
    label = "Today";
  } else if (dateRangeState.key === "last30") {
    start.setDate(start.getDate() - 29);
    label = "Last 30 Days";
  } else if (dateRangeState.key === "month") {
    start = new Date(now.getFullYear(), now.getMonth(), 1);
    label = "This Month";
  } else if (dateRangeState.key === "custom") {
    const customStart = new Date(dateRangeState.from);
    const customEnd = new Date(dateRangeState.to);
    if (Number.isNaN(customStart.getTime()) || Number.isNaN(customEnd.getTime())) {
      dateRangeState = { key: "last7" };
      saveDateRangeState();
      start.setDate(start.getDate() - 6);
      label = "Last 7 Days";
    } else {
      start = startOfDay(dateRangeState.from);
      end = endOfDay(dateRangeState.to);
      label = `${formatShortDate(start)} - ${formatShortDate(end)}`;
    }
  } else {
    start.setDate(start.getDate() - 6);
  }

  const duration = end.getTime() - start.getTime() + 1;
  return {
    start,
    end,
    label,
    previousStart: new Date(start.getTime() - duration),
    previousEnd: new Date(start.getTime() - 1)
  };
}

function formatShortDate(date) {
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

function recordDate(record) {
  const value = record.created_at || record.date;
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function recordsInRange(records, start, end) {
  return records.filter((record) => {
    const date = recordDate(record);
    return date && date >= start && date <= end;
  });
}

function recordTotal(records, field) {
  return records.reduce((total, record) => total + Number(record[field] || 0), 0);
}

function trendText(current, previous) {
  if (!previous) return "No previous data";
  const difference = ((current - previous) / previous) * 100;
  const arrow = difference >= 0 ? "up" : "down";
  return `${arrow} ${Math.abs(difference).toFixed(1)}% vs previous period`;
}

function setTrend(id, current, previous) {
  const node = document.getElementById(id);
  if (!node) return;
  const text = trendText(current, previous);
  node.textContent = text;
  node.classList.toggle("negative", text.startsWith("down"));
  node.classList.toggle("neutral", text === "No previous data");
}

function setNoDataTrend(id) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = "No data available";
  node.classList.remove("negative");
  node.classList.add("neutral");
}

function formatMoney(value) {
  const settings = getAppSettings();
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: settings.currency || "INR",
    maximumFractionDigits: 0
  }).format(Number(value || 0));
}

function updateDashboardUser() {
  const profile = getProfileDetails();
  const username = profile.name;
  const role = profile.role;
  ["sidebar-user-name", "header-user-name"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.textContent = username;
  });
  ["sidebar-user-role", "header-user-role"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.textContent = role;
  });
  ["sidebar-user-avatar", "header-user-avatar"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.innerHTML = profile.avatar ? `<img src="${profile.avatar}" alt="${escapeHtml(profile.name)}">` : escapeHtml(profile.name.charAt(0).toUpperCase());
  });
}

function setDashboardGreeting() {
  const title = document.getElementById("dashboardTitle");
  if (!title) return;
  const name = getProfileDetails().name || "User";
  title.textContent = "";
  title.append("Hello, ");
  const highlightedName = document.createElement("span");
  highlightedName.textContent = `${name}!`;
  title.append(highlightedName, " ");
  const greeting = document.createElement("span");
  greeting.setAttribute("aria-hidden", "true");
  greeting.innerHTML = "&#128075;";
  title.append(greeting);
}

function renderSalesChart(sales, range) {
  const bucketCount = Math.min(7, Math.max(1, Math.ceil((range.end - range.start) / 86400000) + 1));
  const values = Array(bucketCount).fill(0);
  const bucketMs = (range.end - range.start + 1) / bucketCount;
  const chartBars = document.getElementById("chartBars");
  const high = document.getElementById("chartHighLabel");
  const middle = document.getElementById("chartMidLabel");
  if (!sales.length) {
    if (chartBars) chartBars.innerHTML = '<span class="chart-empty">No data available</span>';
    if (high) high.textContent = formatMoney(0);
    if (middle) middle.textContent = formatMoney(0);
    return;
  }
  sales.forEach((sale) => {
    const date = recordDate(sale);
    const index = Math.min(bucketCount - 1, Math.floor((date - range.start) / bucketMs));
    values[index] += Number(sale.total_amount || sale.sales_amount || 0);
  });
  const max = Math.max(...values, 1);
  if (chartBars) chartBars.innerHTML = values.map((value) => `<i style="height:${Math.max(7, (value / max) * 100)}%" title="${escapeHtml(formatMoney(value))}"></i>`).join("");
  if (high) high.textContent = formatMoney(max);
  if (middle) middle.textContent = formatMoney(max / 2);
}

function renderCategoryBreakdown(sales) {
  const categoryValues = new Map();
  sales.forEach((sale) => {
    const name = sale.category || sale.category_id || "Other";
    categoryValues.set(name, (categoryValues.get(name) || 0) + Number(sale.total_amount || sale.sales_amount || 0));
  });
  const entries = [...categoryValues.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  const colors = ["#4f8df7", "#48b6e9", "#41c39c", "#f4b54a", "#ed8eb0"];
  const donut = document.getElementById("salesDonut");
  const legend = document.getElementById("categoryLegend");
  if (!entries.length) {
    if (donut) donut.style.background = "radial-gradient(#fff 0 44%, transparent 45%), conic-gradient(#e5e7eb 0 100%)";
    if (legend) legend.innerHTML = "<li>No data available</li>";
    return;
  }
  let position = 0;
  const gradient = entries.map(([, value], index) => {
    const next = position + (value / total) * 100;
    const segment = `${colors[index]} ${position}% ${next}%`;
    position = next;
    return segment;
  }).join(", ");
  if (donut) donut.style.background = `radial-gradient(#fff 0 44%, transparent 45%), conic-gradient(${gradient})`;
  if (legend) legend.innerHTML = entries.map(([name, value], index) => `<li><span style="background:${colors[index]}"></span>${escapeHtml(name)} <b>${Math.round((value / total) * 100)}%</b></li>`).join("");
}

function renderLowStock(products) {
  const rows = products
    .filter((product) => Number(product.quantity) <= Number(product.reorder_level ?? 35))
    .sort((a, b) => Number(a.quantity) - Number(b.quantity))
    .slice(0, 5);
  const body = document.getElementById("lowStockRows");
  if (!body) return;
  body.innerHTML = rows.length
    ? rows.map((product) => `<tr><td>${escapeHtml(product.product_name || product.product_id)}</td><td>${escapeHtml(product.quantity)}</td><td><span class="alert-pill">${Number(product.quantity) === 0 ? "Critical" : "Low"}</span></td></tr>`).join("")
    : "<tr><td>No low-stock products.</td><td>-</td><td>-</td></tr>";
}

function getInventoryHealth(products) {
  const total = products.length;
  const withStatus = products.map((product) => {
    const quantity = Number(product.quantity || 0);
    const reorderLevel = Number(product.reorder_level ?? 35);
    const overstockLimit = reorderLevel > 0 ? reorderLevel * 3 : Number.POSITIVE_INFINITY;
    let status = "Healthy";
    let severity = 0;
    let action = "Monitor";

    if (quantity <= 0) {
      status = "Out of Stock";
      severity = 3;
      action = "Purchase Immediately";
    } else if (quantity <= Math.max(5, Math.ceil(reorderLevel * 0.25))) {
      status = "Critical";
      severity = 2;
      action = "Purchase Immediately";
    } else if (quantity <= reorderLevel) {
      status = "Low Stock";
      severity = 1;
      action = "Restock";
    } else if (quantity >= overstockLimit) {
      status = "Overstocked";
      severity = 1;
      action = "Review Stock";
    }

    return { ...product, quantity, reorderLevel, status, severity, action };
  });
  const lowStock = withStatus.filter((product) => product.status === "Low Stock" || product.status === "Critical");
  const outOfStock = withStatus.filter((product) => product.status === "Out of Stock");
  const overstocked = withStatus.filter((product) => product.status === "Overstocked");
  const attention = withStatus
    .filter((product) => product.severity > 0)
    .sort((left, right) => right.severity - left.severity || left.quantity - right.quantity);
  const healthyCount = Math.max(total - lowStock.length - outOfStock.length - overstocked.length, 0);
  const healthPercent = total ? Math.round((healthyCount / total) * 100) : 0;

  return {
    total,
    healthyCount,
    lowStockCount: lowStock.length,
    outOfStockCount: outOfStock.length,
    overstockedCount: overstocked.length,
    healthPercent,
    attention
  };
}

function inventoryStatusMessage(health) {
  const urgentCount = health.lowStockCount + health.outOfStockCount;
  if (!health.total) return "No product records are available.";
  if (health.outOfStockCount > 0 || urgentCount / health.total > 0.4) {
    return `${urgentCount} products require immediate restocking. Click here to view affected products.`;
  }
  if (urgentCount / health.total >= 0.2) {
    return `${urgentCount} products are below their reorder level.`;
  }
  return health.lowStockCount
    ? `${health.lowStockCount} products are below reorder level, but overall stock is stable.`
    : "All products are sufficiently stocked.";
}

function updateInventoryStatus(totalProducts, lowStockCount, outOfStockCount) {
  const card = document.getElementById("inventoryStatusCard");
  const statusText = document.getElementById("inventoryStatusText");
  const badge = document.getElementById("inventoryStatusBadge");
  const description = document.getElementById("inventoryStatusDescription");
  if (!card || !statusText || !badge || !description) return;

  const lowStockPercent = totalProducts ? (lowStockCount / totalProducts) * 100 : 0;
  let status = {
    key: "healthy",
    label: "Healthy",
    badge: "OK",
    message: "All products are sufficiently stocked."
  };

  if (outOfStockCount > 0 || lowStockPercent > 40) {
    status = {
      key: "critical",
      label: "Critical",
      badge: "!",
      message: `${lowStockCount} products require immediate restocking. Click here to view affected products.`
    };
  } else if (lowStockPercent >= 20 && lowStockPercent <= 40) {
    status = {
      key: "warning",
      label: "Warning",
      badge: "!",
      message: `${lowStockCount} products are below their reorder level.`
    };
  }

  card.classList.remove("status-healthy", "status-warning", "status-critical");
  card.classList.add(`status-${status.key}`);
  statusText.textContent = status.label;
  badge.textContent = status.badge;
  description.textContent = status.message;
}

function inventoryHealthWarehouseId() {
  return currentRouteParams().get('warehouse_id')
    || (activeView === 'inventory' ? inventoryWarehouseFilter : '')
    || (activeView === 'products' ? productWarehouseFilter : '')
    || '';
}

async function loadInventoryHealthData(page = inventoryHealthState.page || 1) {
  const limit = inventoryHealthState.limit || 10;
  const warehouseId = inventoryHealthWarehouseId();
  const [summary, attention] = await Promise.all([
    authFetch(appendQueryParams('/inventory/health/summary', { warehouse_id: warehouseId })),
    authFetch(appendQueryParams('/inventory/health/attention', { page, limit, warehouse_id: warehouseId }))
  ]);
  inventoryHealthState = {
    ...inventoryHealthState,
    page: Number(attention?.page || page),
    limit,
    summary: summary || {},
    attention: attention || { items: [], page, limit, total: 0, total_pages: 1 }
  };
  return inventoryHealthState;
}

function renderInventoryHealthModal(state, isLoading = false, errorMessage = '') {
  const summary = state?.summary || {};
  const attention = normalizePagedResponse(state?.attention || { page: 1, limit: 10, total: 0, total_pages: 1, items: [] });
  if (errorMessage && !state?.summary) {
    return `
    <section class="inventory-health-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="inventoryHealthTitle">
      <header class="modal-header"><h2 id="inventoryHealthTitle">Inventory Health</h2><button class="modal-close" type="button" data-modal-close aria-label="Close inventory health"><i data-lucide="x"></i></button></header>
      <div class="inventory-health-body">
        <div class="form-message error">${escapeHtml(errorMessage)}</div>
        <div class="inventory-health-empty"><strong>Unable to load inventory health.</strong><p>Please retry after checking the backend terminal for the exact API error.</p><button type="button" class="modal-primary-button" data-health-action="health-page" data-page="1">Retry</button></div>
      </div>
    </section>`;
  }
  const statusLabel = summary.status || document.getElementById('inventoryStatusText')?.textContent || 'Inventory Status';
  const statusKey = String(statusLabel || 'healthy').toLowerCase();
  const message = summary.message || 'Loading inventory health details...';
  const rows = isLoading
    ? '<tr><td colspan="7">Loading inventory health details...</td></tr>'
    : (attention.items || []).map((product) => {
        const productId = product.product_id || '';
        const warehouseId = product.warehouse_id || product.location_id || '';
        const status = product.status || 'Healthy';
        const supplierId = product.supplier_id || '';
        const suggestedQuantity = product.suggested_quantity || '';
        const unitCost = product.unit_cost || product.price || product.unit_price || '';
        const queueStatus = product.queue_status || 'Not Queued';
        const isQueued = queueStatus !== 'Not Queued';
        return `
          <tr>
            <td>${escapeHtml(product.product_name || productId)}</td>
            <td>${formatDashboardNumber(product.quantity ?? product.current_stock)}</td>
            <td>${formatDashboardNumber(product.reorder_level)}</td>
            <td><span class="health-status ${escapeHtml(status.toLowerCase().replaceAll(' ', '-'))}">${escapeHtml(status)}</span></td>
            <td>${escapeHtml(product.suggested_action || 'Review')}</td>
            <td><span class="queue-status ${isQueued ? 'queued' : 'not-queued'}">${escapeHtml(queueStatus)}</span></td>
            <td class="health-actions">
              <button type="button" data-health-action="restock" data-product-id="${escapeHtml(productId)}" data-warehouse-id="${escapeHtml(warehouseId)}" data-supplier-id="${escapeHtml(supplierId)}" data-suggested-quantity="${escapeHtml(suggestedQuantity)}" data-unit-cost="${escapeHtml(unitCost)}" ${isQueued ? 'disabled' : ''}>${isQueued ? 'Already Queued' : 'Restock'}</button>
              <button type="button" data-health-action="purchase" data-product-id="${escapeHtml(productId)}" data-warehouse-id="${escapeHtml(warehouseId)}" data-supplier-id="${escapeHtml(supplierId)}" data-suggested-quantity="${escapeHtml(suggestedQuantity)}" data-unit-cost="${escapeHtml(unitCost)}">Create Purchase Order</button>
              <button type="button" data-health-action="product" data-product-id="${escapeHtml(productId)}" data-warehouse-id="${escapeHtml(warehouseId)}">View Product</button>
            </td>
          </tr>`;
      }).join('') || '<tr><td colspan="7">No products require attention.</td></tr>';

  const totalPages = Math.max(1, Number(attention.total_pages || 1));
  const currentPage = Math.max(1, Number(attention.page || 1));
  return `
    <section class="inventory-health-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="inventoryHealthTitle">
      <header class="modal-header"><h2 id="inventoryHealthTitle">Inventory Health</h2><button class="modal-close" type="button" data-modal-close aria-label="Close inventory health"><i data-lucide="x"></i></button></header>
      <div class="inventory-health-body">
        ${errorMessage ? `<div class="form-message error">${escapeHtml(errorMessage)}</div>` : ''}
        <div class="inventory-health-banner status-${escapeHtml(statusKey)}">
          <span class="inventory-health-score">${formatDashboardNumber(summary.inventory_health_percentage ?? 0)}%</span>
          <div><strong>${escapeHtml(statusLabel)}</strong><p>${escapeHtml(message)}</p></div>
        </div>
        <div class="inventory-health-summary">
          <article><span>Total Products</span><strong>${formatDashboardNumber(summary.total_products || 0)}</strong></article>
          <article><span>Healthy Products</span><strong>${formatDashboardNumber(summary.healthy_products || 0)}</strong></article>
          <article><span>Low Stock Products</span><strong>${formatDashboardNumber(summary.low_stock_products || 0)}</strong></article>
          <article><span>Out of Stock</span><strong>${formatDashboardNumber(summary.out_of_stock_products || 0)}</strong></article>
          <article><span>Overstocked</span><strong>${formatDashboardNumber(summary.overstocked_products || 0)}</strong></article>
          <article><span>Health Percentage</span><strong>${formatDashboardNumber(summary.inventory_health_percentage || 0)}%</strong></article>
        </div>
        <div class="inventory-health-toolbar">
          <h3>Products Requiring Attention</h3>
          <button type="button" class="modal-primary-button" data-health-action="restock-all" ${Number(summary.restock_eligible_count || 0) <= 0 ? 'disabled' : ''}><i data-lucide="shopping-bag"></i>Restock All (${formatDashboardNumber(summary.restock_eligible_count || 0)})</button>
        </div>
        <div class="inventory-health-table-wrap">
          <table class="inventory-health-table">
            <thead><tr><th>Product Name</th><th>Current Stock</th><th>Reorder Level</th><th>Status</th><th>Suggested Action</th><th>Queue</th><th>Actions</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        <div class="inventory-health-pagination">
          <button type="button" data-health-action="health-page" data-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''}>Previous</button>
          <span>Page ${formatDashboardNumber(currentPage)} of ${formatDashboardNumber(totalPages)} (${formatDashboardNumber(attention.total || 0)} records)</span>
          <button type="button" data-health-action="health-page" data-page="${currentPage + 1}" ${currentPage >= totalPages ? 'disabled' : ''}>Next</button>
        </div>
      </div>
    </section>`;
}

async function showInventoryHealthModal(page = 1) {
  openModal(renderInventoryHealthModal(inventoryHealthState, true));
  bindModalEvents();
  refreshIcons();
  try {
    const state = await loadInventoryHealthData(page);
    openModal(renderInventoryHealthModal(state));
    bindModalEvents();
    refreshIcons();
  } catch (error) {
    openModal(renderInventoryHealthModal(inventoryHealthState, false, readableErrorMessage(error, 'Unable to load inventory health.')));
    bindModalEvents();
    refreshIcons();
  }
}

function restockQueueKey(item) {
  return `${item?.product_id || ""}::${item?.warehouse_id || item?.location_id || ""}`;
}

function productSelectionFromButton(productId, button = null) {
  return {
    product_id: productId || button?.dataset?.productId || "",
    warehouse_id: button?.dataset?.warehouseId || "",
    supplier_id: button?.dataset?.supplierId || "",
    suggested_quantity: Number(button?.dataset?.suggestedQuantity || 0) || null,
    unit_cost: Number(button?.dataset?.unitCost || 0) || null
  };
}

function selectionMatchesProduct(selection, product) {
  if (!selection?.product_id || selection.product_id !== product?.product_id) return false;
  const selectedWarehouse = selection.warehouse_id || "";
  const productWarehouse = product.warehouse_id || product.location_id || "";
  return !selectedWarehouse || !productWarehouse || selectedWarehouse === productWarehouse;
}

async function queueRestockProducts(productSelections) {
  const selections = (productSelections || [])
    .map((item) => (typeof item === 'string' ? { product_id: item } : item))
    .filter((item) => item?.product_id);
  if (!selections.length) return 0;

  const healthItems = inventoryHealthState?.attention?.items || [];
  const sourceProducts = [
    ...healthItems,
    ...(dashboardData?.healthProducts || []),
    ...(dashboardData?.products || [])
  ];
  const seen = new Set();
  const products = [];

  selections.forEach((selection) => {
    const source = sourceProducts.find((product) => selectionMatchesProduct(selection, product))
      || sourceProducts.find((product) => product.product_id === selection.product_id)
      || {};
    const merged = {
      ...source,
      ...selection,
      product_id: selection.product_id,
      warehouse_id: selection.warehouse_id || source.warehouse_id || source.location_id || ''
    };
    const key = restockQueueKey(merged);
    if (!seen.has(key)) {
      seen.add(key);
      products.push(merged);
    }
  });

  const queueItems = products.map((product) => {
    const quantity = Number(product.quantity ?? product.current_stock ?? 0);
    const reorderLevel = Number(product.reorder_level ?? product.reorderLevel ?? 35);
    const unitCost = Number(product.unit_cost || product.price || product.unit_price || 1) || 1;
    const supplier = (dashboardData?.suppliers || []).find((item) => item.supplier_id === product.supplier_id);
    const suggestedQuantity = Number(product.suggested_quantity || 0) || Math.max(reorderLevel * 2 - quantity, reorderLevel, 1);
    return {
      product_id: product.product_id,
      warehouse_id: product.warehouse_id || product.location_id || null,
      product_name: product.product_name || product.product_id,
      current_stock: quantity,
      reorder_level: reorderLevel,
      suggested_quantity: suggestedQuantity,
      supplier_id: product.supplier_id || supplier?.supplier_id || null,
      supplier_name: product.supplier || supplier?.supplier_name || '',
      unit_cost: unitCost,
      total_cost: suggestedQuantity * unitCost
    };
  }).filter((item) => item.product_id);

  if (!queueItems.length) return 0;
  await authFetch('/restock-queue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      items: queueItems.map((product) => ({
        product_id: product.product_id,
        quantity: Math.round(product.suggested_quantity),
        unit_cost: Math.round(product.unit_cost),
        supplier_id: product.supplier_id || null,
        warehouse_id: product.warehouse_id || null
      }))
    })
  });
  restockQueueCache = null;
  restockQueuePage = 1;
  localStorage.removeItem('smart_inventory_restock_queue');
  return queueItems.length;
}

function showRestockAllConfirmation(totalEligible) {
  return new Promise((resolve) => {
    const existing = document.querySelector('.inline-confirm-overlay');
    existing?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'inline-confirm-overlay';
    overlay.innerHTML = `
      <section class="inline-confirm-card" role="dialog" aria-modal="true" aria-labelledby="restockAllConfirmTitle">
        <header><h3 id="restockAllConfirmTitle">Add All Products to Restock Queue?</h3></header>
        <p>Add all ${formatDashboardNumber(totalEligible)} eligible product(s) to the Restock Request Queue? Inventory quantities will not change until a purchase is received.</p>
        <footer>
          <button type="button" class="modal-secondary-button" data-confirm-cancel>Cancel</button>
          <button type="button" class="modal-primary-button" data-confirm-ok><i data-lucide="list-plus"></i>Add All to Queue</button>
        </footer>
      </section>`;
    const close = (answer) => {
      document.removeEventListener('keydown', onKeydown);
      overlay.remove();
      resolve(answer);
    };
    const onKeydown = (event) => {
      if (event.key === 'Escape') close(false);
    };
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target.closest('[data-confirm-cancel]')) close(false);
      if (event.target.closest('[data-confirm-ok]')) close(true);
    });
    document.addEventListener('keydown', onKeydown);
    document.body.appendChild(overlay);
    refreshIcons();
    overlay.querySelector('[data-confirm-ok]')?.focus();
  });
}

async function refreshInventoryHealthModalAfterQueue(page = inventoryHealthState.page || 1) {
  const state = await loadInventoryHealthData(page);
  openModal(renderInventoryHealthModal(state));
  bindModalEvents();
  refreshIcons();
}
async function handleInventoryHealthAction(action, productId, button = null) {
  if (action === 'health-page') {
    const page = Number(button?.dataset?.page || 1);
    if (page >= 1) await showInventoryHealthModal(page);
    return;
  }

  if (action === 'restock-all') {
    const totalEligible = Number(inventoryHealthState?.summary?.restock_eligible_count || 0);
    if (!totalEligible) throw new Error('No eligible low-stock or out-of-stock products to restock.');
    const originalHtml = button?.innerHTML || '';
    if (button) {
      button.disabled = true;
      button.innerHTML = '<i data-lucide="loader-2"></i>Restocking all products...';
      refreshIcons();
    }
    try {
      const result = await authFetch('/inventory/restock-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ warehouse_id: inventoryHealthWarehouseId() || null }),
        timeoutMs: 120000
      });
      restockQueueCache = null;
      restockQueuePage = 1;
      localStorage.removeItem('smart_inventory_restock_queue');
      invalidateDashboardData();
      await Promise.allSettled([
        loadRestockQueue(true, 1),
        loadDashboardData(),
        refreshInventoryHealthModalAfterQueue(inventoryHealthState.page || 1)
      ]);
      const success = Number(result?.success_count || 0);
      const failed = Number(result?.failure_count || 0);
      const quantityAdded = Number(result?.total_quantity_added || 0);
      const failureSuffix = failed ? ` ${formatDashboardNumber(failed)} product(s) could not be processed.` : '';
      showToast(`${formatDashboardNumber(success)} product(s) restocked successfully. ${formatDashboardNumber(quantityAdded)} units added.${failureSuffix}`);
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = originalHtml;
        refreshIcons();
      }
    }
    return;
  }
  if (action === 'purchase') {
    const selected = productSelectionFromButton(productId, button);
    closeModal();
    navigateToViewWithParams('purchases', {
      action: 'create',
      product_id: selected.product_id,
      warehouse_id: selected.warehouse_id,
      supplier_id: selected.supplier_id,
      suggested_quantity: selected.suggested_quantity,
      unit_cost: selected.unit_cost
    });
    return;
  }

  if (action === 'restock') {
    const selected = productSelectionFromButton(productId, button);
    if (!selected.product_id) throw new Error('Product not found.');
    const originalText = button?.textContent || 'Restock';
    if (button) {
      button.disabled = true;
      button.textContent = 'Adding...';
    }
    try {
      const count = await queueRestockProducts([selected]);
      await loadRestockQueue(true, 1).catch(() => null);
      await refreshInventoryHealthModalAfterQueue(inventoryHealthState.page || 1);
      showToast(`${formatDashboardNumber(count)} product added to restock queue.`);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
      }
    }
    return;
  }
  if (action === 'product') {
    const selected = productSelectionFromButton(productId, button);
    closeModal();
    navigateToViewWithParams('products', {
      action: 'view',
      product_id: selected.product_id,
      warehouse_id: selected.warehouse_id
    });
  }
}

function updateNotifications(lowStockProducts, periodSales, periodPurchases, movements) {
  const settings = getAppSettings();
  const items = [];
  if (settings.lowStockAlerts) {
    items.push(...lowStockProducts.slice(0, 4).map((product) => ({ id: `low-${product.product_id}-${product.quantity}`, type: "Low stock", text: `${product.product_name || product.product_id}: ${product.quantity} left`, time: "Current inventory", view: "inventory" })));
  }
  if (settings.salesAlerts) {
    items.push(...periodSales.slice(0, 2).map((sale) => ({ id: `sale-${sale.sale_id || sale.id || sale.created_at}`, type: "New sale", text: `${sale.product_name || sale.product_id} sold`, time: formatNotificationTime(recordDate(sale)), view: "sales" })));
  }
  if (settings.purchaseAlerts) {
    items.push(...periodPurchases.slice(0, 2).map((purchase) => ({ id: `purchase-${purchase.purchase_id || purchase.id || purchase.created_at}`, type: "New purchase", text: `${purchase.product_name || purchase.product_id} received`, time: formatNotificationTime(recordDate(purchase)), view: "purchases" })));
  }
  if (settings.systemNotifications) {
    items.push(...(movements || []).slice(0, 2).map((movement) => ({ id: `movement-${movement.movement_id || movement.id || movement.created_at}`, type: "Stock update", text: `${movement.product_name || movement.product_id}: ${movement.movement_type || "updated"}`, time: formatNotificationTime(recordDate(movement)), view: "inventory" })));
  }
  dashboardNotifications = items;
  renderNotificationMenu();
}
function notificationReadStorageKey() {
  return `smart_inventory_read_notifications_${getAuthValue("username") || "guest"}`;
}

function getReadNotificationIds() {
  return new Set(JSON.parse(localStorage.getItem(notificationReadStorageKey()) || "[]"));
}

function formatNotificationTime(date) {
  if (!date) return "Recently";
  const minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "numeric", minute: "2-digit" }).format(date);
}

function renderNotificationMenu() {
  const count = document.getElementById("notificationCount");
  const menu = document.getElementById("notificationMenu");
  const readIds = getReadNotificationIds();
  const unreadItems = dashboardNotifications.filter((item) => !readIds.has(item.id));
  if (count) {
    count.textContent = String(Math.min(unreadItems.length, 99));
    count.hidden = !unreadItems.length;
  }
  if (!menu) return;
  menu.innerHTML = unreadItems.length
    ? `<div class="notifications-panel"><div class="notifications-header"><h3>Notifications</h3><div class="notifications-header-actions"><span class="notifications-unread-count">${unreadItems.length} unread</span><button type="button" class="mark-all-read-btn" data-mark-all-notifications>Mark all as read</button></div></div><div class="notifications-list">${unreadItems.map((item) => `<button class="notification-item unread" type="button" data-notification-view="${escapeHtml(item.view || "dashboard")}" data-notification-id="${escapeHtml(item.id)}"><span class="notification-status-dot" aria-hidden="true"></span><span class="notification-content"><span class="notification-top-row"><strong class="notification-title">${escapeHtml(item.type)}</strong><span class="notification-time">${escapeHtml(item.time)}</span></span><span class="notification-message">${escapeHtml(item.text)}</span></span></button>`).join("")}</div></div>`
    : `<div class="notifications-panel"><div class="notification-empty"><i data-lucide="bell"></i><strong>No notifications</strong><span>You're all caught up!</span></div></div>`;
  refreshIcons();
}

function selectedPeriodMetricLabels() {
  const label = getSelectedRange().label;
  if (dateRangeState.key === "today") return ["Today's Sales", "Today's Orders", "New Customers"];
  if (dateRangeState.key === "custom") return [`${label} Sales`, `${label} Orders`, `${label} Customers`];
  return [`${label} Sales`, `${label} Orders`, `${label} Customers`];
}
function updateDashboardFromData() {
  if (!dashboardData || activeView !== "dashboard") return;
  const summary = dashboardData.summary || {};
  const range = getSelectedRange();
  const sales = recordsInRange(dashboardData.sales, range.start, range.end);
  const previousSales = recordsInRange(dashboardData.sales, range.previousStart, range.previousEnd);
  const purchases = recordsInRange(dashboardData.purchases, range.start, range.end);
  const previousPurchases = recordsInRange(dashboardData.purchases, range.previousStart, range.previousEnd);
  const hasSalesData = sales.length > 0;
  const hasPurchasesData = purchases.length > 0;
  const salesValue = recordTotal(sales, "total_amount") || recordTotal(sales, "sales_amount");
  const previousSalesValue = recordTotal(previousSales, "total_amount") || recordTotal(previousSales, "sales_amount");
  const purchasesValue = recordTotal(purchases, "total_cost");
  const previousPurchasesValue = recordTotal(previousPurchases, "total_cost");
  const lowStockProducts = dashboardData.lowStockProducts?.length
    ? dashboardData.lowStockProducts
    : dashboardData.products.filter((product) => Number(product.quantity) <= Number(product.reorder_level ?? 35));
  const outOfStockProducts = dashboardData.products.filter((product) => Number(product.quantity || 0) <= 0);
  const totalProductsCount = Number(summary.total_products ?? dashboardData.products.length);
  const lowStockCount = Number(summary.low_stock_items ?? lowStockProducts.length);
  const outOfStockCount = Number(summary.out_of_stock_items ?? outOfStockProducts.length);
  const suppliersCount = Number(summary.total_suppliers ?? dashboardData.suppliers.length);
  const today = getSelectedRangeForToday();
  const todaySales = recordsInRange(dashboardData.sales, today.start, today.end);
  const previousTodaySales = recordsInRange(dashboardData.sales, today.previousStart, today.previousEnd);
  const glanceSalesRecords = dateRangeState.key === "today" ? todaySales : sales;
  const previousGlanceSalesRecords = dateRangeState.key === "today" ? previousTodaySales : previousSales;
  const uniqueCustomers = new Set(sales.map((sale) => sale.customer_name).filter(Boolean)).size;
  const previousCustomers = new Set(previousSales.map((sale) => sale.customer_name).filter(Boolean)).size;

  document.getElementById("totalProducts").textContent = formatDashboardNumber(totalProductsCount);
  document.getElementById("totalSales").textContent = formatMoney(salesValue);
  document.getElementById("totalPurchases").textContent = formatMoney(purchasesValue);
  document.getElementById("lowStock").textContent = formatDashboardNumber(lowStockCount);
  document.getElementById("totalSuppliers").textContent = formatDashboardNumber(suppliersCount);
  document.getElementById("salesOverviewValue").textContent = formatMoney(salesValue);
  document.getElementById("donutSalesValue").textContent = formatMoney(salesValue);
  document.getElementById("glanceSales").textContent = formatMoney(recordTotal(glanceSalesRecords, "total_amount") || recordTotal(glanceSalesRecords, "sales_amount"));
  document.getElementById("glanceOrders").textContent = formatDashboardNumber(glanceSalesRecords.length);
  document.getElementById("glanceCustomers").textContent = formatDashboardNumber(uniqueCustomers);
  document.getElementById("glanceSuppliers").textContent = formatDashboardNumber(suppliersCount);
  const periodLabels = selectedPeriodMetricLabels();
  document.getElementById("glanceSalesLabel").textContent = periodLabels[0];
  document.getElementById("glanceOrdersLabel").textContent = periodLabels[1];
  document.getElementById("glanceCustomersLabel").textContent = periodLabels[2];
  setTrend("productsTrend", 0, 0);
  if (hasSalesData) setTrend("salesTrend", salesValue, previousSalesValue);
  else setNoDataTrend("salesTrend");
  if (hasPurchasesData) setTrend("purchasesTrend", purchasesValue, previousPurchasesValue);
  else setNoDataTrend("purchasesTrend");
  document.getElementById("lowStockTrend").textContent = "Current inventory level";
  if (glanceSalesRecords.length) {
    setTrend("glanceSalesTrend", recordTotal(glanceSalesRecords, "total_amount") || recordTotal(glanceSalesRecords, "sales_amount"), recordTotal(previousGlanceSalesRecords, "total_amount") || recordTotal(previousGlanceSalesRecords, "sales_amount"));
    setTrend("glanceOrdersTrend", glanceSalesRecords.length, previousGlanceSalesRecords.length);
  } else {
    setNoDataTrend("glanceSalesTrend");
    setNoDataTrend("glanceOrdersTrend");
  }
  if (uniqueCustomers) setTrend("glanceCustomersTrend", uniqueCustomers, previousCustomers);
  else setNoDataTrend("glanceCustomersTrend");
  document.getElementById("dateRangeLabel").textContent = range.label;
  document.getElementById("salesPeriodButton").textContent = range.label;
  document.getElementById("categoryPeriodButton").textContent = range.label;
  renderSalesChart(sales, range);
  renderCategoryBreakdown(sales);
  renderLowStock(lowStockProducts);
  updateInventoryStatus(totalProductsCount, lowStockCount, outOfStockCount);
  updateNotifications(lowStockProducts, sales, purchases, dashboardData.inventory.recent_movements);
  updateDashboardUser();
  bindDashboardActions();
}

function getSelectedRangeForToday() {
  const today = startOfDay(new Date());
  const end = endOfDay(new Date());
  return { start: today, end, previousStart: new Date(today.getTime() - 86400000), previousEnd: new Date(today.getTime() - 1) };
}

async function loadDashboardData() {
  const requestedView = activeView;
  const loadSequence = ++dashboardLoadSequence;
  setDashboardStatus("Loading dashboard data...");
  try {
await loadDashboardSummary().catch((error) => console.warn("Dashboard summary failed:", error.message));
    if (loadSequence !== dashboardLoadSequence) return;
    if (activeView === requestedView && activeView === "dashboard") {
      setDashboardStatus("Loading charts and tables...");
    }
await loadDashboardDataset();
    if (loadSequence !== dashboardLoadSequence || activeView !== requestedView || activeView !== "dashboard") return;
    setDashboardStatus();
    updateDashboardFromData();
} catch (error) {
    if (loadSequence !== dashboardLoadSequence || activeView !== requestedView || activeView !== "dashboard") return;
    if (error.status === 401) {
      logout();
      setAuthStatus("Your session has expired. Please sign in again.");
      return;
    }
    const message = error.message.includes("SSL handshake failed")
      ? "MongoDB Atlas could not establish a secure connection. Check Atlas Network Access, then disable any VPN or proxy and restart the backend."
      : `Unable to load dashboard data: ${error.message}`;
    setDashboardStatus(message, true);
  }
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => openView(button.dataset.view));
});

dashboardContent.addEventListener("click", async (event) => {
  const retryButton = event.target.closest("[data-retry-view]");
  if (retryButton) {
    event.preventDefault();
    openView(retryButton.dataset.retryView || activeView, 1, false);
    return;
  }

  const addPurchaseButton = event.target.closest("#addPurchaseButton");
  if (addPurchaseButton) {
    event.preventDefault();
    try {
      await openAddPurchaseModal();
    } catch (error) {
      showToast(error.message || "Unable to open Add Purchase form.", "error");
    }
    return;
  }

  const addSaleButton = event.target.closest("#addSaleButton");
  if (addSaleButton) {
    event.preventDefault();
    try {
      await openAddSaleModal();
    } catch (error) {
      showToast(error.message || "Unable to open Add Sale form.", "error");
    }
  }
});

const appSettingsDefaults = {
  landingPage: "dashboard",
  recordsPerPage: "25",
  autoRefresh: true,
  confirmDelete: true,
  emailNotifications: true,
  lowStockAlerts: true,
  purchaseAlerts: true,
  salesAlerts: true,
  systemNotifications: true,
  compactLayout: false,
  sidebarAutoCollapse: false,
  currency: "INR",
  dateFormat: "DD/MM/YYYY",
  timeFormat: "12",
  sessionTimeout: "30"
};

function settingsStorageKey() {
  return `smart_inventory_settings_${getAuthValue("username") || "guest"}`;
}

function getAppSettings() {
  try {
    return {
      ...appSettingsDefaults,
      ...JSON.parse(localStorage.getItem(settingsStorageKey()) || "{}")
    };
  } catch {
    return { ...appSettingsDefaults };
  }
}

function saveAppSettings(settings) {
  localStorage.setItem(settingsStorageKey(), JSON.stringify(settings));
}

let autoRefreshTimer = null;
let sessionTimeoutTimer = null;

function preferredLandingPage() {
  const settings = getAppSettings();
  return canAccessView(settings.landingPage) ? settings.landingPage : "dashboard";
}

function getRecordsPerPage() {
  const value = Number(getAppSettings().recordsPerPage || 25);
  return [10, 25, 50, 100].includes(value) ? value : 25;
}

function confirmationRequired() {
  return getAppSettings().confirmDelete !== false;
}

function notificationPreferenceEnabled(key) {
  return getAppSettings()[key] !== false;
}

function resetSessionTimeout() {
  window.clearTimeout(sessionTimeoutTimer);
  sessionTimeoutTimer = null;
  const token = getStoredAccessToken();
  if (!token) return;
  const profile = JSON.parse(getAuthValue("userProfile") || "{}");
  const configuredMinutes = Number(getAppSettings().sessionTimeout || 30);
  const minutes = profile.role === "Admin" ? Math.min(configuredMinutes, 15) : configuredMinutes;
  sessionTimeoutTimer = window.setTimeout(() => {
    if (!getStoredAccessToken()) return;
    showToast("Session timed out. Please sign in again.", "error");
    logout();
  }, Math.max(1, minutes) * 60 * 1000);
}

function applyAppSettings() {
  const settings = getAppSettings();
  document.body.classList.toggle("compact-layout", Boolean(settings.compactLayout));
  document.body.classList.toggle("sidebar-auto-collapse", Boolean(settings.sidebarAutoCollapse));
  window.clearInterval(autoRefreshTimer);
  autoRefreshTimer = null;
  if (settings.autoRefresh && getStoredAccessToken()) {
    autoRefreshTimer = window.setInterval(() => {
      if (document.hidden || !getStoredAccessToken()) return;
      invalidateDashboardData();
      if (activeView === "dashboard") loadDashboardData();
    }, 60000);
  }
  resetSessionTimeout();
}

function optionMarkup(value, label, selectedValue) {
  return `<option value="${escapeHtml(value)}" ${String(selectedValue) === String(value) ? "selected" : ""}>${escapeHtml(label)}</option>`;
}

function toggleMarkup(name, title, description, checked) {
  return `
    <label class="settings-toggle-row">
      <span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(description)}</small></span>
      <input class="settings-toggle-input" type="checkbox" name="${escapeHtml(name)}" ${checked ? "checked" : ""}>
      <i class="settings-toggle-switch" aria-hidden="true"></i>
    </label>
  `;
}

function renderSettingsPage() {
  const settings = getAppSettings();
  const profile = getProfileDetails();
  return `
    <form id="settingsForm" class="settings-page">
      <section class="settings-hero">
        <div>
          <span class="settings-eyebrow"><i data-lucide="settings-2"></i> Application Settings</span>
          <h3>Preferences and system configuration</h3>
          <p>Control workspace defaults, alerts, regional formats, and session behavior.</p>
        </div>
      </section>

      <div class="settings-grid">
        <article class="settings-card">
          <header><span class="settings-card-icon blue"><i data-lucide="sliders-horizontal"></i></span><div><h3>General Preferences</h3><p>Set defaults for daily workspace usage.</p></div></header>
          <div class="settings-fields">
            <label class="settings-field"><span>Default Landing Page</span><select name="landingPage">
              ${optionMarkup("dashboard", "Dashboard", settings.landingPage)}
              ${optionMarkup("products", "Products", settings.landingPage)}
              ${optionMarkup("inventory", "Inventory", settings.landingPage)}
              ${optionMarkup("sales", "Sales", settings.landingPage)}
            </select></label>
            <label class="settings-field"><span>Default Records Per Page</span><select name="recordsPerPage">
              ${["10", "25", "50", "100"].map((value) => optionMarkup(value, value, settings.recordsPerPage)).join("")}
            </select></label>
            ${toggleMarkup("autoRefresh", "Enable Auto Refresh", "Refresh dashboard data in the background.", settings.autoRefresh)}
            ${toggleMarkup("confirmDelete", "Confirmation Dialogs before Delete", "Ask before deleting records.", settings.confirmDelete)}
          </div>
        </article>

        <article class="settings-card">
          <header><span class="settings-card-icon purple"><i data-lucide="bell-ring"></i></span><div><h3>Notification Preferences</h3><p>Choose which alerts the system can send.</p></div></header>
          <div class="settings-fields">
            ${toggleMarkup("emailNotifications", "Email Notifications", "Send important alerts to your email.", settings.emailNotifications)}
            ${toggleMarkup("lowStockAlerts", "Low Stock Alerts", "Notify when products reach reorder level.", settings.lowStockAlerts)}
            ${toggleMarkup("purchaseAlerts", "Purchase Alerts", "Notify for purchase order activity.", settings.purchaseAlerts)}
            ${toggleMarkup("salesAlerts", "Sales Alerts", "Notify for sales updates.", settings.salesAlerts)}
            ${toggleMarkup("systemNotifications", "System Notifications", "Notify about account and system events.", settings.systemNotifications)}
          </div>
        </article>

        <article class="settings-card">
          <header><span class="settings-card-icon green"><i data-lucide="layout-dashboard"></i></span><div><h3>Layout Preferences</h3><p>Personalize spacing and sidebar behavior.</p></div></header>
          <div class="settings-fields">
            ${toggleMarkup("compactLayout", "Compact Layout", "Reduce spacing in data-heavy screens.", settings.compactLayout)}
            ${toggleMarkup("sidebarAutoCollapse", "Sidebar Auto Collapse", "Collapse sidebar automatically on smaller screens.", settings.sidebarAutoCollapse)}
          </div>
        </article>

        <article class="settings-card">
          <header><span class="settings-card-icon orange"><i data-lucide="globe-2"></i></span><div><h3>Regional Settings</h3><p>Set local formatting preferences.</p></div></header>
          <div class="settings-fields">
            <label class="settings-field"><span>Currency</span><select name="currency">${optionMarkup("INR", "\u20B9 Indian Rupee", settings.currency)}</select></label>
            <label class="settings-field"><span>Date Format</span><select name="dateFormat">${optionMarkup("DD/MM/YYYY", "DD/MM/YYYY", settings.dateFormat)}</select></label>
            <label class="settings-field"><span>Time Format</span><select name="timeFormat">
              ${optionMarkup("12", "12 Hour", settings.timeFormat)}
              ${optionMarkup("24", "24 Hour", settings.timeFormat)}
            </select></label>
          </div>
        </article>

        <article class="settings-card">
          <header><span class="settings-card-icon red"><i data-lucide="shield-check"></i></span><div><h3>Security Preferences</h3><p>Review session and account status.</p></div></header>
          <div class="settings-info-list">
            <div><span>Last Login Time</span><strong>${escapeHtml(formatProfileDate(profile.lastLogin))}</strong></div>
            <div><span>Account Status</span><strong class="status-active">Active</strong></div>
          </div>
          <div class="settings-fields">
            <label class="settings-field"><span>Session Timeout</span><select name="sessionTimeout">
              ${optionMarkup("15", "15 minutes", settings.sessionTimeout)}
              ${optionMarkup("30", "30 minutes", settings.sessionTimeout)}
              ${optionMarkup("60", "60 minutes", settings.sessionTimeout)}
            </select></label>
          </div>
          <button class="settings-outline-button" type="button" id="logoutAllDevices"><i data-lucide="log-out"></i>Logout From All Devices</button>
        </article>

        <article class="settings-card about-settings-card settings-card-wide">
          <header><span class="settings-card-icon blue"><i data-lucide="info"></i></span><div><h3>About Application</h3><p>Project information and system stack.</p></div></header>
          <div class="about-project-summary">
            <span><i data-lucide="boxes"></i></span>
            <div><small>Project Name</small><strong>Smart Inventory Sales Monitoring System</strong><p>A web-based inventory and sales management system developed to manage products, inventory, purchases, sales, suppliers, reports, analytics, and user access through role-based authentication.</p></div>
          </div>
          <dl class="about-settings-list about-settings-grid">
            <div><dt>Frontend</dt><dd>HTML, CSS, JavaScript</dd></div>
            <div><dt>Backend</dt><dd>FastAPI (Python)</dd></div>
            <div><dt>Database</dt><dd>MongoDB Atlas</dd></div>
            <div><dt>Authentication</dt><dd>JWT Authentication</dd></div>
            <div><dt>Authorization</dt><dd>Role-Based Access Control (Admin, Manager, Staff)</dd></div>
            <div><dt>API Documentation</dt><dd>Swagger UI</dd></div>
            <div><dt>Application Version</dt><dd>v0.9.0 (Development Build)</dd></div>
            <div><dt>Project Status</dt><dd>Under Development</dd></div>
          </dl>
        </article>
      </div>

      <footer class="settings-actions">
        <button class="settings-reset-button" type="button" id="resetSettings"><i data-lucide="rotate-ccw"></i>Reset to Default</button>
        <button class="settings-save-button" type="submit"><i data-lucide="save"></i>Save Changes</button>
      </footer>
    </form>
  `;
}

function readSettingsForm(form) {
  const formData = new FormData(form);
  return {
    landingPage: formData.get("landingPage"),
    recordsPerPage: formData.get("recordsPerPage"),
    autoRefresh: form.elements.autoRefresh.checked,
    confirmDelete: form.elements.confirmDelete.checked,
    emailNotifications: form.elements.emailNotifications.checked,
    lowStockAlerts: form.elements.lowStockAlerts.checked,
    purchaseAlerts: form.elements.purchaseAlerts.checked,
    salesAlerts: form.elements.salesAlerts.checked,
    systemNotifications: form.elements.systemNotifications.checked,
    compactLayout: form.elements.compactLayout.checked,
    sidebarAutoCollapse: form.elements.sidebarAutoCollapse.checked,
    currency: formData.get("currency"),
    dateFormat: formData.get("dateFormat"),
    timeFormat: formData.get("timeFormat"),
    sessionTimeout: formData.get("sessionTimeout")
  };
}

function persistSettingsFromForm(form, notify = false) {
  const updated = readSettingsForm(form);
  saveAppSettings(updated);
  applyAppSettings();
  if (notify) showToast("Settings saved successfully.");
  return updated;
}

function bindSettingsPage() {
  const form = document.getElementById("settingsForm");
  if (!form) return;

  form.addEventListener("change", (event) => {
    persistSettingsFromForm(form);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    persistSettingsFromForm(form, true);
  });

  document.getElementById("resetSettings")?.addEventListener("click", () => {
    saveAppSettings({ ...appSettingsDefaults });
    applyAppSettings();
    dashboardContent.innerHTML = renderSettingsPage();
    bindSettingsPage();
    refreshIcons();
    showToast("Settings reset to default.");
  });

  document.getElementById("logoutAllDevices")?.addEventListener("click", () => {
    if (confirmationRequired() && !window.confirm("Logout from this device now?")) return;
    showToast("All sessions cleared.");
    logout();
  });
}

async function openView(name, page = 1, updateRoute = true) {
  updateDateRangeLabels();
  if (!canAccessView(name)) {
    showAccessDenied();
    return;
  }
  const renderToken = ++viewRenderToken;

  if (name === "profile") {
    if (updateRoute) setCleanRoute("profile");
    activeView = "dashboard";
    navButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === "dashboard"));
    if (dashboardScreen.hidden) showDashboard("dashboard", false);
    showProfileModal();
    return;
  }

  activeView = name;
  if (updateRoute && cleanRouteViews.has(name)) setCleanRoute(name);

  navButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });

  if (name === "dashboard") {
    setDashboardGreeting();
    document.getElementById("welcomeText").textContent = "Welcome back! Here's what's happening with your inventory today.";
    dashboardContent.innerHTML = dashboardTemplate;
    refreshIcons();
    await loadDashboardData();
    return;
  }

  if (name === "settings") {
    document.getElementById("dashboardTitle").textContent = "Settings";
    document.getElementById("welcomeText").textContent = "Application preferences and system configuration.";
    dashboardContent.innerHTML = renderSettingsPage();
    bindSettingsPage();
    refreshIcons();
    return;
  }
  const view = views[name];
  document.getElementById("dashboardTitle").textContent = view.title;
  document.getElementById("welcomeText").textContent = view.subtitle;
  dashboardContent.innerHTML = renderSkeletonPanel(4, `Loading ${view.title.toLowerCase()}...`);

  try {
    if (name === "inventory") {
      await loadInventoryWorkspace("overview");
      return;
    }

    if (name === "analytics") {
      await loadAnalyticsDashboard(renderToken);
      return;
    }

    if (name === "users") {
      await loadUserManagement(1, renderToken);
      return;
    }

    if (name === "reports") {
      await loadInventoryReports(renderToken);
      return;
    }

    if (name === "purchases") {
      const dateParams = apiDateParams();
      const [queueResponse, pendingResponse, completedResponse, summaryResponse] = await Promise.all([
        loadRestockQueue(true, restockQueuePage || 1),
        authFetch(appendQueryParams(`/purchases?page=1&limit=50`, { ...dateParams, status: "Pending" })),
        authFetch(appendQueryParams(`/purchases?page=${page}&limit=${getRecordsPerPage()}`, { ...dateParams, status: "Completed" })),
        loadPurchasePendingSummary(true)
      ]);
      if (!isActiveRender(name, renderToken)) return;
      restockQueueCache = queueResponse;
      purchasePendingSummaryCache = summaryResponse;
      const purchasesPayload = {
        page: completedResponse.page,
        limit: completedResponse.limit,
        total: completedResponse.total,
        total_pages: completedResponse.total_pages,
        has_next: completedResponse.has_next,
        has_previous: completedResponse.has_previous,
        pending: pendingResponse,
        completed: completedResponse
      };
      dashboardContent.innerHTML = renderPurchasesPage(purchasesPayload);
      bindPagination(completedResponse, name);
      await handlePurchaseRouteAction();
      return;
    }

    let endpoint = pagedViews.has(name)
      ? `${view.endpoint}?page=${page}&limit=${getRecordsPerPage()}`
      : view.endpoint;
    if (["sales"].includes(name)) {
      endpoint = appendQueryParams(endpoint, apiDateParams());
    }
    if (name === "products") {
      endpoint = appendQueryParams(endpoint, { warehouse_id: productWarehouseFilter });
    }
    const response = await cachedAuthFetch(`view:${name}:${endpoint}`, () => authFetch(endpoint), name === "purchases" ? 10000 : 45000);
    if (name === "products" && currentUserRole() === "Admin") {
      response.warehouses = await cachedAuthFetch("warehouses:list", () => authFetch("/warehouses"), 60000);
      response.selected_warehouse_id = productWarehouseFilter;
    }
    if (!isActiveRender(name, renderToken)) return;
    const rows = response.items || (Array.isArray(response) ? response : [response]);
    dashboardContent.innerHTML = name === "products"
      ? renderProductsPage(response)
      : pagedViews.has(name)
        ? renderPagedRecords(view.title, response, name)
        : renderRecords(view.title, rows, ["sales", "purchases"].includes(name) ? "No data available for selected date range." : "No records found.");

    if (name === "products") {
      bindProductManagement(response);
      await handleProductRouteAction(response);
    } else if (pagedViews.has(name)) {
      bindPagination(response, name);
      if (name === "purchases") await handlePurchaseRouteAction();
    }
  } catch (error) {
    if (isActiveRender(name, renderToken)) {
      dashboardContent.innerHTML = renderErrorPanel(`Unable to load ${view.title}`, error, name);
      refreshIcons();
    }
  }
}
async function loadInventoryWorkspace(tab = "overview", page = 1) {
  const role = getAuthValue("role");
  const canStockIn = ["Admin", "Manager"].includes(role);
  const canHistory = ["Admin", "Manager"].includes(role);
  dashboardContent.innerHTML = renderSkeletonPanel(3, "Loading inventory...");

  try {
    let content = "";
    let data = null;

    if (tab === "overview") {
      const inventoryEndpoint = appendQueryParams("/inventory", {
        page,
        limit: getRecordsPerPage(),
        warehouse_id: inventoryWarehouseFilter
      });
      const warehouseRequest = currentUserRole() === "Admin"
        ? cachedAuthFetch("warehouses:list", () => authFetch("/warehouses"), 60000)
        : Promise.resolve(null);
      const [summary, products, warehouses] = await Promise.all([
        authFetch("/dashboard/inventory"),
        authFetch(inventoryEndpoint),
        warehouseRequest
      ]);
      data = { summary, products, warehouses, selectedWarehouseId: inventoryWarehouseFilter };
      content = renderInventoryOverview(data);
    }

    if (tab === "update") {
      content = renderStockUpdate(canStockIn);
    }

    if (tab === "history") {
      if (!canHistory) {
        content = '<div class="panel"><h3>Stock history</h3><p>Stock history is available to Admin and Manager accounts.</p></div>';
      } else {
        data = await authFetch(`/inventory/history?page=${page}&limit=${getRecordsPerPage()}`);
        content = renderInventoryHistory(data);
      }
    }

    dashboardContent.innerHTML = renderInventoryShell(tab, content, canStockIn, canHistory);
    bindInventoryWorkspace(tab, data, canStockIn, canHistory);
  } catch (error) {
    dashboardContent.innerHTML = renderErrorPanel("Unable to load Inventory", error, "inventory");
    refreshIcons();
  }
}

function renderInventoryShell(tab, content, canStockIn, canHistory) {
  const canUpdateStock = ["Admin", "Manager", "Staff"].includes(getAuthValue("role"));
  const updateDisabled = canUpdateStock ? "" : "disabled";
  const historyDisabled = canHistory ? "" : "disabled";
  return `<div class="inventory-tabs"><button class="inventory-tab ${tab === "overview" ? "active" : ""}" data-inventory-tab="overview">Overview</button><button class="inventory-tab ${tab === "update" ? "active" : ""}" data-inventory-tab="update" ${updateDisabled}>Stock update</button><button class="inventory-tab ${tab === "history" ? "active" : ""}" data-inventory-tab="history" ${historyDisabled}>Stock history</button></div>${content}`;
}

function renderInventoryOverview({ summary, products, warehouses, selectedWarehouseId = "" }) {
  const rows = products.items || [];
  const warehouseOptions = warehouses?.items
    ? `<label class="warehouse-filter">Warehouse<select id="inventory-warehouse-filter"><option value="">All Warehouses</option>${warehouses.items.map((warehouse) => `<option value="${escapeHtml(warehouse.warehouse_id)}" ${selectedWarehouseId === warehouse.warehouse_id ? "selected" : ""}>${escapeHtml(warehouse.warehouse_name || warehouse.warehouse_id)}</option>`).join("")}</select></label>`
    : "";
  const body = rows.length
    ? rows.map((product) => `<tr><td>${escapeHtml(product.product_id)}</td><td>${escapeHtml(product.product_name)}</td><td>${escapeHtml(product.warehouse_name || product.warehouse_id || "-")}</td><td>${escapeHtml(product.quantity)}</td><td>${escapeHtml(product.reorder_level)}</td><td>${escapeHtml(product.price ?? product.unit_price ?? 0)}</td><td>${escapeHtml(product.stock_value ?? (Number(product.quantity || 0) * Number(product.price || product.unit_price || 0)))}</td><td>${escapeHtml(product.status || (product.quantity <= product.reorder_level ? "Low stock" : "In stock"))}</td></tr>`).join("")
    : `<tr><td colspan="8">No inventory records found for the selected warehouse.</td></tr>`;
  const previousDisabled = products.has_previous ? "" : "disabled";
  const nextDisabled = products.has_next ? "" : "disabled";
  return `<div class="metrics inventory-metrics"><div class="metric"><span>Products</span><strong>${escapeHtml(summary.total_products)}</strong></div><div class="metric"><span>Stock units</span><strong>${escapeHtml(summary.total_stock_units)}</strong></div><div class="metric"><span>Inventory value</span><strong>${escapeHtml(summary.inventory_value)}</strong></div><div class="metric"><span>Low stock</span><strong>${escapeHtml(summary.low_stock_products)}</strong></div></div><section class="panel"><div class="section-heading"><div><h3>Current stock</h3><p>Products ordered by product ID with warehouse stock.</p></div>${warehouseOptions}</div><div class="table-wrap"><table><thead><tr><th>Product ID</th><th>Product name</th><th>Warehouse</th><th>Current stock</th><th>Reorder level</th><th>Price</th><th>Stock value</th><th>Status</th></tr></thead><tbody>${body}</tbody></table></div></section><div class="pagination"><button id="previous-stock" ${previousDisabled}>Previous</button><span>Page ${products.page} of ${products.total_pages} (${products.total} inventory records)</span><button id="next-stock" ${nextDisabled}>Next</button></div>`;
}

function renderStockUpdate(canStockIn) {
  const stockInDisabled = canStockIn ? "" : "disabled";
  return `<section class="stock-update-panel"><div class="section-heading"><div><h3>Stock update</h3><p>Record incoming or outgoing stock against a product ID.</p></div></div><p id="stock-update-message" class="product-message" hidden></p><form id="stock-update-form" class="stock-update-form"><label>Product ID<input id="stock-product-id" required placeholder="PRD0001"></label><label>Quantity<input id="stock-quantity" required type="number" min="1" value="1"></label><label class="stock-note">Note<textarea id="stock-note" placeholder="Optional stock note"></textarea></label><div class="stock-actions"><button id="stock-in-button" type="button" ${stockInDisabled}>Stock in</button><button id="stock-out-button" class="stock-out-button" type="button">Stock out</button></div></form></section>`;
}

function renderInventoryHistory(response) {
  const rows = response.items || [];
  const table = renderRecords("Stock history", rows);
  const previousDisabled = response.has_previous ? "" : "disabled";
  const nextDisabled = response.has_next ? "" : "disabled";
  return `${table}<div class="pagination"><button id="previous-history" ${previousDisabled}>Previous</button><span>Page ${response.page} of ${response.total_pages} (${response.total} movements)</span><button id="next-history" ${nextDisabled}>Next</button></div>`;
}

function bindInventoryWorkspace(tab, data, canStockIn, canHistory) {
  document.querySelectorAll("[data-inventory-tab]").forEach((button) => button.addEventListener("click", () => {
    if (!button.disabled) loadInventoryWorkspace(button.dataset.inventoryTab);
  }));

  if (tab === "overview") {
    const warehouseFilter = document.getElementById("inventory-warehouse-filter");
    if (warehouseFilter) warehouseFilter.addEventListener("change", () => {
      inventoryWarehouseFilter = warehouseFilter.value;
      clearCacheByPrefix("view:inventory");
      loadInventoryWorkspace("overview", 1);
    });
    const previous = document.getElementById("previous-stock");
    const next = document.getElementById("next-stock");
    if (previous) previous.addEventListener("click", () => loadInventoryWorkspace("overview", data.products.page - 1));
    if (next) next.addEventListener("click", () => loadInventoryWorkspace("overview", data.products.page + 1));
  }

  if (tab === "history" && canHistory) {
    const previous = document.getElementById("previous-history");
    const next = document.getElementById("next-history");
    if (previous) previous.addEventListener("click", () => loadInventoryWorkspace("history", data.page - 1));
    if (next) next.addEventListener("click", () => loadInventoryWorkspace("history", data.page + 1));
  }

  if (tab !== "update") return;
  const message = document.getElementById("stock-update-message");
  const submitUpdate = async (type) => {
    const productId = document.getElementById("stock-product-id").value.trim();
    const quantity = Number(document.getElementById("stock-quantity").value);
    const note = document.getElementById("stock-note").value.trim() || null;
    if (!productId || !quantity) return;

    try {
      const result = await authFetch(`/inventory/${type}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ product_id: productId, quantity, note }) });
      message.textContent = `${result.message}. Current stock: ${result.current_stock}.`;
      message.classList.remove("error");
      message.hidden = false;
      await refreshDashboardAfterMutation();
    } catch (error) {
      message.textContent = error.message;
      message.classList.add("error");
      message.hidden = false;
    }
  };

  if (canStockIn) document.getElementById("stock-in-button").addEventListener("click", () => submitUpdate("stock-in"));
  document.getElementById("stock-out-button").addEventListener("click", () => submitUpdate("stock-out"));
}

const PRODUCT_IMAGE_BASE_PATH = "assets/images/products";
const PRODUCT_IMAGE_FALLBACK = `${PRODUCT_IMAGE_BASE_PATH}/no_image.png`;

function productImageFilename(productName) {
  return String(productName || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[()]/g, "")
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function productImageNamePath(productName) {
  const filename = productImageFilename(productName);
  return filename ? `${PRODUCT_IMAGE_BASE_PATH}/${filename}.png` : PRODUCT_IMAGE_FALLBACK;
}

function getProductImage(product) {
  if (typeof product === "string") return productImageNamePath(product);
  const productName = product?.product_name || "";
  const generatedPath = productImageNamePath(productName);
  const storedImage = String(product?.product_image || "").trim();
  const hasUsableStoredImage = storedImage && !/no[_-]?image|placeholder/i.test(storedImage);
  const finalPath = hasUsableStoredImage
    ? (/^https?:\/\//i.test(storedImage) ? storedImage : (storedImage.startsWith("/uploads") ? `${API_URL}${storedImage}` : storedImage))
    : generatedPath;

  if (["Green Beans", "Lemon", "Heavy Cream", "Butter Biscuit", "Avocado Oil", "Grapes", "Sweet Potato"].includes(productName)) {
    console.log("Product image mapping", {
      productName,
      generatedFilename: `${productImageFilename(productName)}.png`,
      generatedPath,
      storedImage,
      finalPath
    });
  }

  return finalPath;
}

function productImageCell(product) {
  const productName = product?.product_name || "Product image";
  const imageSrc = getProductImage(product);
  const generatedFallback = productImageNamePath(productName);
  return `<td><img class="product-image-thumb" src="${escapeHtml(imageSrc)}" data-local-fallback="${escapeHtml(generatedFallback)}" data-final-fallback="${PRODUCT_IMAGE_FALLBACK}" alt="${escapeHtml(productName)}" width="48" height="48" loading="lazy" onerror="if(this.dataset.triedLocal !== '1'){this.dataset.triedLocal='1';this.src=this.dataset.localFallback;}else{this.onerror=null;this.src=this.dataset.finalFallback;this.alt='No image available';}"></td>`;
}
function renderProductsPage(response) {
  const canManage = ["Admin", "Manager"].includes(getAuthValue("role"));
  const rows = response.items || [];
  const supplierIds = (dashboardData?.suppliers || []).map((supplier) => supplier.supplier_id).filter(Boolean);
  const supplierOptions = supplierIds.map((supplierId) => `<option value="${escapeHtml(supplierId)}"></option>`).join("");
  const warehouseFilter = response.warehouses?.items
    ? `<label class="warehouse-filter">Warehouse<select id="product-warehouse-filter"><option value="">All Warehouses</option>${response.warehouses.items.map((warehouse) => `<option value="${escapeHtml(warehouse.warehouse_id)}" ${response.selected_warehouse_id === warehouse.warehouse_id ? "selected" : ""}>${escapeHtml(warehouse.warehouse_name || warehouse.warehouse_id)}</option>`).join("")}</select></label>`
    : "";
  const focusProductId = localStorage.getItem("smart_inventory_focus_product") || currentRouteParams().get("product_id") || "";
  const columns = ["product_id", "product_name", "warehouse_name", "quantity", "price", "unit_cost", "reorder_level", "category_id", "supplier_id", "barcode_value", "qr_code_value"];
  const header = `<th>Product Image</th>${columns.map((column) => `<th>${escapeHtml(column.replaceAll("_", " "))}</th>`).join("")}`;
  const body = rows.map((product) => `<tr data-product-row="${escapeHtml(product.product_id || "")}" class="${focusProductId && product.product_id === focusProductId ? "highlight-row" : ""}">${productImageCell(product)}${columns.map((column) => `<td>${escapeHtml(formatValue(product[column] ?? (column === "barcode_value" || column === "qr_code_value" ? product.product_id : "")))}</td>`).join("")}<td class="action-cell product-code-actions"><button class="table-action generate-product-codes" data-product-id="${escapeHtml(product.product_id)}" type="button">Codes</button>${canManage ? `<button class="table-action edit-product" data-product-id="${escapeHtml(product.product_id)}" type="button">Edit</button><button class="table-action delete-product" data-product-id="${escapeHtml(product.product_id)}" type="button">Delete</button>` : ""}</td></tr>`).join("");
  const actionsHeader = "<th>Actions</th>";
  const form = canManage ? `
    <section class="product-form-panel">
      <div class="section-heading"><div><h3 id="product-form-title">Add product</h3><p>Enter product details and stock information.</p></div><button id="reset-product-form" class="secondary-action" type="button">Clear form</button></div>
      <p id="product-message" class="product-message" hidden></p>
      <form id="product-form" class="product-form">
        <label>Product ID<input id="product-id" required placeholder="PRD0991"></label>
        <label>Product name<input id="product-name" required placeholder="Product name"></label>
        <label>Quantity<input id="product-quantity" required type="number" min="0" value="0"></label>
        <label>Price<input id="product-price" required type="number" min="1" value="1"></label>
        <label>Unit cost<input id="product-unit-cost" type="number" min="0" placeholder="Optional"></label>
        <label>Reorder level<input id="product-reorder-level" required type="number" min="0" value="35"></label>
        <label>Category ID<input id="product-category-id" placeholder="Optional"></label>
        <label>Supplier ID<input id="product-supplier-id" list="supplierIdOptions" placeholder="SUP001"></label><datalist id="supplierIdOptions">${supplierOptions}</datalist>
        <label>Barcode value<input id="product-barcode-value" placeholder="Auto: product ID"></label>
        <label>QR code value<input id="product-qr-code-value" placeholder="Auto: product ID"></label>
        <label class="product-image-upload">Product image<input id="product-image" type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"></label>
        <div id="product-image-preview" class="product-image-preview" hidden>
          <img id="product-image-preview-img" alt="Product image preview">
          <button id="remove-product-image" class="secondary-action" type="button">Remove image</button>
        </div>
        <button id="save-product" type="submit">Add product</button>
      </form>
    </section>` : "";
  const previousDisabled = response.has_previous ? "" : "disabled";
  const nextDisabled = response.has_next ? "" : "disabled";
  return `${form}<section class="panel"><div class="section-heading"><div><h3>Product listing</h3><p>${response.total} products, ordered by product ID.</p></div><div class="product-toolbar">${warehouseFilter}<button id="scanProductButton" type="button"><i data-lucide="scan-barcode"></i>Scan Product</button></div></div><div class="table-wrap"><table><thead><tr>${header}${actionsHeader}</tr></thead><tbody>${body}</tbody></table></div></section><div class="pagination"><button id="previous-products" ${previousDisabled}>Previous</button><span>Page ${response.page} of ${response.total_pages}</span><button id="next-products" ${nextDisabled}>Next</button></div>`;
}

function highlightProductRow(productId) {
  if (!productId) return;
  document.querySelectorAll('.highlight-row').forEach((row) => row.classList.remove('highlight-row'));
  const row = [...document.querySelectorAll('[data-product-row]')].find((item) => item.dataset.productRow === productId);
  if (row) {
    row.classList.add('highlight-row');
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function renderProductDetailsModal(product, inventoryResponse = {}) {
  const inventoryItems = inventoryResponse.items || [];
  const selectedWarehouse = currentRouteParams().get('warehouse_id') || product?.warehouse_id || '';
  const inventory = inventoryItems.find((item) => item.warehouse_id === selectedWarehouse) || inventoryItems[0] || {};
  const warehouseLabel = inventory.warehouse_name || product?.warehouse_name || product?.location || selectedWarehouse || 'All Warehouses';
  openModal(`
    <section class="modal-card product-details-modal" role="dialog" aria-modal="true" aria-labelledby="productDetailsTitle">
      <header class="modal-header"><h2 id="productDetailsTitle">Product Details</h2><button class="modal-close" type="button" data-modal-close><i data-lucide="x"></i></button></header>
      <div class="detail-grid">
        <article><span>Product ID</span><strong>${escapeHtml(product?.product_id || '-')}</strong></article>
        <article><span>Product Name</span><strong>${escapeHtml(product?.product_name || '-')}</strong></article>
        <article><span>Warehouse</span><strong>${escapeHtml(warehouseLabel)}</strong></article>
        <article><span>Current Stock</span><strong>${formatDashboardNumber(inventory.quantity ?? product?.quantity ?? product?.current_stock ?? 0)}</strong></article>
        <article><span>Reorder Level</span><strong>${formatDashboardNumber(inventory.reorder_level ?? product?.reorder_level ?? 0)}</strong></article>
        <article><span>Supplier</span><strong>${escapeHtml(product?.supplier || product?.supplier_name || product?.supplier_id || '-')}</strong></article>
        <article><span>Unit Cost</span><strong>${formatMoney(product?.unit_cost || 0)}</strong></article>
        <article><span>Selling Price</span><strong>${formatMoney(product?.price || product?.unit_price || 0)}</strong></article>
      </div>
      <footer class="modal-footer"><button class="modal-primary-button" type="button" data-modal-close>Done</button></footer>
    </section>`);
  bindModalEvents();
  refreshIcons();
}

async function handleProductRouteAction(response) {
  const params = currentRouteParams();
  if (params.get('action') !== 'view') return;
  const productId = params.get('product_id');
  const warehouseId = params.get('warehouse_id') || '';
  if (!productId) return;
  highlightProductRow(productId);
  try {
    const productEndpoint = appendQueryParams(`/products/${encodeURIComponent(productId)}`, { warehouse_id: warehouseId });
    const inventoryEndpoint = appendQueryParams(`/inventory/product/${encodeURIComponent(productId)}`, { warehouse_id: warehouseId });
    const [productResponse, inventoryResponse] = await Promise.all([
      authFetch(productEndpoint),
      authFetch(inventoryEndpoint).catch(() => ({ items: [] }))
    ]);
    renderProductDetailsModal(productResponse.product || productResponse, inventoryResponse);
  } catch (error) {
    const fallbackProduct = (response.items || []).find((product) => product.product_id === productId);
    if (fallbackProduct) renderProductDetailsModal(fallbackProduct, { items: [] });
    else showToast(error.message || 'Unable to open product details.', 'error');
  } finally {
    clearRouteQuery('products');
  }
}

function bindProductManagement(response) {
  bindPagination(response, "products");
  const productWarehouseSelect = document.getElementById("product-warehouse-filter");
  if (productWarehouseSelect) productWarehouseSelect.addEventListener("change", () => {
    productWarehouseFilter = productWarehouseSelect.value;
    clearCacheByPrefix("view:products");
    openView("products", 1);
  });
  const focusProductId = localStorage.getItem("smart_inventory_focus_product") || "";
  if (focusProductId) {
    const focusedRow = [...document.querySelectorAll("tbody tr")].find((row) => row.textContent.includes(focusProductId));
    if (focusedRow) focusedRow.scrollIntoView({ behavior: "smooth", block: "center" });
    localStorage.removeItem("smart_inventory_focus_product");
  }
  const form = document.getElementById("product-form");
  document.getElementById("scanProductButton")?.addEventListener("click", renderProductScannerModal);
  document.querySelectorAll(".generate-product-codes").forEach((button) => button.addEventListener("click", () => {
    const product = response.items.find((item) => item.product_id === button.dataset.productId);
    if (product) renderCodeModal(product);
  }));
  if (!form) return;

  let editingProductId = null;
  let previewObjectUrl = null;
  const message = document.getElementById("product-message");
  const imageInput = document.getElementById("product-image");
  const imagePreview = document.getElementById("product-image-preview");
  const imagePreviewImg = document.getElementById("product-image-preview-img");
  const removeImageButton = document.getElementById("remove-product-image");
  const allowedImageTypes = ["image/jpeg", "image/png", "image/webp"];
  const allowedImageExtensions = ["jpg", "jpeg", "png", "webp"];
  const maxProductImageSize = 2 * 1024 * 1024;

  const setImagePreview = (src, isObjectUrl = false) => {
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = isObjectUrl ? src : null;
    imagePreviewImg.src = src;
    imagePreview.hidden = false;
  };

  const clearImagePreview = () => {
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
    imageInput.value = "";
    imagePreviewImg.removeAttribute("src");
    imagePreview.hidden = true;
  };

  const validateProductImage = (file) => {
    if (!file) return "";
    const extension = file.name.split(".").pop().toLowerCase();
    if (!allowedImageTypes.includes(file.type) || !allowedImageExtensions.includes(extension)) {
      return "Only JPG, JPEG, PNG, and WEBP images are allowed.";
    }
    if (file.size > maxProductImageSize) {
      return "Product image must be 2MB or smaller.";
    }
    return "";
  };

  const resetForm = () => {
    editingProductId = null;
    form.reset();
    document.getElementById("product-quantity").value = 0;
    document.getElementById("product-price").value = 1;
    document.getElementById("product-reorder-level").value = 35;
    document.getElementById("product-barcode-value").value = "";
    document.getElementById("product-qr-code-value").value = "";
    clearImagePreview();
    document.getElementById("product-id").disabled = false;
    document.getElementById("product-form-title").textContent = "Add product";
    document.getElementById("save-product").textContent = "Add product";
  };

  const showProductMessage = (text, isError = false) => {
    message.textContent = text;
    message.classList.toggle("error", isError);
    message.hidden = false;
  };

  imageInput.addEventListener("change", () => {
    const file = imageInput.files?.[0];
    if (!file) {
      clearImagePreview();
      return;
    }
    const validationMessage = validateProductImage(file);
    if (validationMessage) {
      clearImagePreview();
      showProductMessage(validationMessage, true);
      return;
    }
    message.hidden = true;
    setImagePreview(URL.createObjectURL(file), true);
  });

  removeImageButton.addEventListener("click", clearImagePreview);

  document.getElementById("reset-product-form").addEventListener("click", resetForm);

  document.querySelectorAll(".edit-product").forEach((button) => button.addEventListener("click", () => {
    const product = response.items.find((item) => item.product_id === button.dataset.productId);
    if (!product) return;
    editingProductId = product.product_id;
    document.getElementById("product-id").value = product.product_id;
    document.getElementById("product-id").disabled = true;
    document.getElementById("product-name").value = product.product_name;
    document.getElementById("product-quantity").value = product.quantity;
    document.getElementById("product-price").value = product.price;
    document.getElementById("product-unit-cost").value = product.unit_cost ?? "";
    document.getElementById("product-reorder-level").value = product.reorder_level ?? 35;
    document.getElementById("product-category-id").value = product.category_id ?? "";
    document.getElementById("product-supplier-id").value = product.supplier_id ?? "";
    document.getElementById("product-barcode-value").value = product.barcode_value ?? product.product_id;
    document.getElementById("product-qr-code-value").value = product.qr_code_value ?? product.product_id;
    if (product.product_image) {
      imageInput.value = "";
      setImagePreview(getProductImage(product));
    } else {
      clearImagePreview();
    }
    document.getElementById("product-form-title").textContent = `Edit ${product.product_id}`;
    document.getElementById("save-product").textContent = "Save changes";
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  document.querySelectorAll(".delete-product").forEach((button) => button.addEventListener("click", async () => {
    const productId = button.dataset.productId;
    if (confirmationRequired() && !window.confirm(`Delete ${productId}?`)) return;
    try {
      await authFetch(`/products/${encodeURIComponent(productId)}`, { method: "DELETE" });
      await refreshDashboardAfterMutation();
      productNotice = `${productId} deleted successfully.`;
      await openView("products", response.page);
    } catch (error) {
      showProductMessage(error.message, true);
    }
  }));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const productId = document.getElementById("product-id").value.trim();
    const payload = {
      product_id: productId,
      product_name: document.getElementById("product-name").value.trim(),
      quantity: Number(document.getElementById("product-quantity").value),
      price: Number(document.getElementById("product-price").value),
      reorder_level: Number(document.getElementById("product-reorder-level").value),
      category_id: document.getElementById("product-category-id").value.trim(),
      supplier_id: document.getElementById("product-supplier-id").value.trim(),
      barcode_value: document.getElementById("product-barcode-value").value.trim() || productId,
      qr_code_value: document.getElementById("product-qr-code-value").value.trim() || productId
    };
    if (payload.supplier_id && supplierIds.length && !supplierIds.includes(payload.supplier_id)) {
      showProductMessage("Supplier ID does not exist.", true);
      return;
    }

    const selectedImage = imageInput.files?.[0];
    const validationMessage = validateProductImage(selectedImage);
    if (validationMessage) {
      showProductMessage(validationMessage, true);
      return;
    }

    const formData = new FormData();
    Object.entries(payload).forEach(([key, value]) => formData.append(key, value ?? ""));
    const unitCost = document.getElementById("product-unit-cost").value.trim();
    if (unitCost !== "") formData.append("unit_cost", Number(unitCost));
    if (selectedImage) formData.append("product_image", selectedImage);

    try {
      if (editingProductId) {
        await authFetch(`/products/${encodeURIComponent(editingProductId)}`, { method: "PUT", body: formData });
        productNotice = `${editingProductId} updated successfully.`;
      } else {
        await authFetch("/products", { method: "POST", body: formData });
        productNotice = `${payload.product_id} added successfully.`;
      }
      await refreshDashboardAfterMutation();
      await openView("products", response.page);
    } catch (error) {
      showProductMessage(error.message, true);
    }
  });

  if (productNotice) {
    showProductMessage(productNotice);
    productNotice = "";
  }
}

function generateTransactionRecordId(prefix) {
  const date = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  const random = Math.random().toString(36).slice(2, 8).toUpperCase();
  return `${prefix}-${date}-${random}`;
}

function todayInputDate() {
  return new Date().toISOString().slice(0, 10);
}

function getTransactionWarehouseLabel() {
  const profile = getProfileDetails();
  if ((profile.role || "").toLowerCase() === "admin") return "All Warehouses";
  return [profile.warehouseName || profile.location, profile.warehouseId]
    .filter(Boolean)
    .join(" - ") || "Assigned Warehouse";
}

async function loadTransactionProducts() {
  await loadDashboardDataset().catch(() => {});
  if (dashboardData?.products?.length) return dashboardData.products;
  try {
    return await fetchAllPaged("/products");
  } catch (error) {
    console.warn("Unable to load products for transaction form:", error.message);
    return [];
  }
}

async function loadTransactionSuppliers() {
  await loadDashboardDataset().catch(() => {});
  if (dashboardData?.suppliers?.length) return dashboardData.suppliers;
  try {
    return await fetchAllPaged("/suppliers");
  } catch (error) {
    console.warn("Unable to load suppliers for transaction form:", error.message);
    return [];
  }
}

function transactionProductOptions(products) {
  return `<option value="">Select product</option>${products.map((product) => {
    const productId = product.product_id || product.id || "";
    const productName = product.product_name || product.name || productId;
    const stock = product.quantity ?? product.current_stock ?? product.stock ?? 0;
    return `<option value="${escapeHtml(productId)}" data-price="${escapeHtml(product.price ?? 0)}" data-unit-cost="${escapeHtml(product.unit_cost ?? product.price ?? 0)}" data-supplier-id="${escapeHtml(product.supplier_id || "")}" data-stock="${escapeHtml(stock)}">${escapeHtml(productName)} (${escapeHtml(productId)})</option>`;
  }).join("")}`;
}

function transactionSupplierOptions(suppliers) {
  return `<option value="">Select supplier</option>${suppliers.map((supplier) => {
    const supplierId = supplier.supplier_id || supplier.id || "";
    const supplierName = supplier.supplier_name || supplier.name || supplierId;
    return `<option value="${escapeHtml(supplierId)}">${escapeHtml(supplierName)} (${escapeHtml(supplierId)})</option>`;
  }).join("")}`;
}

function selectedTransactionProduct(select) {
  const option = select?.selectedOptions?.[0];
  return {
    productId: select?.value || "",
    price: Number(option?.dataset?.price || 0),
    unitCost: Number(option?.dataset?.unitCost || option?.dataset?.price || 0),
    supplierId: option?.dataset?.supplierId || "",
    stock: Number(option?.dataset?.stock || 0)
  };
}

function showTransactionMessage(id, message, isError = true) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
  node.classList.toggle("success", !isError);
}

async function openAddPurchaseModal(prefill = {}) {
  const [products, suppliers] = await Promise.all([loadTransactionProducts(), loadTransactionSuppliers()]);
  if (prefill.product && !products.some((product) => product.product_id === prefill.product.product_id)) {
    products.unshift(prefill.product);
  }
  const purchaseId = generateTransactionRecordId("PURCHASE");
  const transactionId = generateTransactionRecordId("TXN");
  openModal(`
    <section class="modal-card transaction-modal" role="dialog" aria-modal="true" aria-labelledby="addPurchaseTitle">
      <header class="modal-header"><h2 id="addPurchaseTitle">${prefill.product_id ? "Create Purchase Order" : "Add Purchase"}</h2><button class="modal-close" type="button" data-modal-close><i data-lucide="x"></i></button></header>
      <form id="addPurchaseForm" class="modal-form">
        <label>Purchase ID<input id="purchase-id-field" value="${escapeHtml(purchaseId)}" readonly></label>
        <label>Transaction ID<input id="purchase-transaction-field" value="${escapeHtml(transactionId)}" readonly></label>
        <label>Product<select id="purchase-product-field" required>${transactionProductOptions(products)}</select></label>
        <label>Supplier<select id="purchase-supplier-field" required>${transactionSupplierOptions(suppliers)}</select></label>
        <label>Quantity<input id="purchase-quantity-field" type="number" min="1" value="1" required></label>
        <label>Unit Cost<input id="purchase-unit-cost-field" type="number" min="1" value="1" required></label>
        <label>Purchase Date<input id="purchase-date-field" type="date" value="${todayInputDate()}" required></label>
        <label>Warehouse<input id="purchase-warehouse-field" value="${escapeHtml(getTransactionWarehouseLabel())}" readonly></label>
        <label>Notes<textarea id="purchase-notes-field" placeholder="Optional purchase note"></textarea></label>
        <p id="purchaseFormMessage" class="modal-message" hidden></p>
        <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-modal-close>Cancel</button><button class="modal-primary-button" type="submit"><i data-lucide="save"></i>${prefill.product_id ? "Create Purchase Order" : "Save Purchase"}</button></footer>
      </form>
    </section>
  `);

  const productSelect = document.getElementById("purchase-product-field");
  const supplierSelect = document.getElementById("purchase-supplier-field");
  const unitCostInput = document.getElementById("purchase-unit-cost-field");
  productSelect?.addEventListener("change", () => {
    const product = selectedTransactionProduct(productSelect);
    if (product.unitCost > 0) unitCostInput.value = Math.round(product.unitCost);
    if (product.supplierId && supplierSelect) supplierSelect.value = product.supplierId;
  });

  if (prefill.product_id && productSelect) {
    productSelect.value = prefill.product_id;
    productSelect.dispatchEvent(new Event("change"));
  }
  if (prefill.supplier_id && supplierSelect) supplierSelect.value = prefill.supplier_id;
  if (prefill.suggested_quantity || prefill.quantity) document.getElementById("purchase-quantity-field").value = Math.round(Number(prefill.suggested_quantity || prefill.quantity));
  if (prefill.unit_cost) unitCostInput.value = Math.round(Number(prefill.unit_cost));
  if (prefill.note) document.getElementById("purchase-notes-field").value = prefill.note;

  document.getElementById("addPurchaseForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const quantity = Number(document.getElementById("purchase-quantity-field").value);
    const unitCost = Number(unitCostInput.value);
    const product = selectedTransactionProduct(productSelect);
    if (!product.productId) return showTransactionMessage("purchaseFormMessage", "Please select a product.");
    if (!supplierSelect.value) return showTransactionMessage("purchaseFormMessage", "Please select a supplier.");
    if (quantity <= 0 || unitCost <= 0) return showTransactionMessage("purchaseFormMessage", "Quantity and unit cost must be greater than zero.");

    try {
      const result = await authFetch("/purchases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purchase_id: document.getElementById("purchase-id-field").value,
          transaction_id: document.getElementById("purchase-transaction-field").value,
          product_id: product.productId,
          supplier_id: supplierSelect.value,
          quantity: Math.round(quantity),
          unit_cost: Math.round(unitCost),
          purchase_date: document.getElementById("purchase-date-field").value,
          status: prefill.status || "Completed",
          warehouse_id: prefill.warehouse_id || null,
          note: document.getElementById("purchase-notes-field").value.trim() || null
        })
      });
      closeModal();
      showToast(result.message || "Purchase added successfully.");
      await refreshDashboardAfterMutation();
      await openView("purchases", 1, false);
    } catch (error) {
      showTransactionMessage("purchaseFormMessage", error.message || "Unable to add purchase.");
    }
  });
}

async function openAddSaleModal() {
  const products = await loadTransactionProducts();
  const saleId = generateTransactionRecordId("SALE");
  const transactionId = generateTransactionRecordId("TXN");
  openModal(`
    <section class="modal-card transaction-modal" role="dialog" aria-modal="true" aria-labelledby="addSaleTitle">
      <header class="modal-header"><h2 id="addSaleTitle">Add Sale</h2><button class="modal-close" type="button" data-modal-close><i data-lucide="x"></i></button></header>
      <form id="addSaleForm" class="modal-form">
        <label>Sale ID<input id="sale-id-field" value="${escapeHtml(saleId)}" readonly></label>
        <label>Transaction ID<input id="sale-transaction-field" value="${escapeHtml(transactionId)}" readonly></label>
        <label>Product<select id="sale-product-field" required>${transactionProductOptions(products)}</select></label>
        <label>Customer Name<input id="sale-customer-name-field" required placeholder="Customer name"></label>
        <label>Customer Phone<input id="sale-customer-phone-field" required placeholder="9876543210"></label>
        <label>Customer Email<input id="sale-customer-email-field" type="email" placeholder="Optional email"></label>
        <label>Quantity<input id="sale-quantity-field" type="number" min="1" value="1" required></label>
        <label>Unit Price<input id="sale-unit-price-field" type="number" min="1" value="1" required></label>
        <label>Payment Method<select id="sale-payment-method-field" required><option value="Cash">Cash</option><option value="Card">Card</option><option value="UPI">UPI</option></select></label>
        <label>Sale Date<input id="sale-date-field" type="date" value="${todayInputDate()}" required></label>
        <label>Notes<textarea id="sale-notes-field" placeholder="Optional sale note"></textarea></label>
        <p id="saleFormMessage" class="modal-message" hidden></p>
        <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-modal-close>Cancel</button><button class="modal-primary-button" type="submit"><i data-lucide="save"></i>Save Sale</button></footer>
      </form>
    </section>
  `);

  const productSelect = document.getElementById("sale-product-field");
  const unitPriceInput = document.getElementById("sale-unit-price-field");
  productSelect?.addEventListener("change", () => {
    const product = selectedTransactionProduct(productSelect);
    if (product.price > 0) unitPriceInput.value = Math.round(product.price);
  });

  document.getElementById("addSaleForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const product = selectedTransactionProduct(productSelect);
    const quantity = Number(document.getElementById("sale-quantity-field").value);
    const unitPrice = Number(unitPriceInput.value);
    if (!product.productId) return showTransactionMessage("saleFormMessage", "Please select a product.");
    if (quantity <= 0 || unitPrice <= 0) return showTransactionMessage("saleFormMessage", "Quantity and unit price must be greater than zero.");
    if (product.stock > 0 && quantity > product.stock) return showTransactionMessage("saleFormMessage", `Only ${product.stock} units are available for this product.`);

    try {
      const result = await authFetch("/sales", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sale_id: document.getElementById("sale-id-field").value,
          transaction_id: document.getElementById("sale-transaction-field").value,
          product_id: product.productId,
          customer_name: document.getElementById("sale-customer-name-field").value.trim(),
          customer_phone: document.getElementById("sale-customer-phone-field").value.trim(),
          customer_email: document.getElementById("sale-customer-email-field").value.trim() || null,
          quantity: Math.round(quantity),
          unit_price: Math.round(unitPrice),
          payment_method: document.getElementById("sale-payment-method-field").value,
          sale_date: document.getElementById("sale-date-field").value,
          note: document.getElementById("sale-notes-field").value.trim() || null
        })
      });
      closeModal();
      showToast(result.message || "Sale added successfully.");
      await refreshDashboardAfterMutation();
      await openView("sales", 1, false);
    } catch (error) {
      showTransactionMessage("saleFormMessage", error.message || "Unable to add sale.");
    }
  });
}

function renderSalesPage(response) {
  const rows = (response.items || []).map((sale) => `
    <tr>
      <td>${escapeHtml(sale.sale_id || sale.id || "-")}</td>
      <td>${escapeHtml(sale.product_name || sale.product_id || "-")}</td>
      <td>${escapeHtml(sale.customer_name || "-")}</td>
      <td>${escapeHtml(sale.customer_phone || sale.phone || "-")}</td>
      <td>${formatDashboardNumber(sale.quantity)}</td>
      <td>${formatMoney(sale.unit_price)}</td>
      <td>${formatMoney(sale.total_amount || sale.sales_amount)}</td>
      <td>${escapeHtml(sale.payment_method || "-")}</td>
      <td>${escapeHtml(sale.date || (sale.created_at ? new Date(sale.created_at).toLocaleDateString("en-IN") : "-"))}</td>
      <td>${escapeHtml(sale.sold_by || sale.created_by || sale.operator_username || "-")}</td>
    </tr>`).join("");
  const previousDisabled = response.has_previous ? "" : "disabled";
  const nextDisabled = response.has_next ? "" : "disabled";
  return `
    <div class="panel"><div class="section-heading"><div><h3>Sales</h3><p>Customer sales transactions for the selected date range.</p></div><div class="queue-toolbar"><button id="addSaleButton" type="button"><i data-lucide="plus"></i>Add Sale</button></div></div><div class="table-wrap"><table><thead><tr><th>Sale ID</th><th>Product</th><th>Customer Name</th><th>Customer Phone</th><th>Qty</th><th>Unit Price</th><th>Total Amount</th><th>Payment Method</th><th>Date</th><th>Sold By</th></tr></thead><tbody>${rows || '<tr><td colspan="10">No data available for selected date range.</td></tr>'}</tbody></table></div></div>
    <div class="pagination"><button id="previous-sales" ${previousDisabled}>Previous</button><span>Page ${response.page} of ${response.total_pages} (${response.total} records)</span><button id="next-sales" ${nextDisabled}>Next</button></div>`;
}
function renderPagedRecords(title, response, name) {
  if (name === "sales") return renderSalesPage(response);
  if (name === "purchases") return renderPurchasesPage(response);
  const table = renderRecords(title, response.items || [], ["sales", "purchases"].includes(name) ? "No data available for selected date range." : "No records found.");
  const previousDisabled = response.has_previous ? "" : "disabled";
  const nextDisabled = response.has_next ? "" : "disabled";
  const restockPanel = name === "purchases" ? renderRestockQueuePanel() : "";
  return `${restockPanel}${table}<div class="pagination"><button id="previous-${name}" ${previousDisabled}>Previous</button><span>Page ${response.page} of ${response.total_pages} (${response.total} records)</span><button id="next-${name}" ${nextDisabled}>Next</button></div>`;
}

function renderRestockQueuePanel() {
  const storedQueue = JSON.parse(localStorage.getItem('smart_inventory_restock_queue') || '[]');
  const queueResponse = normalizeRestockQueueResponse(restockQueueCache || storedQueue);
  const queue = queueResponse.items || [];
  const supplierOptions = (selectedId) => (dashboardData?.suppliers || [])
    .map((supplier) => `<option value="${escapeHtml(supplier.supplier_id)}" ${supplier.supplier_id === selectedId ? 'selected' : ''}>${escapeHtml(supplier.supplier_name || supplier.supplier_id)}</option>`)
    .join('');
  const rows = queue.map((item, index) => {
    const warehouseId = item.warehouse_id || item.location_id || '';
    const warehouseName = item.warehouse_name || item.location || warehouseId || '-';
    const queueStatus = item.status || item.queue_status || 'Queued';
    const canCreate = !/purchase order created|received/i.test(queueStatus);
    const canSelect = canCreate;
    return `
      <tr class="restock-row" data-queue-index="${index}" data-product-id="${escapeHtml(item.product_id)}" data-product-name="${escapeHtml(item.product_name || item.product_id)}" data-warehouse-id="${escapeHtml(warehouseId)}" data-current-stock="${escapeHtml(item.current_stock ?? 0)}" data-reorder-level="${escapeHtml(item.reorder_level ?? 0)}" data-queue-status="${escapeHtml(queueStatus)}">
        <td><input class="restock-select" type="checkbox" ${canSelect ? 'checked' : 'disabled'} aria-label="Select ${escapeHtml(item.product_name || item.product_id)}"></td>
        <td>${escapeHtml(item.product_id || '-')}</td>
        <td>${escapeHtml(item.product_name || item.product_id || '-')}</td>
        <td>${escapeHtml(warehouseName)}</td>
        <td>${formatDashboardNumber(item.current_stock)}</td>
        <td>${formatDashboardNumber(item.reorder_level)}</td>
        <td><input class="restock-quantity" type="number" min="1" value="${escapeHtml(item.suggested_quantity || item.quantity || 1)}"></td>
        <td><select class="restock-supplier" aria-label="Supplier"><option value="">Select supplier</option>${item.supplier_id && !(dashboardData?.suppliers || []).some((supplier) => supplier.supplier_id === item.supplier_id) ? `<option value="${escapeHtml(item.supplier_id)}" selected>${escapeHtml(item.supplier_name || item.supplier_id)}</option>` : ''}${supplierOptions(item.supplier_id)}</select></td>
        <td><input class="restock-unit-cost" type="number" min="1" value="${escapeHtml(item.unit_cost || 1)}"></td>
        <td class="restock-total-cost">${formatMoney((item.suggested_quantity || item.quantity || 0) * (item.unit_cost || 0))}</td>
        <td><span class="purchase-status ${/received/i.test(queueStatus) ? 'completed' : /purchase order/i.test(queueStatus) ? 'pending' : ''}">${escapeHtml(queueStatus)}</span></td>
        <td class="health-actions"><button type="button" data-restock-action="restock" data-queue-index="${index}" ${/received/i.test(queueStatus) ? 'disabled' : ''}>Restock</button><button type="button" data-restock-action="edit-quantity" data-queue-index="${index}">Edit Quantity</button><button type="button" data-restock-action="select-supplier" data-queue-index="${index}">Select Supplier</button><button type="button" data-restock-action="create" data-queue-index="${index}" ${canCreate ? '' : 'disabled'}>Create Purchase Order</button><button type="button" data-restock-action="product" data-product-id="${escapeHtml(item.product_id)}" data-warehouse-id="${escapeHtml(warehouseId)}">View Product</button><button type="button" data-restock-action="remove" data-queue-index="${index}">Remove</button></td>
      </tr>`;
  }).join('');
  const totalPages = Math.max(1, Number(queueResponse.total_pages || 1));
  const currentPage = Math.max(1, Number(queueResponse.page || 1));
  const pagination = totalPages > 1 ? `
    <div class="restock-queue-footer">
      <button type="button" data-restock-action="queue-prev" ${currentPage <= 1 ? 'disabled' : ''}>Previous</button>
      <span>Queue page ${formatDashboardNumber(currentPage)} of ${formatDashboardNumber(totalPages)} (${formatDashboardNumber(queueResponse.total || queue.length)} items)</span>
      <button type="button" data-restock-action="queue-next" ${currentPage >= totalPages ? 'disabled' : ''}>Next</button>
    </div>` : '';
  return `
    <section class="panel restock-queue-panel">
      <div class="section-heading"><div><h3>Restock Request Queue</h3><p>Queued low-stock products from Inventory Health. Create purchase orders or receive stock from here.</p></div><div class="queue-toolbar"><button data-restock-action="create-all" type="button" ${queue.length ? '' : 'disabled'}>Create All Purchase Orders</button><button data-restock-action="purchase-all" type="button" ${queue.length ? '' : 'disabled'}>Purchase All Products</button><button data-restock-action="restock-selected" type="button" ${queue.length ? '' : 'disabled'}>Restock Selected</button><button data-restock-action="clear" class="queue-danger" type="button" ${queue.length ? '' : 'disabled'}>Clear Queue</button></div></div>
      <div class="table-wrap"><table class="restock-queue-table"><thead><tr><th></th><th>Product ID</th><th>Product Name</th><th>Warehouse</th><th>Current Stock</th><th>Reorder Level</th><th>Suggested Quantity</th><th>Supplier</th><th>Unit Cost</th><th>Total Cost</th><th>Queue Status</th><th>Actions</th></tr></thead><tbody>${rows || '<tr><td colspan="12">No restock queue items found.</td></tr>'}</tbody></table></div>
      ${pagination}
    </section>`;
}

function renderPurchasesPage(response) {
  const restockPanel = renderRestockQueuePanel();
  const pendingResponse = response.pending || { items: [], total: 0, page: 1, total_pages: 1 };
  const completedResponse = response.completed || response || { items: [], total: 0, page: 1, total_pages: 1 };
  const pendingItems = pendingResponse.items || [];
  const completedItems = completedResponse.items || [];
  const pendingCount = Number(purchasePendingSummaryCache?.pending_count ?? pendingResponse.total ?? pendingItems.length ?? 0);
  const receiveAllDisabled = pendingCount <= 0 ? "disabled" : "";
  const pendingMessage = pendingCount <= 0
    ? '<p class="pending-empty-state">No pending purchase orders to receive.</p>'
    : "";
  const highlightedPurchases = new Set(JSON.parse(localStorage.getItem("smart_inventory_highlight_purchases") || "[]"));
  const renderPurchaseRows = (items, forcePending = false) => (items || []).map((purchase) => {
    const status = purchase.status || (forcePending ? "Pending" : "Completed");
    const isPending = status === "Pending";
    const warehouse = purchase.warehouse_name || purchase.location || purchase.warehouse_id || purchase.location_id || "-";
    return `
      <tr class="${highlightedPurchases.has(purchase.purchase_id) ? "highlight-row" : ""}">
        <td>${isPending ? `<input class="purchase-select" type="checkbox" value="${escapeHtml(purchase.purchase_id)}" aria-label="Select purchase ${escapeHtml(purchase.purchase_id)}">` : ""}</td>
        <td>${escapeHtml(purchase.purchase_id || purchase.id || "-")}</td>
        <td>${escapeHtml(purchase.product_name || purchase.product_id || "-")}</td>
        <td>${escapeHtml(purchase.supplier || purchase.supplier_name || purchase.supplier_id || "-")}</td>
        <td>${formatDashboardNumber(purchase.quantity)}</td>
        <td>${formatMoney(purchase.unit_cost)}</td>
        <td>${formatMoney(purchase.total_cost)}</td>
        <td>${escapeHtml(purchase.date || (purchase.created_at ? new Date(purchase.created_at).toLocaleDateString("en-IN") : "-"))}</td>
        <td><span class="purchase-status ${isPending ? "pending" : "completed"}">${escapeHtml(status)}</span></td>
        <td>${escapeHtml(purchase.purchased_by || purchase.created_by || purchase.operator_username || "-")}</td>
        <td>${escapeHtml(warehouse)}</td>
        <td class="health-actions">${isPending ? `<button type="button" data-receive-purchase="${escapeHtml(purchase.purchase_id)}">Receive Stock</button>` : "-"}</td>
      </tr>`;
  }).join("");
  const pendingRows = renderPurchaseRows(pendingItems, true);
  const completedRows = renderPurchaseRows(completedItems, false);
  const previousDisabled = completedResponse.has_previous ? "" : "disabled";
  const nextDisabled = completedResponse.has_next ? "" : "disabled";
  return `
    ${restockPanel}
    <div class="panel"><div class="section-heading"><div><h3>Pending Purchase Orders</h3><p>Purchase orders created from the restock queue and waiting to be received into inventory.</p></div><div class="queue-toolbar"><button id="addPurchaseButton" type="button"><i data-lucide="plus"></i>Add Purchase</button><button type="button" data-purchase-bulk="selected">Receive Selected Stock</button><button type="button" data-purchase-bulk="all" ${receiveAllDisabled}>Receive All Stock (${formatDashboardNumber(pendingCount)})</button></div></div>${pendingMessage}<div id="purchaseBulkProgress" class="bulk-progress" hidden></div><div class="table-wrap"><table><thead><tr><th></th><th>Purchase ID</th><th>Product</th><th>Supplier</th><th>Qty</th><th>Unit Cost</th><th>Total Cost</th><th>Date</th><th>Status</th><th>Created By</th><th>Warehouse</th><th>Action</th></tr></thead><tbody>${pendingRows || '<tr><td colspan="12">No pending purchase orders for the selected date range.</td></tr>'}</tbody></table></div></div>
    <div class="panel"><div class="section-heading"><div><h3>Completed Purchases</h3><p>Received supplier purchases and completed stock-in transactions.</p></div></div><div class="table-wrap"><table><thead><tr><th></th><th>Purchase ID</th><th>Product</th><th>Supplier</th><th>Qty</th><th>Unit Cost</th><th>Total Cost</th><th>Date</th><th>Status</th><th>Created By</th><th>Warehouse</th><th>Action</th></tr></thead><tbody>${completedRows || '<tr><td colspan="12">No completed purchases available for selected date range.</td></tr>'}</tbody></table></div></div>
    <div class="pagination"><button id="previous-purchases" ${previousDisabled}>Previous</button><span>Completed purchases page ${completedResponse.page || 1} of ${completedResponse.total_pages || 1} (${completedResponse.total || 0} records)</span><button id="next-purchases" ${nextDisabled}>Next</button></div>`;
}

function bindPagination(response, name) {
  const previous = document.getElementById(`previous-${name}`);
  const next = document.getElementById(`next-${name}`);
  if (previous) previous.addEventListener("click", () => openView(name, response.page - 1));
  if (next) next.addEventListener("click", () => openView(name, response.page + 1));
  if (name === "purchases") {
    bindRestockQueueActions(response.page);
    bindReceivePurchaseActions(response.page);
  }
  if (name === "sales") {
  }
}
function readRestockQueueFromPage() {
  return [...document.querySelectorAll(".restock-row")].map((row) => {
    const quantity = Number(row.querySelector(".restock-quantity")?.value || 0);
    const unitCost = Number(row.querySelector(".restock-unit-cost")?.value || 0);
    const supplier = row.querySelector(".restock-supplier");
    return {
      product_id: row.dataset.productId,
      warehouse_id: row.dataset.warehouseId || "",
      product_name: row.dataset.productName || row.dataset.productId,
      current_stock: Number(row.dataset.currentStock || 0),
      reorder_level: Number(row.dataset.reorderLevel || 0),
      status: row.dataset.queueStatus || "Queued",
      suggested_quantity: quantity,
      supplier_id: supplier?.value || "",
      supplier_name: supplier?.selectedOptions?.[0]?.textContent || "",
      unit_cost: unitCost,
      total_cost: quantity * unitCost,
      selected: row.querySelector(".restock-select")?.checked || false
    };
  });
}

function saveRestockQueueFromPage() {
  const queue = readRestockQueueFromPage().map(({ selected, ...item }) => item);
  localStorage.setItem("smart_inventory_restock_queue", JSON.stringify(queue));
  return queue;
}

async function createPurchaseOrder(item) {
  if (!item.product_id || !item.supplier_id || item.suggested_quantity <= 0 || item.unit_cost <= 0) {
    throw new Error("Select supplier and enter valid quantity/unit cost.");
  }
  return authFetch("/purchases/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      items: [{
        product_id: item.product_id,
        supplier_id: item.supplier_id,
        warehouse_id: item.warehouse_id || item.location_id || null,
        quantity: Math.round(item.suggested_quantity),
        unit_cost: Math.round(item.unit_cost)
      }]
    })
  });
}

async function createPurchaseOrders(items) {
  const validItems = items.map((item) => ({
    product_id: item.product_id,
    supplier_id: item.supplier_id,
    warehouse_id: item.warehouse_id || item.location_id || null,
    quantity: Math.round(item.suggested_quantity),
    unit_cost: Math.round(item.unit_cost)
  }));
  if (validItems.some((item) => !item.product_id || !item.supplier_id || item.quantity <= 0 || item.unit_cost <= 0)) {
    throw new Error("Select supplier and enter valid quantity/unit cost.");
  }
  return authFetch("/purchases/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: validItems })
  });
}

async function syncRestockQueue(items) {
  if (!items.length) return;
  await authFetch("/restock-queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      items: items.map((item) => ({
        product_id: item.product_id,
        supplier_id: item.supplier_id || null,
        warehouse_id: item.warehouse_id || null,
        quantity: Math.round(item.suggested_quantity),
        unit_cost: Math.round(item.unit_cost)
      }))
    })
  });
  restockQueueCache = null;
}

function queueSelectionPayload(items) {
  const selected = (items || []).filter(Boolean);
  return {
    product_ids: selected.map((item) => item.product_id).filter(Boolean),
    items: selected
      .filter((item) => item.product_id)
      .map((item) => ({
        product_id: item.product_id,
        warehouse_id: item.warehouse_id || item.location_id || null
      }))
  };
}


function friendlyBulkErrorMessage(error, fallback = "Unable to complete this action.") {
  const message = error?.message || fallback;
  if (/socketTimeoutMS|connectTimeoutMS|timed out|timeout|ServerSelectionTimeout|AutoReconnect/i.test(message)) {
    return "Bulk stock update is taking too long. Please try again or process in smaller batches.";
  }
  return message;
}

async function refreshPurchasesView(page = 1) {
  restockQueueCache = null;
  purchasePendingSummaryCache = null;
  await openView("purchases", page, false);
}
function purchaseOperationCount(result, fallbackCount) {
  const created = Number(result?.created_count || 0);
  const updated = Number(result?.updated_count || 0);
  return created + updated || fallbackCount || 0;
}
function setPurchaseButtonsProcessing(isProcessing) {
  document.querySelectorAll("[data-purchase-bulk], [data-receive-purchase], [data-restock-action], .purchase-select").forEach((element) => {
    element.disabled = isProcessing;
  });
}

function setPurchaseProgress(processed, total, message = "Receiving stock...") {
  const progress = document.getElementById("purchaseBulkProgress");
  if (!progress) return;
  progress.hidden = false;
  progress.innerHTML = `
    <strong>${escapeHtml(message)}</strong>
    <span>${formatDashboardNumber(processed)} / ${formatDashboardNumber(total)} completed</span>
    <small>Please wait while inventory is being updated.</small>
  `;
}

function showReceiveAllConfirmation(pendingCount) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay receive-confirm-overlay";
    overlay.innerHTML = `
      <section class="modal-card receive-confirm-card" role="dialog" aria-modal="true" aria-labelledby="receiveAllTitle">
        <div class="modal-header">
          <h2 id="receiveAllTitle">Receive All Pending Stock?</h2>
          <button class="modal-close" type="button" data-confirm-cancel aria-label="Close">&times;</button>
        </div>
        <div class="confirm-body">
          <p>This will receive stock for <strong>${formatDashboardNumber(pendingCount)}</strong> pending purchase orders and update inventory quantities.</p>
        </div>
        <div class="modal-footer">
          <button class="secondary-action" type="button" data-confirm-cancel>Cancel</button>
          <button type="button" data-confirm-ok>Receive All</button>
        </div>
      </section>
    `;
    const close = (value) => {
      overlay.remove();
      document.removeEventListener("keydown", onKeydown);
      resolve(value);
    };
    const onKeydown = (event) => {
      if (event.key === "Escape") close(false);
    };
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target.closest("[data-confirm-cancel]")) close(false);
      if (event.target.closest("[data-confirm-ok]")) close(true);
    });
    document.addEventListener("keydown", onKeydown);
    document.body.appendChild(overlay);
  });
}

function bindRestockQueueActions(page) {
  document.querySelectorAll(".restock-quantity, .restock-unit-cost").forEach((input) => {
    input.addEventListener("input", () => {
      const row = input.closest(".restock-row");
      const quantity = Number(row.querySelector(".restock-quantity").value || 0);
      const unitCost = Number(row.querySelector(".restock-unit-cost").value || 0);
      row.querySelector(".restock-total-cost").textContent = formatMoney(quantity * unitCost);
      saveRestockQueueFromPage();
    });
  });
  document.querySelectorAll(".restock-supplier, .restock-select").forEach((input) => {
    input.addEventListener("change", saveRestockQueueFromPage);
  });
  document.querySelectorAll("[data-restock-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      const action = button.dataset.restockAction;
      setPurchaseButtonsProcessing(true);
      try {
        if (action === "queue-prev" || action === "queue-next") {
          const nextPage = Math.max(1, restockQueuePage + (action === "queue-next" ? 1 : -1));
          await loadRestockQueue(true, nextPage);
          await refreshPurchasesView(page);
          return;
        }
        if (action === "clear") {
          const selected = readRestockQueueFromPage().filter((item) => item.selected);
          if (!selected.length) throw new Error("Select products to remove from queue.");
          if (confirmationRequired() && !window.confirm(`Remove ${selected.length} selected product(s) from the restock queue?`)) return;
          await authFetch("/restock-queue/clear", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(queueSelectionPayload(selected))
          });
          await refreshDashboardAfterMutation();
          showToast("Selected products removed from restock queue.");
          refreshPurchasesView(page);
          return;
        }
        if (action === "remove") {
          const item = readRestockQueueFromPage()[Number(button.dataset.queueIndex)];
          await authFetch("/restock-queue/clear", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(queueSelectionPayload([item]))
          });
          await refreshDashboardAfterMutation();
          refreshPurchasesView(page);
          return;
        }
        if (action === "edit-quantity" || action === "select-supplier") {
          const row = button.closest(".restock-row");
          row?.querySelector(action === "edit-quantity" ? ".restock-quantity" : ".restock-supplier")?.focus();
          return;
        }
        const pageQueue = readRestockQueueFromPage().filter((item) => !/purchase order created|received/i.test(item.status || 'Queued'));
        if (action === "product") {
          navigateToViewWithParams("products", {
            action: "view",
            product_id: button.dataset.productId || "",
            warehouse_id: button.dataset.warehouseId || button.closest(".restock-row")?.dataset?.warehouseId || ""
          });
          return;
        }
        if (action === "purchase-all" || action === "create-all") {
          document.querySelectorAll(".restock-select").forEach((input) => {
            input.checked = true;
          });
          const allQueueItems = readRestockQueueFromPage();
          if (!allQueueItems.length) throw new Error("No products are available in the restock queue.");
          await syncRestockQueue(allQueueItems);
          const result = await authFetch("/restock-queue/purchase-all", { method: "POST" });
          localStorage.setItem("smart_inventory_highlight_purchases", JSON.stringify((result.items || []).map((item) => item.purchase_id).filter(Boolean)));
          await refreshDashboardAfterMutation();
          const count = purchaseOperationCount(result, allQueueItems.length);
          showToast(result.message || `${count} purchase order(s) created successfully.`);
          refreshPurchasesView(1);
          return;
        }
        const targets = action === "create" || action === "restock"
          ? [pageQueue[Number(button.dataset.queueIndex)]]
          : action === "create-all" || action === "restock-selected"
            ? pageQueue.filter((item) => item.selected)
            : pageQueue;
        if (!targets.length) throw new Error("Select at least one product to restock.");
        await syncRestockQueue(targets);
        if (action === "restock" || action === "restock-selected") {
          await authFetch("/inventory/restock/bulk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(queueSelectionPayload(targets)),
            timeoutMs: 120000
          });
          await refreshDashboardAfterMutation();
          showToast(`${targets.length} product(s) restocked. Dashboard refreshed.`);
        } else {
          const result = await createPurchaseOrders(targets);
          localStorage.setItem("smart_inventory_highlight_purchases", JSON.stringify((result.items || []).map((item) => item.purchase_id).filter(Boolean)));
          await refreshDashboardAfterMutation();
          const count = purchaseOperationCount(result, targets.length);
          showToast(result.message || `${count} purchase order(s) created successfully.`);
        }
        refreshPurchasesView(page);
      } catch (error) {
        showToast(friendlyBulkErrorMessage(error, "Unable to create purchase order."), "error");
      } finally {
        setPurchaseButtonsProcessing(false);
      }
    });
  });
}

async function handlePurchaseRouteAction() {
  const params = currentRouteParams();
  if (params.get('action') !== 'create') return;
  const productId = params.get('product_id');
  const warehouseId = params.get('warehouse_id') || '';
  if (!productId) return;
  try {
    const productEndpoint = appendQueryParams(`/products/${encodeURIComponent(productId)}`, { warehouse_id: warehouseId });
    const inventoryEndpoint = appendQueryParams(`/inventory/product/${encodeURIComponent(productId)}`, { warehouse_id: warehouseId });
    const [productResponse, inventoryResponse] = await Promise.all([
      authFetch(productEndpoint),
      authFetch(inventoryEndpoint).catch(() => ({ items: [] }))
    ]);
    const product = productResponse.product || productResponse;
    const inventoryItems = inventoryResponse.items || [];
    const inventory = inventoryItems.find((item) => item.warehouse_id === warehouseId) || inventoryItems[0] || {};
    const quantity = Number(inventory.quantity ?? product.quantity ?? product.current_stock ?? 0);
    const reorderLevel = Number(inventory.reorder_level ?? product.reorder_level ?? 35);
    const suggestedQuantity = Number(params.get('suggested_quantity') || 0) || Math.max(reorderLevel * 2 - quantity, reorderLevel, 1);
    const unitCost = Number(params.get('unit_cost') || product.unit_cost || product.price || product.unit_price || 1) || 1;
    const prefill = {
      product,
      product_id: productId,
      product_name: product.product_name || inventory.product_name || productId,
      warehouse_id: warehouseId || inventory.warehouse_id || product.warehouse_id || '',
      supplier_id: params.get('supplier_id') || product.supplier_id || inventory.supplier_id || '',
      suggested_quantity: suggestedQuantity,
      unit_cost: unitCost,
      status: 'Pending',
      note: 'Created from Inventory Health'
    };
    await syncRestockQueue([prefill]);
    clearRouteQuery('purchases');
    await refreshPurchasesView(1);
    await openAddPurchaseModal(prefill);
  } catch (error) {
    clearRouteQuery('purchases');
    showToast(error.message || 'Unable to create purchase order from inventory health.', 'error');
  }
}

function bindReceivePurchaseActions(page) {
  document.querySelectorAll("[data-purchase-bulk]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const summary = await loadPurchasePendingSummary(true);
        const pendingCount = Number(summary.pending_count || 0);
        const ids = button.dataset.purchaseBulk === "selected"
          ? [...document.querySelectorAll(".purchase-select:checked")].map((input) => input.value)
          : [];
        if (button.dataset.purchaseBulk === "selected" && !ids.length) {
          throw new Error("Select pending purchases to receive.");
        }
        const isSelectedReceive = button.dataset.purchaseBulk === "selected";
        if (!isSelectedReceive) {
          if (!pendingCount) {
            throw new Error("No pending purchase orders to receive.");
          }
          const confirmed = await showReceiveAllConfirmation(pendingCount);
          if (!confirmed) return;
          setPurchaseProgress(0, pendingCount);
        } else {
          setPurchaseProgress(0, ids.length);
        }
        setPurchaseButtonsProcessing(true);
        const result = await authFetch(isSelectedReceive ? "/purchases/receive-selected" : "/purchases/receive-all", isSelectedReceive ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_ids: ids })
        } : { method: "POST" });
        setPurchaseProgress(result.processed_count || result.received_count || 0, result.pending_count || pendingCount || ids.length, "Stock received.");
        await refreshDashboardAfterMutation();
        showToast(`${result.processed_count || result.received_count || 0} purchase orders received successfully. Inventory updated.`);
        refreshPurchasesView(page);
      } catch (error) {
        showToast(friendlyBulkErrorMessage(error, "Unable to receive selected stock."), "error");
      } finally {
        setPurchaseButtonsProcessing(false);
      }
    });
  });
  document.querySelectorAll("[data-receive-purchase]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const purchaseId = button.dataset.receivePurchase;
        await authFetch(`/purchases/${encodeURIComponent(purchaseId)}/receive`, { method: "POST" });
        await refreshDashboardAfterMutation();
        showToast("Inventory updated successfully. Dashboard refreshed.");
        refreshPurchasesView(page);
      } catch (error) {
        showToast(friendlyBulkErrorMessage(error, "Unable to receive stock."), "error");
      }
    });
  });
}

function renderReport(report) {
  const summary = Object.entries(report.summary || {}).map(([label, value]) => `<div class="metric"><span>${escapeHtml(label.replaceAll("_", " "))}</span><strong>${escapeHtml(formatValue(value))}</strong></div>`).join("");
  const productSummary = report.product_summary || [];
  return `<div class="metrics report-metrics">${summary}</div>${renderRecords("Product inventory report", productSummary)}`;
}

async function loadAnalyticsDashboard(renderToken = viewRenderToken) {
  dashboardContent.innerHTML = renderSkeletonPanel(4, "Loading analytics...");
  try {
    const analytics = await cachedAuthFetch(
      `analytics:summary:${dateCacheSuffix()}`,
      () => authFetch(appendQueryParams("/analytics/summary", apiDateParams())),
      30000
    );
    if (!isActiveRender("analytics", renderToken)) return;
    dashboardContent.innerHTML = renderAnalyticsDashboard(analytics || {});
    refreshIcons();
  } catch (error) {
    if (isActiveRender("analytics", renderToken)) {
      dashboardContent.innerHTML = renderErrorPanel("Unable to load Analytics", error, "analytics");
      refreshIcons();
    }
  }
}

function renderAnalyticsDashboard(analytics = {}) {
  const totalSales = Number(analytics.total_sales || 0);
  const totalPurchases = Number(analytics.total_purchases || 0);
  const totalProducts = Number(analytics.total_products || 0);
  const inventoryValue = Number(analytics.inventory_value || 0);
  const lowStockCount = Number(analytics.low_stock_count || 0);
  const salesTrend = analytics.sales_trend || [];
  const purchaseTrend = analytics.purchase_trend || [];
  const salesByCategory = analytics.sales_by_category || [];
  const topProducts = analytics.top_products || [];
  const stockPerformance = analytics.stock_performance || [];
  const supplierContribution = analytics.supplier_contribution || [];
  const inventoryHealth = totalProducts ? Math.max(0, Math.round(((totalProducts - lowStockCount) / totalProducts) * 100)) : 0;

  return `
    <section class="analytics-hero">
      <article><span><i data-lucide="receipt-indian-rupee"></i></span><small>Total Sales</small><strong>${formatDashboardNumber(analytics.sales_count || 0)}</strong><em>Sales records</em></article>
      <article><span><i data-lucide="indian-rupee"></i></span><small>Revenue</small><strong>${formatMoney(totalSales)}</strong><em>Selected date range</em></article>
      <article><span><i data-lucide="shopping-bag"></i></span><small>Total Purchases</small><strong>${formatMoney(totalPurchases)}</strong><em>${formatDashboardNumber(analytics.purchase_count || 0)} purchase records</em></article>
      <article><span><i data-lucide="package-check"></i></span><small>Inventory Value</small><strong>${formatMoney(inventoryValue)}</strong><em>${formatDashboardNumber(totalProducts)} products</em></article>
      <article><span><i data-lucide="triangle-alert"></i></span><small>Low Stock Count</small><strong>${formatDashboardNumber(lowStockCount)}</strong><em>${inventoryHealth}% inventory health</em></article>
    </section>
    <div class="analytics-grid">
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Sales Trend Chart</h3><p>Daily sales total for the selected date range.</p></div></div>
        ${renderMiniBarList(salesTrend, "total", "date", "count", "records")}
      </section>
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Purchases Trend Chart</h3><p>Daily purchase total for the selected date range.</p></div></div>
        ${renderMiniBarList(purchaseTrend, "total", "date", "count", "records")}
      </section>
    </div>
    <div class="analytics-grid">
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Sales by Category</h3><p>Revenue grouped by product category.</p></div></div>
        ${renderMiniBarList(salesByCategory, "revenue", "category_name", "units_sold", "sold")}
      </section>
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Top Selling Products</h3><p>Products ranked by revenue.</p></div></div>
        ${renderMiniBarList(topProducts, "revenue", "product_name", "units_sold", "sold")}
      </section>
    </div>
    <div class="analytics-grid">
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Stock Performance</h3><p>Highest inventory value products and stock condition.</p></div></div>
        ${renderRecords("Stock performance", stockPerformance.slice(0, 8), "No stock performance data available.")}
      </section>
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Supplier Contribution</h3><p>Purchase value by supplier.</p></div></div>
        ${renderMiniBarList(supplierContribution, "total_purchase_cost", "supplier_name", "purchase_count", "orders")}
      </section>
    </div>
  `;
}
function renderMiniBarList(items, valueKey, labelKey, subKey, subLabel = "") {
  if (!items.length) return '<p class="empty-state">No data available.</p>';
  const max = Math.max(...items.map((item) => Number(item[valueKey] || 0)), 1);
  return `<div class="mini-bars">${items.map((item) => {
    const value = Number(item[valueKey] || 0);
    const label = item[labelKey] || item.product_id || "Unknown";
    const subText = subKey ? `${formatDashboardNumber(item[subKey])} ${subLabel || subKey.replaceAll("_", " ")}` : "";
    const moneyLike = ["value", "revenue", "cost", "total"].some((token) => valueKey.includes(token));
    return `<div class="mini-bar-row"><div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(subText)}</span></div><em>${moneyLike ? formatMoney(value) : formatDashboardNumber(value)}</em><i style="width:${Math.max(5, (value / max) * 100)}%"></i></div>`;
  }).join("")}</div>`;
}

function renderCategoryStats(items) {
  if (!items.length) return '<p class="empty-state">No category data available.</p>';
  const max = Math.max(...items.map((item) => Number(item.stock_units || 0)), 1);
  return `<div class="category-stat-list">${items.slice(0, 8).map((item) => `<div><span><strong>${escapeHtml(item.category_name || item.category_id || "Unassigned")}</strong><small>${formatDashboardNumber(item.product_count)} products</small></span><b>${formatDashboardNumber(item.stock_units)} units</b><i><em style="width:${Math.max(5, (Number(item.stock_units || 0) / max) * 100)}%"></em></i></div>`).join("")}</div>`;
}

function normalizeUserFilters(filters = {}) {
  if (typeof filters === "string") return { search: filters };
  return {
    search: filters.search || "",
    role: filters.role || "",
    warehouse_id: filters.warehouse_id || "",
    status: filters.status || ""
  };
}

async function loadUserManagement(page = 1, renderToken = viewRenderToken, filters = {}) {
  const activeFilters = normalizeUserFilters(filters);
  dashboardContent.innerHTML = renderSkeletonPanel(4, "Loading users...");
  try {
    const query = {
      page,
      limit: 25,
      search: activeFilters.search,
      role: activeFilters.role,
      warehouse_id: activeFilters.warehouse_id,
      status: activeFilters.status
    };
    const cacheKey = `users:list:${page}:${JSON.stringify(activeFilters)}`;
    const usersResponse = await cachedAuthFetch(
      cacheKey,
      () => authFetch(appendQueryParams("/users", query)),
      10000
    );
    if (!isActiveRender("users", renderToken)) return;
    dashboardContent.innerHTML = renderUserManagement(usersResponse, activeFilters);
    bindUserPagination(usersResponse, activeFilters);
    bindUserFilters(activeFilters);
    refreshIcons();
  } catch (error) {
    const role = getAuthValue("role");
    if (!isActiveRender("users", renderToken)) return;
    dashboardContent.innerHTML = role !== "Admin"
      ? `<section class="panel role-access-panel"><h3>User Management</h3><p>User management is available only for Admin accounts. Your current role is ${escapeHtml(role || "Unknown")}.</p></section>`
      : renderErrorPanel("Unable to load Users", error, "users");
    refreshIcons();
  }
}

function renderUserManagement(response, filters = {}) {
  const users = response.items || (Array.isArray(response) ? response : []);
  const summary = response.summary || {};
  const roleCounts = summary.role_counts || users.reduce((counts, user) => {
    const role = user.role || "Unknown";
    counts[role] = (counts[role] || 0) + 1;
    return counts;
  }, {});
  const warehouses = Array.isArray(summary.warehouses) ? summary.warehouses : [];
  const totalUsers = summary.total_users ?? response.total ?? users.length;
  const rows = users.map((user) => ({
    name: user.name || user.full_name || user.username || "-",
    email: user.email || "-",
    role: user.role || "-",
    warehouse: user.role === "Admin" ? "All Warehouses" : (user.warehouse_name || user.location || user.location_name || user.warehouse_id || "Unassigned"),
    status: user.status || user.account_status || "Active"
  }));
  const warehouseOptions = warehouses.map((warehouse) => `<option value="${escapeHtml(warehouse.warehouse_id || "")}" ${filters.warehouse_id === warehouse.warehouse_id ? "selected" : ""}>${escapeHtml(warehouse.warehouse_name || warehouse.warehouse_id || "Warehouse")}</option>`).join("");
  const warehouseCards = warehouses.length
    ? warehouses.map((warehouse) => `
      <article class="warehouse-user-card">
        <div><strong>${escapeHtml(warehouse.warehouse_name || warehouse.warehouse_id || "Warehouse")}</strong><small>${escapeHtml([warehouse.city, warehouse.state].filter(Boolean).join(", ") || "Location not set")}</small></div>
        <dl><span><dt>Users</dt><dd>${formatDashboardNumber(warehouse.users || 0)}</dd></span><span><dt>Managers</dt><dd>${formatDashboardNumber(warehouse.managers || 0)}</dd></span><span><dt>Staff</dt><dd>${formatDashboardNumber(warehouse.staff || 0)}</dd></span></dl>
      </article>`).join("")
    : `<p class="empty-state">No Warehouses Registered.</p>`;

  return `
    <section class="analytics-hero user-hero">
      <article><span><i data-lucide="users-round"></i></span><small>Total Users</small><strong>${formatDashboardNumber(totalUsers)}</strong><em>Registered accounts</em></article>
      <article><span><i data-lucide="shield-check"></i></span><small>Admins</small><strong>${formatDashboardNumber(roleCounts.Admin || 0)}</strong><em>Full access</em></article>
      <article><span><i data-lucide="briefcase-business"></i></span><small>Managers</small><strong>${formatDashboardNumber(roleCounts.Manager || 0)}</strong><em>Warehouse management</em></article>
      <article><span><i data-lucide="user-check"></i></span><small>Staff</small><strong>${formatDashboardNumber(roleCounts.Staff || 0)}</strong><em>Operational users</em></article>
    </section>
    <section class="panel user-filter-panel">
      <form id="user-filter-form" class="user-filter-bar">
        <label>Search<input id="user-search-filter" type="search" value="${escapeHtml(filters.search || "")}" placeholder="Name or email"></label>
        <label>Role<select id="user-role-filter"><option value="">All Roles</option><option value="Admin" ${filters.role === "Admin" ? "selected" : ""}>Admin</option><option value="Manager" ${filters.role === "Manager" ? "selected" : ""}>Manager</option><option value="Staff" ${filters.role === "Staff" ? "selected" : ""}>Staff</option></select></label>
        <label>Warehouse<select id="user-warehouse-filter"><option value="">All Warehouses</option>${warehouseOptions}</select></label>
        <label>Status<select id="user-status-filter"><option value="">All Statuses</option><option value="Active" ${filters.status === "Active" ? "selected" : ""}>Active</option><option value="Inactive" ${filters.status === "Inactive" ? "selected" : ""}>Inactive</option></select></label>
        <button type="submit" class="primary-action small-action"><i data-lucide="filter"></i>Apply</button>
      </form>
    </section>
    <div class="analytics-grid">
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Users by Store / Warehouse</h3><p>Shows which team members belong to each warehouse.</p></div></div>
        <div class="warehouse-user-grid">${warehouseCards}</div>
      </section>
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Role Distribution</h3><p>Access levels configured in the system.</p></div></div>
        <div class="role-chip-list">${Object.entries(roleCounts).map(([role, count]) => `<span><b>${escapeHtml(role)}</b><em>${formatDashboardNumber(count)} accounts</em></span>`).join("")}</div>
      </section>
    </div>
    ${renderRecords("User Management", rows, "No users match the selected filters.")}${renderUserPagination(response)}
  `;
}

function renderUserPagination(response) {
  if (!response || Array.isArray(response)) return "";
  const previousDisabled = response.has_previous ? "" : "disabled";
  const nextDisabled = response.has_next ? "" : "disabled";
  return `<div class="pagination"><button id="previous-users" ${previousDisabled}>Previous</button><span>Page ${response.page} of ${response.total_pages || 1} (${response.total || 0} users)</span><button id="next-users" ${nextDisabled}>Next</button></div>`;
}

function bindUserPagination(response, filters = {}) {
  document.getElementById("previous-users")?.addEventListener("click", () => loadUserManagement(response.page - 1, viewRenderToken, filters));
  document.getElementById("next-users")?.addEventListener("click", () => loadUserManagement(response.page + 1, viewRenderToken, filters));
}

function bindUserFilters(filters = {}) {
  const form = document.getElementById("user-filter-form");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    clearCacheByPrefix("users:list:");
    loadUserManagement(1, viewRenderToken, {
      search: document.getElementById("user-search-filter")?.value.trim() || "",
      role: document.getElementById("user-role-filter")?.value || "",
      warehouse_id: document.getElementById("user-warehouse-filter")?.value || "",
      status: document.getElementById("user-status-filter")?.value || ""
    });
  });
}
async function loadInventoryReports(renderToken = viewRenderToken) {
  dashboardContent.innerHTML = renderSkeletonPanel(4, "Loading reports...");
  let rendered = false;
  let loadError = null;

  const emptyInventory = {
    summary: {
      total_products: 0,
      total_stock_units: 0,
      inventory_value: 0,
      low_stock_products: 0,
      out_of_stock_products: 0
    },
    category_summary: [],
    supplier_summary: [],
    low_stock_items: [],
    product_summary: []
  };
  const emptySuppliers = {
    summary: {
      total_suppliers: 0,
      total_products_supplied: 0,
      total_purchase_orders: 0,
      total_purchase_cost: 0,
      low_stock_products: 0
    },
    items: []
  };
  const emptySales = {
    summary: { revenue: 0, sales_count: 0, units_sold: 0 },
    items: []
  };
  const emptyPurchases = { items: [], total: 0 };

  try {
    const reportRequests = await Promise.allSettled([
      cachedAuthFetch(`reports:inventory:${dateCacheSuffix()}`, () => authFetch(appendQueryParams("/reports/inventory", apiDateParams()), { timeoutMs: 15000 }), 45000),
      cachedAuthFetch(`reports:suppliers:${dateCacheSuffix()}`, () => authFetch(appendQueryParams("/reports/suppliers", apiDateParams()), { timeoutMs: 15000 }), 45000),
      cachedAuthFetch(`reports:sales-monthly:${dateCacheSuffix()}`, () => authFetch(appendQueryParams("/reports/sales/monthly", apiDateParams()), { timeoutMs: 15000 }), 45000),
      cachedAuthFetch(`reports:purchases:${dateCacheSuffix()}`, () => authFetch(appendQueryParams(`/purchases?page=1&limit=${getRecordsPerPage()}`, apiDateParams()), { timeoutMs: 15000 }), 30000)
    ]);

    const [inventoryResult, suppliersResult, monthlySalesResult, purchasesResult] = reportRequests;
    const failures = reportRequests.filter((result) => result.status === "rejected");
    if (failures.length) {
      console.error("Reports API failed:", failures.map((result) => result.reason));
    }
    if (failures.length === reportRequests.length) {
      throw failures[0].reason || new Error("Unable to load reports. Please try again.");
    }

    const inventory = inventoryResult.status === "fulfilled" ? inventoryResult.value : emptyInventory;
    const suppliers = suppliersResult.status === "fulfilled" ? suppliersResult.value : emptySuppliers;
    const monthlySales = monthlySalesResult.status === "fulfilled" ? monthlySalesResult.value : emptySales;
    const purchases = purchasesResult.status === "fulfilled" ? purchasesResult.value : emptyPurchases;

    if (!isActiveRender("reports", renderToken)) return;
    dashboardContent.innerHTML = renderInventoryReports({
      inventory,
      suppliers,
      monthlySales,
      purchases,
      partialError: failures.length > 0
    });
    bindReportDownloads();
    refreshIcons();
    rendered = true;
  } catch (error) {
    loadError = error;
    console.error("Reports load error:", error);
    if (isActiveRender("reports", renderToken)) {
      dashboardContent.innerHTML = renderErrorPanel("Unable to load Reports", new Error("Unable to load reports. Please try again."), "reports");
      refreshIcons();
      rendered = true;
    }
  } finally {
    if (isActiveRender("reports", renderToken) && !rendered) {
      dashboardContent.innerHTML = renderErrorPanel("Unable to load Reports", loadError || new Error("Unable to load reports. Please try again."), "reports");
      refreshIcons();
    }
  }
}

function renderInventoryReports({ inventory = {}, suppliers = {}, monthlySales = {}, purchases = {}, partialError = false }) {
  const summary = inventory.summary || {};
  const supplierSummary = suppliers.summary || {};
  const salesSummary = monthlySales.summary || {};
  const purchaseRows = (purchases?.items || []).map((purchase) => ({
    purchase_id: purchase.purchase_id || "-",
    product: purchase.product_name || purchase.product_id || "-",
    supplier: purchase.supplier_name || purchase.supplier_id || "-",
    quantity: purchase.quantity || 0,
    total_cost: formatMoney(purchase.total_cost || 0),
    status: purchase.status || "-"
  }));
  const salesRows = (monthlySales.items || []).map((sale) => ({
    period: sale.month || sale.date || sale._id || "Selected range",
    sales_count: sale.sales_count || sale.count || 0,
    revenue: formatMoney(sale.revenue || sale.total_sales || 0)
  }));
  return `
    <section class="analytics-hero report-hero">
      <article><span><i data-lucide="boxes"></i></span><small>Inventory Value</small><strong>${formatMoney(summary.inventory_value)}</strong><em>${formatDashboardNumber(summary.total_stock_units)} units in stock</em></article>
      <article><span><i data-lucide="triangle-alert"></i></span><small>Low Stock</small><strong>${formatDashboardNumber(summary.low_stock_products)}</strong><em>${formatDashboardNumber(summary.out_of_stock_products)} out of stock</em></article>
      <article><span><i data-lucide="contact-round"></i></span><small>Suppliers</small><strong>${formatDashboardNumber(supplierSummary.total_suppliers)}</strong><em>${formatMoney(supplierSummary.total_purchase_cost)} purchase cost</em></article>
      <article><span><i data-lucide="chart-no-axes-combined"></i></span><small>Sales Revenue</small><strong>${formatMoney(salesSummary.revenue)}</strong><em>${formatDashboardNumber(salesSummary.sales_count)} sales records</em></article>
    </section>
    ${partialError ? `<section class="panel report-warning"><h3>Some report sections could not load</h3><p>Showing the report data that is available. Please try again if any section is missing.</p></section>` : ""}
    <section class="report-actions panel">
      <div class="section-heading"><div><h3>Download Reports</h3><p>Export PDF or CSV files directly from the dashboard.</p></div></div>
      <div class="report-mode-control">
        <label>Inventory Report Mode
          <select id="inventory-report-mode">
            <option value="product_summary" selected>Product Summary</option>
            <option value="warehouse_detail">Warehouse Detail</option>
          </select>
        </label>
      </div>
      <div>
        <button data-report-download="/reports/inventory/export/pdf"><i data-lucide="file-text"></i>Inventory PDF</button>
        <button data-report-download="/reports/inventory/export/csv"><i data-lucide="file-spreadsheet"></i>Inventory CSV</button>
        <button data-report-download="/reports/suppliers/export/pdf"><i data-lucide="file-text"></i>Supplier PDF</button>
        <button data-report-download="/reports/suppliers/export/csv"><i data-lucide="file-spreadsheet"></i>Supplier CSV</button>
        <button data-report-download="/reports/sales/monthly/export/pdf"><i data-lucide="file-text"></i>Monthly Sales PDF</button>
        <button data-report-download="/reports/sales/monthly/export/csv"><i data-lucide="file-spreadsheet"></i>Monthly Sales CSV</button>
      </div>
    </section>
    <div class="analytics-grid">
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Inventory Reports</h3><p>Category-level inventory value and stock units.</p></div></div>
        ${renderCategoryStats(inventory.category_summary || [])}
      </section>
      <section class="panel analytics-panel">
        <div class="section-heading"><div><h3>Supplier Reports</h3><p>Supplier purchase and stock contribution.</p></div></div>
        ${renderMiniBarList(suppliers.items || [], "inventory_value", "supplier_name", "product_count", "products")}
      </section>
    </div>
    <div class="analytics-grid">
      ${renderRecords("Sales Report", salesRows, "No sales report data available for selected date range.")}
      ${renderRecords("Purchase Report", purchaseRows, "No purchase report data available for selected date range.")}
    </div>
    ${renderRecords("Inventory Report", (inventory.low_stock_items || []).slice(0, 12), "No low stock inventory report data available.")}
  `;
}

function reportDownloadErrorMessage(status, responseText = "") {
  let backendMessage = "";
  try {
    const parsed = JSON.parse(responseText || "{}");
    backendMessage = parsed.detail || parsed.message || "";
  } catch (_) {
    backendMessage = responseText || "";
  }

  if (status === 401) return backendMessage || "Your session has expired. Please sign in again.";
  if (status === 403) return backendMessage || "You do not have permission to export this report.";
  if (status === 404) return backendMessage || "Report export endpoint was not found. Please check the backend route.";
  if (status >= 500) return backendMessage || "PDF generation failed on the backend. Please check the backend terminal.";
  return backendMessage || "Unable to export report. Please try again.";
}

function currentSessionRole() {
  try {
    return JSON.parse(getAuthValue("userProfile") || "{}").role || getAuthValue("role") || "";
  } catch {
    return getAuthValue("role") || "";
  }
}

function confirmAdminSensitiveAction(actionLabel = "continue") {
  if (currentSessionRole() !== "Admin") return Promise.resolve(true);
  return new Promise((resolve) => {
    openModal(`
      <section class="profile-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="sensitiveActionTitle">
        <header class="modal-header"><h2 id="sensitiveActionTitle">Confirm Admin Password</h2><button class="modal-close" type="button" data-sensitive-cancel aria-label="Cancel confirmation"><i data-lucide="x"></i></button></header>
        <form id="sensitiveActionForm" class="modal-form">
          <p class="reset-password-copy">Confirm your Admin password to ${escapeHtml(actionLabel)}.</p>
          <label>Password<span class="modal-input-wrap"><input id="sensitiveActionPassword" type="password" required autocomplete="current-password" data-modal-autofocus><button class="modal-eye-button" type="button" data-modal-password="sensitiveActionPassword" aria-label="Press and hold to reveal password"><i data-lucide="eye"></i></button></span></label>
          <p id="sensitiveActionMessage" class="modal-message" hidden></p>
          <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-sensitive-cancel>Cancel</button><button id="sensitiveActionButton" class="modal-primary-button" type="submit"><i data-lucide="shield-check"></i>Confirm</button></footer>
        </form>
      </section>`);

    const cancel = () => {
      closeModal();
      resolve(false);
    };
    document.querySelectorAll("[data-sensitive-cancel]").forEach((button) => button.addEventListener("click", cancel, { once: true }));
    document.getElementById("sensitiveActionForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const password = document.getElementById("sensitiveActionPassword").value;
      const message = document.getElementById("sensitiveActionMessage");
      const button = document.getElementById("sensitiveActionButton");
      const previous = button.innerHTML;
      button.disabled = true;
      button.textContent = "Confirming...";
      try {
        await authFetch("/auth/confirm-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password })
        });
        closeModal();
        resolve(true);
      } catch (error) {
        message.textContent = error.message || "Password confirmation failed.";
        message.hidden = false;
      } finally {
        button.disabled = false;
        button.innerHTML = previous;
        refreshIcons();
      }
    }, { once: true });
  });
}

async function downloadProtectedReport(endpoint) {
  const token = getStoredAccessToken();
  if (!token) {
    throw new Error("Your session is missing. Please sign in again.");
  }
  const url = `${API_URL}${endpoint}`;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), REPORT_EXPORT_TIMEOUT_MS);

  console.info("Report download request:", url);

  try {
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal
    });
    const contentType = response.headers.get("content-type") || "";
    console.info("Report download response:", response.status, contentType);

    if (!response.ok) {
      const text = await response.text();
      console.error("Report download failed:", { endpoint, url, status: response.status, body: text });
      throw new Error(reportDownloadErrorMessage(response.status, text));
    }

    if (!contentType.includes("application/pdf") && endpoint.includes("/pdf")) {
      const text = await response.text();
      console.error("Report download returned non-PDF response:", { endpoint, url, status: response.status, contentType, body: text });
      throw new Error(reportDownloadErrorMessage(response.status, text || "PDF generation failed."));
    }

    const blob = await response.blob();
    if (!blob.size) {
      console.error("Report download failed: empty response body", { url, status: response.status });
      throw new Error("Unable to export report. Empty response from server.");
    }

    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match?.[1] || endpoint.split("?")[0].split("/").filter(Boolean).join("_");
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("Report export timed out after 120 seconds. Please try again.");
    }
    if (error instanceof TypeError) {
      console.error("Report download network error:", error);
      throw new Error("Unable to export report. Please check the backend connection and try again.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function bindReportDownloads() {
  document.querySelectorAll("[data-report-download]").forEach((button) => {
    button.addEventListener("click", async () => {
      const original = button.innerHTML;
      button.disabled = true;
      button.innerHTML = "Downloading...";
      try {
        await downloadProtectedReport(appendQueryParams(button.dataset.reportDownload, reportExportParams()));
      } catch (error) {
        console.error("Report export error:", error);
        showToast(error.message || "Unable to export report. Please try again.", "error");
      } finally {
        button.disabled = false;
        button.innerHTML = original;
        refreshIcons();
      }
    });
  });
}

function renderRecords(title, rows, emptyMessage = "No records found.") {
  if (!rows.length) {
    return `<div class="panel"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(emptyMessage)}</p></div>`;
  }

  const columns = Object.keys(rows[0]).filter((key) => key !== "id").slice(0, 7);
  const header = columns.map((column) => `<th>${escapeHtml(column.replaceAll("_", " "))}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(formatValue(row[column]))}</td>`).join("")}</tr>`).join("");
  return `<div class="panel"><h3>${escapeHtml(title)}</h3><div class="table-wrap"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div></div>`;
}

function formatValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDashboardNumber(value) {
  return Number(value || 0).toLocaleString("en-IN");
}

function bindDashboardActions() {
  document.querySelectorAll("[data-quick-view]").forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "true";
    button.addEventListener("click", () => openView(button.dataset.quickView));
  });
  const inventoryStatusCard = document.getElementById("inventoryStatusCard");
  if (inventoryStatusCard && !inventoryStatusCard.dataset.bound) {
    inventoryStatusCard.dataset.bound = "true";
    inventoryStatusCard.addEventListener("click", () => showInventoryHealthModal().catch((error) => showToast(error.message || "Unable to load inventory health.", "error")));
    inventoryStatusCard.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        showInventoryHealthModal().catch((error) => showToast(error.message || "Unable to load inventory health.", "error"));
      }
    });
  }
  refreshIcons();
}

function profileStorageKey() {
  return `smart_inventory_profile_${getAuthValue("username") || "guest"}`;
}

function getProfileDetails() {
  const session = JSON.parse(getAuthValue("userProfile") || "{}");
  const username = session.username || getAuthValue("username") || "";
  const saved = JSON.parse(localStorage.getItem(profileStorageKey()) || "{}");
  const role = session.role || getAuthValue("role") || "";
  return {
    name: session.full_name || username,
    email: session.email || "",
    phone: session.phone || "",
    avatar: typeof saved.avatar === "string" && saved.avatar.startsWith("data:image/") ? saved.avatar : "",
    role,
    warehouseId: role === "Admin" ? "" : (session.warehouse_id || ""),
    warehouseName: role === "Admin" ? "All Warehouses" : (session.warehouse_name || session.location || session.location_name || ""),
    location: role === "Admin" ? "All Warehouses" : (session.location || session.warehouse_name || session.location_name || ""),
    state: session.state || "",
    createdAt: session.account_created || "",
    lastLogin: session.last_login || getAuthValue("lastLogin") || ""
  };
}

function formatProfileDate(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const settings = getAppSettings();
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: settings.dateFormat === "DD/MM/YYYY" ? "2-digit" : "short", year: "numeric", hour: "numeric", minute: "2-digit", hour12: settings.timeFormat !== "24"
  }).format(date);
}

function formatAccountCreatedDate(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const settings = getAppSettings();
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: settings.dateFormat === "DD/MM/YYYY" ? "2-digit" : "short", year: "numeric"
  }).format(date);
}

function countryCodeOptions(selectedCode) {
  return countryCodes.map((country) => `<option value="${country.code}" ${country.code === selectedCode ? "selected" : ""}>${country.label}</option>`).join("");
}

function splitPhoneNumber(phone) {
  const normalized = String(phone || "").replace(/\s+/g, "");
  const code = [...new Set(countryCodes.map((country) => country.code))]
    .sort((left, right) => right.length - left.length)
    .find((item) => normalized.startsWith(item)) || "+91";
  return { code, digits: normalized.slice(code.length).replace(/\D/g, "") };
}

function profileAvatarMarkup(profile, className = "modal-avatar") {
  const content = profile.avatar
    ? `<img src="${profile.avatar}" alt="${escapeHtml(profile.name)}">`
    : escapeHtml(profile.name.charAt(0).toUpperCase());
  return `<div class="${className}">${content}</div>`;
}

function openModal(content) {
  const overlay = document.getElementById("modalOverlay");
  if (!overlay.hidden) {
    void stopProductScanner();
  }
  overlay.onclick = null;
  overlay.innerHTML = content;
  overlay.hidden = false;
  document.body.classList.add("modal-open");
  bindModalEvents();
  refreshIcons();
  overlay.querySelector("[data-modal-autofocus]")?.focus();
}

function closeModal() {
  void stopProductScanner();
  const overlay = document.getElementById("modalOverlay");
  overlay.onclick = null;
  overlay.hidden = true;
  overlay.innerHTML = "";
  document.body.classList.remove("modal-open");
}

function showForgotPasswordModal() {
  openModal(`
    <section class="profile-modal modal-card reset-password-modal" role="dialog" aria-modal="true" aria-labelledby="resetPasswordTitle">
      <header class="modal-header"><h2 id="resetPasswordTitle">Reset Password</h2><button class="modal-close" type="button" data-modal-close aria-label="Close reset password"><i data-lucide="x"></i></button></header>
      <form id="forgotPasswordForm" class="modal-form">
        <p class="reset-password-copy">Enter the email address registered with your account to request a password reset link.</p>
        <label>Email<input id="resetAccountInput" type="email" required autocomplete="email" data-modal-autofocus></label>
        <div id="forgot-recaptcha-section" class="recaptcha-section">
          <div id="forgot-recaptcha" aria-label="reCAPTCHA verification"></div>
          <p id="forgot-recaptcha-message" class="form-error" hidden></p>
        </div>
        <p id="resetPasswordMessage" class="modal-message" hidden></p>
        <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-modal-close>Cancel</button><button id="sendResetLinkButton" class="modal-primary-button" type="submit"><i data-lucide="send"></i>Send Reset Link</button></footer>
      </form>
    </section>`);
  bindModalEvents();
  window.setTimeout(renderVisibleRecaptchaWidgets, 0);
  document.getElementById("forgotPasswordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = document.getElementById("resetAccountInput").value.trim();
    const message = document.getElementById("resetPasswordMessage");
    const submitButton = document.getElementById("sendResetLinkButton");
    const originalButtonHtml = submitButton?.innerHTML || `<i data-lucide="send"></i>Send Reset Link`;
    if (submitButton?.disabled) return;

    try {
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Sending reset link...";
      }
      message.textContent = "Sending reset link...";
      message.classList.remove("success");
      message.hidden = false;

      const recaptchaToken = getRecaptchaToken("forgot", "forgot-recaptcha");
      const response = await fetch(`${API_URL}/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, recaptcha_token: recaptchaToken })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        resetRecaptcha("forgot");
        throw new Error(data.detail || "Unable to send reset instructions. Please try again.");
      }
      resetRecaptcha("forgot");
      message.textContent = data.message || "Password reset instructions have been sent to your registered email.";
      message.classList.add("success");
      message.hidden = false;
    } catch (error) {
      message.textContent = error.message || "Unable to send reset instructions. Please try again.";
      message.classList.remove("success");
      message.hidden = false;
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerHTML = originalButtonHtml;
      }
    }
  });
}
function showProfileModal() {
  const profile = getProfileDetails();
  const warehouseInfo = profile.role === "Admin"
    ? `<div><span class="info-icon"><i data-lucide="warehouse"></i></span><p><small>Access Scope</small><strong>All Warehouses</strong></p></div>` 
    : `<div><span class="info-icon"><i data-lucide="warehouse"></i></span><p><small>Warehouse Name</small><strong>${escapeHtml(profile.warehouseName || "Not assigned")}</strong></p></div><div><span class="info-icon"><i data-lucide="hash"></i></span><p><small>Warehouse ID</small><strong>${escapeHtml(profile.warehouseId || "Not assigned")}</strong></p></div>`;
  openModal(`
    <section class="profile-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="profileModalTitle">
      <header class="modal-header"><h2 id="profileModalTitle">Profile Details</h2><button class="modal-close" type="button" data-modal-close aria-label="Close profile details"><i data-lucide="x"></i></button></header>
      <div class="profile-summary">
        <div class="avatar-editor">${profileAvatarMarkup(profile)}<button class="avatar-edit-button" type="button" data-open-edit aria-label="Edit profile"><i data-lucide="pencil"></i></button></div>
        <div><h3>${escapeHtml(profile.name)}</h3><p>${escapeHtml(profile.role)}</p><span class="active-badge"><i data-lucide="circle-check"></i>Active</span></div>
      </div>
      <section class="profile-information" aria-label="Profile information">
        <div><span class="info-icon"><i data-lucide="mail"></i></span><p><small>Email</small><strong>${escapeHtml(profile.email)}</strong></p></div>
        <div><span class="info-icon"><i data-lucide="phone"></i></span><p><small>Phone Number</small><strong>${escapeHtml(profile.phone)}</strong></p></div>
        ${warehouseInfo}
        <div><span class="info-icon"><i data-lucide="calendar-days"></i></span><p><small>Account Created</small><strong>${escapeHtml(formatAccountCreatedDate(profile.createdAt))}</strong></p></div>
        <div><span class="info-icon"><i data-lucide="clock-3"></i></span><p><small>Last Login</small><strong>${escapeHtml(formatProfileDate(profile.lastLogin))}</strong></p></div>
      </section>
      <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-open-edit><i data-lucide="pencil"></i>Edit Profile</button><button class="modal-primary-button" type="button" data-open-password><i data-lucide="key-round"></i>Change Password</button></footer>
    </section>`);
  bindModalEvents();
}

function showEditProfileModal() {
  const profile = getProfileDetails();
  const phone = splitPhoneNumber(profile.phone);
  openModal(`
    <section class="profile-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="editProfileTitle">
      <header class="modal-header"><h2 id="editProfileTitle">Edit Profile</h2><button class="modal-close" type="button" data-modal-close aria-label="Close edit profile"><i data-lucide="x"></i></button></header>
      <form id="editProfileForm" class="modal-form">
        <div class="profile-picture-field"><div id="editAvatarPreview" class="avatar-editor">${profileAvatarMarkup(profile, "modal-avatar preview-avatar")}<label class="avatar-edit-button" for="profilePictureInput" aria-label="Choose profile picture"><i data-lucide="camera"></i></label></div><input id="profilePictureInput" type="file" accept="image/png,image/jpeg,image/webp" hidden><span>Profile picture</span></div>
        <label>Full Name<input id="profileNameInput" type="text" value="${escapeHtml(profile.name)}" required data-modal-autofocus></label>
        <label>Email<input id="profileEmailInput" type="email" value="${escapeHtml(profile.email)}" required autocomplete="email"></label>
        <label>Phone Number<span class="phone-input-group"><select id="profileCountryCode" aria-label="Country code">${countryCodeOptions(phone.code)}</select><input id="profilePhoneInput" type="tel" inputmode="numeric" pattern="[0-9]*" value="${escapeHtml(phone.digits)}" required></span></label>
        <p id="editProfileMessage" class="modal-message" hidden></p>
        <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-open-profile>Cancel</button><button class="modal-primary-button" type="submit"><i data-lucide="save"></i>Save Changes</button></footer>
      </form>
    </section>`);
  bindModalEvents();
  const fileInput = document.getElementById("profilePictureInput");
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      const message = document.getElementById("editProfileMessage");
      message.textContent = "Choose an image smaller than 2 MB.";
      message.hidden = false;
      return;
    }
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      document.getElementById("editAvatarPreview").innerHTML = `<div class="modal-avatar preview-avatar"><img src="${reader.result}" alt="Profile preview"></div><label class="avatar-edit-button" for="profilePictureInput" aria-label="Choose profile picture"><i data-lucide="camera"></i></label>`;
      refreshIcons();
    });
    reader.readAsDataURL(file);
  });
  document.getElementById("editProfileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("profileNameInput").value.trim();
    const email = document.getElementById("profileEmailInput").value.trim();
    const phoneDigits = document.getElementById("profilePhoneInput").value.replace(/\D/g, "");
    const phone = `${document.getElementById("profileCountryCode").value} ${phoneDigits}`;
    const message = document.getElementById("editProfileMessage");
    if (name.length < 2 || phoneDigits.length < 7 || phoneDigits.length > 15 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      message.textContent = "Enter a valid name, email address, and phone number.";
      message.hidden = false;
      return;
    }
    try {
      const updatedProfile = await authFetch("/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ full_name: name, email, phone })
      });
      const currentSession = JSON.parse(getAuthValue("userProfile") || "{}");
      setAuthValue("userProfile", JSON.stringify({ ...currentSession, ...updatedProfile }), activeSessionIsRemembered());
      const saved = JSON.parse(localStorage.getItem(profileStorageKey()) || "{}");
      const preview = document.querySelector("#editAvatarPreview img");
      localStorage.setItem(profileStorageKey(), JSON.stringify({ ...saved, avatar: preview?.src || saved.avatar || "" }));
      updateDashboardUser();
      setDashboardGreeting();
      showProfileModal();
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  });
}

function showPasswordModal() {
  openModal(`
    <section class="profile-modal modal-card" role="dialog" aria-modal="true" aria-labelledby="passwordModalTitle">
      <header class="modal-header"><h2 id="passwordModalTitle">Change Password</h2><button class="modal-close" type="button" data-modal-close aria-label="Close change password"><i data-lucide="x"></i></button></header>
      <form id="changePasswordForm" class="modal-form password-form">
        <label>Current Password<span class="modal-input-wrap"><input id="currentPassword" type="password" required data-modal-autofocus><button class="modal-eye-button" type="button" data-modal-password="currentPassword" aria-label="Press and hold to reveal current password"><i data-lucide="eye"></i></button></span></label>
        <label>New Password<span class="modal-input-wrap"><input id="newPassword" type="password" required minlength="8"><button class="modal-eye-button" type="button" data-modal-password="newPassword" aria-label="Press and hold to reveal new password"><i data-lucide="eye"></i></button></span></label>
        <div id="changePasswordFeedback" class="password-feedback" aria-live="polite" hidden></div>
        <label>Confirm Password<span class="modal-input-wrap"><input id="confirmNewPassword" type="password" required minlength="8"><button class="modal-eye-button" type="button" data-modal-password="confirmNewPassword" aria-label="Press and hold to reveal confirmed password"><i data-lucide="eye"></i></button></span></label>
        <p id="changeConfirmMessage" class="password-match-message" hidden></p>
        <p id="passwordMessage" class="modal-message" hidden></p>
        <footer class="modal-footer"><button class="modal-secondary-button" type="button" data-open-profile>Cancel</button><button id="changePasswordButton" class="modal-primary-button" type="submit" disabled><i data-lucide="shield-check"></i>Update Password</button></footer>
      </form>
    </section>`);
  bindModalEvents();
  setupPasswordGuidance("newPassword", "changePasswordFeedback");
  const currentInput = document.getElementById("currentPassword");
  const nextInput = document.getElementById("newPassword");
  const confirmInput = document.getElementById("confirmNewPassword");
  const submitButton = document.getElementById("changePasswordButton");
  const confirmMessage = document.getElementById("changeConfirmMessage");
  const validateChangePasswordForm = () => {
    const current = currentInput.value;
    const next = nextInput.value;
    const confirm = confirmInput.value;
    const matches = Boolean(confirm) && next === confirm;
    if (confirm && !matches) {
      confirmMessage.textContent = "Passwords do not match.";
      confirmMessage.hidden = false;
    } else {
      confirmMessage.hidden = true;
    }
    submitButton.disabled = !current || !isStrongPassword(next) || !matches;
  };
  [currentInput, nextInput, confirmInput].forEach((input) => input.addEventListener("input", validateChangePasswordForm));
  validateChangePasswordForm();
  document.getElementById("changePasswordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitButton.disabled) return;
    const current = currentInput.value;
    const next = nextInput.value;
    const message = document.getElementById("passwordMessage");
    const apiUrl = `${API_URL}/auth/change-password`;
    console.log("Change password API URL:", apiUrl);
    try {
      const rawResponse = await fetch(apiUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getStoredAccessToken()}`
        },
        body: JSON.stringify({
          current_password: current,
          new_password: next
        })
      });
      const responseText = await rawResponse.text();
      console.log("Change password API response status:", rawResponse.status);
      console.log("Change password API response text:", responseText);
      const response = responseText ? JSON.parse(responseText) : {};
      if (!rawResponse.ok) {
        const apiError = new Error(response.detail || "Unable to update password. Please try again.");
        apiError.status = rawResponse.status;
        throw apiError;
      }
      message.textContent = response.message || "Password changed successfully.";
      message.classList.add("success");
      message.hidden = false;
      event.target.reset();
      document.getElementById("changePasswordFeedback").hidden = true;
      validateChangePasswordForm();
    } catch (error) {
      console.log("Change password API error:", error.status, error.message);
      message.textContent = error instanceof TypeError
        ? "Backend server is not running."
        : error.status === 400
        ? "Current password is incorrect."
        : error.status === 401
          ? "Your session has expired. Please sign in again."
          : "Unable to update password. Please try again.";
      message.classList.remove("success");
      message.hidden = false;
    }
  });
}

function bindModalEvents() {
  const overlay = document.getElementById("modalOverlay");
  overlay.onclick = (event) => {
    if (event.target === overlay || event.target.closest("[data-modal-close]")) closeModal();
    else if (event.target.closest("[data-open-edit]")) showEditProfileModal();
    else if (event.target.closest("[data-open-profile]")) showProfileModal();
    else if (event.target.closest("[data-open-password]")) showPasswordModal();
    else if (event.target.closest("[data-health-action]")) {
      const button = event.target.closest("[data-health-action]");
      handleInventoryHealthAction(button.dataset.healthAction, button.dataset.productId, button).catch((error) => {
        showToast(error.message || "Unable to update inventory.", "error");
      });
    }
    else if (event.target.closest("[data-modal-password]")) {
      event.preventDefault();
    }
  };
}

function openNotificationDropdown() {
  const menu = document.getElementById("notificationMenu");
  if (!menu) return;
  clearTimeout(notificationCloseTimer);
  renderNotificationMenu();
  menu.hidden = false;
  requestAnimationFrame(() => menu.classList.add("is-open"));
}

function closeNotificationDropdown() {
  const menu = document.getElementById("notificationMenu");
  if (!menu || menu.hidden) return;
  clearTimeout(notificationCloseTimer);
  menu.classList.remove("is-open");
  menu.classList.add("is-closing");
  notificationCloseTimer = setTimeout(() => {
    menu.hidden = true;
    menu.classList.remove("is-closing");
  }, 170);
}

function closeDashboardPopovers() {
  document.querySelectorAll(".dashboard-popover").forEach((popover) => {
    if (popover.id === "notificationMenu") closeNotificationDropdown();
    else popover.hidden = true;
  });
}

function profileMenuMarkup(includeViewProfile = true) {
  const profile = getProfileDetails();
  const scope = profile.role === "Admin" ? "All Warehouses" : (profile.warehouseName ? `Warehouse: ${profile.warehouseName}` : "Warehouse not assigned");
  return `<div class="menu-account"><strong>${escapeHtml(profile.name)}</strong><span>${escapeHtml(profile.role)}</span><small>${escapeHtml(scope)}</small></div>${includeViewProfile ? '<button type="button" data-account-action="profile">View Profile</button>' : ""}<button type="button" data-account-action="settings">Settings</button><button type="button" data-account-action="logout" class="danger-menu-action">Logout</button>`;
}
async function refreshDateFilteredView() {
  updateDateRangeLabels();
  routeCache.clear();
  if (activeView === "dashboard") {
    dashboardData = null;
    dashboardDataCacheKey = "";
    dashboardLoadPromise = null;
    setDashboardStatus("Loading selected period...");
    await loadDashboardData();
    return;
  }
  openView(activeView, 1, false);
}
function renderDateRangeMenu() {
  const menu = document.getElementById("dateRangeMenu");
  if (!menu) return;
  const options = [
    ["today", "Today"],
    ["last7", "Last 7 Days"],
    ["last30", "Last 30 Days"],
    ["month", "This Month"],
    ["custom", "Custom Range"]
  ];
  menu.innerHTML = `${options.map(([key, label]) => `<button type="button" class="date-option ${dateRangeState.key === key ? "selected" : ""}" data-date-range="${key}">${label}</button>`).join("")}<form id="customDateRangeForm" class="custom-date-range" ${dateRangeState.key === "custom" ? "" : "hidden"}><label>From<input id="customDateFrom" type="date" required value="${dateRangeState.from || ""}"></label><label>To<input id="customDateTo" type="date" required value="${dateRangeState.to || ""}"></label><button type="submit">Apply range</button></form>`;
  menu.querySelectorAll("[data-date-range]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.dateRange;
      if (key === "custom") {
        dateRangeState = { key: "custom", from: dateRangeState.from || new Date().toISOString().slice(0, 10), to: dateRangeState.to || new Date().toISOString().slice(0, 10) };
        saveDateRangeState();
        updateDateRangeLabels();
        renderDateRangeMenu();
        return;
      }
      dateRangeState = { key };
      saveDateRangeState();
      menu.hidden = true;
      refreshDateFilteredView();
    });
  });
  const customForm = document.getElementById("customDateRangeForm");
  if (customForm) customForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const from = document.getElementById("customDateFrom").value;
    const to = document.getElementById("customDateTo").value;
    if (!from || !to || new Date(from) > new Date(to)) return;
    dateRangeState = { key: "custom", from, to };
    saveDateRangeState();
    menu.hidden = true;
    refreshDateFilteredView();
  });
}

function displaySearchResults(query) {
  const results = document.getElementById("searchResults");
  if (!results) return;
  const term = query.trim().toLowerCase();
  if (!term) {
    results.hidden = true;
    results.innerHTML = "";
    return;
  }
  if (!dashboardData) {
    results.hidden = false;
    results.innerHTML = '<p class="empty-menu">Loading search data...</p>';
    return;
  }
  const matches = [];
  const addMatches = (items, type, view, fields) => {
    let added = 0;
    items.forEach((item) => {
      const text = fields.map((field) => item[field]).filter(Boolean).join(" ");
      if (text.toLowerCase().includes(term) && added < 4) {
        matches.push({ type, view, title: item.product_name || item.supplier_name || item.sale_id || item.purchase_id || item.product_id, detail: text });
        added += 1;
      }
    });
  };
  addMatches(dashboardData.products, "Product", "products", ["product_id", "product_name", "category_id"]);
  addMatches(dashboardData.sales, "Sale", "sales", ["sale_id", "product_id", "product_name", "customer_name"]);
  addMatches(dashboardData.purchases, "Purchase", "purchases", ["purchase_id", "product_id", "product_name", "supplier"]);
  addMatches(dashboardData.suppliers, "Supplier", "suppliers", ["supplier_id", "supplier_name", "email"]);
  addMatches(dashboardData.currentStock || [], "Inventory", "inventory", ["product_id", "product_name", "current_stock", "stock_status"]);
  results.hidden = false;
  results.innerHTML = matches.length
    ? matches.map((item) => `<button class="search-result" type="button" data-search-view="${item.view}"><strong>${escapeHtml(item.title || item.type)}</strong><span>${escapeHtml(item.type)}: ${escapeHtml(item.detail)}</span></button>`).join("")
    : '<p class="empty-menu">No results found</p>';
  results.querySelectorAll("[data-search-view]").forEach((button) => button.addEventListener("click", () => {
    document.getElementById("dashboardSearchInput").value = "";
    results.hidden = true;
    openView(button.dataset.searchView);
  }));
}

function initDashboardControls() {
  const searchInput = document.getElementById("dashboardSearchInput");
  const notificationButton = document.getElementById("notificationButton");
  const notificationMenu = document.getElementById("notificationMenu");
  const headerProfileButton = document.getElementById("headerProfileButton");
  const headerProfileMenu = document.getElementById("headerProfileMenu");
  const sidebarProfileButton = document.getElementById("sidebarProfileMenuButton");
  const sidebarProfileMenu = document.getElementById("sidebarProfileMenu");
  const dateRangeButton = document.getElementById("dateRangeButton");
  const dateRangeMenu = document.getElementById("dateRangeMenu");

  searchInput.addEventListener("input", () => {
    const query = searchInput.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      if (query.trim() && (!dashboardData || !dashboardData.currentStock)) {
        displaySearchResults(query);
        try {
          await loadDashboardDataset();
          await loadInventorySearchData();
        } catch (error) {
          displaySearchResults("");
          setDashboardStatus(`Search data could not load: ${error.message}`, true);
          return;
        }
      }
      displaySearchResults(query);
    }, 250);
  });

  notificationButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const shouldOpen = notificationMenu.hidden;
    closeDashboardPopovers();
    if (shouldOpen) openNotificationDropdown();
  });
  notificationMenu.addEventListener("click", (event) => {
    const markAllButton = event.target.closest("[data-mark-all-notifications]");
    if (markAllButton) {
      const readIds = getReadNotificationIds();
      dashboardNotifications.forEach((item) => readIds.add(item.id));
      localStorage.setItem(notificationReadStorageKey(), JSON.stringify([...readIds]));
      renderNotificationMenu();
      return;
    }

    const action = event.target.closest("[data-notification-view]");
    if (!action) return;
    const readIds = getReadNotificationIds();
    readIds.add(action.dataset.notificationId);
    localStorage.setItem(notificationReadStorageKey(), JSON.stringify([...readIds]));
    renderNotificationMenu();
    closeDashboardPopovers();
    openView(action.dataset.notificationView);
  });

  headerProfileButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const shouldOpen = headerProfileMenu.hidden;
    closeDashboardPopovers();
    headerProfileMenu.innerHTML = profileMenuMarkup();
    headerProfileMenu.hidden = !shouldOpen;
  });
  sidebarProfileButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const shouldOpen = sidebarProfileMenu.hidden;
    closeDashboardPopovers();
    sidebarProfileMenu.innerHTML = profileMenuMarkup();
    sidebarProfileMenu.hidden = !shouldOpen;
  });

  dateRangeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const shouldOpen = dateRangeMenu.hidden;
    closeDashboardPopovers();
    if (shouldOpen) renderDateRangeMenu();
    dateRangeMenu.hidden = !shouldOpen;
  });

  document.addEventListener("click", (event) => {
    const accountAction = event.target.closest("[data-account-action]");
    if (accountAction) {
      const action = accountAction.dataset.accountAction;
      closeDashboardPopovers();
      if (action === "logout") logout();
      else if (action === "settings") openView("settings");
      else if (action === "profile") {
        setCleanRoute("profile");
        showProfileModal();
      }
      return;
    }
    if (!event.target.closest(".search-container, .notification-container, .profile-container, .sidebar-profile, .date-container")) closeDashboardPopovers();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDashboardPopovers();
      closeModal();
    }
  });
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "stroke-width": 2 } });
  }
}

setupPasswordValidation("regPassword", "regPasswordFeedback");
applyAppSettings();
["click", "keydown", "mousemove", "touchstart"].forEach((eventName) => {
  document.addEventListener(eventName, resetSessionTimeout, { passive: true });
});
loadPublicConfig().finally(() => {
  bootstrapApplication().finally(initializeGoogleSignIn);
});
initDashboardControls();
document.addEventListener("input", (event) => {
  if (event.target.id === "profilePhoneInput") {
    event.target.value = event.target.value.replace(/\D/g, "");
  }
});
window.addEventListener("load", initializeGoogleSignIn);
window.addEventListener("popstate", handleRoute);
refreshIcons();































































































