const adminOrganizationId = new URLSearchParams(location.search).get("organization");
const adminHeaders = adminOrganizationId ? { "X-Organization-Id": adminOrganizationId } : {};
const adminMessage = document.querySelector("#admin-message");
const adminRows = document.querySelector("#admin-organizations");
const adminDialog = document.querySelector("#admin-organization-dialog");
const pageSize = 50;
let adminOffset = 0;
let adminTotal = 0;
let selectedOrganizationId = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function planLabel(value) {
  return { internal: "Interno", trial: "Teste" }[value] || value;
}

function statusLabel(value) {
  return { trialing: "Em teste", active: "Ativo", past_due: "Pagamento pendente", canceled: "Cancelado", suspended: "Suspenso" }[value] || value;
}

function notify(message, target = adminMessage, success = false) {
  target.textContent = message;
  target.dataset.type = success ? "success" : "";
  target.classList.remove("hidden");
}

async function adminFetch(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...adminHeaders, ...(options.headers || {}) } });
  const payload = await response.json();
  if (response.status === 401) {
    location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    throw new Error("Sua sessão expirou.");
  }
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Não foi possível concluir a operação.");
  return payload;
}

function renderMetrics(data) {
  const values = [data.total, data.user_count, data.pending_signups, data.metrics.credits_available, data.metrics.unlocked_companies];
  document.querySelectorAll("#admin-metrics strong").forEach((element, index) => {
    element.textContent = Number(values[index] || 0).toLocaleString("pt-BR");
  });
}

function renderOrganizations(data) {
  adminTotal = data.total;
  adminRows.innerHTML = data.organizations.length ? data.organizations.map((organization) => {
    const billing = organization.billing;
    return `<tr><td><strong>${escapeHtml(organization.name)}</strong><small>#${organization.id} · ${formatDate(organization.created_at)}</small></td><td>${escapeHtml(organization.owner_email)}</td><td>${organization.member_count.toLocaleString("pt-BR")}</td><td>${escapeHtml(planLabel(billing.plan_code))}</td><td>${billing.unlimited_credits ? "Ilimitados" : Number(billing.credit_balance).toLocaleString("pt-BR")}</td><td><span class="admin-status status-${escapeHtml(billing.subscription_status)}">${escapeHtml(statusLabel(billing.subscription_status))}</span></td><td><button class="secondary-button admin-open" type="button" data-organization-id="${organization.id}">Abrir</button></td></tr>`;
  }).join("") : `<tr><td colspan="7"><div class="admin-empty"><strong>Nenhuma empresa encontrada.</strong><span>Tente outro nome ou e-mail.</span></div></td></tr>`;
  const start = data.total ? data.offset + 1 : 0;
  const end = Math.min(data.offset + data.organizations.length, data.total);
  document.querySelector("#admin-range").textContent = `${start}–${end} de ${data.total.toLocaleString("pt-BR")}`;
  document.querySelector("#admin-previous").disabled = data.offset === 0;
  document.querySelector("#admin-next").disabled = data.offset + data.organizations.length >= data.total;
}

async function loadOrganizations() {
  adminMessage.classList.add("hidden");
  adminRows.innerHTML = `<tr><td colspan="7">Carregando empresas…</td></tr>`;
  const query = document.querySelector("#admin-search").value.trim();
  try {
    const data = await adminFetch(`/api/admin/overview?query=${encodeURIComponent(query)}&limit=${pageSize}&offset=${adminOffset}`);
    renderMetrics(data);
    renderOrganizations(data);
  } catch (error) {
    adminRows.innerHTML = "";
    notify(error.message);
  }
}

