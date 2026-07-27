const tokenStoreKey = "access_token";
const userStoreKey = "smart_inventory_user";

function rememberedSessionEnabled() { return localStorage.getItem("smart_inventory_remember_session") === "true"; }
function activeToken() { return sessionStorage.getItem(tokenStoreKey) || (rememberedSessionEnabled() ? localStorage.getItem(tokenStoreKey) : ""); }
function activeUser() { return sessionStorage.getItem(userStoreKey) || (rememberedSessionEnabled() ? localStorage.getItem(userStoreKey) : ""); }

const api = {
  get token() { return activeToken(); },
  get user() { return JSON.parse(activeUser() || "null"); },
  setSession(token, user, remember = false) {
    const target = remember ? localStorage : sessionStorage;
    const other = remember ? sessionStorage : localStorage;
    other.removeItem(tokenStoreKey);
    other.removeItem(userStoreKey);
    target.setItem(tokenStoreKey, token);
    localStorage.removeItem("token");
    localStorage.removeItem("smart_inventory_token");
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("smart_inventory_token");
    target.setItem(userStoreKey, JSON.stringify(user));
  },
  clearSession() {
    [localStorage, sessionStorage].forEach((storage) => {
      storage.removeItem(tokenStoreKey);
      storage.removeItem("token");
      storage.removeItem("smart_inventory_token");
      storage.removeItem(userStoreKey);
    });
  },

  async login(username, password) {
    const body = new URLSearchParams({ username, password });
    const data = await this.fetchJson("/login", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body, timeoutMs: 60000 }, false);
    const user = data.user || {
      username: data.username,
      full_name: data.full_name,
      email: data.email,
      phone: data.phone || "",
      role: data.role,
      location_id: data.location_id,
      warehouse_id: data.warehouse_id,
      warehouse_name: data.warehouse_name,
      location: data.location,
      state: data.state,
      account_created: data.account_created,
      last_login: data.last_login
    };
    this.setSession(data.access_token, user);
    return data;
  },

  async register(details) {
    const query = new URLSearchParams(details);
    return this.fetchJson(`/register?${query}`, { method: "POST" }, false);
  },

  async fetchJson(path, options = {}, authenticated = true) {
    const headers = { ...(options.headers || {}) };
    if (authenticated && this.token) headers.Authorization = `Bearer ${this.token}`;
    const timeoutMs = options.timeoutMs || 15000;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const { timeoutMs: _timeoutMs, signal: callerSignal, ...fetchOptions } = options;

    if (callerSignal) {
      if (callerSignal.aborted) controller.abort();
      else callerSignal.addEventListener("abort", () => controller.abort(), { once: true });
    }

    let response;
    try {
      response = await fetch(`${window.APP_CONFIG.API_BASE_URL}${path}`, { ...fetchOptions, headers, signal: controller.signal });
    } catch (error) {
      const message = error.name === "AbortError" ? "Request timed out. Please try again." : "Backend server is not running. Please start the backend and try again.";
      const networkError = new Error(message);
      networkError.status = 0;
      networkError.cause = error;
      throw networkError;
    } finally {
      clearTimeout(timeoutId);
    }

    const text = await response.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (error) { data = { detail: text }; }
    }
    if (!response.ok) {
      const error = new Error(data?.detail || data?.message || `Request failed: ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data || {};
  },

  request(path, options = {}) { return this.fetchJson(path, options); },
  getProducts(params = {}) { return this.request(`/products${toQuery(params)}`); },
  getInventoryDashboard() { return this.request("/dashboard/inventory"); },
  getSalesDashboard() { return this.request("/dashboard/sales"); },
  getCurrentStock() { return this.request("/inventory/current-stock"); },
  getLowStock() { return this.request("/inventory/low-stock"); },
  getSales() { return this.request("/sales?limit=20"); },
  getPurchases() { return this.request("/purchases?limit=20"); },
  getSuppliers() { return this.request("/suppliers"); },
  getInventoryReport() { return this.request("/reports/inventory"); },
  getSupplierReport() { return this.request("/reports/suppliers"); },
  getMonthlySalesReport() { return this.request("/reports/sales/monthly"); }
};

function toQuery(params) { const query = new URLSearchParams(); Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") query.append(key, value); }); const text = query.toString(); return text ? `?${text}` : ""; }

