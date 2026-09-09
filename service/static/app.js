const nativeFetch = window.fetch.bind(window);
let activeOrganizationId = null;
window.fetch = async (input, init = {}) => {
  const workspace = await window.echoWorkspace;
  if (!workspace) throw new Error("Entre em uma organização para continuar.");
  activeOrganizationId = workspace.organization.id;
  const headers = new Headers(init.headers);
  headers.set("X-Organization-Id", String(activeOrganizationId));
  const response = await nativeFetch(input, { ...init, headers });
  if (response.status === 401) {
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.replace(`/login?next=${encodeURIComponent(next)}`);
  }
  return response;
};

const themeToggle = document.querySelector("#theme-toggle");
const themeColor = document.querySelector('meta[name="theme-color"]');

function applyTheme(theme) {
  const resolvedTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = resolvedTheme;
  themeToggle.setAttribute("aria-pressed", String(resolvedTheme === "dark"));
  themeToggle.setAttribute("aria-label", resolvedTheme === "dark" ? "Ativar tema claro" : "Ativar tema escuro");
  themeColor?.setAttribute("content", resolvedTheme === "dark" ? "#111114" : "#f8f9fa");
  try {
    localStorage.setItem("echopjs-theme", resolvedTheme);
  } catch (_) {}
}

applyTheme(document.documentElement.dataset.theme);
themeToggle.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

const form = document.querySelector("#match-form");
const loading = document.querySelector("#loading");
const result = document.querySelector("#result");
const batchForm = document.querySelector("#batch-form");
const batchLoading = document.querySelector("#batch-loading");
const batchResult = document.querySelector("#batch-result");
const historyLoading = document.querySelector("#history-loading");
const historyList = document.querySelector("#history-list");
const companySearchForm = document.querySelector("#company-search-form");
const companySearchLoading = document.querySelector("#search-loading");
const companySearchResult = document.querySelector("#search-result");
const saasOverviewLoading = document.querySelector("#saas-overview-loading");
const saasOverview = document.querySelector("#saas-overview");
const explorerOverviewLoading = document.querySelector("#explorer-overview-loading");
const explorerOverview = document.querySelector("#explorer-overview");
const schemaLoading = document.querySelector("#schema-loading");
const schemaCatalog = document.querySelector("#schema-catalog");
const relationPreview = document.querySelector("#relation-preview");
const explorerCnpjForm = document.querySelector("#explorer-cnpj-form");
const explorerCompanyLoading = document.querySelector("#explorer-company-loading");
const explorerCompanyResult = document.querySelector("#explorer-company-result");
const explorerEstablishmentsLoading = document.querySelector("#explorer-establishments-loading");
const explorerEstablishmentsResult = document.querySelector("#explorer-establishments-result");
const bulkCnpjForm = document.querySelector("#bulk-cnpj-form");
const bulkCnpjLoading = document.querySelector("#bulk-cnpj-loading");
const bulkCnpjResult = document.querySelector("#bulk-cnpj-result");
const listsLoading = document.querySelector("#lists-loading");
const listsGrid = document.querySelector("#lists-grid");
const listDetail = document.querySelector("#list-detail");
const savedSearchesLoading = document.querySelector("#saved-searches-loading");
const savedSearchesGrid = document.querySelector("#saved-searches-grid");
const billingLoading = document.querySelector("#billing-loading");
const billingSummary = document.querySelector("#billing-summary");
const cancelSubscriptionDialog = document.querySelector("#cancel-subscription-dialog");
const saveSearchDialog = document.querySelector("#save-search-dialog");
const saveListDialog = document.querySelector("#save-list-dialog");
const createListDialog = document.querySelector("#create-list-dialog");
let batchSourceRows = [];
let historyPoll = null;
let searchCapabilitiesLoaded = false;
let searchCapabilities = {};
let searchCnaeOptionsLoaded = false;
let municipalityOptionsRequest = 0;
let explorerSchemaLoaded = false;
let lastCompanySearch = [];
let lastCompanySearchPayload = null;
let activeSavedSearchId = null;
let activeSavedSearchFilters = null;
let activeSavedSearchName = null;
let selectedCompanyCnpjs = new Set();
let searchSelectionEstimateTimer = null;
let searchSelectionEstimateRequest = 0;
let activeCompanySearchView = "total";
let lastCompanySearchData = null;
let searchPreviewTimer = null;
let searchPreviewController = null;
let searchPreviewRequest = 0;
let lastBulkCnpjLookup = [];

const allUfs = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"];
const allRegistrationStatuses = ["ATIVA", "BAIXADA", "INAPTA", "NULA", "SUSPENSA", "NAO INFORMADA"];
const regionStates = {
  N: ["AC", "AP", "AM", "PA", "RO", "RR", "TO"],
  NE: ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
  CO: ["DF", "GO", "MT", "MS"],
  SE: ["ES", "MG", "RJ", "SP"],
  S: ["PR", "RS", "SC"],
};
const ufNames = {
  AC: "Acre", AL: "Alagoas", AP: "Amapá", AM: "Amazonas", BA: "Bahia", CE: "Ceará", DF: "Distrito Federal",
  ES: "Espírito Santo", GO: "Goiás", MA: "Maranhão", MT: "Mato Grosso", MS: "Mato Grosso do Sul", MG: "Minas Gerais",
  PA: "Pará", PB: "Paraíba", PR: "Paraná", PE: "Pernambuco", PI: "Piauí", RJ: "Rio de Janeiro", RN: "Rio Grande do Norte",
  RS: "Rio Grande do Sul", RO: "Rondônia", RR: "Roraima", SC: "Santa Catarina", SP: "São Paulo", SE: "Sergipe", TO: "Tocantins",
};

const searchTemplates = {
  "smb-contact": {
    name: "PMEs com contato",
    filters: {
      registration_statuses: ["ATIVA"],
      company_sizes: ["MICRO EMPRESA", "EMPRESA DE PEQUENO PORTE"],
      has_email: true,
      has_phone: true,
      limit: 500,
    },
  },
  "expanding-headquarters": {
    name: "Matrizes em expansão",
    filters: {
      registration_statuses: ["ATIVA"],
      branch_type: "1",
      active_branch_count_min: 2,
      limit: 500,
    },
  },
  "simples-contact": {
    name: "Simples com contato",
    filters: {
      registration_statuses: ["ATIVA"],
      simples: true,
      has_phone: true,
      limit: 500,
    },
  },
};

const listTemplates = {
  priority: { name: "Prospecção prioritária", description: "Empresas aprovadas para contato imediato pela equipe comercial." },
  research: { name: "Em qualificação", description: "Empresas que ainda precisam de pesquisa ou validação antes do contato." },
  nurture: { name: "Acompanhar depois", description: "Contas para retomar em outro momento ou manter em acompanhamento." },
};

function createMultiPicker(root, emptyLabel) {
  const trigger = root.querySelector(".multi-picker-trigger");
  const triggerLabel = trigger.querySelector("span");
  const triggerCount = trigger.querySelector("small");
  const panel = root.querySelector(".multi-picker-panel");
  const search = root.querySelector(".multi-picker-search");
  const optionsContainer = root.querySelector(".multi-picker-options");
  const tags = root.querySelector(".multi-picker-tags");
  const clearButton = root.querySelector(".multi-picker-clear");
  let options = [];
  let optionByValue = new Map();
  const selected = new Map();
  const changeListeners = [];
  let disabled = trigger.disabled;

  function closePanel({ restoreFocus = false } = {}) {
    panel.classList.add("hidden");
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  }

  function notifyChange() {
    const values = [...selected.keys()];
    changeListeners.forEach((listener) => listener(values));
  }

  function renderSummary() {
    const count = selected.size;
    triggerLabel.textContent = count === 0
      ? emptyLabel
      : count === 1
        ? [...selected.values()][0].displayLabel
        : `${count.toLocaleString("pt-BR")} selecionados`;
    triggerCount.textContent = count === 0 ? "Nenhum selecionado" : `${count.toLocaleString("pt-BR")} marcado${count === 1 ? "" : "s"}`;
    tags.innerHTML = [...selected.values()].map((option) => `
      <button type="button" class="multi-picker-tag" data-remove-value="${escapeHtml(option.value)}" title="Remover ${escapeHtml(option.displayLabel)}">
        <span>${escapeHtml(option.displayLabel)}</span><strong aria-hidden="true">×</strong>
      </button>`).join("");
  }

  function renderOptions() {
    const query = search.value.trim().toLocaleUpperCase("pt-BR");
    const matches = options.filter((option) => !query || option.searchText.includes(query));
    const visible = matches.slice(0, 250);
    optionsContainer.innerHTML = visible.length
      ? `${visible.map((option) => `
          <label class="multi-picker-option">
            <input type="checkbox" value="${escapeHtml(option.value)}" ${selected.has(option.value) ? "checked" : ""}>
            <span><strong>${escapeHtml(option.optionLabel)}</strong>${option.optionDescription ? `<small>${escapeHtml(option.optionDescription)}</small>` : ""}</span>
          </label>`).join("")}
          ${matches.length > visible.length ? `<p class="multi-picker-more">Mais ${matches.length - visible.length} opções. Digite parte do código ou nome para refinar.</p>` : ""}`
      : `<p class="multi-picker-empty">Nenhuma opção encontrada.</p>`;
  }

  trigger.addEventListener("click", () => {
    if (disabled) return;
    const willOpen = panel.classList.contains("hidden");
    document.querySelectorAll(".multi-picker-panel").forEach((otherPanel) => otherPanel.classList.add("hidden"));
    document.querySelectorAll(".multi-picker-trigger").forEach((otherTrigger) => otherTrigger.setAttribute("aria-expanded", "false"));
    panel.classList.toggle("hidden", !willOpen);
    trigger.setAttribute("aria-expanded", String(willOpen));
    if (willOpen) {
      renderOptions();
      search.focus();
    }
  });
  root.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || panel.classList.contains("hidden")) return;
    event.preventDefault();
    closePanel({ restoreFocus: true });
  });
  document.addEventListener("pointerdown", (event) => {
    if (!root.contains(event.target)) closePanel();
  });
  search.addEventListener("input", renderOptions);
  optionsContainer.addEventListener("change", (event) => {
    const checkbox = event.target.closest("input[type='checkbox']");
    if (!checkbox) return;
    const option = optionByValue.get(checkbox.value);
    if (checkbox.checked && option) selected.set(option.value, option);
    else selected.delete(checkbox.value);
    renderSummary();
    notifyChange();
  });
  tags.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-value]");
    if (!button) return;
    selected.delete(button.dataset.removeValue);
    renderSummary();
    renderOptions();
    notifyChange();
  });
  clearButton.addEventListener("click", () => {
    selected.clear();
    renderSummary();
    renderOptions();
    notifyChange();
  });

  return {
    values: () => [...selected.keys()],
    setOptions(newOptions, { clear = false } = {}) {
      options = newOptions.map((option) => ({
        value: String(option.value),
        label: String(option.label || option.value),
        optionLabel: String(option.option_label || option.value),
        optionDescription: option.option_description !== undefined
          ? String(option.option_description)
          : option.option_label ? String(option.label || "") : option.label && option.label !== option.value ? String(option.label) : "",
        displayLabel: String(option.display_label || (option.label && option.label !== option.value ? `${option.value} — ${option.label}` : option.value)),
        searchText: `${option.value} ${option.label || ""} ${option.option_label || ""} ${option.display_label || ""}`.toLocaleUpperCase("pt-BR"),
      }));
      optionByValue = new Map(options.map((option) => [option.value, option]));
      if (clear) selected.clear();
      else [...selected.keys()].forEach((value) => {
        if (!optionByValue.has(value)) selected.delete(value);
      });
      search.value = "";
      renderSummary();
      renderOptions();
    },
    clear() {
      selected.clear();
      renderSummary();
      renderOptions();
    },
    setSelected(values) {
      selected.clear();
      values.forEach((value) => {
        const option = optionByValue.get(String(value));
        if (option) selected.set(option.value, option);
      });
      renderSummary();
      renderOptions();
    },
    onChange(listener) {
      changeListeners.push(listener);
    },
    setDisabled(value, label = emptyLabel) {
      disabled = value;
      trigger.disabled = value;
      root.classList.toggle("disabled", value);
      if (value) {
        panel.classList.add("hidden");
        trigger.setAttribute("aria-expanded", "false");
      }
      if (!selected.size) triggerLabel.textContent = label;
    },
    setLoading(label) {
      disabled = true;
      trigger.disabled = true;
      root.classList.add("disabled");
      triggerLabel.textContent = label;
    },
  };
}

const cnaePicker = createMultiPicker(document.querySelector("#search-cnae-picker"), "Selecionar CNAEs");
const municipalityPicker = createMultiPicker(document.querySelector("#search-municipality-picker"), "Selecionar municípios");
const regionPicker = createMultiPicker(document.querySelector("#search-region-picker"), "Brasil inteiro");
const ufPicker = createMultiPicker(document.querySelector("#search-uf-picker"), "Todos os estados");
const statusPicker = createMultiPicker(document.querySelector("#search-status-picker"), "Ativa (padrão)");
const sizePicker = createMultiPicker(document.querySelector("#search-size-picker"), "Todos os portes");
const partnerAgePicker = createMultiPicker(document.querySelector("#search-partner-age-picker"), "Todas as faixas etárias");

regionPicker.setOptions([
  { value: "N", option_label: "Norte", display_label: "Norte" }, { value: "NE", option_label: "Nordeste", display_label: "Nordeste" }, { value: "CO", option_label: "Centro-Oeste", display_label: "Centro-Oeste" },
  { value: "SE", option_label: "Sudeste", display_label: "Sudeste" }, { value: "S", option_label: "Sul", display_label: "Sul" },
]);
statusPicker.setOptions([
  { value: "ATIVA", option_label: "Ativa", display_label: "Ativa" }, { value: "BAIXADA", option_label: "Baixada", display_label: "Baixada" }, { value: "INAPTA", option_label: "Inapta", display_label: "Inapta" },
  { value: "SUSPENSA", option_label: "Suspensa", display_label: "Suspensa" }, { value: "NULA", option_label: "Nula", display_label: "Nula" }, { value: "NAO INFORMADA", option_label: "Não informada", display_label: "Não informada" },
]);
statusPicker.setSelected(["ATIVA"]);
sizePicker.setOptions([
  { value: "MICRO EMPRESA", option_label: "Microempresa", display_label: "Microempresa" },
  { value: "EMPRESA DE PEQUENO PORTE", option_label: "Empresa de Pequeno Porte", display_label: "Empresa de Pequeno Porte" },
  { value: "DEMAIS", option_label: "Demais", display_label: "Demais" },
  { value: "NAO INFORMADO", option_label: "Não informado", display_label: "Não informado" },
]);
partnerAgePicker.setOptions([
  { value: "1", option_label: "0 a 12 anos", display_label: "0 a 12 anos" },
  { value: "2", option_label: "13 a 20 anos", display_label: "13 a 20 anos" },
  { value: "3", option_label: "21 a 30 anos", display_label: "21 a 30 anos" },
  { value: "4", option_label: "31 a 40 anos", display_label: "31 a 40 anos" },
  { value: "5", option_label: "41 a 50 anos", display_label: "41 a 50 anos" },
  { value: "6", option_label: "51 a 60 anos", display_label: "51 a 60 anos" },
  { value: "7", option_label: "61 a 70 anos", display_label: "61 a 70 anos" },
  { value: "8", option_label: "71 a 80 anos", display_label: "71 a 80 anos" },
  { value: "9", option_label: "Maior de 80 anos", display_label: "Maior de 80 anos" },
]);

