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
let batchSourceRows = [];
let historyPoll = null;
let searchCapabilitiesLoaded = false;
let lastCompanySearch = [];

const allUfs = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"];
const allRegistrationStatuses = ["ATIVA", "BAIXADA", "INAPTA", "NULA", "SUSPENSA", "NAO INFORMADA"];

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
  const text = String(value ?? "");
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
  document.querySelectorAll(".tab-button").forEach((button) => button.classList.toggle("active", button.dataset.tab === tabName));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("hidden", panel.id !== `${tabName}-tab`));
  if (tabName === "history") loadHistory();
  if (tabName === "search") loadSearchCapabilities();
}

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function renderHistory(jobs) {
  if (!jobs.length) {
    historyList.innerHTML = `<div class="empty-state"><strong>Nenhuma consulta em lote ainda.</strong><p>Envie um CSV na aba “Nova consulta”.</p></div>`;
    return;
  }
  historyList.innerHTML = jobs.map((job) => {
    const active = job.status === "queued" || job.status === "running";
    const download = job.processed > 0
      ? `<a class="download-link" href="/api/jobs/${job.id}/export.csv">${active ? "Baixar parcial" : "Baixar CSV"}</a>`
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
  if (searchCapabilitiesLoaded) return;
  try {
    const response = await fetch("/api/search/capabilities");
    if (!response.ok) throw new Error("Não foi possível verificar os filtros disponíveis");
    const data = await response.json();
    document.querySelectorAll("[data-capability]").forEach((field) => {
      field.disabled = !data.filters[field.dataset.capability];
    });
    const available = Object.values(data.filters).every(Boolean);
    if (available) {
      document.querySelector("#complementary-filters legend span").textContent = "Disponível";
      document.querySelector("#complementary-note").textContent = `Dados complementares disponíveis na versão ${data.dataset_version}.`;
    }
    searchCapabilitiesLoaded = true;
  } catch (error) {
    document.querySelector("#complementary-note").textContent = error.message;
  }
}

function companySearchPayload() {
  const status = document.querySelector("#search-status").value;
  const size = document.querySelector("#search-size").value;
  const uf = document.querySelector("#search-uf").value;
  return {
    cnae: document.querySelector("#search-cnae").value || null,
    cnae_scope: document.querySelector("#search-cnae-scope").value,
    company_name: document.querySelector("#search-name").value || null,
    registration_statuses: status === "TODAS" ? allRegistrationStatuses : [status],
    company_sizes: size ? [size] : [],
    share_capital_min: optionalNumber(document.querySelector("#search-capital-min").value),
    share_capital_max: optionalNumber(document.querySelector("#search-capital-max").value),
    opened_from: document.querySelector("#search-opened-from").value || null,
    opened_to: document.querySelector("#search-opened-to").value || null,
    region: document.querySelector("#search-region").value || null,
    ufs: uf ? [uf] : [],
    municipality: document.querySelector("#search-municipality").value || null,
    postal_code_prefix: document.querySelector("#search-postal-code").value || null,
    simples: optionalBoolean(document.querySelector("#search-simples").value),
    mei: optionalBoolean(document.querySelector("#search-mei").value),
    legal_nature_code: document.querySelector("#search-legal-nature").value || null,
    branch_type: document.querySelector("#search-branch-type").value || null,
    has_email: optionalBoolean(document.querySelector("#search-has-email").value),
    has_phone: optionalBoolean(document.querySelector("#search-has-phone").value),
    limit: Number(document.querySelector("#search-limit").value),
  };
}

function displayValue(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function formatMoney(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function renderCompanySearch(data) {
  lastCompanySearch = data.results;
  const preview = data.results.slice(0, 100);
  const rows = preview.map((company) => `<tr>
    <td><strong>${escapeHtml(formatCnpj(company.cnpj))}</strong></td>
    <td>${escapeHtml(company.legal_name || company.trade_name || "—")}</td>
    <td>${escapeHtml(company.primary_cnae || "—")}</td>
    <td>${escapeHtml(company.municipality || "—")}/${escapeHtml(company.uf || "—")}</td>
    <td>${escapeHtml(company.company_size || "—")}</td>
    <td>${escapeHtml(formatMoney(company.share_capital))}</td>
    <td>${escapeHtml(company.opened_at || "—")}</td>
    <td>${escapeHtml(company.registration_status || "—")}</td>
  </tr>`).join("");
  const limitNotice = data.has_more
    ? `A busca atingiu o limite de ${data.limit.toLocaleString("pt-BR")}. Refine os filtros para ver outro recorte.`
    : "Todos os resultados encontrados dentro deste recorte foram retornados.";
  companySearchResult.innerHTML = `<div class="search-summary">
      <div><strong>${data.returned.toLocaleString("pt-BR")}</strong><span>CNPJs retornados</span></div>
      <div><strong>${(data.timing_ms / 1000).toFixed(1)}s</strong><span>Tempo de consulta</span></div>
      <div><strong>${escapeHtml(data.dataset_version || "—")}</strong><span>Versão da Receita</span></div>
    </div>
    <p class="search-notice">${escapeHtml(limitNotice)} A tabela mostra os primeiros ${Math.min(100, data.returned)}; o CSV contém todos.</p>
    ${data.results.length ? `<div class="table-wrap"><table><thead><tr><th>CNPJ</th><th>Razão social</th><th>CNAE</th><th>Município/UF</th><th>Porte</th><th>Capital</th><th>Abertura</th><th>Situação</th></tr></thead><tbody>${rows}</tbody></table></div>
    <button id="download-company-search" class="download" type="button">Baixar ${data.returned.toLocaleString("pt-BR")} empresas em CSV</button>` : `<div class="empty-state"><strong>Nenhuma empresa encontrada.</strong><p>Altere ou remova algum filtro e tente novamente.</p></div>`}`;
  document.querySelector("#download-company-search")?.addEventListener("click", downloadCompanySearch);
}

function downloadCompanySearch() {
  const headers = ["CNPJ", "Razão Social", "Nome Fantasia", "Situação", "Data Situação", "Data Abertura", "Porte", "Capital Social", "CNAE Principal", "CNAEs Secundários", "Município", "UF", "CEP", "Endereço", "Simples", "MEI", "Natureza Jurídica", "Matriz/Filial", "E-mail", "Telefone", "Versão Receita"];
  const rows = lastCompanySearch.map((company) => [
    company.cnpj, company.legal_name, company.trade_name, company.registration_status,
    company.registration_status_date, company.opened_at, company.company_size, company.share_capital,
    company.primary_cnae, (company.secondary_cnaes || []).join(";"), company.municipality, company.uf,
    company.postal_code, company.address, company.is_simples, company.is_mei, company.legal_nature_code,
    company.branch_type_code, company.email,
    [company.phone_area_code, company.phone].filter(Boolean).join(" "), company.dataset_version,
  ]);
  const content = "\uFEFF" + [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `empresas-receita-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

companySearchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  companySearchResult.classList.add("hidden");
  companySearchLoading.classList.remove("hidden");
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(companySearchPayload()),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Falha na busca");
    renderCompanySearch(await response.json());
  } catch (error) {
    companySearchResult.innerHTML = `<div class="error"><strong>Não foi possível buscar as empresas.</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    companySearchLoading.classList.add("hidden");
    companySearchResult.classList.remove("hidden");
  }
});

document.querySelector("#search-uf").insertAdjacentHTML("beforeend", allUfs.map((uf) => `<option value="${uf}">${uf}</option>`).join(""));

document.querySelectorAll(".tab-button").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
document.querySelector("#refresh-history").addEventListener("click", loadHistory);
document.querySelector("#download-template").addEventListener("click", downloadTemplate);
