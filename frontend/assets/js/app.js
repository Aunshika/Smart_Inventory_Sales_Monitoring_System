const view = document.getElementById("view");
const statusBox = document.getElementById("status");
const title = document.getElementById("view-title");
const subtitle = document.getElementById("view-subtitle");
const navButtons = document.querySelectorAll(".nav-link");
const authScreen = document.getElementById("auth-screen");
const appShell = document.getElementById("app-shell");
const authStatus = document.getElementById("auth-status");

const viewConfig = {
  dashboard: ["Dashboard", "Inventory and sales overview"], products: ["Products", "Search, filter, and review product stock"], inventory: ["Inventory", "Current stock, low stock, and alerts"], sales: ["Sales", "Recent sales records"], purchases: ["Purchases", "Recent purchase records"], suppliers: ["Suppliers", "Supplier directory"], reports: ["Reports", "Inventory, sales, and supplier reports"]
};

function setAuthView(name) {
  const isRegister = name === "register";
  document.getElementById("login-panel").hidden = isRegister;
  document.getElementById("register-panel").hidden = !isRegister;
  document.getElementById("auth-visual-heading").innerHTML = isRegister ? 'Create <span>Your Account</span>' : 'Smart Inventory &amp;<br>Sales Monitoring System';
  document.getElementById("auth-visual-description").textContent = isRegister ? 'Join Smart Inventory today and take control of your business.' : 'Track inventory in real time, monitor sales, manage stock, and grow your business smarter.';
  authStatus.hidden = true;
}
function showAuthStatus(message, isError = true) { authStatus.textContent = message; authStatus.classList.toggle("error", isError); authStatus.hidden = false; }
function showStatus(message, isError = false) { statusBox.textContent = message; statusBox.classList.toggle("error", isError); statusBox.hidden = false; }
function routeName() { const route = window.location.hash.replace("#", "").replace(/^\//, ""); return viewConfig[route] ? route : "dashboard"; }
function goTo(route) { window.location.hash = `/${route}`; }
function requireAuthentication() {
  if (api.token) return true;
  appShell.hidden = true; authScreen.hidden = false; setAuthView("login");
  return false;
}

document.querySelectorAll("[data-auth-view]").forEach((button) => button.addEventListener("click", () => setAuthView(button.dataset.authView)));
document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await api.login(document.getElementById("username").value.trim(), document.getElementById("password").value); goTo("dashboard"); } catch (error) { showAuthStatus(error.message); }
});
document.getElementById("register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = document.getElementById("register-password").value;
  const confirmPassword = document.getElementById("confirm-password").value;
  if (password !== confirmPassword) { showAuthStatus("Passwords do not match"); return; }
  try {
    await api.register({ username: document.getElementById("register-username").value.trim(), email: document.getElementById("register-email").value.trim(), password, confirm_password: confirmPassword, role: document.getElementById("register-role").value });
    setAuthView("login"); document.getElementById("username").value = document.getElementById("register-username").value.trim(); showAuthStatus("Account created. Sign in to continue.", false);
  } catch (error) { showAuthStatus(error.message); }
});
document.getElementById("logout-button").addEventListener("click", () => { api.clearSession(); window.location.hash = ""; requireAuthentication(); });
navButtons.forEach((button) => button.addEventListener("click", () => goTo(button.dataset.view)));
window.addEventListener("hashchange", renderRoute);

async function renderRoute() {
  if (!requireAuthentication()) return;
  authScreen.hidden = true; appShell.hidden = false;
  const user = api.user; document.getElementById("current-user").textContent = user ? `${user.username} (${user.role})` : "Signed in";
  const name = routeName(); navButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  title.textContent = viewConfig[name][0]; subtitle.textContent = viewConfig[name][1]; view.innerHTML = '<div class="panel">Loading...</div>';
  try {
    if (name === "dashboard") await renderDashboard(); if (name === "products") await renderProducts(); if (name === "inventory") await renderInventory(); if (name === "sales") await renderSales(); if (name === "purchases") await renderPurchases(); if (name === "suppliers") await renderSuppliers(); if (name === "reports") await renderReports();
  } catch (error) {
    if (error.status === 401) { api.clearSession(); showAuthStatus("Your session has expired. Sign in again."); requireAuthentication(); return; }
    view.innerHTML = `<div class="panel">${escapeHtml(error.message)}</div>`;
  }
}

async function renderDashboard() { const [inventory, sales] = await Promise.all([api.getInventoryDashboard(), api.getSalesDashboard()]); view.innerHTML = `<div class="grid metrics">${metric("Products", inventory.total_products)}${metric("Stock Units", inventory.total_stock_units)}${metric("Inventory Value", inventory.inventory_value)}${metric("Revenue", sales.total_revenue)}</div><div class="grid" style="margin-top:14px"><div class="panel"><h3>Recent Inventory Movements</h3>${table(inventory.recent_movements, ["product_id", "product_name", "movement_type", "quantity", "performed_by"])}</div></div>`; }
async function renderProducts() { view.innerHTML = `<div class="toolbar"><input id="product-search" placeholder="Search product"><input id="category-filter" placeholder="Category ID"><input id="supplier-filter" placeholder="Supplier ID"><button id="search-products">Search</button></div><div id="products-table"></div>`; document.getElementById("search-products").addEventListener("click", loadProductsTable); await loadProductsTable(); }
async function loadProductsTable() { const data = await api.getProducts({ search: document.getElementById("product-search").value, category_id: document.getElementById("category-filter").value, supplier_id: document.getElementById("supplier-filter").value, limit: 20 }); document.getElementById("products-table").innerHTML = table(data.items, ["product_id", "product_name", "quantity", "price", "unit_cost", "reorder_level", "category_id", "supplier_id"]); }
async function renderInventory() { const [stock, lowStock] = await Promise.all([api.getCurrentStock(), api.getLowStock()]); view.innerHTML = `<div class="grid metrics">${metric("Products", stock.length)}${metric("Low Stock", lowStock.total)}</div><div class="panel" style="margin-top:14px"><h3>Current Stock</h3>${table(stock, ["product_id", "product_name", "current_stock", "stock_value", "stock_status"])}</div>`; }
async function renderSales() { const data = await api.getSales(); view.innerHTML = table(data.items, ["sale_id", "product_id", "product_name", "quantity", "unit_price", "total_amount", "sold_by"]); }
async function renderPurchases() { const data = await api.getPurchases(); view.innerHTML = table(data.items, ["purchase_id", "product_id", "product_name", "quantity", "unit_cost", "total_cost", "purchased_by"]); }
async function renderSuppliers() { const data = await api.getSuppliers(); view.innerHTML = table(data.slice(0, 50), ["supplier_id", "supplier_name", "email", "phone", "address"]); }
async function renderReports() { const [inventory, suppliers, monthly] = await Promise.all([api.getInventoryReport(), api.getSupplierReport(), api.getMonthlySalesReport()]); view.innerHTML = `<div class="grid metrics">${metric("Inventory Value", inventory.summary.inventory_value)}${metric("Supplier Count", suppliers.summary.total_suppliers)}${metric("Monthly Groups", monthly.items.length)}${metric("Sales Revenue", monthly.summary.revenue)}</div>`; }
function metric(label, value) { return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`; }
function table(rows, columns) { if (!rows || rows.length === 0) return '<div class="empty">No records</div>'; const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join(""); const body = rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? "")}</td>`).join("")}</tr>`).join(""); return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`; }
function escapeHtml(value) { return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
renderRoute();
