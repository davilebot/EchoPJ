const adminOrganizationId = new URLSearchParams(location.search).get("organization");
const adminHeaders = adminOrganizationId ? { "X-Organization-Id": adminOrganizationId } : {};
const adminMessage = document.querySelector("#admin-message");
const adminRows = document.querySelector("#admin-organizations");
const adminDialog = document.querySelector("#admin-organization-dialog");
const adminCustomerDialog = document.querySelector("#admin-customer-dialog");
const adminCatalogDialog = document.querySelector("#admin-catalog-dialog");
const adminSupportDialog = document.querySelector("#admin-support-dialog");
const adminPrivacyDialog = document.querySelector("#admin-privacy-dialog");
const pageSize = 50;
let adminOffset = 0;
let adminTotal = 0;
let selectedOrganizationId = null;
let selectedSupportTicketId = null;
let selectedPrivacyRequestId = null;
let adminBillingCatalog = null;
let privacyRequests = [];
const supportStatusLabels = { open: "Aberto", in_progress: "Em atendimento", waiting_customer: "Aguardando cliente", resolved: "Resolvido", closed: "Encerrado" };
const supportPriorityLabels = { low: "Baixa", normal: "Normal", high: "Alta", urgent: "Urgente" };
const supportCategoryLabels = { question: "Dúvida", technical: "Técnico", billing: "Cobrança", suggestion: "Sugestão" };
const privacyStatusLabels = { requested: "Recebida", in_review: "Em análise", waiting_user: "Aguardando usuário", approved: "Aprovada", canceled: "Cancelada", closed: "Encerrada" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function formatMoney(cents) {
  return (Number(cents || 0) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function planLabel(value) {
  return { internal: "Interno", trial: "Teste" }[value] || value;
}

function statusLabel(value) {
  return { trialing: "Em teste", active: "Ativo", past_due: "Pagamento pendente", inactive: "Inativo", pending: "Aguardando pagamento", canceled: "Cancelado", suspended: "Suspenso" }[value] || value;
}

function stageLabel(value) {
  return { internal: "EchoHub", registered: "Cadastro", activated: "Ativada", value: "Gerou valor", converted: "Cliente" }[value] || "Cadastro";
}

function notify(message, target = adminMessage, success = false) {
  target.textContent = message;
  target.dataset.type = success ? "success" : "";
  target.classList.remove("hidden");
}

async function adminFetch(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...adminHeaders, ...(options.headers || {}) } });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (response.status === 401) {
    location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    throw new Error("Sua sessão expirou.");
  }
  if (!response.ok) throw new Error(typeof payload?.detail === "string" ? payload.detail : "A plataforma recebeu uma resposta inesperada. Tente novamente em instantes.");
  if (!payload) throw new Error("A plataforma recebeu uma resposta inesperada. Atualize a página e tente novamente.");
  return payload;
}

function renderMetrics(data) {
  const values = [data.total, data.user_count, data.pending_signups, data.metrics.credits_available, data.metrics.unlocked_companies];
  document.querySelectorAll("#admin-metrics strong").forEach((element, index) => {
    element.textContent = Number(values[index] || 0).toLocaleString("pt-BR");
  });
}

function renderFunnel(funnel) {
  const stages = {
    registered: [funnel.registered_organizations, 100],
    activated: [funnel.activated_organizations, funnel.activation_rate],
    converted: [funnel.converted_organizations, funnel.conversion_rate],
    retained: [funnel.retained_organizations, funnel.retention_rate],
  };
  Object.entries(stages).forEach(([key, values]) => {
    const card = document.querySelector(`[data-funnel="${key}"]`);
    card.querySelector("strong").textContent = Number(values[0] || 0).toLocaleString("pt-BR");
    card.querySelector("small").textContent = `${Number(values[1] || 0).toLocaleString("pt-BR")}%`;
    card.querySelector(".admin-funnel-bar i").style.width = `${Math.max(0, Math.min(100, Number(values[1] || 0)))}%`;
  });
  document.querySelector("#admin-active-30").textContent = Number(funnel.active_last_30_days || 0).toLocaleString("pt-BR");
  document.querySelector("#admin-new-30").textContent = Number(funnel.registrations_last_30_days || 0).toLocaleString("pt-BR");
}

function renderBillingEvents(events) {
  const target = document.querySelector("#admin-billing-events");
  target.innerHTML = events.length ? events.map((event) => `<tr><td>${formatDate(event.received_at)}</td><td><strong>${escapeHtml(event.event_type)}</strong><small>${escapeHtml(event.provider)}</small></td><td>${escapeHtml(event.object_id || "—")}</td><td><span class="admin-status status-${escapeHtml(event.processing_status)}">${escapeHtml(event.processing_status)}</span></td><td>${escapeHtml(event.detail || "Processado sem ressalvas")}</td></tr>`).join("") : `<tr><td colspan="5"><div class="admin-empty"><strong>Nenhum evento recebido.</strong><span>Os webhooks aparecerão aqui quando a cobrança for ativada.</span></div></td></tr>`;
}