function renderOrganizationDetail(data) {
  const profile = data.commercial.profile;
  document.querySelector("#admin-organization-name").textContent = data.name;
  document.querySelector("#admin-organization-meta").textContent = `${data.owner_email} · criada em ${formatDate(data.created_at)}`;
  document.querySelector("#admin-detail-metrics").innerHTML = `<article><strong>${data.member_count.toLocaleString("pt-BR")}</strong><span>Pessoas</span></article><article><strong>${data.commercial.unlocked_companies.toLocaleString("pt-BR")}</strong><span>CNPJs desbloqueados</span></article><article><strong>${profile.unlimited_credits ? "Ilimitados" : Number(profile.credit_balance).toLocaleString("pt-BR")}</strong><span>Créditos</span></article>`;
  document.querySelector("#admin-plan").value = profile.plan_code;
  document.querySelector("#admin-status").value = profile.subscription_status;
  document.querySelector("#admin-unlimited").checked = profile.unlimited_credits;
  document.querySelector("#admin-credit-form").classList.toggle("hidden", profile.unlimited_credits);
  document.querySelector("#admin-ledger").innerHTML = data.commercial.ledger.length ? data.commercial.ledger.map((entry) => {
    const delta = Number(entry.delta);
    const deltaClass = delta > 0 ? "credit-positive" : delta < 0 ? "credit-negative" : "";
    const deltaLabel = delta === 0 ? "—" : `${delta > 0 ? "+" : ""}${delta.toLocaleString("pt-BR")}`;
    return `<tr><td>${formatDate(entry.created_at)}</td><td>${escapeHtml(entry.description)}</td><td class="${deltaClass}">${deltaLabel}</td><td>${Number(entry.balance_after).toLocaleString("pt-BR")}</td></tr>`;
  }).join("") : `<tr><td colspan="4">Ainda não há movimentações.</td></tr>`;
}

async function openOrganization(organizationId) {
  selectedOrganizationId = Number(organizationId);
  document.querySelector("#admin-dialog-message").classList.add("hidden");
  adminDialog.showModal();
  document.querySelector("#admin-organization-name").textContent = "Carregando…";
  try {
    renderOrganizationDetail(await adminFetch(`/api/admin/organizations/${selectedOrganizationId}`));
  } catch (error) {
    notify(error.message, document.querySelector("#admin-dialog-message"));
  }
}

document.querySelector("#admin-search-form").addEventListener("submit", (event) => { event.preventDefault(); adminOffset = 0; loadOrganizations(); });
document.querySelector("#admin-previous").addEventListener("click", () => { adminOffset = Math.max(0, adminOffset - pageSize); loadOrganizations(); });
document.querySelector("#admin-next").addEventListener("click", () => { if (adminOffset + pageSize < adminTotal) adminOffset += pageSize; loadOrganizations(); });
adminRows.addEventListener("click", (event) => { const button = event.target.closest("[data-organization-id]"); if (button) openOrganization(button.dataset.organizationId); });
document.querySelector("#admin-dialog-close").addEventListener("click", () => adminDialog.close());
adminDialog.addEventListener("click", (event) => { if (event.target === adminDialog) adminDialog.close(); });

document.querySelector("#admin-billing-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#admin-dialog-message");
  message.classList.add("hidden");
  try {
    await adminFetch(`/api/admin/organizations/${selectedOrganizationId}/billing`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_code: document.querySelector("#admin-plan").value, subscription_status: document.querySelector("#admin-status").value, unlimited_credits: document.querySelector("#admin-unlimited").checked }),
    });
    renderOrganizationDetail(await adminFetch(`/api/admin/organizations/${selectedOrganizationId}`));
    notify("Plano atualizado.", message, true);
    loadOrganizations();
  } catch (error) { notify(error.message, message); }
});

document.querySelector("#admin-credit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#admin-dialog-message");
  message.classList.add("hidden");
  try {
    await adminFetch(`/api/admin/organizations/${selectedOrganizationId}/credits`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: Number(document.querySelector("#admin-credit-amount").value), description: document.querySelector("#admin-credit-description").value }),
    });
    event.target.reset();
    renderOrganizationDetail(await adminFetch(`/api/admin/organizations/${selectedOrganizationId}`));
    notify("Ajuste registrado no histórico.", message, true);
    loadOrganizations();
  } catch (error) { notify(error.message, message); }
});

loadOrganizations();