function updateUfOptions() {
  const regions = regionPicker.values();
  const allowed = regions.length
    ? [...new Set(regions.flatMap((region) => regionStates[region]))]
    : allUfs;
  ufPicker.setOptions(allowed.map((uf) => ({ value: uf, label: ufNames[uf] })));
  loadMunicipalityOptions();
}

updateUfOptions();
regionPicker.onChange(updateUfOptions);
ufPicker.onChange(loadMunicipalityOptions);

document.addEventListener("click", (event) => {
  if (event.target.closest(".multi-picker")) return;
  document.querySelectorAll(".multi-picker-panel").forEach((panel) => panel.classList.add("hidden"));
  document.querySelectorAll(".multi-picker-trigger").forEach((trigger) => trigger.setAttribute("aria-expanded", "false"));
});

const statusLabel = {
  confirmado: "Confirmado",
  revisao: "Revisão necessária",
  nao_encontrado: "Não encontrado",
};

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character]);
}

function formatCnpj(value = "") {
  const digits = value.replace(/\D/g, "");
  return digits.length === 14 ? digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5") : value;
}

function render(data) {
  const item = data.results[0];
  const selected = item.selected;
  const candidates = item.candidates || [];
  const timing = (data.timing_ms.total / 1000).toFixed(1);
  const selectedHtml = selected ? `
    <div class="selected">
      <span class="status ${item.status}">${statusLabel[item.status]}</span>
      <h2>${escapeHtml(selected.trade_name || selected.legal_name)}</h2>
      <strong>${escapeHtml(formatCnpj(selected.cnpj))}</strong>
      <p>${escapeHtml(selected.legal_name)}</p>
      <dl>
        <div><dt>Score</dt><dd>${selected.score}/100</dd></div>
        <div><dt>Situação</dt><dd>${escapeHtml(selected.registration_status)}</dd></div>
        <div><dt>Município</dt><dd>${escapeHtml(selected.municipality)}/${escapeHtml(selected.uf)}</dd></div>
        <div><dt>Endereço</dt><dd>${escapeHtml(selected.address || "—")}</dd></div>
      </dl>
    </div>` : `<div class="selected"><span class="status nao_encontrado">Não encontrado</span><h2>Nenhum CNPJ seguro</h2><p>Os dados informados não foram suficientes para escolher automaticamente.</p></div>`;
  const candidatesHtml = candidates.length ? `<h3>Melhores candidatos</h3><div class="candidates">${candidates.map((candidate, index) => `
    <article>
      <span>#${index + 1} · score ${candidate.score}</span>
      <strong>${escapeHtml(formatCnpj(candidate.cnpj))}</strong>
      <p>${escapeHtml(candidate.legal_name)}</p>
      <small>${escapeHtml(candidate.municipality)}/${escapeHtml(candidate.uf)} · ${escapeHtml(candidate.postal_code || "sem CEP")}</small>
    </article>`).join("")}</div>` : "";
  result.innerHTML = `${selectedHtml}${candidatesHtml}<p class="timing">Consulta concluída em ${timing}s · Receita ${data.dataset_version}</p>`;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  result.classList.add("hidden");
  loading.classList.remove("hidden");
  const payload = {
    check_website: document.querySelector("#check-website").checked,
    active_only: true,
    items: [{
      local_id: "consulta-web",
      name: document.querySelector("#name").value,
      address: document.querySelector("#address").value || null,
      municipality: document.querySelector("#municipality").value || null,
      uf: document.querySelector("#uf").value,
      postal_code: document.querySelector("#postal-code").value || null,
      website: document.querySelector("#website").value || null,
    }],
  };
  try {
    const response = await fetch("/api/matches/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error((await response.json()).detail || "Falha na consulta");
    render(await response.json());
  } catch (error) {
    result.innerHTML = `<div class="error"><strong>Não foi possível consultar.</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    loading.classList.add("hidden");
    result.classList.remove("hidden");
  }
});

function parseCsv(text) {
  const firstLine = text.split(/\r?\n/, 1)[0] || "";
  const delimiter = (firstLine.match(/;/g) || []).length > (firstLine.match(/,/g) || []).length ? ";" : ",";
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === '"') {
      if (quoted && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === delimiter && !quoted) {
      row.push(field);
      field = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      row.push(field);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }
  row.push(field);
  if (row.some((value) => value.trim())) rows.push(row);
  if (rows.length < 2) throw new Error("O CSV precisa ter cabeçalho e pelo menos uma empresa.");
  const headers = rows[0].map((value, index) => index === 0 ? value.replace(/^\uFEFF/, "").trim() : value.trim());
  return rows.slice(1).map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}

function pick(row, names) {
  const normalized = new Map(Object.entries(row).map(([key, value]) => [key.trim().toLowerCase(), value]));
  for (const name of names) {
    const value = normalized.get(name.toLowerCase());
    if (value && value.trim()) return value.trim();
  }
  return "";
}

function batchItem(row, index) {
  const name = pick(row, ["Company Name", "Nome da empresa", "Razão Social", "Nome"]);
  const uf = pick(row, ["Company State", "UF", "Estado"]).toUpperCase();
  if (!name) throw new Error(`Linha ${index + 2}: não encontrei o nome da empresa.`);
  if (uf.length !== 2) throw new Error(`Linha ${index + 2}: a UF precisa ter duas letras.`);
  return {
    local_id: String(index + 2),
    name,
    address: pick(row, ["Company Street", "Company Address", "Endereço", "Logradouro"]) || null,
    municipality: pick(row, ["Company City", "Município", "Cidade"]) || null,
    uf,
    postal_code: pick(row, ["Company Postal Code", "CEP"]) || null,
    website: pick(row, ["Website", "Site", "Company Website"]) || null,
    source: row,
  };
}

function csvCell(value) {
  let text = String(value ?? "");
  if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadTemplate() {
  const lines = [
    ["Company Name", "Company Street", "Company City", "Company State", "Company Postal Code", "Website"],
    ["Empresa Exemplo", "Rua Brasil 100", "Campinas", "SP", "13010-000", "https://empresa.com.br"],
  ];
  const blob = new Blob(["\uFEFF" + lines.map((row) => row.map(csvCell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "modelo-matcher-cnpj.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}

const jobStatusLabel = {
  queued: "Na fila",
  running: "Processando",
  completed: "Concluída",
  completed_with_errors: "Concluída com avisos",
};

function switchTab(tabName) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("hidden", panel.id !== `${tabName}-tab`));
  const activeButton = document.querySelector(`.tab-button[data-tab="${tabName}"]`);
  document.querySelector("#current-view").textContent = activeButton?.dataset.viewTitle || "EchoPJs";
  if (tabName === "history") loadHistory();
  if (tabName === "overview") {
    loadSaaSOverview();
    loadExplorerOverview();
  }
  if (tabName === "search") {
    loadSearchCapabilities();
    loadCnaeOptions();
  }
  if (tabName === "lists") loadCompanyLists();
  if (tabName === "saved-searches") loadSavedSearches();
  if (tabName === "billing") loadBillingSummary();
  if (tabName === "data") loadDatabaseSchema();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function formatBillingDate(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : formatDate(value);
}

function renderHistory(jobs) {
  if (!jobs.length) {
    historyList.innerHTML = `<div class="empty-state"><strong>Nenhuma consulta em lote ainda.</strong><p>Envie um CSV na aba “Nova consulta”.</p></div>`;
    return;
  }
  historyList.innerHTML = jobs.map((job) => {
    const active = job.status === "queued" || job.status === "running";
    const download = job.processed > 0
      ? `<button class="download-link" type="button" data-download-job="${job.id}" data-capability="export">${active ? "Baixar parcial" : "Baixar CSV"}</button>`
      : `<span class="download-disabled">Download após a primeira linha</span>`;
    return `<article class="job-card">
      <div class="job-topline">
        <div><strong>${escapeHtml(job.filename)}</strong><span>${formatDate(job.created_at)}</span></div>
        <span class="job-status ${job.status}">${jobStatusLabel[job.status] || job.status}</span>
      </div>
      <div class="progress-label"><span>${job.processed.toLocaleString("pt-BR")} de ${job.total.toLocaleString("pt-BR")} empresas</span><strong>${job.progress_percent}%</strong></div>
      <div class="progress-track"><span style="width:${Math.min(100, job.progress_percent)}%"></span></div>
      <div class="job-counts">
        <span><strong>${job.confirmed}</strong> confirmadas</span>
        <span><strong>${job.review}</strong> revisão</span>
        <span><strong>${job.not_found}</strong> sem resultado</span>
        ${job.failed ? `<span><strong>${job.failed}</strong> erros</span>` : ""}
      </div>
      <div class="job-footer"><span>${job.check_website ? "Banco + websites" : "Somente banco"}</span>${download}</div>
    </article>`;
  }).join("");
}

async function loadHistory() {
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) throw new Error("Não foi possível carregar o histórico");
    const data = await response.json();
    renderHistory(data.jobs);
    historyLoading.classList.add("hidden");
    historyList.classList.remove("hidden");
    const hasActive = data.jobs.some((job) => job.status === "queued" || job.status === "running");
    clearTimeout(historyPoll);
    if (hasActive && !document.querySelector("#history-tab").classList.contains("hidden")) {
      historyPoll = setTimeout(loadHistory, 3000);
    }
  } catch (error) {
    historyLoading.textContent = error.message;
  }
}

batchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  batchResult.classList.add("hidden");
  batchLoading.classList.remove("hidden");
  try {
    const file = document.querySelector("#csv-file").files[0];
    const allRows = parseCsv(await file.text());
    if (allRows.length > 10000) throw new Error(`O arquivo tem ${allRows.length.toLocaleString("pt-BR")} linhas. O limite atual é 10.000.`);
    batchSourceRows = allRows;
    const items = batchSourceRows.map(batchItem);
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name,
        active_only: true,
        check_website: document.querySelector("#batch-check-website").checked,
        items,
      }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Falha na consulta em lote");
    const job = await response.json();
    batchResult.innerHTML = `<div class="queued-message"><strong>Arquivo recebido.</strong><p>${job.total.toLocaleString("pt-BR")} empresas foram colocadas na fila. O processamento continuará uma por vez mesmo se você fechar esta página.</p><button id="view-job" type="button">Acompanhar nas últimas consultas</button></div>`;
    document.querySelector("#view-job").addEventListener("click", () => switchTab("history"));
  } catch (error) {
    batchResult.innerHTML = `<div class="error"><strong>Não foi possível processar o CSV.</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    batchLoading.classList.add("hidden");
    batchResult.classList.remove("hidden");
  }
});