function renderBillingEmails(data) {
  const deliveries = data?.deliveries || [];
  const counts = data?.counts || {};
  const lastRun = data?.last_run_at ? ` · último ciclo ${formatDate(data.last_run_at)}` : "";
  document.querySelector("#admin-billing-email-summary").textContent = data?.enabled
    ? `${Number(counts.sent || 0).toLocaleString("pt-BR")} enviados · ${Number(counts.failed || 0).toLocaleString("pt-BR")} com falha${lastRun}`
    : "Envio automático aguardando ativação e SMTP.";
  const kindLabels = { renewal: "Renovação próxima", due: "Vence hoje", past_due: "Pagamento pendente" };
  const statusLabels = { sending: "Enviando", sent: "Enviado", failed: "Falhou" };
  const target = document.querySelector("#admin-billing-emails");
  target.innerHTML = deliveries.length ? deliveries.map((delivery) => `<tr><td>${formatDate(delivery.last_attempt_at)}</td><td>#${Number(delivery.organization_id).toLocaleString("pt-BR")}<small>${escapeHtml(delivery.plan_name)}</small></td><td>${escapeHtml(delivery.recipient)}</td><td>${escapeHtml(kindLabels[delivery.kind] || delivery.kind)}${delivery.due_date ? `<small>${escapeHtml(delivery.due_date)}</small>` : ""}</td><td>${Number(delivery.attempts).toLocaleString("pt-BR")}</td><td><span class="admin-status status-${escapeHtml(delivery.status)}">${escapeHtml(statusLabels[delivery.status] || delivery.status)}</span></td></tr>`).join("") : `<tr><td colspan="6"><div class="admin-empty"><strong>Nenhum aviso financeiro processado.</strong><span>Renovações e pendências aparecerão aqui após a ativação.</span></div></td></tr>`;
}

function renderOperations(data) {
  const ready = data.readiness.ready;
  const backup = data.backup || {};
  const windowData = data.traffic.window || {};
  const backupLabel = backup.status === "ok" ? "Em dia" : backup.status === "stale" ? "Atrasado" : backup.status === "failed" ? "Falhou" : "Sem leitura";
  const cards = [
    [ready ? "Operacional" : "Atenção", "Disponibilidade", ready ? "ok" : "warning"],
    [backupLabel, "Backup", backup.status === "ok" ? "ok" : "warning"],
    [Number(windowData.requests || 0).toLocaleString("pt-BR"), "Requisições · 5 min", ""],
    [`${Number(windowData.error_rate_percent || 0).toLocaleString("pt-BR")}%`, "Erros · 5 min", Number(windowData.server_errors || 0) ? "warning" : "ok"],
    [`${Number(windowData.latency_ms?.p95 || 0).toLocaleString("pt-BR")} ms`, "Latência p95", ""],
    [data.billing.enabled ? "Ativo" : "Preparação", "Checkout", data.billing.enabled ? "ok" : ""],
  ];
  document.querySelector("#admin-operations-grid").innerHTML = cards.map(([value, label, status]) => `<article data-state="${status}"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></article>`).join("");
  const backupTime = backup.checked_at ? ` · backup ${formatDate(backup.checked_at)}` : "";
  document.querySelector("#admin-operations-updated").textContent = `Atualizado ${new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}${backupTime}`;
  renderLaunchReadiness(data.launch);
}

function renderLaunchReadiness(launch) {
  if (!launch) return;
  const percent = launch.total_count ? Math.round(launch.ready_count / launch.total_count * 100) : 0;
  document.querySelector("#admin-launch-score").textContent = `${launch.ready_count}/${launch.total_count}`;
  document.querySelector("#admin-launch-status").textContent = launch.ready
    ? "Pronto para lançamento"
    : `${launch.blocker_count} ${launch.blocker_count === 1 ? "item pendente" : "itens pendentes"}`;
  document.querySelector("#admin-launch-progress").style.width = `${percent}%`;
  document.querySelector("#admin-launch-checklist").innerHTML = launch.checks.map((item) => {
    const action = typeof item.action_url === "string" && item.action_url.startsWith("/")
      ? `<a href="${escapeHtml(item.action_url)}" target="_blank" rel="noopener">Revisar</a>`
      : "";
    return `<article data-state="${item.ready ? "ready" : "pending"}"><span class="admin-launch-icon" aria-hidden="true">${item.ready ? "✓" : "!"}</span><div><small>${escapeHtml(item.category)}</small><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(item.detail)}</p></div>${action}</article>`;
  }).join("");
}

function renderBillingCatalogSummary(data) {
  adminBillingCatalog = data;
  const activeOffers = data.active?.offers || [];
  const revision = data.active?.revision ? `Versão ${data.active.revision}` : data.source === "deployment" ? "Configuração do servidor" : "Nenhuma versão publicada";
  const draft = data.draft ? `Rascunho ${data.draft.revision} pronto para revisão` : "Nenhum rascunho pendente";
  document.querySelector("#admin-catalog-summary").innerHTML = [
    [activeOffers.length.toLocaleString("pt-BR"), activeOffers.length === 1 ? "oferta publicada" : "ofertas publicadas", revision],
    [data.draft ? "Pendente" : "Em dia", "estado editorial", draft],
    [data.checkout_enabled ? "Ativo" : "Bloqueado", "checkout para clientes", data.checkout_enabled ? "Provedor e webhook prontos" : "Aguardando configuração segura"],
  ].map(([value, label, detail]) => `<article><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span><small>${escapeHtml(detail)}</small></article>`).join("");
}

async function loadBillingCatalog() {
  try { renderBillingCatalogSummary(await adminFetch("/api/admin/billing/catalog")); }
  catch (error) { notify(error.message); }
}

function emptyCatalogOffer() {
  return { code: "", name: "", kind: "subscription", price_cents: 0, credits: 0, description: "", features: [], cycle: "MONTHLY", highlighted: false };
}

