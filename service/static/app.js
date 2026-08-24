const form = document.querySelector("#match-form");
const loading = document.querySelector("#loading");
const result = document.querySelector("#result");
const batchForm = document.querySelector("#batch-form");
const batchLoading = document.querySelector("#batch-loading");
const batchResult = document.querySelector("#batch-result");
let batchSourceRows = [];
let batchResponse = null;

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
  };
}

function csvCell(value) {
  const text = String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadBatch() {
  if (!batchResponse) return;
  const extraHeaders = ["Matcher Status", "CNPJ Receita", "Razão Social Receita", "Nome Fantasia Receita", "Score", "Confiança", "Versão Receita"];
  const originalHeaders = Object.keys(batchSourceRows[0] || {});
  const lines = [[...originalHeaders, ...extraHeaders].map(csvCell).join(",")];
  batchResponse.results.forEach((result, index) => {
    const source = batchSourceRows[index];
    const selected = result.selected || {};
    lines.push([
      ...originalHeaders.map((header) => source[header]),
      result.status,
      selected.cnpj || "",
      selected.legal_name || "",
      selected.trade_name || "",
      selected.score ?? "",
      result.confidence,
      batchResponse.dataset_version,
    ].map(csvCell).join(","));
  });
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "resultado-matcher-cnpj.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}

function renderBatch(data) {
  batchResponse = data;
  const counts = { confirmado: 0, revisao: 0, nao_encontrado: 0 };
  data.results.forEach((item) => { counts[item.status] = (counts[item.status] || 0) + 1; });
  const rows = data.results.map((item, index) => {
    const selected = item.selected || {};
    return `<tr>
      <td>${index + 1}</td>
      <td>${escapeHtml(pick(batchSourceRows[index], ["Company Name", "Nome da empresa", "Razão Social", "Nome"]))}</td>
      <td><span class="status ${item.status}">${statusLabel[item.status]}</span></td>
      <td>${escapeHtml(formatCnpj(selected.cnpj || "—"))}</td>
      <td>${escapeHtml(selected.legal_name || "—")}</td>
      <td>${selected.score ?? "—"}</td>
    </tr>`;
  }).join("");
  batchResult.innerHTML = `
    <div class="summary">
      <div><strong>${data.results.length}</strong><span>consultadas</span></div>
      <div><strong>${counts.confirmado}</strong><span>confirmadas</span></div>
      <div><strong>${counts.revisao}</strong><span>para revisão</span></div>
      <div><strong>${counts.nao_encontrado}</strong><span>sem resultado</span></div>
    </div>
    <div class="table-wrap"><table><thead><tr><th>#</th><th>Empresa</th><th>Resultado</th><th>CNPJ</th><th>Razão social</th><th>Score</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="timing">Lote concluído em ${(data.timing_ms.total / 1000).toFixed(1)}s · Receita ${escapeHtml(data.dataset_version)}</p>
    <button id="download-batch" class="download" type="button">Baixar resultado em CSV</button>`;
  document.querySelector("#download-batch").addEventListener("click", downloadBatch);
}

batchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  batchResult.classList.add("hidden");
  batchLoading.classList.remove("hidden");
  try {
    const file = document.querySelector("#csv-file").files[0];
    const allRows = parseCsv(await file.text());
    batchSourceRows = allRows.slice(0, 200);
    const items = batchSourceRows.map(batchItem);
    const response = await fetch("/api/matches/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active_only: true,
        check_website: document.querySelector("#batch-check-website").checked,
        items,
      }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Falha na consulta em lote");
    renderBatch(await response.json());
  } catch (error) {
    batchResult.innerHTML = `<div class="error"><strong>Não foi possível processar o CSV.</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    batchLoading.classList.add("hidden");
    batchResult.classList.remove("hidden");
  }
});