function optionalBoolean(value) {
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function optionalNumber(value) {
  return value === "" ? null : Number(value);
}

async function loadSearchCapabilities() {
  if (searchCapabilitiesLoaded) return searchCapabilities;
  try {
    const response = await fetch("/api/search/capabilities");
    if (!response.ok) throw new Error("Não foi possível verificar os filtros disponíveis");
    const data = await response.json();
    searchCapabilities = data.filters;
    document.querySelectorAll("[data-capability]").forEach((field) => {
      field.disabled = !data.filters[field.dataset.capability];
    });
    partnerAgePicker.setDisabled(!data.filters.partners, data.filters.partners ? "Todas as faixas etárias" : "Sócios ainda indisponíveis");
    const filterCapabilities = ["simples_mei", "legal_nature", "establishment_details", "branch_counts", "partners"];
    const available = filterCapabilities.every((key) => data.filters[key]);
    const availableCount = filterCapabilities.filter((key) => data.filters[key]).length;
    if (available) {
      document.querySelector("#complementary-filters .availability-badge").textContent = "Disponível";
      document.querySelector("#complementary-note").textContent = `Dados complementares disponíveis na versão ${data.dataset_version}.`;
    } else if (availableCount) {
      document.querySelector("#complementary-filters .availability-badge").textContent = "Parcialmente disponível";
      document.querySelector("#complementary-note").textContent = "Os grupos já concluídos estão liberados. Os demais serão habilitados automaticamente quando terminarem.";
    }
    searchCapabilitiesLoaded = true;
    return searchCapabilities;
  } catch (error) {
    document.querySelector("#complementary-note").textContent = error.message;
    return searchCapabilities;
  }
}

async function loadCnaeOptions() {
  if (searchCnaeOptionsLoaded) return;
  cnaePicker.setLoading("Carregando CNAEs da Receita…");
  try {
    const response = await fetch("/api/search/options/cnaes");
    if (!response.ok) throw new Error("Não foi possível carregar os CNAEs");
    const data = await response.json();
    cnaePicker.setOptions(data.options);
    cnaePicker.setDisabled(false);
    searchCnaeOptionsLoaded = true;
  } catch (error) {
    cnaePicker.setDisabled(true, error.message);
  }
}

async function loadMunicipalityOptions() {
  const ufs = ufPicker.values();
  const requestNumber = ++municipalityOptionsRequest;
  municipalityPicker.clear();
  if (!ufs.length) {
    municipalityPicker.setOptions([], { clear: true });
    municipalityPicker.setDisabled(true, "Escolha ao menos uma UF");
    return;
  }
  municipalityPicker.setLoading(`Carregando municípios de ${ufs.length === 1 ? ufs[0] : `${ufs.length} UFs`}…`);
  try {
    const parameters = new URLSearchParams();
    ufs.forEach((uf) => parameters.append("ufs", uf));
    const response = await fetch(`/api/search/options/municipalities?${parameters}`);
    if (!response.ok) throw new Error("Não foi possível carregar os municípios");
    const data = await response.json();
    if (requestNumber !== municipalityOptionsRequest) return;
    municipalityPicker.setOptions(data.options, { clear: true });
    municipalityPicker.setDisabled(false);
  } catch (error) {
    if (requestNumber === municipalityOptionsRequest) municipalityPicker.setDisabled(true, error.message);
  }
}

function companySearchPayload() {
  return {
    cnaes: cnaePicker.values(),
    cnae_scope: document.querySelector("#search-cnae-scope").value,
    company_name: document.querySelector("#search-name").value || null,
    excluded_company_names: document.querySelector("#search-excluded-names").value
      .split(/[\n,;]+/).map((value) => value.trim()).filter(Boolean),
    registration_statuses: statusPicker.values(),
    company_sizes: sizePicker.values(),
    share_capital_min: optionalNumber(document.querySelector("#search-capital-min").value),
    share_capital_max: optionalNumber(document.querySelector("#search-capital-max").value),
    opened_from: document.querySelector("#search-opened-from").value || null,
    opened_to: document.querySelector("#search-opened-to").value || null,
    regions: regionPicker.values(),
    ufs: ufPicker.values(),
    municipalities: municipalityPicker.values(),
    postal_code_prefixes: document.querySelector("#search-postal-codes").value
      .split(/[\n,;]+/).map((value) => value.trim()).filter(Boolean),
    partner_age_ranges: partnerAgePicker.values(),
    simples: optionalBoolean(document.querySelector("#search-simples").value),
    mei: optionalBoolean(document.querySelector("#search-mei").value),
    legal_nature_code: document.querySelector("#search-legal-nature").value || null,
    branch_type: document.querySelector("#search-branch-type").value || null,
    has_email: optionalBoolean(document.querySelector("#search-has-email").value),
    has_phone: optionalBoolean(document.querySelector("#search-has-phone").value),
    active_branch_count_min: optionalNumber(document.querySelector("#search-branches-min").value),
    active_branch_count_max: optionalNumber(document.querySelector("#search-branches-max").value),
    limit: Math.max(1, Math.min(10000, Number(document.querySelector("#search-limit").value) || 1)),
  };
}

function displayValue(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function formatMoney(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function companySizeLabel(value) {
  const labels = {
    "00": "Não informado", "01": "Microempresa", "03": "Empresa de Pequeno Porte", "05": "Demais",
    "NAO INFORMADO": "Não informado", "MICRO EMPRESA": "Microempresa",
    "EMPRESA DE PEQUENO PORTE": "Empresa de Pequeno Porte", "DEMAIS": "Demais",
  };
  return labels[String(value || "").toUpperCase()] || value || "—";
}

function formatBytes(value) {
  if (!value) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Number(value);
  let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} ${units[index]}`;
}

function yesNo(value) {
  if (value === true) return "Sim";
  if (value === false) return "Não";
  return "Não informado";
}

const explorerKindLabels = {
  companies: "Dados da empresa",
  establishments: "Contatos e complementos",
  partners: "Sócios e administradores",
  simples: "Simples e MEI",
  reference_cnaes: "CNAEs",
  reference_countries: "Países",
  reference_legal_natures: "Naturezas jurídicas",
  reference_municipalities: "Municípios",
  reference_qualifications: "Qualificações",
  reference_status_reasons: "Motivos cadastrais",
};

function renderExplorerOverview(data) {
  const auxiliary = data.auxiliary;
  const status = auxiliary?.status || "não iniciada";
  const statusText = { staging: "Carga em andamento", current: "Disponível", ready: "Versão anterior", failed: "Falha na carga" }[status] || status;
  const progressRows = (data.progress || []).map((item) => {
    const percent = item.files ? Math.round(100 * item.completed_files / item.files) : 0;
    const state = item.failed ? "Falha" : item.running ? "Processando" : percent === 100 ? "Concluído" : "Aguardando";
    return `<tr><td>${escapeHtml(explorerKindLabels[item.kind] || item.kind)}</td><td>${item.completed_files}/${item.files} arquivos</td><td>${Number(item.rows_loaded).toLocaleString("pt-BR")}</td><td>${state}</td></tr>`;
  }).join("");
  const groups = (data.field_groups || []).map((group) => `<article><strong>${escapeHtml(group.label)}</strong><p>${escapeHtml(group.description)}</p></article>`).join("");
  explorerOverview.innerHTML = `<div class="explorer-summary">
      <div><strong>${Number(data.total_establishments).toLocaleString("pt-BR")}</strong><span>Estabelecimentos</span></div>
      <div><strong>${Number(data.active_establishments).toLocaleString("pt-BR")}</strong><span>Ativos</span></div>
      <div><strong>${escapeHtml(data.dataset_version || "—")}</strong><span>Versão da Receita</span></div>
      <div><strong>${formatBytes(data.database_bytes)}</strong><span>Tamanho do banco</span></div>
    </div>
    <div class="explorer-status"><div><strong>Dados complementares: ${escapeHtml(statusText)}</strong><p>${auxiliary ? `Versão ${escapeHtml(auxiliary.version)} · dados parciais não são exibidos nas consultas.` : "Nenhuma carga complementar encontrada."}</p></div><span class="aux-status ${escapeHtml(status)}">${escapeHtml(statusText)}</span></div>
    ${progressRows ? `<div class="table-wrap explorer-progress"><table><thead><tr><th>Grupo</th><th>Arquivos</th><th>Linhas carregadas</th><th>Estado</th></tr></thead><tbody>${progressRows}</tbody></table></div>` : ""}
    <div class="field-catalog">${groups}</div>`;
}

async function loadExplorerOverview() {
  explorerOverviewLoading.classList.remove("hidden");
  explorerOverview.classList.add("hidden");
  try {
    const response = await fetch("/api/explorer/overview");
    if (!response.ok) throw new Error("Não foi possível carregar a visão da base");
    renderExplorerOverview(await response.json());
    explorerOverview.classList.remove("hidden");
    explorerOverviewLoading.classList.add("hidden");
  } catch (error) {
    explorerOverviewLoading.textContent = error.message;
  }
}

function renderDatabaseSchema(data) {
  const relationsByGroup = new Map();
  (data.relations || []).forEach((relation) => {
    if (!relationsByGroup.has(relation.group)) relationsByGroup.set(relation.group, []);
    relationsByGroup.get(relation.group).push(relation);
  });
  const groupsHtml = (data.groups || []).map((group) => {
    const relations = relationsByGroup.get(group.key) || [];
    if (!relations.length) return "";
    const cards = relations.map((relation) => {
      const type = relation.relation_type === "view" ? "Visão" : "Tabela";
      const rows = relation.approximate_rows === null || relation.approximate_rows === undefined
        ? "Contagem sob demanda"
        : `≈ ${Number(relation.approximate_rows).toLocaleString("pt-BR")} linhas`;
      return `<article class="relation-card ${relation.recommended ? "recommended" : ""}">
        <div class="relation-card-top"><span class="relation-type ${relation.relation_type}">${type}</span>${relation.recommended ? `<span class="recommended-label">Recomendada</span>` : ""}</div>
        <h4>${escapeHtml(relation.label)}</h4>
        <code>${escapeHtml(relation.name)}</code>
        <p>${escapeHtml(relation.description)}</p>
        <div class="relation-stats"><span>${relation.columns.length} colunas</span><span>${escapeHtml(rows)}</span>${relation.size_bytes ? `<span>${formatBytes(relation.size_bytes)}</span>` : ""}</div>
        <button class="secondary compact" type="button" data-preview-relation="${escapeHtml(relation.name)}">Ver estrutura e registros</button>
      </article>`;
    }).join("");
    return `<section class="schema-group"><h4>${escapeHtml(group.label)}</h4><div class="relation-grid">${cards}</div></section>`;
  }).join("");
  schemaCatalog.innerHTML = `${groupsHtml}<p class="schema-note">${escapeHtml(data.hidden_note || "")}</p>`;
}

async function loadDatabaseSchema(force = false) {
  if (explorerSchemaLoaded && !force) return;
  schemaLoading.classList.remove("hidden");
  schemaCatalog.classList.add("hidden");
  try {
    const response = await fetch("/api/explorer/schema");
    if (!response.ok) throw new Error("Não foi possível mapear as tabelas e visões");
    renderDatabaseSchema(await response.json());
    explorerSchemaLoaded = true;
    schemaCatalog.classList.remove("hidden");
    schemaLoading.classList.add("hidden");
  } catch (error) {
    schemaLoading.textContent = error.message;
  }
}

function previewCell(value) {
  if (value === null || value === undefined || value === "") return `<span class="null-value">NULL</span>`;
  const full = typeof value === "object" ? JSON.stringify(value) : String(value);
  const shortened = full.length > 180 ? `${full.slice(0, 177)}…` : full;
  return `<code title="${escapeHtml(full.slice(0, 500))}">${escapeHtml(shortened)}</code>`;
}

function renderRelationPreview(data) {
  const relation = data.relation;
  const columnRows = data.columns.map((column) => `<tr><td><code>${escapeHtml(column.name)}</code></td><td>${escapeHtml(column.type)}</td><td>${column.nullable ? "Pode ficar vazio" : "Obrigatória"}</td></tr>`).join("");
  const headers = data.columns.map((column) => `<th>${escapeHtml(column.name)}</th>`).join("");
  const rows = data.rows.map((row) => `<tr>${data.columns.map((column) => `<td>${previewCell(row[column.name])}</td>`).join("")}</tr>`).join("");
  relationPreview.innerHTML = `<div class="relation-preview-head"><div><span class="eyebrow">${relation.group === "ready" ? "PRONTA PARA CONSULTA" : "ESTRUTURA TÉCNICA"}</span><h3>${escapeHtml(relation.label)}</h3><code>${escapeHtml(relation.name)}</code><p>${escapeHtml(relation.description)}</p></div><button class="secondary compact" type="button" data-close-preview>Fechar prévia</button></div>
    <h4>Dicionário de colunas</h4>
    <div class="table-wrap column-dictionary"><table><thead><tr><th>Coluna</th><th>Tipo PostgreSQL</th><th>Preenchimento</th></tr></thead><tbody>${columnRows}</tbody></table></div>
    <h4>Pré-visualização de ${data.rows.length} registro${data.rows.length === 1 ? "" : "s"}</h4>
    ${rows ? `<div class="table-wrap raw-preview"><table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><strong>Esta relação não possui registros visíveis agora.</strong><p>Uma visão vigente pode ficar vazia enquanto sua carga ainda não foi publicada.</p></div>`}`;
  relationPreview.classList.remove("hidden");
  relationPreview.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadRelationPreview(relationName) {
  relationPreview.innerHTML = `<div class="loading-inline">Abrindo ${escapeHtml(relationName)} em modo somente leitura…</div>`;
  relationPreview.classList.remove("hidden");
  try {
    const response = await fetch(`/api/explorer/relations/${encodeURIComponent(relationName)}/preview?limit=10`);
    if (!response.ok) throw new Error((await response.json()).detail || "Não foi possível abrir a prévia");
    renderRelationPreview(await response.json());
  } catch (error) {
    relationPreview.innerHTML = `<div class="error"><strong>Não foi possível abrir esta relação.</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function referenceLabel(data, kind, code) {
  if (!code) return "—";
  const label = data.references?.[`${kind}:${code}`];
  return label ? `${code} — ${label}` : code;
}

function renderCompanyDetail(data) {
  const core = data.core;
  const company = data.company || {};
  const establishment = data.establishment || {};
  const simples = data.simples;
  const partners = data.partners || [];
  const branchCounts = data.branch_counts || {};
  const cnaes = [core.primary_cnae, ...(core.secondary_cnaes || [])].filter(Boolean);
  const partnersHtml = partners.length ? partners.map((partner) => `<article class="partner-card">
      <strong>${escapeHtml(partner.partner_name || "Não informado")}</strong>
      <span>${escapeHtml(partner.qualification || partner.qualification_code || partner.partner_type || "—")}</span>
      <dl>
        <div><dt>Tipo</dt><dd>${escapeHtml(partner.partner_type || "—")}</dd></div>
        <div><dt>Documento público</dt><dd>${escapeHtml(partner.partner_document || "—")}</dd></div>
        <div><dt>Entrada</dt><dd>${escapeHtml(partner.joined_at || "—")}</dd></div>
        <div><dt>Faixa etária</dt><dd>${escapeHtml(partner.age_range || "—")}</dd></div>
        <div><dt>País</dt><dd>${escapeHtml(partner.country || "—")}</dd></div>
        <div><dt>Representante legal</dt><dd>${escapeHtml(partner.legal_representative_name || "—")}</dd></div>
      </dl>
    </article>`).join("") : `<div class="empty-state"><strong>Nenhum sócio publicado para esta empresa.</strong><p>Algumas naturezas jurídicas não possuem QSA ou a carga complementar ainda está em andamento.</p></div>`;
  const complementaryNote = data.capabilities.simples_mei
    ? "Dados complementares da mesma versão da Receita."
    : "A carga complementar ainda não foi publicada; por enquanto mostramos os dados cadastrais já disponíveis.";
  explorerCompanyResult.innerHTML = `<article class="company-detail">
      <div class="company-detail-head"><div><span class="status ${core.is_active ? "confirmado" : "revisao"}">${escapeHtml(core.registration_status)}</span><h2>${escapeHtml(core.trade_name || core.legal_name || "Empresa")}</h2><strong>${escapeHtml(formatCnpj(core.cnpj))}</strong><p>${escapeHtml(core.legal_name || "—")}</p></div><span>Receita ${escapeHtml(core.dataset_version)}</span></div>
      <p class="search-notice">${escapeHtml(complementaryNote)}</p>
      <h3>Cadastro</h3><dl class="detail-grid">
        <div><dt>Abertura</dt><dd>${escapeHtml(core.opened_at || establishment.opened_at || "—")}</dd></div>
        <div><dt>Porte</dt><dd>${escapeHtml(core.company_size || company.company_size || "—")}</dd></div>
        <div><dt>Capital social</dt><dd>${escapeHtml(formatMoney(core.share_capital ?? company.share_capital))}</dd></div>
        <div><dt>Matriz/filial</dt><dd>${establishment.branch_type_code === "1" ? "Matriz" : establishment.branch_type_code === "2" ? "Filial" : "—"}</dd></div>
        <div><dt>Filiais ativas</dt><dd>${Number(branchCounts.active_branch_count || 0).toLocaleString("pt-BR")}</dd></div>
        <div><dt>Filiais totais</dt><dd>${Number(branchCounts.branch_count || 0).toLocaleString("pt-BR")}</dd></div>
        <div><dt>Natureza jurídica</dt><dd>${escapeHtml(referenceLabel(data, "legal_nature", company.legal_nature_code))}</dd></div>
        <div><dt>Qualificação do responsável</dt><dd>${escapeHtml(referenceLabel(data, "qualification", company.responsible_qualification_code))}</dd></div>
        <div><dt>Endereço</dt><dd>${escapeHtml(core.address || "—")}</dd></div>
        <div><dt>Município/UF/CEP</dt><dd>${escapeHtml(`${core.municipality || "—"}/${core.uf || "—"} · ${core.postal_code || "—"}`)}</dd></div>
      </dl>
      <h3>Atividades</h3><div class="tag-list">${cnaes.length ? cnaes.map((code) => `<span>${escapeHtml(referenceLabel(data, "cnae", code))}</span>`).join("") : "—"}</div>
      <h3>Simples Nacional e MEI</h3><dl class="detail-grid">
        <div><dt>Optante pelo Simples</dt><dd>${simples ? yesNo(simples.is_simples) : "Aguardando carga"}</dd></div>
        <div><dt>Início / exclusão</dt><dd>${simples ? `${simples.simples_started_at || "—"} / ${simples.simples_ended_at || "—"}` : "—"}</dd></div>
        <div><dt>MEI</dt><dd>${simples ? yesNo(simples.is_mei) : "Aguardando carga"}</dd></div>
        <div><dt>Início / exclusão MEI</dt><dd>${simples ? `${simples.mei_started_at || "—"} / ${simples.mei_ended_at || "—"}` : "—"}</dd></div>
      </dl>
      <h3>Contatos públicos</h3><dl class="detail-grid">
        <div><dt>E-mail</dt><dd>${escapeHtml(establishment.email || "—")}</dd></div>
        <div><dt>Telefone 1</dt><dd>${escapeHtml([establishment.phone1_area_code, establishment.phone1].filter(Boolean).join(" ") || "—")}</dd></div>
        <div><dt>Telefone 2</dt><dd>${escapeHtml([establishment.phone2_area_code, establishment.phone2].filter(Boolean).join(" ") || "—")}</dd></div>
        <div><dt>Situação especial</dt><dd>${escapeHtml(establishment.special_status || "—")}</dd></div>
      </dl>
      <h3>Sócios e administradores (${partners.length})</h3><div class="partners-list">${partnersHtml}</div>
    </article>`;
}

explorerCnpjForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  explorerCompanyResult.classList.add("hidden");
  explorerCompanyLoading.classList.remove("hidden");
  try {
    const cnpj = document.querySelector("#explorer-cnpj").value.toUpperCase().replace(/[^0-9A-Z]/g, "");
    const response = await fetch(`/api/explorer/companies/${encodeURIComponent(cnpj)}`);
    if (!response.ok) throw new Error((await response.json()).detail || "Não foi possível abrir a empresa");
    renderCompanyDetail(await response.json());
  } catch (error) {
    explorerCompanyResult.innerHTML = `<div class="error explorer-card"><strong>Não foi possível abrir a empresa.</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    explorerCompanyLoading.classList.add("hidden");
    explorerCompanyResult.classList.remove("hidden");
  }
});

function renderCompanyEstablishments(data) {
  const rows = data.establishments.map((company) => `<tr>
    <td>${company.branch_type_code === "1" ? "Matriz" : company.branch_type_code === "2" ? "Filial" : "Mesmo CNPJ-base"}</td>
    <td><button class="table-link" type="button" data-company-cnpj="${escapeHtml(company.cnpj)}">${escapeHtml(formatCnpj(company.cnpj))}</button></td>
    <td>${escapeHtml(company.trade_name || company.legal_name || "—")}</td>
    <td>${escapeHtml(company.municipality || "—")}/${escapeHtml(company.uf || "—")}</td>
    <td>${escapeHtml(company.registration_status || "—")}</td>
    <td>${escapeHtml(company.primary_cnae || "—")}</td>
  </tr>`).join("");
  const branchNote = data.branch_type_available
    ? "A classificação matriz/filial vem do arquivo oficial de estabelecimentos da Receita."
    : "A relação pelo CNPJ-base já é exata. A etiqueta matriz/filial aparecerá quando o arquivo complementar de estabelecimentos terminar.";
  explorerEstablishmentsResult.innerHTML = `<article class="company-detail">
    <div class="company-detail-head"><div><span class="eyebrow">MESMA PESSOA JURÍDICA</span><h2>Matriz e filiais</h2><p>CNPJ-base ${escapeHtml(data.cnpj_root)}</p></div><strong>${Number(data.active_branch_count || 0).toLocaleString("pt-BR")} filiais ativas · ${Number(data.branch_count || 0).toLocaleString("pt-BR")} totais</strong></div>
    <p class="search-notice">${escapeHtml(branchNote)} Isso identifica filiais da mesma empresa; não identifica franqueados independentes ou um grupo econômico.</p>
    ${data.has_more ? `<p class="search-notice">A lista foi limitada aos primeiros 10.000 CNPJs.</p>` : ""}
    <div class="table-wrap"><table><thead><tr><th>Relação</th><th>CNPJ</th><th>Nome</th><th>Município/UF</th><th>Situação</th><th>CNAE</th></tr></thead><tbody>${rows}</tbody></table></div>
  </article>`;
}

async function loadCompanyEstablishments() {
  explorerEstablishmentsResult.classList.add("hidden");
  explorerEstablishmentsLoading.classList.remove("hidden");
  try {
    const cnpj = document.querySelector("#explorer-cnpj").value.toUpperCase().replace(/[^0-9A-Z]/g, "");
    if (cnpj.length !== 14) throw new Error("Informe um CNPJ completo com 14 caracteres.");
    const response = await fetch(`/api/explorer/companies/${encodeURIComponent(cnpj)}/establishments`);
    if (!response.ok) throw new Error((await response.json()).detail || "Não foi possível localizar matriz e filiais");
    renderCompanyEstablishments(await response.json());
  } catch (error) {
    explorerEstablishmentsResult.innerHTML = `<div class="error explorer-card"><strong>Não foi possível abrir a relação.</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    explorerEstablishmentsLoading.classList.add("hidden");
    explorerEstablishmentsResult.classList.remove("hidden");
  }
}

document.querySelector("#explorer-establishments").addEventListener("click", loadCompanyEstablishments);

function parseCnpjList(value) {
  return value.split(/[\n,;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function downloadBulkCnpjLookup() {
  const inputs = lastBulkCnpjLookup.map((item) => item.input);
  const foundCnpjs = lastBulkCnpjLookup.filter((item) => item.company).map((item) => item.company.cnpj);
  const estimate = await estimateCredits(foundCnpjs);
  if (!confirmCreditUse(estimate, "baixar este resultado")) return;
  await downloadCsvResponse("/api/exports/cnpj-lookup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cnpjs: inputs }),
  }, "consulta-cnpjs-echopjs.csv");
}

function renderBulkCnpjLookup(data) {
  lastBulkCnpjLookup = data.results;
  const preview = data.results.slice(0, 100);
  const rows = preview.map((item) => {
    const company = item.company;
    if (!company) return `<tr><td>${escapeHtml(item.input)}</td><td colspan="8"><span class="status ${item.status === "invalid" ? "nao_encontrado" : "revisao"}">${item.status === "invalid" ? "CNPJ inválido" : "Não encontrado"}</span></td></tr>`;
    return `<tr>
      <td>${escapeHtml(item.input)}</td>
      <td><button class="table-link" type="button" data-company-cnpj="${escapeHtml(company.cnpj)}">${escapeHtml(formatCnpj(company.cnpj))}</button></td>
      <td>${escapeHtml(company.legal_name || company.trade_name || "—")}</td>
      <td>${escapeHtml(company.registration_status || "—")}</td>
      <td>${yesNo(company.is_simples)}</td><td>${yesNo(company.is_mei)}</td>
      <td>${Number(company.active_branch_count || 0).toLocaleString("pt-BR")}</td>
      <td>${Number(company.branch_count || 0).toLocaleString("pt-BR")}</td>
      <td>${Number(company.partner_count || 0).toLocaleString("pt-BR")}</td>
    </tr>`;
  }).join("");
  bulkCnpjResult.innerHTML = `<article class="company-detail">
    <div class="search-summary">
      <div><strong>${data.found.toLocaleString("pt-BR")}</strong><span>Encontrados</span></div>
      <div><strong>${data.not_found.toLocaleString("pt-BR")}</strong><span>Não encontrados</span></div>
      <div><strong>${data.invalid.toLocaleString("pt-BR")}</strong><span>Inválidos</span></div>
    </div>
    <p class="search-notice">Consulta concluída em ${(data.timing_ms / 1000).toFixed(1)}s. A prévia mostra os primeiros ${Math.min(100, data.total)}; o CSV preserva toda a lista e sua ordem.</p>
    <div class="table-wrap"><table><thead><tr><th>Informado</th><th>CNPJ Receita</th><th>Razão social</th><th>Situação</th><th>Simples</th><th>MEI</th><th>Filiais ativas</th><th>Filiais totais</th><th>Sócios</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="save-actions"><button id="download-bulk-cnpj" data-capability="export" type="button">Baixar resultado completo com sócios</button></div>
  </article>`;
  const downloadButton = document.querySelector("#download-bulk-cnpj");
  downloadButton.addEventListener("click", () => runButtonAction(downloadButton, "Preparando CSV…", downloadBulkCnpjLookup));
}

bulkCnpjForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  bulkCnpjResult.classList.add("hidden");
  bulkCnpjLoading.classList.remove("hidden");
  try {
    const cnpjs = parseCnpjList(document.querySelector("#bulk-cnpj-list").value);
    if (!cnpjs.length) throw new Error("Cole pelo menos um CNPJ.");
    if (cnpjs.length > 10000) throw new Error("O limite é de 10.000 CNPJs por consulta.");
    const response = await fetch("/api/explorer/company-lookup", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cnpjs }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Não foi possível consultar a lista");
    renderBulkCnpjLookup(await response.json());
  } catch (error) {
    bulkCnpjResult.innerHTML = `<div class="error explorer-card"><strong>Não foi possível consultar a lista.</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    bulkCnpjLoading.classList.add("hidden");
    bulkCnpjResult.classList.remove("hidden");
  }
});

function companySearchViewResults() {
  if (activeCompanySearchView === "new") return lastCompanySearch.filter((company) => !company.saved);
  if (activeCompanySearchView === "saved") return lastCompanySearch.filter((company) => company.saved);
  return lastCompanySearch;
}

function updateCompanySearchTabs(data) {
  const segments = data.segments || {
    total: data.results.length,
    new: data.results.filter((company) => !company.saved).length,
    saved: data.results.filter((company) => company.saved).length,
  };
  document.querySelectorAll("[data-search-view-count]").forEach((element) => {
    const value = Number(segments[element.dataset.searchViewCount] || 0);
    element.textContent = value.toLocaleString("pt-BR");
  });
  document.querySelectorAll("[data-search-view]").forEach((button) => {
    const active = button.dataset.searchView === activeCompanySearchView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function savedListsMarkup(company) {
  const lists = company.saved_lists || [];
  if (!lists.length) return '<span class="empty-list-cell" aria-label="Não salva em nenhuma lista"></span>';
  return `<span class="company-list-tags">${lists.map((list) => `<span title="${escapeHtml(list.name)}">${escapeHtml(list.name)}</span>`).join("")}</span>`;
}

function renderCompanySearchView() {
  if (!lastCompanySearchData) return;
  const data = lastCompanySearchData;
  const visible = companySearchViewResults().slice(0, 100);
  const rows = visible.map((company) => `<tr>
    <td><input class="row-selector" type="checkbox" data-select-company="${escapeHtml(company.cnpj)}" aria-label="Selecionar ${escapeHtml(company.legal_name || company.cnpj)}" ${selectedCompanyCnpjs.has(company.cnpj) ? "checked" : ""}></td>
    <td><button class="table-link" type="button" data-company-cnpj="${escapeHtml(company.cnpj)}">${escapeHtml(formatCnpj(company.cnpj))}</button></td>
    <td><span class="company-name-cell"><strong>${escapeHtml(company.legal_name || company.trade_name || "—")}</strong>${company.trade_name && company.trade_name !== company.legal_name ? `<small>${escapeHtml(company.trade_name)}</small>` : ""}</span></td>
    <td><span class="company-cnae-cell"><strong>${escapeHtml(company.primary_cnae || "—")}</strong><small>${escapeHtml(company.primary_cnae_description || "")}</small></span></td>
    <td>${escapeHtml(company.municipality || "—")}/${escapeHtml(company.uf || "—")}</td>
    <td>${escapeHtml(companySizeLabel(company.company_size))}</td>
    <td>${savedListsMarkup(company)}</td>
  </tr>`).join("");
  const viewLabels = { total: "nesta prévia", new: "que ainda não estão em listas", saved: "já salvas em listas" };
  const loadedLabel = `${data.returned.toLocaleString("pt-BR")}${data.has_more ? "+" : ""}`;
  companySearchResult.innerHTML = `<div class="search-result-context"><span><strong>${loadedLabel}</strong> empresas carregadas</span><span>${(data.timing_ms / 1000).toFixed(1)}s · Receita ${escapeHtml(data.dataset_version || "—")}</span></div>
    ${visible.length ? `<div class="preview-toolbar"><div><button id="select-preview" class="secondary compact" type="button">Selecionar visíveis</button><button id="clear-preview-selection" class="secondary compact" type="button">Limpar seleção</button></div>${data.preview ? "" : `<button id="download-company-search" class="secondary compact" data-capability="export" type="button">Baixar todas (${data.returned.toLocaleString("pt-BR")})</button>`}</div>
    <div id="selection-action-bar" class="selection-action-bar" aria-label="Ações das empresas selecionadas">
      <div class="selection-action-copy"><strong id="selection-count">0 selecionadas</strong><small id="selection-credit-summary" aria-live="polite">Selecione empresas para salvar em uma lista ou baixar o CSV.</small></div>
      <div class="selection-action-buttons"><button id="save-selected-company-search" data-capability="manage-library" type="button" disabled>Salvar em uma lista</button><button id="download-selected-company-search" class="secondary" data-capability="export" type="button" disabled>Baixar selecionadas</button></div>
    </div>
    <div class="table-wrap search-results-table"><table><thead><tr><th>Selecionar</th><th>CNPJ</th><th>Empresa</th><th>CNAE principal</th><th>Município/UF</th><th>Porte</th><th>Listas</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="search-notice">Mostrando ${visible.length.toLocaleString("pt-BR")} empresa${visible.length === 1 ? "" : "s"} ${viewLabels[activeCompanySearchView]}. Nada é salvo automaticamente.</p>
    ` : `<div class="empty-state"><strong>Nenhuma empresa ${activeCompanySearchView === "saved" ? "salva" : activeCompanySearchView === "new" ? "nova" : "encontrada"} nesta prévia.</strong><p>${activeCompanySearchView === "total" ? "Altere ou remova algum filtro e tente novamente." : "Escolha outra categoria ou ajuste os filtros."}</p></div>`}`;
  document.querySelector("#save-selected-company-search")?.addEventListener("click", () => openSaveListDialog().catch((error) => showToast(error.message)));
  const downloadAllButton = document.querySelector("#download-company-search");
  downloadAllButton?.addEventListener("click", () => runButtonAction(downloadAllButton, "Preparando CSV…", () => downloadCompanySearch(lastCompanySearch)));
  const downloadSelectedButton = document.querySelector("#download-selected-company-search");
  downloadSelectedButton?.addEventListener("click", () => runButtonAction(downloadSelectedButton, "Preparando CSV…", () => (
    downloadCompanySearch(lastCompanySearch.filter((company) => selectedCompanyCnpjs.has(company.cnpj)), "empresas-selecionadas.csv")
  )));
  document.querySelector("#select-preview")?.addEventListener("click", () => {
    visible.forEach((company) => selectedCompanyCnpjs.add(company.cnpj));
    companySearchResult.querySelectorAll("[data-select-company]").forEach((checkbox) => { checkbox.checked = true; });
    updateSearchSelection();
  });
  document.querySelector("#clear-preview-selection")?.addEventListener("click", () => {
    selectedCompanyCnpjs.clear();
    companySearchResult.querySelectorAll("[data-select-company]").forEach((checkbox) => { checkbox.checked = false; });
    updateSearchSelection();
  });
  updateSearchSelection();
}

function renderCompanySearch(data) {
  lastCompanySearch = data.results;
  lastCompanySearchData = data;
  selectedCompanyCnpjs = new Set();
  updateCompanySearchTabs(data);
  renderCompanySearchView();
}

function updateSearchSelection() {
  const count = selectedCompanyCnpjs.size;
  const label = document.querySelector("#selection-count");
  const creditSummary = document.querySelector("#selection-credit-summary");
  const button = document.querySelector("#download-selected-company-search");
  const saveButton = document.querySelector("#save-selected-company-search");
  if (label) label.textContent = `${count.toLocaleString("pt-BR")} selecionada${count === 1 ? "" : "s"}`;
  if (button) button.disabled = count === 0;
  if (saveButton) saveButton.disabled = count === 0;
  if (button) button.textContent = count ? `Baixar ${count.toLocaleString("pt-BR")} selecionada${count === 1 ? "" : "s"}` : "Baixar selecionadas";
  if (saveButton) saveButton.textContent = count ? `Salvar ${count.toLocaleString("pt-BR")} em lista` : "Salvar em uma lista";

  clearTimeout(searchSelectionEstimateTimer);
  const requestId = ++searchSelectionEstimateRequest;
  if (!creditSummary) return;
  creditSummary.classList.remove("insufficient");
  if (!count) {
    creditSummary.textContent = "Selecione empresas para salvar em uma lista ou baixar o CSV.";
    return;
  }
  creditSummary.textContent = "Calculando o consumo de créditos…";
  const cnpjs = lastCompanySearch.filter((company) => selectedCompanyCnpjs.has(company.cnpj)).map((company) => company.cnpj);
  searchSelectionEstimateTimer = setTimeout(async () => {
    try {
      const estimate = await estimateCredits(cnpjs);
      if (requestId !== searchSelectionEstimateRequest || !creditSummary.isConnected) return;
      if (estimate.unlimited_credits) {
        creditSummary.textContent = "Créditos ilimitados neste workspace.";
      } else if (!estimate.credits_required) {
        creditSummary.textContent = "Todos os CNPJs selecionados já estão desbloqueados. Nenhum crédito será usado.";
      } else {
        const repeated = estimate.already_unlocked
          ? ` · ${estimate.already_unlocked.toLocaleString("pt-BR")} já desbloqueada${estimate.already_unlocked === 1 ? "" : "s"}`
          : "";
        creditSummary.textContent = `${estimate.credits_required.toLocaleString("pt-BR")} crédito${estimate.credits_required === 1 ? " será usado" : "s serão usados"}${repeated}.`;
        creditSummary.classList.toggle("insufficient", !estimate.can_complete);
      }
    } catch (_) {
      if (requestId === searchSelectionEstimateRequest && creditSummary.isConnected) {
        creditSummary.textContent = "O consumo será confirmado antes de concluir a ação.";
      }
    }
  }, 220);
}

async function downloadCompanySearch(companies, filename = "empresas-echopjs.csv") {
  const cnpjs = companies.map((company) => company.cnpj);
  const estimate = await estimateCredits(cnpjs);
  if (!confirmCreditUse(estimate, "baixar estas empresas")) return;
  await downloadCsvResponse("/api/exports/companies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cnpjs }),
  }, filename);
}

function hasMeaningfulCompanyFilters(payload) {
  return Boolean(
    payload.cnaes.length || (payload.company_name && payload.company_name.trim().length >= 3) || payload.excluded_company_names.length
    || payload.company_sizes.length || payload.share_capital_min !== null || payload.share_capital_max !== null
    || payload.opened_from || payload.opened_to || payload.regions.length || payload.ufs.length
    || payload.municipalities.length || payload.postal_code_prefixes.length || payload.partner_age_ranges.length
    || payload.simples !== null || payload.mei !== null || payload.legal_nature_code || payload.branch_type
    || payload.has_email !== null || payload.has_phone !== null || payload.active_branch_count_min !== null
    || payload.active_branch_count_max !== null
    || payload.registration_statuses.length !== 1 || payload.registration_statuses[0] !== "ATIVA"
  );
}

function setSearchPreviewStatus(label, state = "idle") {
  const status = document.querySelector("#search-preview-status");
  status.className = `preview-status ${state}`;
  status.innerHTML = `<i aria-hidden="true"></i>${escapeHtml(label)}`;
}

async function runCompanySearch({ preview = false, scroll = false } = {}) {
  const submitButton = companySearchForm.querySelector('button[type="submit"]');
  const originalSubmitLabel = submitButton.textContent;
  let searchProgressTimer = null;
  const requestNumber = ++searchPreviewRequest;
  if (searchPreviewController) searchPreviewController.abort();
  searchPreviewController = new AbortController();
  companySearchLoading.innerHTML = `<span class="spinner" aria-hidden="true"></span><span><strong>${preview ? "Atualizando a prévia…" : "Consultando a base da Receita…"}</strong><small>Aplicando os filtros aos dados publicados.</small></span>`;
  companySearchLoading.classList.remove("hidden");
  companySearchForm.setAttribute("aria-busy", "true");
  setSearchPreviewStatus(preview ? "Atualizando" : "Gerando lista", "loading");
  if (!preview) {
    submitButton.disabled = true;
    submitButton.textContent = "Buscando…";
  }
  searchProgressTimer = setTimeout(() => {
    const detail = companySearchLoading.querySelector("small");
    if (detail) detail.textContent = "Filtros amplos podem levar alguns segundos. A busca continua normalmente.";
  }, 3500);
  try {
    const payload = companySearchPayload();
    if (!preview) lastCompanySearchPayload = payload;
    const response = await fetch(preview ? "/api/search/preview" : "/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: searchPreviewController.signal,
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Falha na busca");
    const data = await response.json();
    if (requestNumber !== searchPreviewRequest) return;
    renderCompanySearch(data);
    setSearchPreviewStatus("Prévia atualizada", "ready");
    if (!preview && activeSavedSearchId && JSON.stringify(lastCompanySearchPayload) === activeSavedSearchFilters) {
      fetch(`/api/saved-searches/${activeSavedSearchId}/runs`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ result_count: data.returned }),
      }).catch(() => {});
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    companySearchResult.innerHTML = `<div class="error"><strong>Não foi possível buscar as empresas.</strong><p>${escapeHtml(error.message)}</p></div>`;
    setSearchPreviewStatus("Falha na prévia", "error");
  } finally {
    clearTimeout(searchProgressTimer);
    if (requestNumber !== searchPreviewRequest) return;
    companySearchForm.removeAttribute("aria-busy");
    if (!preview) {
      submitButton.disabled = false;
      submitButton.textContent = originalSubmitLabel;
    }
    companySearchLoading.classList.add("hidden");
    if (scroll) companySearchResult.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function scheduleCompanySearchPreview({ immediate = false } = {}) {
  clearTimeout(searchPreviewTimer);
  const payload = companySearchPayload();
  if (!hasMeaningfulCompanyFilters(payload)) {
    if (searchPreviewController) searchPreviewController.abort();
    setSearchPreviewStatus("Aguardando filtros");
    return;
  }
  setSearchPreviewStatus("Filtros alterados", "pending");
  searchPreviewTimer = setTimeout(() => runCompanySearch({ preview: true }), immediate ? 0 : 650);
}

companySearchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(searchPreviewTimer);
  runCompanySearch({ preview: false, scroll: true });
});

companySearchForm.addEventListener("input", (event) => {
  if (event.target.closest(".multi-picker-search") || event.target.id === "search-limit") return;
  scheduleCompanySearchPreview();
});
companySearchForm.addEventListener("change", (event) => {
  if (event.target.closest(".multi-picker-options") || event.target.id === "search-limit") return;
  scheduleCompanySearchPreview();
});
[cnaePicker, municipalityPicker, regionPicker, ufPicker, statusPicker, sizePicker, partnerAgePicker]
  .forEach((picker) => picker.onChange(() => scheduleCompanySearchPreview()));

document.querySelector("#search-result-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-search-view]");
  if (!button || !lastCompanySearchData) return;
  activeCompanySearchView = button.dataset.searchView;
  updateCompanySearchTabs(lastCompanySearchData);
  renderCompanySearchView();
});

function showToast(message) {
  const current = document.querySelector(".saas-toast");
  current?.remove();
  const toast = document.createElement("div");
  toast.className = "saas-toast";
  toast.setAttribute("role", "status");
  toast.textContent = message;
  document.body.append(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 180);
  }, 3200);
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json();
    return typeof payload.detail === "string" ? payload.detail : fallback;
  } catch (_) {
    return fallback;
  }
}

async function estimateCredits(cnpjs) {
  if (!cnpjs.length) {
    return { requested_companies: 0, already_unlocked: 0, credits_required: 0, credit_balance: 0, unlimited_credits: false, can_complete: true };
  }
  const response = await fetch("/api/credits/estimate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cnpjs }),
  });
  if (!response.ok) throw new Error(await responseError(response, "Não foi possível calcular os créditos."));
  return response.json();
}

function confirmCreditUse(estimate, action) {
  if (!estimate.can_complete) {
    showToast(`Créditos insuficientes: esta ação precisa de ${estimate.credits_required.toLocaleString("pt-BR")} e o saldo é ${estimate.credit_balance.toLocaleString("pt-BR")}.`);
    switchTab("billing");
    return false;
  }
  if (estimate.unlimited_credits || estimate.credits_required === 0) return true;
  const repeated = estimate.already_unlocked
    ? ` ${estimate.already_unlocked.toLocaleString("pt-BR")} já ${estimate.already_unlocked === 1 ? "está desbloqueada" : "estão desbloqueadas"} e não ${estimate.already_unlocked === 1 ? "será cobrada" : "serão cobradas"}.`
    : "";
  return window.confirm(`Para ${action}, serão usados ${estimate.credits_required.toLocaleString("pt-BR")} crédito${estimate.credits_required === 1 ? "" : "s"}.${repeated}`);
}

function filenameFromResponse(response, fallback) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  try { return decodeURIComponent(encoded?.[1] || plain?.[1] || fallback); }
  catch (_) { return plain?.[1] || fallback; }
}

function updateCreditsFromResponse(response) {
  const unlimited = response.headers.get("X-Unlimited-Credits");
  const balance = response.headers.get("X-Credit-Balance");
  if (unlimited === null || balance === null) return;
  updateCreditIndicator({ unlimited_credits: unlimited === "true", credit_balance: Number(balance) });
}

async function downloadCsvResponse(url, init, fallbackFilename) {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(await responseError(response, "Não foi possível gerar o CSV."));
  const blob = await response.blob();
  const link = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  link.href = objectUrl;
  link.download = filenameFromResponse(response, fallbackFilename);
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  updateCreditsFromResponse(response);
  const spent = Number(response.headers.get("X-Credits-Spent") || 0);
  showToast(spent ? `CSV gerado. ${spent.toLocaleString("pt-BR")} crédito${spent === 1 ? " usado" : "s usados"}.` : "CSV gerado sem novo consumo de créditos.");
}

async function runButtonAction(button, busyLabel, action) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyLabel;
  try { await action(); }
  catch (error) { showToast(error.message); }
  finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function setDialogFeedback(element, message = "") {
  element.textContent = message;
  element.classList.toggle("hidden", !message);
}

function updateCreditIndicator(profile) {
  const value = document.querySelector("#credit-indicator strong");
  if (!value) return;
  value.textContent = profile.unlimited_credits
    ? "Ilimitados"
    : `${Number(profile.credit_balance).toLocaleString("pt-BR")} disponíveis`;
}

async function loadSaaSOverview() {
  saasOverviewLoading.classList.remove("hidden");
  saasOverview.classList.add("hidden");
  try {
    const response = await fetch("/api/dashboard");
    if (!response.ok) throw new Error(await responseError(response, "Não foi possível carregar o workspace."));
    const data = await response.json();
    updateCreditIndicator(data.profile);
    const onboardingSteps = [
      { complete: data.saved_search_count > 0, title: "Salve seu primeiro segmento", description: "Monte os filtros e guarde os critérios para repetir a busca.", tab: "search", action: "Criar busca" },
      { complete: data.list_count > 0, title: "Organize empresas em uma lista", description: "Separe campanhas, territórios ou prioridades da equipe.", tab: "lists", action: "Criar lista" },
      { complete: data.onboarding.member_count > 1 || data.onboarding.pending_invitation_count > 0, title: "Convide alguém da equipe", description: "Cada pessoa acessa com senha própria e permissões controladas.", href: `/organizations?organization=${data.organization_id}`, action: "Convidar pessoa" },
    ];
    const completedOnboarding = onboardingSteps.filter((step) => step.complete).length;
    const onboarding = completedOnboarding === onboardingSteps.length ? "" : `<section class="onboarding-card section-card">
      <div class="onboarding-heading"><div><span class="eyebrow">PRIMEIROS PASSOS</span><h2>Prepare seu workspace</h2><p>Conclua o essencial para sua equipe começar com contexto e organização.</p></div><div class="onboarding-progress"><strong>${completedOnboarding} de ${onboardingSteps.length}</strong><span><i style="width:${Math.round(completedOnboarding / onboardingSteps.length * 100)}%"></i></span></div></div>
      <div class="onboarding-steps">${onboardingSteps.map((step, index) => `<article class="onboarding-step ${step.complete ? "complete" : ""}"><span class="onboarding-number">${step.complete ? "✓" : index + 1}</span><div><strong>${escapeHtml(step.title)}</strong><small>${escapeHtml(step.description)}</small></div>${step.complete ? `<span class="onboarding-done">Concluído</span>` : step.href ? `<a href="${step.href}">${escapeHtml(step.action)}</a>` : `<button type="button" data-switch-tab="${step.tab}">${escapeHtml(step.action)}</button>`}</article>`).join("")}</div>
    </section>`;
    const recentLists = data.recent_lists.length ? data.recent_lists.map((item) => `<button class="workspace-row" type="button" data-dashboard-list="${item.id}"><span><strong>${escapeHtml(item.name)}</strong><small>${Number(item.company_count).toLocaleString("pt-BR")} empresa${item.company_count === 1 ? "" : "s"}</small></span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg></button>`).join("") : `<div class="workspace-empty"><span>Nenhuma lista ainda</span><button type="button" data-switch-tab="search">Encontrar empresas</button></div>`;
    const recentSearches = data.recent_searches.length ? data.recent_searches.map((saved) => `<button class="workspace-row" type="button" data-dashboard-search="${saved.id}"><span><strong>${escapeHtml(saved.name)}</strong><small>${escapeHtml(filtersDescription(saved.filters))}</small></span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg></button>`).join("") : `<div class="workspace-empty"><span>Nenhuma busca salva</span><button type="button" data-switch-tab="search">Criar uma busca</button></div>`;
    const usage = data.usage || {};
    const usageDays = Array.isArray(usage.daily) ? usage.daily : [];
    const unlockedInPeriod = Number(usage.unlocked_companies || 0);
    const creditsSpentInPeriod = Number(usage.credits_spent || 0);
    const activeDays = Number(usage.active_days || 0);
    const usagePeak = Math.max(1, ...usageDays.map((day) => Number(day.unlocked_companies || 0)));
    const usageBars = usageDays.map((day) => {
      const unlocked = Number(day.unlocked_companies || 0);
      const height = unlocked ? Math.max(12, Math.round(unlocked / usagePeak * 100)) : 3;
      const label = `${formatBillingDate(day.date)}: ${unlocked.toLocaleString("pt-BR")} empresa${unlocked === 1 ? "" : "s"} desbloqueada${unlocked === 1 ? "" : "s"}`;
      return `<span class="usage-bar${unlocked ? " has-usage" : ""}" title="${label}" aria-label="${label}"><i style="height:${height}%"></i></span>`;
    }).join("");
    let usageMessage = "As buscas, listas e exportações da equipe aparecerão aqui conforme o workspace for usado.";
    if (activeDays && !unlockedInPeriod) usageMessage = `Sua equipe esteve ativa em ${activeDays.toLocaleString("pt-BR")} dia${activeDays === 1 ? "" : "s"}, sem novos desbloqueios no período.`;
    if (unlockedInPeriod) {
      const averageValue = unlockedInPeriod / Math.max(1, activeDays);
      const average = averageValue.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
      usageMessage = data.profile.unlimited_credits
        ? `Média de ${average} empresa${averageValue === 1 ? "" : "s"} por dia ativo. O uso interno da EchoHub não desconta créditos.`
        : `Média de ${average} empresa${averageValue === 1 ? "" : "s"} por dia ativo e ${creditsSpentInPeriod.toLocaleString("pt-BR")} crédito${creditsSpentInPeriod === 1 ? "" : "s"} usado${creditsSpentInPeriod === 1 ? "" : "s"}.`;
    }
    const usageCard = `<section class="usage-card section-card"><div class="workspace-usage-head"><div><span class="eyebrow">RITMO DA EQUIPE</span><h2>Uso nos últimos 30 dias</h2><p>${usageMessage}</p></div><span class="usage-period">${formatBillingDate(usage.start_date)} — ${formatBillingDate(usage.end_date)}</span></div>
      <div class="usage-kpis"><article><span>Empresas desbloqueadas</span><strong>${unlockedInPeriod.toLocaleString("pt-BR")}</strong></article><article><span>Dias com atividade</span><strong>${activeDays.toLocaleString("pt-BR")}</strong></article><article><span>Créditos usados</span><strong>${data.profile.unlimited_credits ? "Sem cobrança" : creditsSpentInPeriod.toLocaleString("pt-BR")}</strong></article></div>
      <div class="usage-chart-wrap"><div class="usage-chart" role="img" aria-label="Empresas desbloqueadas por dia nos últimos 30 dias">${usageBars}</div><div class="usage-chart-labels"><span>${formatBillingDate(usage.start_date)}</span><span>Empresas desbloqueadas por dia</span><span>${formatBillingDate(usage.end_date)}</span></div></div>
    </section>`;
    saasOverview.dataset.searches = JSON.stringify(data.recent_searches);
    saasOverview.innerHTML = `${onboarding}<div class="workspace-metrics">
      <button type="button" data-switch-tab="billing"><span>Créditos</span><strong>${data.profile.unlimited_credits ? "Ilimitados" : Number(data.profile.credit_balance).toLocaleString("pt-BR")}</strong><small>${data.profile.unlimited_credits ? "Plano interno EchoHub" : "Saldo compartilhado"}</small></button>
      <button type="button" data-switch-tab="lists"><span>Empresas desbloqueadas</span><strong>${Number(data.unlocked_companies).toLocaleString("pt-BR")}</strong><small>Disponíveis sem nova cobrança</small></button>
      <button type="button" data-switch-tab="lists"><span>Listas</span><strong>${Number(data.list_count).toLocaleString("pt-BR")}</strong><small>Organizadas pela equipe</small></button>
      <button type="button" data-switch-tab="saved-searches"><span>Buscas salvas</span><strong>${Number(data.saved_search_count).toLocaleString("pt-BR")}</strong><small>Segmentos reutilizáveis</small></button>
    </div>
    ${data.active_jobs ? `<button class="active-jobs-banner" type="button" data-switch-tab="history"><span class="spinner" aria-hidden="true"></span><span><strong>${data.active_jobs} processamento${data.active_jobs === 1 ? "" : "s"} em andamento</strong><small>Acompanhe o progresso e baixe os resultados quando quiser.</small></span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg></button>` : ""}
    ${usageCard}
    <div class="workspace-columns"><section class="workspace-feed section-card"><div class="workspace-feed-head"><div><span class="eyebrow">LISTAS RECENTES</span><h2>Empresas organizadas</h2></div><button class="secondary compact" type="button" data-switch-tab="lists">Ver todas</button></div>${recentLists}</section><section class="workspace-feed section-card"><div class="workspace-feed-head"><div><span class="eyebrow">BUSCAS RECENTES</span><h2>Segmentos da equipe</h2></div><button class="secondary compact" type="button" data-switch-tab="saved-searches">Ver todas</button></div>${recentSearches}</section></div>`;
    saasOverviewLoading.classList.add("hidden");
    saasOverview.classList.remove("hidden");
  } catch (error) {
    saasOverviewLoading.textContent = error.message;
  }
}

async function loadBillingSummary() {
  billingLoading.classList.remove("hidden");
  billingSummary.classList.add("hidden");
  try {
    const [summaryResponse, catalogResponse, workspace] = await Promise.all([
      fetch("/api/billing/summary"), fetch("/api/billing/catalog"), window.echoWorkspace,
    ]);
    if (!summaryResponse.ok) throw new Error(await responseError(summaryResponse, "Não foi possível carregar os créditos."));
    if (!catalogResponse.ok) throw new Error(await responseError(catalogResponse, "Não foi possível carregar os planos."));
    const data = await summaryResponse.json();
    const catalog = await catalogResponse.json();
    const profile = data.profile;
    updateCreditIndicator(profile);
    const subscription = data.subscription;
    const planName = profile.plan_code === "internal" ? "EchoHub interno" : profile.plan_code === "trial" ? "Avaliação" : subscription?.plan_name || profile.plan_code;
    const subscriptionLabels = { trialing: "Avaliação", active: "Ativa", past_due: "Pagamento pendente", inactive: "Inativa", canceled: "Cancelada", suspended: "Suspensa", pending: "Aguardando pagamento" };
    const ledger = data.ledger.length ? data.ledger.map((entry) => `<tr>
      <td>${formatDate(entry.created_at)}</td><td>${escapeHtml(entry.description)}</td>
      <td class="ledger-value ${entry.delta > 0 ? "positive" : entry.delta < 0 ? "negative" : ""}">${entry.delta === 0 ? "—" : `${entry.delta > 0 ? "+" : ""}${Number(entry.delta).toLocaleString("pt-BR")}`}</td>
    </tr>`).join("") : `<tr><td colspan="3">Nenhuma movimentação de créditos.</td></tr>`;
    const orderLabels = { creating: "Criando checkout", pending: "Aguardando pagamento", checkout_paid: "Confirmando pagamento", paid: "Pago", failed: "Falhou", expired: "Expirado", canceled: "Cancelado", past_due: "Pagamento pendente", needs_review: "Em revisão" };
    const orders = data.orders?.length ? `<section class="section-card billing-orders"><div class="section-card-heading"><div><span class="eyebrow">PAGAMENTOS</span><h2>Pedidos recentes</h2><p>Acompanhe a confirmação feita pelo provedor.</p></div></div><div class="billing-order-list">${data.orders.map((order) => `<article><span class="billing-order-status status-${escapeHtml(order.status)}">${escapeHtml(orderLabels[order.status] || order.status)}</span><div><strong>${escapeHtml(order.plan_name)}</strong><small>${formatDate(order.created_at)} · ${(Number(order.price_cents) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })}</small></div>${order.checkout_url && ["pending", "creating"].includes(order.status) ? `<a href="${escapeHtml(order.checkout_url)}" rel="noopener">Continuar pagamento</a>` : ""}</article>`).join("")}</div></section>` : "";
    const canManagePlan = workspace?.organization?.role === "admin";
    const cancellationPending = data.cancellation?.status === "pending";
    const canCancelSubscription = subscription && ["active", "past_due", "inactive"].includes(subscription.status) && catalog.enabled && canManagePlan;
    let billingAttention = "";
    if (subscription?.status === "past_due") {
      billingAttention = `<section class="billing-attention danger" role="status"><span class="billing-attention-mark" aria-hidden="true">!</span><div><strong>Pagamento pendente</strong><p>A renovação ainda não foi confirmada. Consulte os pagamentos abaixo e regularize a cobrança para manter os próximos ciclos.</p></div><span class="billing-attention-tag">AÇÃO NECESSÁRIA</span></section>`;
    } else if (subscription?.status === "active" && subscription.next_due_date) {
      const dueMatch = String(subscription.next_due_date).match(/^(\d{4})-(\d{2})-(\d{2})/);
      const dueParts = dueMatch ? dueMatch.slice(1).map(Number) : [];
      const today = new Date();
      const dueTime = dueParts.length === 3 && dueParts.every(Number.isFinite) ? Date.UTC(dueParts[0], dueParts[1] - 1, dueParts[2]) : NaN;
      const todayTime = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
      const daysUntilDue = Number.isFinite(dueTime) ? Math.round((dueTime - todayTime) / 86400000) : null;
      if (daysUntilDue !== null && daysUntilDue >= 0 && daysUntilDue <= 7) {
        const renewalWhen = daysUntilDue === 0 ? "hoje" : daysUntilDue === 1 ? "amanhã" : `em ${daysUntilDue} dias`;
        billingAttention = `<section class="billing-attention warning" role="status"><span class="billing-attention-mark" aria-hidden="true">↻</span><div><strong>Sua assinatura renova ${renewalWhen}</strong><p>A cobrança está prevista para ${formatBillingDate(subscription.next_due_date)}. Confira o histórico financeiro após o processamento.</p></div><span class="billing-attention-tag">RENOVAÇÃO</span></section>`;
      }
    }
    const subscriptionCard = subscription ? `<section class="section-card billing-subscription"><div><span class="eyebrow">ASSINATURA</span><h2>${escapeHtml(subscription.plan_name)}</h2><p>${Number(subscription.credits_per_cycle).toLocaleString("pt-BR")} créditos por ciclo · ${subscription.cycle === "MONTHLY" ? "mensal" : escapeHtml(subscription.cycle)}</p></div><div class="billing-subscription-state"><span class="billing-order-status status-${escapeHtml(subscription.status)}">${escapeHtml(subscriptionLabels[subscription.status] || subscription.status)}</span>${subscription.next_due_date && ["active", "past_due"].includes(subscription.status) ? `<small>Próxima cobrança: ${formatBillingDate(subscription.next_due_date)}</small>` : subscription.status === "canceled" ? `<small>Para reativar, escolha um plano abaixo.</small>` : ""}</div>${canCancelSubscription ? `<button class="secondary danger-button" data-cancel-subscription type="button" ${cancellationPending ? "disabled" : ""}>${cancellationPending ? "Cancelamento em andamento" : "Cancelar renovação"}</button>` : ""}</section>` : "";
    const paymentLabels = { pending: "Aguardando", confirmed: "Confirmado", received: "Recebido", overdue: "Em atraso", failed: "Falhou", canceled: "Cancelado", refund_pending: "Estorno em andamento", refunded: "Estornado", chargeback: "Contestado", review: "Em revisão" };
    const cyclePayments = data.payments?.length ? `<section class="section-card billing-orders"><div class="section-card-heading"><div><span class="eyebrow">CICLOS E RECARGAS</span><h2>Histórico financeiro</h2><p>Cada cobrança fica registrada separadamente, inclusive renovações.</p></div></div><div class="billing-order-list">${data.payments.map((payment) => {
      const paid = ["confirmed", "received"].includes(payment.status);
      const documentUrl = paid && payment.receipt_url ? payment.receipt_url : payment.invoice_url;
      const documentLabel = paid && payment.receipt_url ? "Ver comprovante" : paid ? "Ver cobrança" : "Abrir cobrança";
      return `<article><span class="billing-order-status status-${escapeHtml(payment.status)}">${escapeHtml(paymentLabels[payment.status] || payment.status)}</span><div><strong>${escapeHtml(payment.plan_name)}</strong><small>${payment.due_date ? `Vencimento ${formatBillingDate(payment.due_date)}` : formatDate(payment.updated_at)} · ${payment.amount_cents === null ? "Valor em conferência" : (Number(payment.amount_cents) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })}${payment.billing_type ? ` · ${escapeHtml(payment.billing_type === "CREDIT_CARD" ? "Cartão" : payment.billing_type)}` : ""}</small></div><div class="billing-cycle-end"><strong class="billing-cycle-credit">${payment.credits_granted ? `+${Number(payment.credits_granted).toLocaleString("pt-BR")} créditos` : "—"}</strong>${documentUrl ? `<a href="${escapeHtml(documentUrl)}" target="_blank" rel="noopener noreferrer">${documentLabel}</a>` : ""}</div></article>`;
    }).join("")}</div></section>` : "";
    const offerCards = profile.is_internal ? `<article class="billing-internal"><span>PLANO INTERNO</span><strong>A EchoHub não precisa contratar um plano.</strong><p>Este workspace permanece ativo com créditos ilimitados.</p></article>` : catalog.offers.length ? catalog.offers.map((offer) => `<article class="billing-offer ${offer.highlighted ? "highlighted" : ""}">${offer.highlighted ? '<span class="billing-recommended">RECOMENDADO</span>' : ""}<small>${offer.kind === "subscription" ? "ASSINATURA" : "PACOTE AVULSO"}</small><h3>${escapeHtml(offer.name)}</h3><p>${escapeHtml(offer.description)}</p><strong>${(Number(offer.price_cents) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })}${offer.cycle === "MONTHLY" ? "<i>/mês</i>" : ""}</strong><b>${Number(offer.credits).toLocaleString("pt-BR")} créditos${offer.cycle ? " por ciclo" : ""}</b><ul>${offer.features.map((feature) => `<li>✓ ${escapeHtml(feature)}</li>`).join("")}</ul><button type="button" data-billing-plan="${escapeHtml(offer.code)}" ${catalog.enabled && canManagePlan ? "" : "disabled"}>${!canManagePlan ? "Administrador contrata" : catalog.enabled ? `Escolher ${escapeHtml(offer.name)}` : "Checkout em preparação"}</button></article>`).join("") : `<article class="billing-catalog-pending"><span>PRÓXIMA ETAPA</span><strong>Planos comerciais em preparação</strong><p>Preços e quantidades de créditos serão publicados aqui antes da abertura do checkout.</p><a href="/plans">Ver como os créditos funcionam</a></article>`;
    const returnStatus = new URLSearchParams(location.search).get("billing_return");
    const returnBanner = returnStatus ? `<div class="billing-return ${escapeHtml(returnStatus)}"><strong>${returnStatus === "success" ? "Pagamento enviado para confirmação" : returnStatus === "expired" ? "O checkout expirou" : "Pagamento não concluído"}</strong><span>${returnStatus === "success" ? "O saldo é atualizado automaticamente após o webhook financeiro do provedor." : "Você pode escolher o plano novamente quando quiser."}</span></div>` : "";
    billingSummary.innerHTML = `${returnBanner}${billingAttention}<div class="billing-hero section-card">
      <div><span class="eyebrow">SALDO DA ORGANIZAÇÃO</span><strong>${profile.unlimited_credits ? "Créditos ilimitados" : `${Number(profile.credit_balance).toLocaleString("pt-BR")} créditos`}</strong><p>${profile.unlimited_credits ? "A EchoHub pode desbloquear empresas sem limite de uso." : "Um crédito é usado somente na primeira vez que a organização salva ou exporta uma empresa."}</p></div>
      <span class="plan-badge">${escapeHtml(planName)}</span>
    </div>
    <div class="billing-metrics"><article><strong>${Number(data.unlocked_companies).toLocaleString("pt-BR")}</strong><span>Empresas desbloqueadas</span></article><article><strong>${profile.unlimited_credits ? "Sem limite" : Number(profile.credit_balance).toLocaleString("pt-BR")}</strong><span>Saldo disponível</span></article><article><strong>${escapeHtml(subscriptionLabels[profile.subscription_status] || profile.subscription_status)}</strong><span>Status do plano</span></article></div>
    ${subscriptionCard}
    <section class="billing-catalog"><div class="section-card-heading"><div><span class="eyebrow">CONTRATAÇÃO</span><h2>Planos e recargas</h2><p>O pagamento acontece em ambiente seguro do provedor.</p></div><a href="/plans">Comparar em página completa</a></div><div class="billing-offer-grid">${offerCards}</div></section>
    ${cyclePayments}
    ${orders}
    <section class="section-card billing-history"><div class="section-card-heading"><div><span class="eyebrow">HISTÓRICO</span><h2>Movimentações</h2><p>Entradas e usos de créditos desta organização.</p></div></div><div class="table-wrap"><table><thead><tr><th>Data</th><th>Descrição</th><th>Créditos</th></tr></thead><tbody>${ledger}</tbody></table></div></section>`;
    billingLoading.classList.add("hidden");
    billingSummary.classList.remove("hidden");
  } catch (error) {
    billingLoading.textContent = error.message;
  }
}

billingSummary.addEventListener("click", async (event) => {
  const cancelButton = event.target.closest("[data-cancel-subscription]");
  if (cancelButton && !cancelButton.disabled) {
    document.querySelector("#cancel-subscription-form").reset();
    setDialogFeedback(document.querySelector("#cancel-subscription-feedback"));
    cancelSubscriptionDialog.showModal();
    document.querySelector("#cancel-subscription-password").focus();
    return;
  }
  const button = event.target.closest("[data-billing-plan]");
  if (!button || button.disabled) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Abrindo checkout…";
  try {
    const response = await fetch("/api/billing/checkouts", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ plan_code: button.dataset.billingPlan }),
    });
    if (!response.ok) throw new Error(await responseError(response, "Não foi possível abrir o checkout."));
    const order = await response.json();
    if (!String(order.checkout_url || "").startsWith("https://")) throw new Error("O provedor não devolveu um link seguro.");
    location.assign(order.checkout_url);
  } catch (error) {
    showToast(error.message);
    button.disabled = false;
    button.textContent = original;
  }
});

document.querySelector("#cancel-subscription-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  const feedback = document.querySelector("#cancel-subscription-feedback");
  setDialogFeedback(feedback);
  submit.disabled = true;
  submit.textContent = "Cancelando…";
  try {
    const response = await fetch("/api/billing/subscription", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: document.querySelector("#cancel-subscription-password").value,
        reason: document.querySelector("#cancel-subscription-reason").value,
      }),
    });
    if (!response.ok) throw new Error(await responseError(response, "Não foi possível cancelar a assinatura."));
    const result = await response.json();
    cancelSubscriptionDialog.close();
    showToast(result.message || "Renovação cancelada.");
    await loadBillingSummary();
  } catch (error) {
    setDialogFeedback(feedback, error.message);
  } finally {
    submit.disabled = false;
    submit.textContent = "Cancelar renovação";
  }
});

function filtersDescription(filters) {
  const parts = [];
  if (filters.cnaes?.length) parts.push(`${filters.cnaes.length} CNAE${filters.cnaes.length === 1 ? "" : "s"}`);
  if (filters.regions?.length) parts.push(filters.regions.join(", "));
  if (filters.ufs?.length) parts.push(filters.ufs.join(", "));
  if (filters.municipalities?.length) parts.push(`${filters.municipalities.length} município${filters.municipalities.length === 1 ? "" : "s"}`);
  if (filters.company_name) parts.push(`Nome: ${filters.company_name}`);
  if (filters.company_sizes?.length) parts.push(`${filters.company_sizes.length} porte${filters.company_sizes.length === 1 ? "" : "s"}`);
  return parts.length ? parts.slice(0, 4).join(" · ") : "Busca ampla na base da Receita";
}

async function loadSavedSearches() {
  savedSearchesLoading.classList.remove("hidden");
  savedSearchesGrid.classList.add("hidden");
  try {
    const response = await fetch("/api/saved-searches");
    if (!response.ok) throw new Error(await responseError(response, "Não foi possível carregar as buscas salvas."));
    const data = await response.json();
    savedSearchesGrid.innerHTML = data.saved_searches.length ? data.saved_searches.map((saved) => `<article class="resource-card">
      <div class="resource-card-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4h12v17l-6-4-6 4V4Z"/></svg></div>
      <div class="resource-card-body"><span class="resource-meta">${saved.last_run_at ? `Usada ${formatDate(saved.last_run_at)}` : "Ainda não executada"}</span><h2>${escapeHtml(saved.name)}</h2><p>${escapeHtml(filtersDescription(saved.filters))}</p>${saved.last_result_count !== null ? `<small>${Number(saved.last_result_count).toLocaleString("pt-BR")} resultados na última execução</small>` : ""}</div>
      <div class="resource-card-actions"><button type="button" data-use-saved-search="${saved.id}">Usar busca</button><button class="secondary" data-capability="manage-library" type="button" data-edit-saved-search="${saved.id}">Editar</button><button class="secondary" data-capability="manage-library" type="button" data-delete-saved-search="${saved.id}">Excluir</button></div>
    </article>`).join("") : `<div class="empty-state resource-empty"><strong>Nenhuma busca salva.</strong><p>Monte um segmento em “Busca com filtros” e use o botão “Salvar busca”.</p><button type="button" data-switch-tab="search">Criar primeira busca</button></div>`;
    savedSearchesGrid.dataset.searches = JSON.stringify(data.saved_searches);
    savedSearchesLoading.classList.add("hidden");
    savedSearchesGrid.classList.remove("hidden");
  } catch (error) {
    savedSearchesLoading.textContent = error.message;
  }
}

function setInputValue(selector, value) {
  const input = document.querySelector(selector);
  if (input) input.value = value ?? "";
}

async function applySearchFilters(filters) {
  await Promise.all([loadSearchCapabilities(), loadCnaeOptions()]);
  cnaePicker.setSelected(filters.cnaes || []);
  statusPicker.setSelected(filters.registration_statuses || []);
  sizePicker.setSelected(filters.company_sizes || []);
  partnerAgePicker.setSelected(filters.partner_age_ranges || []);
  regionPicker.setSelected(filters.regions || []);
  updateUfOptions();
  ufPicker.setSelected(filters.ufs || []);
  await loadMunicipalityOptions();
  municipalityPicker.setSelected(filters.municipalities || []);
  setInputValue("#search-cnae-scope", filters.cnae_scope || "primary");
  setInputValue("#search-name", filters.company_name);
  setInputValue("#search-excluded-names", (filters.excluded_company_names || []).join("\n"));
  setInputValue("#search-capital-min", filters.share_capital_min);
  setInputValue("#search-capital-max", filters.share_capital_max);
  setInputValue("#search-opened-from", filters.opened_from);
  setInputValue("#search-opened-to", filters.opened_to);
  setInputValue("#search-postal-codes", (filters.postal_code_prefixes || []).join("\n"));
  setInputValue("#search-simples", filters.simples === null || filters.simples === undefined ? "" : String(filters.simples));
  setInputValue("#search-mei", filters.mei === null || filters.mei === undefined ? "" : String(filters.mei));
  setInputValue("#search-legal-nature", filters.legal_nature_code);
  setInputValue("#search-branch-type", filters.branch_type);
  setInputValue("#search-has-email", filters.has_email === null || filters.has_email === undefined ? "" : String(filters.has_email));
  setInputValue("#search-has-phone", filters.has_phone === null || filters.has_phone === undefined ? "" : String(filters.has_phone));
  setInputValue("#search-branches-min", filters.active_branch_count_min);
  setInputValue("#search-branches-max", filters.active_branch_count_max);
  setInputValue("#search-limit", filters.limit || 500);
}

async function applySavedSearch(saved) {
  switchTab("search");
  const filters = saved.filters;
  await applySearchFilters(filters);
  setActiveSavedSearch(saved);
  const guidance = document.querySelector(".filter-guidance");
  guidance.innerHTML = `<span aria-hidden="true"></span>Busca salva: ${escapeHtml(saved.name)}`;
  companySearchForm.scrollIntoView({ behavior: "smooth", block: "start" });
  showToast(`Critérios de “${saved.name}” carregados.`);
  scheduleCompanySearchPreview({ immediate: true });
}

function supportedTemplateFilters(template) {
  const filters = { ...template.filters };
  if (!searchCapabilities.establishment_details) {
    delete filters.branch_type;
    delete filters.has_email;
    delete filters.has_phone;
  }
  if (!searchCapabilities.branch_counts) {
    delete filters.active_branch_count_min;
    delete filters.active_branch_count_max;
  }
  if (!searchCapabilities.simples_mei) {
    delete filters.simples;
    delete filters.mei;
  }
  return filters;
}

async function applySearchTemplate(templateKey) {
  const template = searchTemplates[templateKey];
  if (!template) return;
  await loadSearchCapabilities();
  await applySearchFilters(supportedTemplateFilters(template));
  setActiveSavedSearch();
  const guidance = document.querySelector(".filter-guidance");
  guidance.innerHTML = `<span aria-hidden="true"></span>Modelo aplicado: ${escapeHtml(template.name)}`;
  document.querySelector(".filter-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  showToast(`Modelo “${template.name}” aplicado. A prévia está sendo atualizada.`);
  scheduleCompanySearchPreview({ immediate: true });
}

document.querySelector("#search-templates").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-search-template]");
  if (!button) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { await applySearchTemplate(button.dataset.searchTemplate); }
  catch (error) { showToast(error.message); }
  finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
});

async function deleteSavedSearch(searchId) {
  const response = await fetch(`/api/saved-searches/${searchId}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await responseError(response, "Não foi possível excluir a busca."));
  if (activeSavedSearchId === searchId) {
    setActiveSavedSearch();
  }
  await loadSavedSearches();
  showToast("Busca excluída.");
}

function listTemplateMarkup() {
  return Object.entries(listTemplates).map(([key, template]) => `
    <button class="list-template" data-capability="manage-library" type="button" data-list-template="${key}">
      <span><strong>${escapeHtml(template.name)}</strong><small>${escapeHtml(template.description)}</small></span><b aria-hidden="true">+</b>
    </button>`).join("");
}

async function loadCompanyLists() {
  listsLoading.classList.remove("hidden");
  listsGrid.classList.add("hidden");
  listDetail.classList.add("hidden");
  try {
    const response = await fetch("/api/company-lists");
    if (!response.ok) throw new Error(await responseError(response, "Não foi possível carregar as listas."));
    const data = await response.json();
    listsGrid.innerHTML = data.lists.length ? data.lists.map((item) => `<article class="resource-card list-card">
      <div class="resource-card-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 12h14M5 18h14"/></svg></div>
      <div class="resource-card-body"><span class="resource-meta">Atualizada ${formatDate(item.updated_at)}</span><h2>${escapeHtml(item.name)}</h2><p>${escapeHtml(item.description || "Lista compartilhada com sua organização.")}</p><small>${Number(item.company_count).toLocaleString("pt-BR")} empresa${item.company_count === 1 ? "" : "s"}</small></div>
      <div class="resource-card-actions"><button type="button" data-open-list="${item.id}">Abrir lista</button></div>
    </article>`).join("") : `<div class="empty-state resource-empty template-empty"><span class="eyebrow">MODELOS DE LISTA</span><strong>Como sua equipe quer organizar as empresas?</strong><p>Escolha um modelo para preencher nome e objetivo, ou comece com uma lista em branco.</p><div class="list-template-grid">${listTemplateMarkup()}</div><button class="secondary" data-capability="manage-library" type="button" data-open-create-list>Criar lista em branco</button><p data-viewer-library-note>Seu perfil pode consultar listas existentes. Um administrador pode liberar a criação e a exportação.</p></div>`;
    listsLoading.classList.add("hidden");
    listsGrid.classList.remove("hidden");
    return data.lists;
  } catch (error) {
    listsLoading.textContent = error.message;
    return [];
  }
}

async function loadCompanyListDetail(listId, query = "", offset = 0) {
  listDetail.innerHTML = `<div class="loading-inline">Abrindo lista…</div>`;
  listDetail.classList.remove("hidden");
  try {
    const parameters = new URLSearchParams({ limit: "50", offset: String(Math.max(0, Number(offset) || 0)) });
    if (query.trim()) parameters.set("q", query.trim());
    const response = await fetch(`/api/company-lists/${listId}?${parameters}`);
    if (!response.ok) throw new Error(await responseError(response, "Não foi possível abrir a lista."));
    const data = await response.json();
    const rows = data.companies.map((company) => `<tr><td><button class="table-link" type="button" data-company-cnpj="${escapeHtml(company.cnpj)}">${escapeHtml(formatCnpj(company.cnpj))}</button></td><td>${escapeHtml(company.legal_name || company.trade_name || "—")}</td><td>${escapeHtml(company.municipality || "—")}/${escapeHtml(company.uf || "—")}</td><td>${escapeHtml(company.registration_status || "—")}</td><td><button class="table-danger" data-capability="manage-library" type="button" data-remove-list-company="${escapeHtml(company.cnpj)}">Remover</button></td></tr>`).join("");
    const page = data.pagination;
    const firstItem = data.filtered_count ? page.offset + 1 : 0;
    const lastItem = Math.min(page.offset + data.companies.length, data.filtered_count);
    const resultLabel = data.query
      ? `${Number(data.filtered_count).toLocaleString("pt-BR")} de ${Number(data.company_count).toLocaleString("pt-BR")} empresa${data.company_count === 1 ? "" : "s"}`
      : `${Number(data.company_count).toLocaleString("pt-BR")} empresa${data.company_count === 1 ? "" : "s"}`;
    const pagination = data.filtered_count > page.limit ? `<div class="list-pagination"><button class="secondary compact" type="button" data-list-offset="${Math.max(0, page.offset - page.limit)}" ${page.has_previous ? "" : "disabled"}>Anterior</button><span>${firstItem.toLocaleString("pt-BR")}–${lastItem.toLocaleString("pt-BR")} de ${Number(data.filtered_count).toLocaleString("pt-BR")}</span><button class="secondary compact" type="button" data-list-offset="${page.offset + page.limit}" ${page.has_next ? "" : "disabled"}>Próxima</button></div>` : "";
    listDetail.dataset.listId = listId;
    listDetail.dataset.listQuery = data.query || "";
    listDetail.dataset.listOffset = String(page.offset);
    listDetail.dataset.list = JSON.stringify({ id: data.id, name: data.name, description: data.description });
    listDetail.innerHTML = `<div class="list-detail-head"><div><span class="eyebrow">LISTA</span><h2>${escapeHtml(data.name)}</h2><p>${escapeHtml(data.description || "Compartilhada com toda a organização.")}</p></div><div><button class="secondary compact" data-capability="manage-library" type="button" data-edit-list>Editar</button><button class="secondary compact" data-capability="export" type="button" data-download-list ${data.company_count ? "" : "disabled"}>Baixar CSV</button><button class="danger-button compact" data-capability="manage-library" type="button" data-delete-list>Excluir lista</button></div></div>
      ${data.company_count ? `<div class="list-detail-toolbar"><form data-list-search-form><label><span class="sr-only">Buscar dentro da lista</span><input data-list-query type="search" maxlength="120" value="${escapeHtml(data.query || "")}" placeholder="Buscar por empresa, CNPJ ou cidade"></label><button class="secondary compact" type="submit">Buscar</button>${data.query ? `<button class="secondary compact" type="button" data-clear-list-search>Limpar</button>` : ""}</form><span>${resultLabel}</span></div>` : ""}
      ${rows ? `<div class="table-wrap"><table><thead><tr><th>CNPJ</th><th>Empresa</th><th>Município/UF</th><th>Situação</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>${pagination}` : data.query ? `<div class="empty-state"><strong>Nenhuma empresa encontrada nesta lista.</strong><p>Tente outro nome, CNPJ, município ou estado.</p><button class="secondary" type="button" data-clear-list-search>Limpar busca</button></div>` : `<div class="empty-state"><strong>Esta lista ainda está vazia.</strong><p>Selecione empresas em uma busca e use “Salvar em uma lista”.</p></div>`}`;
    listDetail.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    listDetail.innerHTML = `<div class="error"><strong>Não foi possível abrir a lista.</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function openSaveListDialog() {
  const selected = lastCompanySearch.filter((company) => selectedCompanyCnpjs.has(company.cnpj));
  if (!selected.length) return;
  const cnpjs = selected.map((company) => company.cnpj);
  const [response, estimate] = await Promise.all([
    fetch("/api/company-lists"),
    estimateCredits(cnpjs),
  ]);
  if (!response.ok) {
    showToast(await responseError(response, "Não foi possível carregar as listas."));
    return;
  }
  const data = await response.json();
  const select = document.querySelector("#target-list");
  const submitButton = document.querySelector("#save-list-form button[type='submit']");
  select.innerHTML = `<option value="">Selecione uma lista</option>${data.lists.map((item) => `<option value="${item.id}">${escapeHtml(item.name)} (${item.company_count})</option>`).join("")}`;
  document.querySelector("#new-list-name").value = "";
  const creditCopy = estimate.unlimited_credits
    ? "Sua organização possui créditos ilimitados."
    : estimate.credits_required
      ? `${estimate.credits_required.toLocaleString("pt-BR")} crédito${estimate.credits_required === 1 ? " será usado" : "s serão usados"}; ${estimate.already_unlocked.toLocaleString("pt-BR")} já ${estimate.already_unlocked === 1 ? "está desbloqueada" : "estão desbloqueadas"}.`
      : "Todas já estão desbloqueadas, sem novo consumo de créditos.";
  document.querySelector("#save-list-count").textContent = `${selected.length.toLocaleString("pt-BR")} empresa${selected.length === 1 ? "" : "s"} selecionada${selected.length === 1 ? "" : "s"}. ${creditCopy}`;
  setDialogFeedback(
    document.querySelector("#save-list-feedback"),
    estimate.can_complete ? "" : `Saldo insuficiente: você tem ${estimate.credit_balance.toLocaleString("pt-BR")} crédito${estimate.credit_balance === 1 ? "" : "s"}.`,
  );
  submitButton.disabled = !estimate.can_complete;
  saveListDialog.showModal();
}

async function removeCompanyFromList(listId, cnpj) {
  const response = await fetch(`/api/company-lists/${listId}/companies/${encodeURIComponent(cnpj)}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await responseError(response, "Não foi possível remover a empresa."));
  const query = listDetail.dataset.listQuery || "";
  const offset = Number(listDetail.dataset.listOffset || 0);
  await loadCompanyLists();
  await loadCompanyListDetail(listId, query, offset);
  showToast("Empresa removida da lista. O desbloqueio continua disponível para a organização.");
}

function openCreateListDialog(templateKey = null) {
  const form = document.querySelector("#create-list-form");
  form.reset();
  form.dataset.listId = "";
  document.querySelector("#list-dialog-eyebrow").textContent = "NOVA LISTA";
  document.querySelector("#list-dialog-title").textContent = "Crie uma lista para sua equipe";
  document.querySelector("#list-dialog-description").textContent = "Use listas para separar campanhas, territórios e etapas comerciais.";
  document.querySelector("#list-dialog-submit").textContent = "Criar lista";
  const template = listTemplates[templateKey];
  if (template) {
    document.querySelector("#create-list-name").value = template.name;
    document.querySelector("#create-list-description").value = template.description;
  }
  setDialogFeedback(document.querySelector("#create-list-feedback"));
  createListDialog.showModal();
  document.querySelector("#create-list-name").focus();
}

function openEditListDialog(companyList) {
  const form = document.querySelector("#create-list-form");
  form.reset();
  form.dataset.listId = companyList.id;
  document.querySelector("#list-dialog-eyebrow").textContent = "EDITAR LISTA";
  document.querySelector("#list-dialog-title").textContent = "Atualize esta lista";
  document.querySelector("#list-dialog-description").textContent = "O novo nome e a descrição ficam visíveis para toda a equipe.";
  document.querySelector("#list-dialog-submit").textContent = "Salvar alterações";
  document.querySelector("#create-list-name").value = companyList.name || "";
  document.querySelector("#create-list-description").value = companyList.description || "";
  setDialogFeedback(document.querySelector("#create-list-feedback"));
  createListDialog.showModal();
  document.querySelector("#create-list-name").focus();
  document.querySelector("#create-list-name").select();
}

function setActiveSavedSearch(saved = null) {
  activeSavedSearchId = saved?.id || null;
  activeSavedSearchFilters = saved ? JSON.stringify(saved.filters) : null;
  activeSavedSearchName = saved?.name || null;
  document.querySelector("#save-current-search").textContent = saved ? "Salvar alterações" : "Salvar busca";
}

function openSaveSearchDialog() {
  const editing = Boolean(activeSavedSearchId);
  document.querySelector("#save-search-eyebrow").textContent = editing ? "ATUALIZAR SEGMENTO" : "SALVAR SEGMENTO";
  document.querySelector("#save-search-title").textContent = editing ? "Salve os ajustes desta busca" : "Nomeie esta busca";
  document.querySelector("#save-search-description").textContent = editing
    ? "Atualize a busca da equipe ou guarde os critérios como um novo segmento."
    : "Os critérios atuais ficarão disponíveis para toda a organização.";
  document.querySelector("#saved-search-name").value = activeSavedSearchName || "";
  document.querySelector("#save-search-copy").classList.toggle("hidden", !editing);
  document.querySelector("#save-search-submit").textContent = editing ? "Atualizar busca" : "Salvar busca";
  setDialogFeedback(document.querySelector("#save-search-feedback"));
  saveSearchDialog.showModal();
  document.querySelector("#saved-search-name").focus();
  document.querySelector("#saved-search-name").select();
}

document.querySelector("#save-current-search").addEventListener("click", openSaveSearchDialog);

document.querySelector("#save-search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const feedback = document.querySelector("#save-search-feedback");
  setDialogFeedback(feedback);
  const filters = companySearchPayload();
  const sameAsLastRun = lastCompanySearchPayload && JSON.stringify(filters) === JSON.stringify(lastCompanySearchPayload);
  const payload = { name: document.querySelector("#saved-search-name").value, filters };
  if (sameAsLastRun) payload.result_count = lastCompanySearch.length;
  const updateExisting = Boolean(activeSavedSearchId && event.submitter?.value !== "create");
  const endpoint = updateExisting ? `/api/saved-searches/${activeSavedSearchId}` : "/api/saved-searches";
  const response = await fetch(endpoint, { method: updateExisting ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) {
    setDialogFeedback(feedback, await responseError(response, "Não foi possível salvar a busca."));
    return;
  }
  const saved = await response.json();
  setActiveSavedSearch(saved);
  saveSearchDialog.close();
  const guidance = document.querySelector(".filter-guidance");
  guidance.innerHTML = `<span aria-hidden="true"></span>Busca salva: ${escapeHtml(saved.name)}`;
  showToast(updateExisting ? "Alterações salvas para toda a organização." : "Nova busca salva para toda a organização.");
});

document.querySelector("#save-list-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const feedback = document.querySelector("#save-list-feedback");
  setDialogFeedback(feedback);
  const cnpjs = lastCompanySearch.filter((company) => selectedCompanyCnpjs.has(company.cnpj)).map((company) => company.cnpj);
  let listId = document.querySelector("#target-list").value;
  const newName = document.querySelector("#new-list-name").value.trim();
  try {
    const estimate = await estimateCredits(cnpjs);
    if (!estimate.can_complete) {
      setDialogFeedback(feedback, `Créditos insuficientes. Esta ação precisa de ${estimate.credits_required.toLocaleString("pt-BR")} e o saldo é ${estimate.credit_balance.toLocaleString("pt-BR")}.`);
      return;
    }
  } catch (error) {
    setDialogFeedback(feedback, error.message);
    return;
  }
  if (newName) {
    const createResponse = await fetch("/api/company-lists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: newName }) });
    if (!createResponse.ok) {
      setDialogFeedback(feedback, await responseError(createResponse, "Não foi possível criar a lista."));
      return;
    }
    listId = (await createResponse.json()).id;
  }
  if (!listId) {
    setDialogFeedback(feedback, "Escolha uma lista ou informe o nome de uma nova.");
    return;
  }
  const response = await fetch(`/api/company-lists/${listId}/companies`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cnpjs }) });
  if (!response.ok) {
    setDialogFeedback(feedback, await responseError(response, "Não foi possível salvar as empresas."));
    return;
  }
  const result = await response.json();
  saveListDialog.close();
  updateCreditIndicator({ unlimited_credits: result.unlimited_credits, credit_balance: result.credit_balance });
  const selectedOption = document.querySelector("#target-list").selectedOptions[0];
  const listName = newName || (selectedOption?.textContent || "").replace(/\s+\(\d+\)$/, "");
  lastCompanySearch.forEach((company) => {
    if (!selectedCompanyCnpjs.has(company.cnpj)) return;
    company.saved = true;
    company.saved_lists = company.saved_lists || [];
    if (!company.saved_lists.some((list) => list.id === listId)) {
      company.saved_lists.unshift({ id: listId, name: listName });
    }
  });
  if (lastCompanySearchData) {
    const savedCount = lastCompanySearch.filter((company) => company.saved).length;
    lastCompanySearchData.segments = {
      total: lastCompanySearch.length,
      new: lastCompanySearch.length - savedCount,
      saved: savedCount,
    };
    updateCompanySearchTabs(lastCompanySearchData);
    renderCompanySearchView();
  }
  showToast(`${result.added.toLocaleString("pt-BR")} empresa${result.added === 1 ? "" : "s"} adicionada${result.added === 1 ? "" : "s"} à lista.`);
});