function catalogOfferCard(offer, index) {
  const price = offer.price_cents ? (Number(offer.price_cents) / 100).toFixed(2) : "";
  const selected = (value, expected) => value === expected ? " selected" : "";
  const cycleDisabled = offer.kind === "credit_pack";
  return `<article class="admin-catalog-offer" data-offer-index="${index}">
    <div class="admin-catalog-offer-header"><strong>Oferta ${index + 1}</strong><button class="admin-remove-offer" type="button" data-remove-offer>Remover</button></div>
    <div class="admin-offer-grid">
      <label class="wide">Nome<input data-offer-field="name" required maxlength="80" value="${escapeHtml(offer.name)}" placeholder="Ex.: Crescimento"></label>
      <label>Tipo<select data-offer-field="kind"><option value="subscription"${selected(offer.kind, "subscription")}>Assinatura</option><option value="credit_pack"${selected(offer.kind, "credit_pack")}>Pacote avulso</option></select></label>
      <label>Código interno<input data-offer-field="code" required maxlength="50" pattern="[a-z0-9][a-z0-9_-]*" value="${escapeHtml(offer.code)}" placeholder="crescimento"></label>
      <label>Preço em reais<input data-offer-field="price" required type="number" min="0.01" max="1000000" step="0.01" value="${escapeHtml(price)}" placeholder="149,90"></label>
      <label>Créditos<input data-offer-field="credits" required type="number" min="1" max="100000000" step="1" value="${offer.credits || ""}" placeholder="1000"></label>
      <label>Ciclo<select data-offer-field="cycle" ${cycleDisabled ? "disabled" : ""}><option value="MONTHLY"${selected(offer.cycle, "MONTHLY")}>Mensal</option><option value="QUARTERLY"${selected(offer.cycle, "QUARTERLY")}>Trimestral</option><option value="SEMIANNUALLY"${selected(offer.cycle, "SEMIANNUALLY")}>Semestral</option><option value="YEARLY"${selected(offer.cycle, "YEARLY")}>Anual</option></select></label>
      <label class="full">Descrição<input data-offer-field="description" required maxlength="240" value="${escapeHtml(offer.description)}" placeholder="Para equipes que prospectam de forma recorrente."></label>
      <label class="full">Benefícios <span class="field-hint">Um benefício por linha, até 12.</span><textarea data-offer-field="features" required maxlength="1932" placeholder="Créditos compartilhados pela equipe&#10;Buscas e listas ilimitadas">${escapeHtml((offer.features || []).join("\n"))}</textarea></label>
      <label class="checkbox-label full"><input data-offer-field="highlighted" type="checkbox" ${offer.highlighted ? "checked" : ""}>Destacar como oferta recomendada</label>
    </div>
  </article>`;
}

function renderCatalogEditor(offers) {
  const values = offers.length ? offers : [emptyCatalogOffer()];
  document.querySelector("#admin-catalog-offers").innerHTML = values.map(catalogOfferCard).join("");
  document.querySelector("#admin-publish-catalog").disabled = !adminBillingCatalog?.draft;
}

function openBillingCatalog() {
  const offers = adminBillingCatalog?.draft?.offers || adminBillingCatalog?.active?.offers || [];
  document.querySelector("#admin-catalog-message").classList.add("hidden");
  renderCatalogEditor(offers);
  adminCatalogDialog.showModal();
}

function readCatalogOffers() {
  return Array.from(document.querySelectorAll(".admin-catalog-offer")).map((card) => {
    const value = (field) => card.querySelector(`[data-offer-field="${field}"]`);
    const kind = value("kind").value;
    return {
      code: value("code").value.trim().toLowerCase(), name: value("name").value.trim(), kind,
      price_cents: Math.round(Number(value("price").value.replace(",", ".")) * 100),
      credits: Number(value("credits").value), description: value("description").value.trim(),
      features: value("features").value.split("\n").map((item) => item.trim()).filter(Boolean),
      cycle: kind === "subscription" ? value("cycle").value : null,
      highlighted: value("highlighted").checked,
    };
  });
}

async function loadOperations() {
  const button = document.querySelector("#admin-refresh-operations");
  button.disabled = true;
  try { renderOperations(await adminFetch("/api/admin/operations")); }
  catch (error) { notify(error.message); }
  finally { button.disabled = false; }
}

function renderPrivacyRequests(requests) {
  privacyRequests = requests;
  const target = document.querySelector("#admin-privacy-requests");
  target.innerHTML = requests.length ? requests.map((request) => `<tr>
    <td><strong>${formatDate(request.requested_at)}</strong><small>Atualizada ${formatDate(request.updated_at)}</small></td>
    <td><strong>${escapeHtml(request.identifier)}</strong><small>#${escapeHtml(request.id.slice(0, 8).toUpperCase())}</small></td>
    <td>${escapeHtml(request.organizations || "Sem workspace")}</td>
    <td>${escapeHtml(request.reason || "Não informado")}</td>
    <td><span class="admin-status status-${escapeHtml(request.status)}">${escapeHtml(privacyStatusLabels[request.status] || request.status)}</span></td>
    <td><button class="secondary-button admin-open" type="button" data-privacy-request-id="${escapeHtml(request.id)}">Revisar</button></td>
  </tr>`).join("") : `<tr><td colspan="6"><div class="admin-empty"><strong>Nenhuma solicitação neste filtro.</strong><span>Novos pedidos aparecerão aqui.</span></div></td></tr>`;
}

async function loadPrivacyRequests() {
  const status = document.querySelector("#admin-privacy-status").value;
  try {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    renderPrivacyRequests((await adminFetch(`/api/admin/privacy/requests${query}`)).requests || []);
  } catch (error) { notify(error.message); }
}

