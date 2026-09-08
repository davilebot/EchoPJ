const $ = (selector) => document.querySelector(selector);
const organizationParam = new URLSearchParams(location.search).get("organization");
let activeOrganization = null;
let diagnosticText = "";

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
  const [dashboard, health] = await Promise.all([api("/api/dashboard"), api("/health")]);
  const profile = dashboard.profile || activeOrganization.billing || {};
  const role = activeOrganization.role === "admin" ? "Administrador" : "Membro";
  const plan = profile.plan_code === "internal" ? "Interno EchoHub" : profile.plan_code || "Não informado";
  const credits = profile.unlimited_credits ? "Ilimitados" : Number(profile.credit_balance || 0).toLocaleString("pt-BR");
  const dataset = health.dataset_version || "Não informado";
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

loadContext().catch((error) => {
  $("#workspace-pill strong").textContent = "Contexto indisponível";
  $("#copy-status").textContent = error.message;
  $("#copy-diagnostic").disabled = true;
});
