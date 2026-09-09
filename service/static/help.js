const $ = (selector) => document.querySelector(selector);
const organizationParam = new URLSearchParams(location.search).get("organization");
let activeOrganization = null;
let diagnosticText = "";
let selectedSupportTicketId = null;

const supportStatusLabels = { open: "Aberto", in_progress: "Em atendimento", waiting_customer: "Aguardando você", resolved: "Resolvido", closed: "Encerrado" };
const supportCategoryLabels = { question: "Dúvida de uso", technical: "Problema técnico", billing: "Plano ou cobrança", suggestion: "Sugestão" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function normalized(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function organizationUrl(path, extra = {}) {
  const url = new URL(path, location.origin);
  if (activeOrganization) url.searchParams.set("organization", activeOrganization.id);
  Object.entries(extra).forEach(([key, value]) => url.searchParams.set(key, value));
  return `${url.pathname}${url.search}${url.hash}`;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers);
  if (activeOrganization && path.startsWith("/api/")) headers.set("X-Organization-Id", String(activeOrganization.id));
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    throw new Error("Entre novamente para abrir a ajuda.");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Não foi possível carregar estas informações.");
  return data;
}

function updateLinks() {
  $("#help-back").href = organizationUrl("/");
  $("#help-organizations").href = organizationUrl("/organizations");
  $("#help-account").href = organizationUrl("/account");
  document.querySelectorAll("[data-platform-tab]").forEach((link) => {
    link.href = organizationUrl("/", { tab: link.dataset.platformTab });
  });
}

async function loadContext() {
  const organizations = await api("/api/organizations");
  activeOrganization = organizations.organizations.find((item) => String(item.id) === organizationParam)
    || organizations.organizations[0];
  if (!activeOrganization) {
    location.replace("/organizations");
    return;
  }
  updateLinks();
  $("#workspace-pill strong").textContent = activeOrganization.name;
  const context = await api("/api/support/context");
  const profile = context;
  const role = { admin: "Administrador", member: "Membro", viewer: "Consulta" }[activeOrganization.role] || "Consulta";
  const plan = profile.plan_code === "internal" ? "Interno EchoHub" : profile.plan_code || "Não informado";
  const credits = profile.unlimited_credits ? "Ilimitados" : Number(profile.credit_balance || 0).toLocaleString("pt-BR");
  const dataset = context.dataset_version || "Não informado";
  $("#diagnostic-organization").textContent = `${activeOrganization.name} · ${role}`;
  $("#diagnostic-plan").textContent = plan;
  $("#diagnostic-credits").textContent = credits;
  $("#diagnostic-dataset").textContent = dataset;
  diagnosticText = [
    "Diagnóstico EchoPJs",
    `Organização: ${activeOrganization.name} (#${activeOrganization.id})`,
    `Acesso: ${role}`,
    `Plano: ${plan}`,
    `Status: ${profile.subscription_status || "não informado"}`,
    `Créditos: ${credits}`,
    `Base da Receita: ${dataset}`,
    `Gerado em: ${new Date().toLocaleString("pt-BR")}`,
  ].join("\n");
}

function filterHelp() {
  const query = normalized($("#help-search").value.trim());
  let visible = 0;
  document.querySelectorAll(".help-topic").forEach((topic) => {
    const match = !query || normalized(`${topic.dataset.helpSearch} ${topic.textContent}`).includes(query);
    topic.classList.toggle("hidden", !match);
    if (match) visible += 1;
  });
  $("#help-empty").classList.toggle("hidden", visible !== 0);
  $("#help-search-status").textContent = query ? `${visible} ${visible === 1 ? "guia encontrado" : "guias encontrados"}` : "";
}

$("#help-search").addEventListener("input", filterHelp);
$("#clear-help-search").addEventListener("click", () => { $("#help-search").value = ""; filterHelp(); $("#help-search").focus(); });
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    event.preventDefault(); $("#help-search").focus();
  }
});

function renderTickets(tickets) {
  const target = $("#support-tickets");
  if (!tickets.length) {
    target.innerHTML = `<div class="support-empty"><strong>Nenhum chamado aberto.</strong><span>Quando precisar, use o formulário acima. O histórico ficará disponível para toda a equipe do workspace.</span></div>`;
    return;
  }
  target.innerHTML = tickets.map((ticket) => `<button class="support-ticket" type="button" data-support-ticket="${escapeHtml(ticket.id)}">
    <span class="support-ticket-top"><b class="support-status status-${escapeHtml(ticket.status)}">${escapeHtml(supportStatusLabels[ticket.status] || ticket.status)}</b><small>#${escapeHtml(ticket.id.slice(0, 8).toUpperCase())}</small></span>
    <strong>${escapeHtml(ticket.subject)}</strong>
    <span>${escapeHtml(ticket.last_message_preview || "Sem mensagens")}</span>
    <small>${escapeHtml(supportCategoryLabels[ticket.category] || ticket.category)} · ${formatDate(ticket.updated_at)} · ${Number(ticket.message_count || 0)} mensagen${Number(ticket.message_count || 0) === 1 ? "" : "s"}</small>
  </button>`).join("");
}