function openPrivacyRequest(requestId) {
  const request = privacyRequests.find((item) => item.id === requestId);
  if (!request) return;
  selectedPrivacyRequestId = request.id;
  document.querySelector("#admin-privacy-message").classList.add("hidden");
  document.querySelector("#admin-privacy-dialog-title").textContent = request.identifier;
  document.querySelector("#admin-privacy-dialog-meta").textContent = `${request.organizations || "Sem workspace"} · solicitada em ${formatDate(request.requested_at)}`;
  document.querySelector("#admin-privacy-reason").innerHTML = `<strong>Motivo informado</strong><p>${escapeHtml(request.reason || "O titular não informou um motivo.")}</p>`;
  document.querySelector("#admin-privacy-edit-status").value = request.status;
  document.querySelector("#admin-privacy-note").value = request.resolution_note || "";
  adminPrivacyDialog.showModal();
}

function renderSupportTickets(data) {
  const counts = data.counts || {};
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  document.querySelector("#admin-support-counts").innerHTML = [
    [total, "Total"], [counts.open || 0, "Abertos"], [counts.in_progress || 0, "Em atendimento"], [counts.waiting_customer || 0, "Aguardando cliente"],
  ].map(([value, label]) => `<span><strong>${Number(value).toLocaleString("pt-BR")}</strong>${escapeHtml(label)}</span>`).join("");
  const target = document.querySelector("#admin-support-tickets");
  target.innerHTML = data.tickets.length ? data.tickets.map((ticket) => `<tr>
    <td><strong>${formatDate(ticket.updated_at)}</strong><small>#${escapeHtml(ticket.id.slice(0, 8).toUpperCase())}</small></td>
    <td><strong>${escapeHtml(ticket.organization_name || `Organização #${ticket.organization_id}`)}</strong><small>${escapeHtml(ticket.requester_identifier)}</small></td>
    <td><strong>${escapeHtml(ticket.subject)}</strong><small>${escapeHtml(ticket.last_message_preview || "Sem mensagem")}</small></td>
    <td>${escapeHtml(supportCategoryLabels[ticket.category] || ticket.category)}</td>
    <td><span class="admin-priority priority-${escapeHtml(ticket.priority)}">${escapeHtml(supportPriorityLabels[ticket.priority] || ticket.priority)}</span></td>
    <td><span class="admin-status status-${escapeHtml(ticket.status)}">${escapeHtml(supportStatusLabels[ticket.status] || ticket.status)}</span></td>
    <td><button class="secondary-button admin-open" type="button" data-support-ticket-id="${escapeHtml(ticket.id)}">Abrir</button></td>
  </tr>`).join("") : `<tr><td colspan="7"><div class="admin-empty"><strong>Nenhum chamado neste filtro.</strong><span>A fila está em dia.</span></div></td></tr>`;
}

async function loadSupportTickets() {
  const target = document.querySelector("#admin-support-tickets");
  const status = document.querySelector("#admin-support-status").value;
  const query = document.querySelector("#admin-support-query").value.trim();
  target.innerHTML = `<tr><td colspan="7">Carregando chamados…</td></tr>`;
  try { renderSupportTickets(await adminFetch(`/api/admin/support/tickets?status=${encodeURIComponent(status)}&query=${encodeURIComponent(query)}&limit=100`)); }
  catch (error) { target.innerHTML = ""; notify(error.message); }
}

function renderSupportTicketDetail(ticket) {
  document.querySelector("#admin-support-reference").textContent = `CHAMADO #${ticket.id.slice(0, 8).toUpperCase()}`;
  document.querySelector("#admin-support-dialog-title").textContent = ticket.subject;
  document.querySelector("#admin-support-dialog-meta").textContent = `${ticket.requester_identifier} · organização #${ticket.organization_id} · aberto em ${formatDate(ticket.created_at)}`;
  document.querySelector("#admin-support-edit-status").value = ticket.status;
  document.querySelector("#admin-support-edit-priority").value = ticket.priority;
  const diagnosticLabels = { request_id: "Requisição", dataset_version: "Base", plan_code: "Plano", subscription_status: "Assinatura", organization_role: "Perfil" };
  document.querySelector("#admin-support-diagnostic").innerHTML = Object.entries(ticket.diagnostic || {}).map(([key, value]) => `<span><small>${escapeHtml(diagnosticLabels[key] || key)}</small><strong>${escapeHtml(value || "—")}</strong></span>`).join("");
  document.querySelector("#admin-support-thread").innerHTML = ticket.messages.map((message) => `<article class="admin-support-message ${escapeHtml(message.author_kind)}"><div><strong>${message.author_kind === "support" ? "Equipe EchoHub" : "Cliente"}</strong><time>${formatDate(message.created_at)}</time></div><p>${escapeHtml(message.body).replace(/\n/g, "<br>")}</p></article>`).join("");
  document.querySelector("#admin-support-thread").scrollTop = document.querySelector("#admin-support-thread").scrollHeight;
}

async function openSupportTicket(ticketId) {
  selectedSupportTicketId = ticketId;
  document.querySelector("#admin-support-message").classList.add("hidden");
  document.querySelector("#admin-support-dialog-title").textContent = "Carregando…";
  document.querySelector("#admin-support-thread").innerHTML = "";
  adminSupportDialog.showModal();
  try { renderSupportTicketDetail(await adminFetch(`/api/admin/support/tickets/${encodeURIComponent(ticketId)}`)); }
  catch (error) { notify(error.message, document.querySelector("#admin-support-message")); }
}

function activitySummary(activity) {
  const parts = [];
  if (activity.search_count) parts.push(`${activity.search_count} busca${activity.search_count === 1 ? "" : "s"}`);
  if (activity.saved_search_count) parts.push(`${activity.saved_search_count} salva${activity.saved_search_count === 1 ? "" : "s"}`);
  if (activity.list_count) parts.push(`${activity.list_count} lista${activity.list_count === 1 ? "" : "s"}`);
  if (activity.job_count) parts.push(`${activity.job_count} processamento${activity.job_count === 1 ? "" : "s"}`);
  return parts.slice(0, 2).join(" · ") || "Ainda sem uso";
}

function renderOrganizations(data) {
  adminTotal = data.total;
  adminRows.innerHTML = data.organizations.length ? data.organizations.map((organization) => {
    const billing = organization.billing;
    const activity = organization.activity || {};
    const provisioning = organization.provisioning || { status: "active", label: "Operação ativa" };
    const stage = billing.is_internal ? "internal" : activity.stage;
    const activityDate = activity.last_activity_at ? formatDate(activity.last_activity_at) : "Sem atividade";
    const provisioningStatus = billing.is_internal ? "internal" : provisioning.status;
    const provisioningLabel = billing.is_internal ? "Equipe EchoHub" : provisioning.label;
    return `<tr><td><strong>${escapeHtml(organization.name)}</strong><small>#${organization.id} · ${formatDate(organization.created_at)}</small></td><td>${escapeHtml(organization.owner_email)}</td><td>${organization.member_count.toLocaleString("pt-BR")}</td><td><span class="admin-stage stage-${escapeHtml(stage)}">${escapeHtml(stageLabel(stage))}</span></td><td><strong>${activityDate}</strong><small>${escapeHtml(activitySummary(activity))}</small></td><td><span class="admin-stage pilot-${escapeHtml(provisioningStatus)}">${escapeHtml(provisioningLabel)}</span></td><td>${escapeHtml(planLabel(billing.plan_code))}</td><td>${billing.unlimited_credits ? "Ilimitados" : Number(billing.credit_balance).toLocaleString("pt-BR")}</td><td><span class="admin-status status-${escapeHtml(billing.subscription_status)}">${escapeHtml(statusLabel(billing.subscription_status))}</span></td><td><button class="secondary-button admin-open" type="button" data-organization-id="${organization.id}">Abrir</button></td></tr>`;
  }).join("") : `<tr><td colspan="10"><div class="admin-empty"><strong>Nenhuma empresa encontrada.</strong><span>Tente outro nome ou e-mail.</span></div></td></tr>`;
  const start = data.total ? data.offset + 1 : 0;
  const end = Math.min(data.offset + data.organizations.length, data.total);
  document.querySelector("#admin-range").textContent = `${start}–${end} de ${data.total.toLocaleString("pt-BR")}`;
  document.querySelector("#admin-previous").disabled = data.offset === 0;
  document.querySelector("#admin-next").disabled = data.offset + data.organizations.length >= data.total;
}

async function loadOrganizations() {
  adminMessage.classList.add("hidden");
  adminRows.innerHTML = `<tr><td colspan="10">Carregando empresas…</td></tr>`;
  const query = document.querySelector("#admin-search").value.trim();
  try {
    const data = await adminFetch(`/api/admin/overview?query=${encodeURIComponent(query)}&limit=${pageSize}&offset=${adminOffset}`);
    renderMetrics(data);
    renderFunnel(data.funnel);
    renderBillingEvents(data.billing_events || []);
    renderBillingEmails(data.billing_emails || {});
    renderOrganizations(data);
  } catch (error) {
    adminRows.innerHTML = "";
    notify(error.message);
  }
}

function renderOrganizationDetail(data) {
  const profile = data.commercial.profile;
  const activity = data.activity || {};
  document.querySelector("#admin-organization-name").textContent = data.name;
  const lastActivity = activity.last_activity_at ? ` · última atividade ${formatDate(activity.last_activity_at)}` : " · ainda sem atividade";
  document.querySelector("#admin-organization-meta").textContent = `${data.owner_email} · criada em ${formatDate(data.created_at)}${lastActivity}`;
  document.querySelector("#admin-detail-metrics").innerHTML = `<article><strong>${data.member_count.toLocaleString("pt-BR")}</strong><span>Pessoas</span></article><article><strong>${Number(activity.saved_search_count || 0).toLocaleString("pt-BR")}</strong><span>Buscas salvas</span></article><article><strong>${Number(activity.list_count || 0).toLocaleString("pt-BR")}</strong><span>Listas</span></article><article><strong>${Number(activity.job_count || 0).toLocaleString("pt-BR")}</strong><span>Processamentos</span></article><article><strong>${data.commercial.unlocked_companies.toLocaleString("pt-BR")}</strong><span>CNPJs desbloqueados</span></article><article><strong>${profile.unlimited_credits ? "Ilimitados" : Number(profile.credit_balance).toLocaleString("pt-BR")}</strong><span>Créditos</span></article>`;
  document.querySelector("#admin-plan").value = profile.plan_code;
  document.querySelector("#admin-status").value = profile.subscription_status;
  document.querySelector("#admin-unlimited").checked = profile.unlimited_credits;
  document.querySelector("#admin-credit-form").classList.toggle("hidden", profile.unlimited_credits);
  const provisioning = data.provisioning || { kind: "standard" };
  const provisioningCard = document.querySelector("#admin-provisioning");
  provisioningCard.classList.toggle("hidden", provisioning.kind !== "pilot");
  if (provisioning.kind === "pilot") {
    document.querySelector("#admin-pilot-invitation-result").classList.add("hidden");
    document.querySelector("#admin-provisioning-status").className = `admin-stage pilot-${provisioning.status}`;
    document.querySelector("#admin-provisioning-status").textContent = provisioning.label;
    document.querySelector("#admin-provisioning-detail").textContent = provisioning.detail;
    document.querySelector("#admin-provisioning-email").textContent = provisioning.responsible_email;
    document.querySelector("#admin-provisioning-owner").textContent = data.owner_email;
    const invitation = provisioning.invitation;
    const invitationLabels = { pending: "Aguardando até", accepted: "Aceito em", expired: "Expirou em", revoked: "Cancelado em" };
    document.querySelector("#admin-provisioning-invite").textContent = invitation
      ? `${invitationLabels[invitation.status] || "Atualizado em"} ${formatDate(invitation.accepted_at || invitation.revoked_at || invitation.expires_at)}`
      : "Nenhum convite ativo";
    const transfer = document.querySelector("#admin-transfer-responsibility");
    transfer.classList.toggle("hidden", provisioning.status !== "transfer_pending" || !provisioning.responsible_user_id);
    transfer.dataset.userId = provisioning.responsible_user_id || "";
    const renew = document.querySelector("#admin-renew-pilot-invitation");
    const canRenew = ["waiting_acceptance", "invitation_action_needed", "action_needed"].includes(provisioning.status);
    renew.classList.toggle("hidden", !canRenew);
    renew.dataset.status = provisioning.status;
    renew.textContent = provisioning.status === "waiting_acceptance" ? "Gerar novo link" : "Renovar convite";
    document.querySelector("#admin-open-team").href = `/organizations?organization=${encodeURIComponent(data.id)}`;
  }
  const subscription = data.commercial.subscription;
  const cancellation = data.commercial.cancellation;
  document.querySelector("#admin-subscription-summary").innerHTML = subscription ? `<div><span class="admin-status status-${escapeHtml(subscription.status)}">${escapeHtml(statusLabel(subscription.status))}</span><strong>${escapeHtml(subscription.plan_name)}</strong><small>${Number(subscription.credits_per_cycle).toLocaleString("pt-BR")} créditos · ${escapeHtml(subscription.cycle)}${subscription.next_due_date ? ` · próxima cobrança ${formatDate(subscription.next_due_date)}` : ""}</small></div>${cancellation ? `<div><small>Último cancelamento</small><strong>${escapeHtml(cancellation.status)}</strong><span>${escapeHtml(cancellation.reason || cancellation.detail || "Sem motivo informado")}</span></div>` : ""}` : `<span class="muted">Nenhuma assinatura recorrente conciliada.</span>`;
  const paymentLabels = { pending: "Aguardando", confirmed: "Confirmado", received: "Recebido", overdue: "Em atraso", failed: "Falhou", canceled: "Cancelado", refund_pending: "Estorno pendente", refunded: "Estornado", chargeback: "Contestado", review: "Revisar" };
  document.querySelector("#admin-payments").innerHTML = data.commercial.payments.length ? data.commercial.payments.map((payment) => `<tr><td>${formatDate(payment.updated_at)}</td><td>${escapeHtml(payment.plan_name)}</td><td>${payment.amount_cents === null ? "—" : (Number(payment.amount_cents) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })}</td><td>${Number(payment.credits_granted).toLocaleString("pt-BR")}</td><td><span class="admin-status status-${escapeHtml(payment.status)}">${escapeHtml(paymentLabels[payment.status] || payment.status)}</span></td></tr>`).join("") : `<tr><td colspan="5">Ainda não há ciclos financeiros.</td></tr>`;
  const orderLabels = { creating: "Criando", pending: "Aguardando", checkout_paid: "Confirmando", paid: "Pago", failed: "Falhou", expired: "Expirado", canceled: "Cancelado", past_due: "Pendente", needs_review: "Revisar" };
  document.querySelector("#admin-orders").innerHTML = data.commercial.orders.length ? data.commercial.orders.map((order) => `<tr><td>${formatDate(order.created_at)}</td><td><strong>${escapeHtml(order.plan_name)}</strong><small>${escapeHtml(order.kind)}</small></td><td>${(Number(order.price_cents) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })}</td><td>${Number(order.credits).toLocaleString("pt-BR")}</td><td><span class="admin-status status-${escapeHtml(order.status)}">${escapeHtml(orderLabels[order.status] || order.status)}</span></td></tr>`).join("") : `<tr><td colspan="5">Ainda não há pedidos.</td></tr>`;
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

function openCustomerDialog() {
  const form = document.querySelector("#admin-customer-form");
  form.reset();
  form.classList.remove("hidden");
  document.querySelector("#admin-customer-message").classList.add("hidden");
  document.querySelector("#admin-customer-result").classList.add("hidden");
  adminCustomerDialog.showModal();
  document.querySelector("#admin-customer-name").focus();
}

async function copyInputValue(input, button, successLabel, defaultLabel) {
  try {
    await navigator.clipboard.writeText(input.value);
  } catch (_) {
    input.focus();
    input.select();
    document.execCommand("copy");
  }
  button.textContent = successLabel;
  window.setTimeout(() => { button.textContent = defaultLabel; }, 1800);
}

async function copyCustomerInvitation() {
  return copyInputValue(
    document.querySelector("#admin-customer-link"),
    document.querySelector("#admin-customer-copy"),
    "Convite copiado",
    "Copiar convite",
  );
}

document.querySelector("#admin-create-customer").addEventListener("click", openCustomerDialog);
document.querySelector("#admin-customer-close").addEventListener("click", () => adminCustomerDialog.close());
adminCustomerDialog.addEventListener("click", (event) => { if (event.target === adminCustomerDialog) adminCustomerDialog.close(); });
document.querySelector("#admin-customer-copy").addEventListener("click", copyCustomerInvitation);
document.querySelector("#admin-customer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#admin-customer-message");
  const button = event.target.querySelector('button[type="submit"]');
  const credits = document.querySelector("#admin-customer-credits").value.trim();
  const payload = {
    name: document.querySelector("#admin-customer-name").value,
    owner_email: document.querySelector("#admin-customer-email").value,
    send_email: document.querySelector("#admin-customer-send-email").checked,
  };
  if (credits !== "") payload.trial_credits = Number(credits);
  message.classList.add("hidden");
  button.disabled = true;
  try {
    const data = await adminFetch("/api/admin/customer-workspaces", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const balance = Number(data.organization.billing.credit_balance || 0).toLocaleString("pt-BR");
    const delivery = { sent: "Convite enviado por e-mail.", manual: "Envio manual: copie e compartilhe o link.", failed: "O e-mail falhou; use o link abaixo." }[data.invitation.delivery] || "Convite gerado.";
    document.querySelector("#admin-customer-result-title").textContent = data.organization.name;
    document.querySelector("#admin-customer-result-meta").textContent = `${balance} créditos liberados · ${delivery}`;
    document.querySelector("#admin-customer-link").value = data.invitation.link;
    document.querySelector("#admin-customer-manage").href = `/organizations?organization=${encodeURIComponent(data.organization.id)}`;
    document.querySelector("#admin-customer-next-step").textContent = data.handoff.next_step;
    document.querySelector("#admin-customer-copy").textContent = "Copiar convite";
    event.target.classList.add("hidden");
    document.querySelector("#admin-customer-result").classList.remove("hidden");
    adminOffset = 0;
    await loadOrganizations();
  } catch (error) { notify(error.message, message); }
  finally { button.disabled = false; }
});

document.querySelector("#admin-manage-catalog").addEventListener("click", openBillingCatalog);
document.querySelector("#admin-catalog-close").addEventListener("click", () => adminCatalogDialog.close());
adminCatalogDialog.addEventListener("click", (event) => { if (event.target === adminCatalogDialog) adminCatalogDialog.close(); });
document.querySelector("#admin-add-offer").addEventListener("click", () => {
  const offers = readCatalogOffers();
  if (offers.length >= 12) {
    notify("O catálogo aceita até 12 ofertas.", document.querySelector("#admin-catalog-message"));
    return;
  }
  renderCatalogEditor([...offers, emptyCatalogOffer()]);
});
document.querySelector("#admin-catalog-offers").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-offer]");
  if (!button) return;
  const offers = readCatalogOffers();
  if (offers.length === 1) {
    notify("Mantenha pelo menos uma oferta no catálogo.", document.querySelector("#admin-catalog-message"));
    return;
  }
  offers.splice(Number(button.closest("[data-offer-index]").dataset.offerIndex), 1);
  renderCatalogEditor(offers);
});
document.querySelector("#admin-catalog-offers").addEventListener("change", (event) => {
  if (event.target.matches('[data-offer-field="kind"]')) {
    const cycle = event.target.closest(".admin-catalog-offer").querySelector('[data-offer-field="cycle"]');
    cycle.disabled = event.target.value === "credit_pack";
  }
  if (event.target.matches('[data-offer-field="highlighted"]') && event.target.checked) {
    document.querySelectorAll('[data-offer-field="highlighted"]').forEach((checkbox) => {
      if (checkbox !== event.target) checkbox.checked = false;
    });
  }
});
document.querySelector("#admin-catalog-offers").addEventListener("focusout", (event) => {
  if (!event.target.matches('[data-offer-field="name"]')) return;
  const code = event.target.closest(".admin-catalog-offer").querySelector('[data-offer-field="code"]');
  if (code.value.trim()) return;
  code.value = event.target.value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 50);
});
document.querySelector("#admin-catalog-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#admin-catalog-message");
  const button = document.querySelector("#admin-save-catalog");
  message.classList.add("hidden");
  button.disabled = true;
  try {
    const data = await adminFetch("/api/admin/billing/catalog/draft", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ offers: readCatalogOffers() }),
    });
    renderBillingCatalogSummary(data);
    renderCatalogEditor(data.draft.offers);
    notify("Rascunho salvo. Os clientes ainda veem a versão publicada.", message, true);
  } catch (error) { notify(error.message, message); }
  finally { button.disabled = false; }
});
document.querySelector("#admin-publish-catalog").addEventListener("click", async (event) => {
  if (!window.confirm("Publicar este catálogo na página de planos para todos os clientes?")) return;
  const message = document.querySelector("#admin-catalog-message");
  message.classList.add("hidden");
  event.currentTarget.disabled = true;
  try {
    const data = await adminFetch("/api/admin/billing/catalog/publish", { method: "POST" });
    renderBillingCatalogSummary(data);
    renderCatalogEditor(data.active.offers);
    notify("Catálogo publicado na página de planos.", message, true);
    await loadOperations();
  } catch (error) { notify(error.message, message); }
  finally { event.currentTarget.disabled = !adminBillingCatalog?.draft; }
});