document.querySelector("#create-list-button").addEventListener("click", () => openCreateListDialog());
document.querySelector("#create-list-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const feedback = document.querySelector("#create-list-feedback");
  setDialogFeedback(feedback);
  const listId = form.dataset.listId;
  const response = await fetch(listId ? `/api/company-lists/${listId}` : "/api/company-lists", { method: listId ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: document.querySelector("#create-list-name").value, description: document.querySelector("#create-list-description").value }) });
  if (!response.ok) {
    setDialogFeedback(feedback, await responseError(response, listId ? "Não foi possível atualizar a lista." : "Não foi possível criar a lista."));
    return;
  }
  const saved = await response.json();
  createListDialog.close();
  await loadCompanyLists();
  await loadCompanyListDetail(saved.id);
  showToast(listId ? "Alterações da lista salvas." : "Lista criada.");
});

document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
}));

saasOverview.addEventListener("click", async (event) => {
  const switchButton = event.target.closest("[data-switch-tab]");
  if (switchButton) switchTab(switchButton.dataset.switchTab);
  const listButton = event.target.closest("[data-dashboard-list]");
  if (listButton) {
    switchTab("lists");
    await loadCompanyListDetail(listButton.dataset.dashboardList);
  }
  const searchButton = event.target.closest("[data-dashboard-search]");
  if (searchButton) {
    const searches = JSON.parse(saasOverview.dataset.searches || "[]");
    const saved = searches.find((item) => item.id === searchButton.dataset.dashboardSearch);
    if (saved) await applySavedSearch(saved);
  }
});