async function loadTickets() {
  const button = $("#refresh-support");
  button.disabled = true;
  try { renderTickets((await api("/api/support/tickets")).tickets || []); }
  catch (error) { $("#support-tickets").innerHTML = `<div class="support-empty"><strong>Não foi possível carregar os chamados.</strong><span>${escapeHtml(error.message)}</span></div>`; }
  finally { button.disabled = false; }
}

function renderTicketDetail(ticket) {
  $("#support-dialog-reference").textContent = `CHAMADO #${ticket.id.slice(0, 8).toUpperCase()}`;
  $("#support-dialog-title").textContent = ticket.subject;
  $("#support-dialog-meta").innerHTML = `<span class="support-status status-${escapeHtml(ticket.status)}">${escapeHtml(supportStatusLabels[ticket.status] || ticket.status)}</span> ${escapeHtml(supportCategoryLabels[ticket.category] || ticket.category)} · aberto por ${escapeHtml(ticket.requester_identifier)} em ${formatDate(ticket.created_at)}`;
  $("#support-thread").innerHTML = ticket.messages.map((message) => `<article class="support-message ${message.author_kind}"><div><strong>${message.author_kind === "support" ? "Equipe EchoHub" : "Sua equipe"}</strong><time>${formatDate(message.created_at)}</time></div><p>${escapeHtml(message.body).replace(/\n/g, "<br>")}</p></article>`).join("");
  $("#support-thread").scrollTop = $("#support-thread").scrollHeight;
}

async function openTicket(ticketId) {
  selectedSupportTicketId = ticketId;
  $("#support-dialog-title").textContent = "Carregando…";
  $("#support-thread").innerHTML = "";
  $("#support-reply-feedback").textContent = "";
  $("#support-dialog").showModal();
  try { renderTicketDetail(await api(`/api/support/tickets/${encodeURIComponent(ticketId)}`)); }
  catch (error) { $("#support-reply-feedback").textContent = error.message; }
}

$("#support-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#support-submit");
  const feedback = $("#support-feedback");
  button.disabled = true;
  feedback.textContent = "Enviando o chamado…";
  feedback.dataset.type = "";
  try {
    const ticket = await api("/api/support/tickets", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: $("#support-category").value, priority: $("#support-priority").value, subject: $("#support-subject").value, message: $("#support-message").value }),
    });
    event.target.reset();
    $("#support-message-count").textContent = "0";
    feedback.textContent = `Chamado #${ticket.id.slice(0, 8).toUpperCase()} aberto. Acompanhe a resposta abaixo.`;
    feedback.dataset.type = "success";
    await loadTickets();
    await openTicket(ticket.id);
  } catch (error) { feedback.textContent = error.message; }
  finally { button.disabled = false; }
});

$("#support-message").addEventListener("input", (event) => { $("#support-message-count").textContent = event.target.value.length.toLocaleString("pt-BR"); });
$("#refresh-support").addEventListener("click", loadTickets);
$("#support-tickets").addEventListener("click", (event) => { const ticket = event.target.closest("[data-support-ticket]"); if (ticket) openTicket(ticket.dataset.supportTicket); });
$("#support-dialog-close").addEventListener("click", () => $("#support-dialog").close());
$("#support-dialog").addEventListener("click", (event) => { if (event.target === $("#support-dialog")) $("#support-dialog").close(); });
$("#support-reply-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  const feedback = $("#support-reply-feedback");
  button.disabled = true;
  feedback.textContent = "Enviando…";
  try {
    const ticket = await api(`/api/support/tickets/${encodeURIComponent(selectedSupportTicketId)}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: $("#support-reply").value }),
    });
    event.target.reset();
    feedback.textContent = "Resposta enviada.";
    renderTicketDetail(ticket);
    await loadTickets();
  } catch (error) { feedback.textContent = error.message; }
  finally { button.disabled = false; }
});
$("#copy-diagnostic").addEventListener("click", async () => {
  const button = $("#copy-diagnostic");
  try {
    await navigator.clipboard.writeText(diagnosticText);
    button.textContent = "Diagnóstico copiado";
    $("#copy-status").textContent = "Cole estas informações na sua mensagem para o suporte.";
  } catch (_) {
    $("#copy-status").textContent = "Não foi possível copiar automaticamente. Tente novamente pelo navegador.";
  }
});

loadContext().then(loadTickets).catch((error) => {
  $("#workspace-pill strong").textContent = "Contexto indisponível";
  $("#copy-status").textContent = error.message;
  $("#copy-diagnostic").disabled = true;
});