document.querySelector("#admin-search-form").addEventListener("submit", (event) => { event.preventDefault(); adminOffset = 0; loadOrganizations(); });
document.querySelector("#admin-previous").addEventListener("click", () => { adminOffset = Math.max(0, adminOffset - pageSize); loadOrganizations(); });
document.querySelector("#admin-next").addEventListener("click", () => { if (adminOffset + pageSize < adminTotal) adminOffset += pageSize; loadOrganizations(); });
adminRows.addEventListener("click", (event) => { const button = event.target.closest("[data-organization-id]"); if (button) openOrganization(button.dataset.organizationId); });
document.querySelector("#admin-dialog-close").addEventListener("click", () => adminDialog.close());
adminDialog.addEventListener("click", (event) => { if (event.target === adminDialog) adminDialog.close(); });
document.querySelector("#admin-support-filter").addEventListener("submit", (event) => { event.preventDefault(); loadSupportTickets(); });
document.querySelector("#admin-support-tickets").addEventListener("click", (event) => { const button = event.target.closest("[data-support-ticket-id]"); if (button) openSupportTicket(button.dataset.supportTicketId); });
document.querySelector("#admin-support-close").addEventListener("click", () => adminSupportDialog.close());
adminSupportDialog.addEventListener("click", (event) => { if (event.target === adminSupportDialog) adminSupportDialog.close(); });
document.querySelector("#admin-privacy-filter").addEventListener("submit", (event) => { event.preventDefault(); loadPrivacyRequests(); });
document.querySelector("#admin-privacy-requests").addEventListener("click", (event) => { const button = event.target.closest("[data-privacy-request-id]"); if (button) openPrivacyRequest(button.dataset.privacyRequestId); });
document.querySelector("#admin-privacy-close").addEventListener("click", () => adminPrivacyDialog.close());
adminPrivacyDialog.addEventListener("click", (event) => { if (event.target === adminPrivacyDialog) adminPrivacyDialog.close(); });