savedSearchesGrid.addEventListener("click", async (event) => {
  const switchButton = event.target.closest("[data-switch-tab]");
  if (switchButton) switchTab(switchButton.dataset.switchTab);
  const useButton = event.target.closest("[data-use-saved-search]");
  if (useButton) {
    const searches = JSON.parse(savedSearchesGrid.dataset.searches || "[]");
    const saved = searches.find((item) => item.id === useButton.dataset.useSavedSearch);
    if (saved) await applySavedSearch(saved);
  }
  const editButton = event.target.closest("[data-edit-saved-search]");
  if (editButton) {
    const searches = JSON.parse(savedSearchesGrid.dataset.searches || "[]");
    const saved = searches.find((item) => item.id === editButton.dataset.editSavedSearch);
    if (saved) {
      await applySavedSearch(saved);
      openSaveSearchDialog();
    }
  }
  const deleteButton = event.target.closest("[data-delete-saved-search]");
  if (deleteButton) {
    if (!window.confirm("Excluir esta busca salva para toda a organização?")) return;
    try { await deleteSavedSearch(deleteButton.dataset.deleteSavedSearch); }
    catch (error) { showToast(error.message); }
  }
});

listsGrid.addEventListener("click", (event) => {
  const createButton = event.target.closest("[data-open-create-list]");
  if (createButton) openCreateListDialog();
  const templateButton = event.target.closest("[data-list-template]");
  if (templateButton) openCreateListDialog(templateButton.dataset.listTemplate);
  const openButton = event.target.closest("[data-open-list]");
  if (openButton) loadCompanyListDetail(openButton.dataset.openList);
});