document.querySelector("#admin-copy-pilot-invitation").addEventListener("click", () => copyInputValue(
  document.querySelector("#admin-pilot-invitation-link"),
  document.querySelector("#admin-copy-pilot-invitation"),
  "Convite copiado",
  "Copiar novo convite",
));

document.querySelector("#admin-renew-pilot-invitation").addEventListener("click", async (event) => {
  if (event.currentTarget.dataset.status === "waiting_acceptance" && !window.confirm("Gerar um novo link? O convite anterior deixará de funcionar.")) return;
  const message = document.querySelector("#admin-dialog-message");
  message.classList.add("hidden");
  event.currentTarget.disabled = true;
  try {
    const invitation = await adminFetch(`/api/admin/organizations/${selectedOrganizationId}/pilot-invitation`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ send_email: false }),
    });
    renderOrganizationDetail(await adminFetch(`/api/admin/organizations/${selectedOrganizationId}`));
    document.querySelector("#admin-pilot-invitation-link").value = invitation.link;
    document.querySelector("#admin-pilot-invitation-expiry").textContent = `Use este novo link até ${formatDate(invitation.expires_at)}. Ele só aparece agora.`;
    document.querySelector("#admin-copy-pilot-invitation").textContent = "Copiar novo convite";
    document.querySelector("#admin-pilot-invitation-result").classList.remove("hidden");
    notify("Novo convite gerado; o link anterior foi invalidado.", message, true);
    await loadOrganizations();
  } catch (error) { notify(error.message, message); }
  finally { event.currentTarget.disabled = false; }
});