listDetail.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-list-search-form]");
  if (!form) return;
  event.preventDefault();
  loadCompanyListDetail(listDetail.dataset.listId, form.querySelector("[data-list-query]").value, 0);
});

listDetail.addEventListener("click", async (event) => {
  const listId = listDetail.dataset.listId;
  if (event.target.closest("[data-clear-list-search]")) {
    await loadCompanyListDetail(listId);
  }
  const pageButton = event.target.closest("[data-list-offset]");
  if (pageButton && !pageButton.disabled) {
    await loadCompanyListDetail(listId, listDetail.dataset.listQuery || "", Number(pageButton.dataset.listOffset));
  }
  if (event.target.closest("[data-edit-list]")) {
    openEditListDialog(JSON.parse(listDetail.dataset.list || "{}"));
  }
  const companyButton = event.target.closest("[data-company-cnpj]");
  if (companyButton) {
    switchTab("batch");
    document.querySelector("#explorer-cnpj").value = companyButton.dataset.companyCnpj;
    explorerCnpjForm.requestSubmit();
  }
  const removeButton = event.target.closest("[data-remove-list-company]");
  if (removeButton) {
    try { await removeCompanyFromList(listId, removeButton.dataset.removeListCompany); }
    catch (error) { showToast(error.message); }
  }
  const downloadButton = event.target.closest("[data-download-list]");
  if (downloadButton) {
    await runButtonAction(downloadButton, "Preparando CSV…", () => downloadCsvResponse(`/api/company-lists/${listId}/export.csv`, {}, "lista-empresas.csv"));
  }
  if (event.target.closest("[data-delete-list]")) {
    if (!window.confirm("Excluir esta lista e remover todas as empresas dela?")) return;
    const response = await fetch(`/api/company-lists/${listId}`, { method: "DELETE" });
    if (!response.ok) { showToast(await responseError(response, "Não foi possível excluir a lista.")); return; }
    await loadCompanyLists();
    showToast("Lista excluída.");
  }
});