document.querySelector("#admin-transfer-responsibility").addEventListener("click", async (event) => {
  const message = document.querySelector("#admin-dialog-message");
  const targetUserId = event.currentTarget.dataset.userId;
  if (!selectedOrganizationId || !targetUserId) return;
  message.classList.add("hidden");
  event.currentTarget.disabled = true;
  try {
    await adminFetch(`/api/organizations/${selectedOrganizationId}/owner/${encodeURIComponent(targetUserId)}`, { method: "PUT" });
    renderOrganizationDetail(await adminFetch(`/api/admin/organizations/${selectedOrganizationId}`));
    notify("Responsabilidade transferida ao cliente.", message, true);
    await loadOrganizations();
  } catch (error) { notify(error.message, message); }
  finally { event.currentTarget.disabled = false; }
});

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

document.querySelector("#admin-support-state-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#admin-support-message");
  message.classList.add("hidden");
  try {
    const ticket = await adminFetch(`/api/admin/support/tickets/${encodeURIComponent(selectedSupportTicketId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: document.querySelector("#admin-support-edit-status").value, priority: document.querySelector("#admin-support-edit-priority").value }),
    });
    renderSupportTicketDetail(ticket);
    notify("Tratamento atualizado.", message, true);
    loadSupportTickets();
  } catch (error) { notify(error.message, message); }
});

document.querySelector("#admin-support-reply-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#admin-support-message");
  const button = event.target.querySelector("button");
  message.classList.add("hidden");
  button.disabled = true;
  try {
    const ticket = await adminFetch(`/api/admin/support/tickets/${encodeURIComponent(selectedSupportTicketId)}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: document.querySelector("#admin-support-reply").value }),
    });
    event.target.reset();
    renderSupportTicketDetail(ticket);
    notify("Resposta enviada e notificação criada.", message, true);
    loadSupportTickets();
  } catch (error) { notify(error.message, message); }
  finally { button.disabled = false; }
});

document.querySelector("#admin-privacy-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#admin-privacy-message");
  message.classList.add("hidden");
  try {
    const updated = await adminFetch(`/api/admin/privacy/requests/${encodeURIComponent(selectedPrivacyRequestId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: document.querySelector("#admin-privacy-edit-status").value, resolution_note: document.querySelector("#admin-privacy-note").value }),
    });
    notify("Tratamento de privacidade atualizado.", message, true);
    await loadPrivacyRequests();
    openPrivacyRequest(updated.id);
  } catch (error) { notify(error.message, message); }
});

loadOrganizations();
loadOperations();
loadBillingCatalog();
loadSupportTickets();
loadPrivacyRequests();
document.querySelector("#admin-refresh-operations").addEventListener("click", loadOperations);