document.querySelectorAll(".tab-button").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
document.querySelectorAll("[data-switch-tab]").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.switchTab)));
document.querySelector("#refresh-history").addEventListener("click", loadHistory);
historyList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-download-job]");
  if (!button) return;
  await runButtonAction(button, "Preparando CSV…", async () => {
    const estimateResponse = await fetch(`/api/jobs/${button.dataset.downloadJob}/credit-estimate`);
    if (!estimateResponse.ok) throw new Error(await responseError(estimateResponse, "Não foi possível calcular os créditos."));
    const estimate = await estimateResponse.json();
    if (!confirmCreditUse(estimate, "baixar este processamento")) return;
    await downloadCsvResponse(`/api/jobs/${button.dataset.downloadJob}/export.csv`, {}, `resultado-${button.dataset.downloadJob}.csv`);
  });
});
document.querySelector("#refresh-explorer").addEventListener("click", loadExplorerOverview);
document.querySelector("#refresh-schema").addEventListener("click", () => loadDatabaseSchema(true));
document.querySelector("#download-template").addEventListener("click", downloadTemplate);
schemaCatalog.addEventListener("click", (event) => {
  const button = event.target.closest("[data-preview-relation]");
  if (button) loadRelationPreview(button.dataset.previewRelation);
});
relationPreview.addEventListener("click", (event) => {
  if (!event.target.closest("[data-close-preview]")) return;
  relationPreview.classList.add("hidden");
  schemaCatalog.scrollIntoView({ behavior: "smooth", block: "start" });
});
companySearchResult.addEventListener("click", (event) => {
  const button = event.target.closest("[data-company-cnpj]");
  if (!button) return;
  switchTab("batch");
  document.querySelector("#explorer-cnpj").value = button.dataset.companyCnpj;
  explorerCnpjForm.requestSubmit();
  explorerCnpjForm.scrollIntoView({ behavior: "smooth", block: "start" });
});
companySearchResult.addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-select-company]");
  if (!checkbox) return;
  if (checkbox.checked) selectedCompanyCnpjs.add(checkbox.dataset.selectCompany);
  else selectedCompanyCnpjs.delete(checkbox.dataset.selectCompany);
  updateSearchSelection();
});
explorerEstablishmentsResult.addEventListener("click", (event) => {
  const button = event.target.closest("[data-company-cnpj]");
  if (!button) return;
  document.querySelector("#explorer-cnpj").value = button.dataset.companyCnpj;
  explorerCnpjForm.requestSubmit();
  explorerCnpjForm.scrollIntoView({ behavior: "smooth", block: "start" });
});
bulkCnpjResult.addEventListener("click", (event) => {
  const button = event.target.closest("[data-company-cnpj]");
  if (!button) return;
  switchTab("batch");
  document.querySelector("#explorer-cnpj").value = button.dataset.companyCnpj;
  explorerCnpjForm.requestSubmit();
  explorerCnpjForm.scrollIntoView({ behavior: "smooth", block: "start" });
});

const requestedInitialTab = new URLSearchParams(location.search).get("tab");
const initialTab = Array.from(document.querySelectorAll(".tab-button[data-tab]"))
  .some((button) => button.dataset.tab === requestedInitialTab)
  ? requestedInitialTab
  : "overview";
switchTab(initialTab);
