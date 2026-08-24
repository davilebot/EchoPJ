import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const data = JSON.parse(await fs.readFile("work/workbook_data.json", "utf8"));
const outputDir = "/Users/davibotelho/Documents/Codex/2026-08-23/plataforma-receita/outputs";
const previewDir = "work/previews";
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const workbook = Workbook.create();
const summarySheet = workbook.worksheets.add("Resumo");
const companiesSheet = workbook.worksheets.add("Empresas enriquecidas");
const partnersSheet = workbook.worksheets.add("Sócios e administradores");
const reviewSheet = workbook.worksheets.add("Fila de revisão");
const auditSheet = workbook.worksheets.add("Candidatos e auditoria");
const dictionarySheet = workbook.worksheets.add("Dicionário");

const COLORS = {
  navy: "#0F172A",
  blue: "#2563EB",
  paleBlue: "#DBEAFE",
  paleGreen: "#DCFCE7",
  paleAmber: "#FEF3C7",
  paleRed: "#FEE2E2",
  palePurple: "#EDE9FE",
  palePink: "#FCE7F3",
  gray: "#64748B",
  paleGray: "#F8FAFC",
  border: "#CBD5E1",
  white: "#FFFFFF",
};

function columnLetter(indexOneBased) {
  let index = indexOneBased;
  let result = "";
  while (index > 0) {
    const remainder = (index - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    index = Math.floor((index - 1) / 26);
  }
  return result;
}

function orderedHeaders(rows, preferred = []) {
  if (!rows.length) return preferred;
  const seen = new Set(preferred);
  const headers = [...preferred];
  for (const key of Object.keys(rows[0])) {
    if (!seen.has(key)) {
      seen.add(key);
      headers.push(key);
    }
  }
  return headers;
}

function typedValue(header, value) {
  if (value === undefined || value === null || value === "") return null;
  if ((header.includes("Data") || header.includes("data")) && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) {
    return new Date(`${value}T00:00:00Z`);
  }
  return value;
}

function matrixFor(rows, headers) {
  return [headers, ...rows.map((row) => headers.map((header) => typedValue(header, row[header])))];
}

function estimateWidth(header, rows) {
  const samples = rows.slice(0, 80).map((row) => String(row[header] ?? ""));
  const longest = Math.max(header.length, ...samples.map((value) => Math.min(value.length, 70)));
  if (/Descrição|Observação|Address|Endereço|Razão social|Nome \/ razão|Sinais|CNAEs secundários/.test(header)) return Math.min(44, Math.max(24, longest * 0.72));
  if (/Website|Url|URL/.test(header)) return 28;
  if (/CNPJ|CPF|CEP|código|Código/.test(header)) return 18;
  if (/Data|data/.test(header)) return 14;
  if (/Status|Situação|Confiança|Score|Optante|Decisão/.test(header)) return 18;
  return Math.min(28, Math.max(11, longest * 0.72));
}

function applyColumnFormats(sheet, headers, rows) {
  headers.forEach((header, index) => {
    const column = sheet.getRangeByIndexes(0, index, rows.length + 1, 1);
    column.format.columnWidth = estimateWidth(header, rows);
    const dataRange = rows.length ? sheet.getRangeByIndexes(1, index, rows.length, 1) : null;
    if (!dataRange) return;
    if (/Similaridade|aproveitamento|Aproveitamento/.test(header)) dataRange.format.numberFormat = "0.0%";
    if (/Capital social/.test(header)) dataRange.format.numberFormat = '"R$" #,##0.00';
    if (/Score|Confiança|Linha original|Posição|Quantidade/.test(header)) dataRange.format.numberFormat = "0";
    if (/Data|data/.test(header)) dataRange.format.numberFormat = "dd/mm/yyyy";
    if (/CNPJ|CPF|CEP|Account Id|Record Id|código|Código/.test(header)) dataRange.format.numberFormat = "@";
  });
}

function addDataSheet(sheet, rows, headers, tableName, options = {}) {
  const matrix = matrixFor(rows, headers);
  const lastColumn = columnLetter(headers.length);
  const lastRow = rows.length + 1;
  sheet.getRange(`A1:${lastColumn}${lastRow}`).values = matrix;
  const table = sheet.tables.add(`A1:${lastColumn}${lastRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: COLORS.navy,
    font: { bold: true, color: COLORS.white },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 42;
  if (rows.length) {
    sheet.getRange(`A2:${lastColumn}${lastRow}`).format.verticalAlignment = "top";
    sheet.getRange(`A2:${lastColumn}${lastRow}`).format.rowHeight = 20;
  }
  applyColumnFormats(sheet, headers, rows);
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(options.freezeColumns ?? 2);
  sheet.showGridLines = false;
  return { lastColumn, lastRow, table };
}

const companyHeaders = orderedHeaders(data.enriched, data.source_headers);
const companyLayout = addDataSheet(companiesSheet, data.enriched, companyHeaders, "EmpresasEnriquecidas", { freezeColumns: 2 });

const companyPrefixColors = [
  ["Match -", COLORS.paleBlue],
  ["Site -", COLORS.palePurple],
  ["Receita -", COLORS.paleGreen],
  ["Simples -", COLORS.paleAmber],
  ["MEI -", COLORS.paleAmber],
  ["Auditoria -", COLORS.palePink],
];
companyHeaders.forEach((header, index) => {
  const pair = companyPrefixColors.find(([prefix]) => header.startsWith(prefix));
  if (pair) {
    companiesSheet.getCell(0, index).format = { fill: pair[1], font: { bold: true, color: COLORS.navy }, wrapText: true };
  }
});

const companyStatusIndex = companyHeaders.indexOf("Match - Status");
if (companyStatusIndex >= 0) {
  const range = companiesSheet.getRangeByIndexes(1, companyStatusIndex, data.enriched.length, 1);
  range.conditionalFormats.add("containsText", { text: "Confirmado", format: { fill: COLORS.paleGreen, font: { color: "#166534", bold: true } } });
  range.conditionalFormats.add("containsText", { text: "Revisão", format: { fill: COLORS.paleAmber, font: { color: "#92400E", bold: true } } });
  range.conditionalFormats.add("containsText", { text: "Não encontrado", format: { fill: COLORS.paleRed, font: { color: "#991B1B", bold: true } } });
}

const confidenceIndex = companyHeaders.indexOf("Match - Confiança (0-5)");
if (confidenceIndex >= 0) {
  companiesSheet.getRangeByIndexes(1, confidenceIndex, data.enriched.length, 1).conditionalFormats.add("colorScale", {
    colors: ["#FEE2E2", "#FEF3C7", "#DCFCE7"],
    thresholds: ["min", "50%", "max"],
  });
}

const partnerHeaders = orderedHeaders(data.partners);
addDataSheet(partnersSheet, data.partners, partnerHeaders, "SociosAdministradores", { freezeColumns: 4 });

const reviewHeaders = orderedHeaders(data.review);
const reviewLayout = addDataSheet(reviewSheet, data.review, reviewHeaders, "FilaRevisao", { freezeColumns: 3 });
const decisionIndex = reviewHeaders.indexOf("Decisão humana");
const chosenIndex = reviewHeaders.indexOf("CNPJ escolhido");
if (data.review.length && decisionIndex >= 0) {
  const decisionRange = reviewSheet.getRangeByIndexes(1, decisionIndex, data.review.length, 1);
  decisionRange.dataValidation = { rule: { type: "list", values: ["PENDENTE", "APROVAR CANDIDATO 1", "APROVAR CANDIDATO 2", "APROVAR CANDIDATO 3", "REJEITAR TODOS"] } };
  decisionRange.conditionalFormats.add("containsText", { text: "PENDENTE", format: { fill: COLORS.paleAmber, font: { color: "#92400E", bold: true } } });
  decisionRange.conditionalFormats.add("beginsWith", { text: "APROVAR", format: { fill: COLORS.paleGreen, font: { color: "#166534", bold: true } } });
  decisionRange.conditionalFormats.add("containsText", { text: "REJEITAR", format: { fill: COLORS.paleRed, font: { color: "#991B1B", bold: true } } });
}
if (data.review.length && chosenIndex >= 0 && decisionIndex >= 0) {
  const candidate1 = reviewHeaders.indexOf("Candidato 1 - CNPJ");
  const candidate2 = reviewHeaders.indexOf("Candidato 2 - CNPJ");
  const candidate3 = reviewHeaders.indexOf("Candidato 3 - CNPJ");
  const d = columnLetter(decisionIndex + 1);
  const c1 = columnLetter(candidate1 + 1);
  const c2 = columnLetter(candidate2 + 1);
  const c3 = columnLetter(candidate3 + 1);
  const formulaCell = reviewSheet.getCell(1, chosenIndex);
  formulaCell.formulas = [[`=IF(${d}2="APROVAR CANDIDATO 1",${c1}2,IF(${d}2="APROVAR CANDIDATO 2",${c2}2,IF(${d}2="APROVAR CANDIDATO 3",${c3}2,"")))`]];
  reviewSheet.getRangeByIndexes(1, chosenIndex, data.review.length, 1).fillDown();
}

const auditHeaders = orderedHeaders(data.audit);
addDataSheet(auditSheet, data.audit, auditHeaders, "CandidatosAuditoria", { freezeColumns: 4 });

const dictionaryRows = [
  { "Grupo": "Escopo", "Campo / termo": "Piloto", "Definição": "Primeiras 200 linhas de dados do CSV, mantendo exatamente a ordem original." },
  { "Grupo": "Match", "Campo / termo": "Confirmado", "Definição": "CNPJ com combinação forte de nome e localização, ou CNPJ extraído do site e validado na Receita." },
  { "Grupo": "Match", "Campo / termo": "Revisão manual", "Definição": "Há candidato plausível, mas os sinais não permitem preencher automaticamente com segurança." },
  { "Grupo": "Match", "Campo / termo": "Não encontrado", "Definição": "Nenhum candidato atingiu o mínimo conservador. O campo CNPJ permanece vazio." },
  { "Grupo": "Match", "Campo / termo": "Aproveitamento", "Definição": "Empresas confirmadas divididas pelas 200 empresas do piloto. Revisões não contam como sucesso." },
  { "Grupo": "Sócios", "Campo / termo": "Pessoa física", "Definição": "CPF permanece mascarado conforme a publicação da Receita Federal." },
  { "Grupo": "Sócios", "Campo / termo": "Pessoa jurídica", "Definição": "É sinalizada como empresa, mas não recebe enriquecimento adicional neste piloto." },
  { "Grupo": "Simples", "Campo / termo": "Não disponível", "Definição": "O CNPJ raiz não foi localizado no arquivo de Simples da competência; não equivale automaticamente a NÃO optante." },
  { "Grupo": "Fonte", "Campo / termo": "Receita 2026-08", "Definição": "Dados abertos do CNPJ da Receita Federal, competência 2026-08." },
  { "Grupo": "Fonte", "Campo / termo": "Metadados oficiais", "Definição": "https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf" },
];
const dictionaryHeaders = orderedHeaders(dictionaryRows);
addDataSheet(dictionarySheet, dictionaryRows, dictionaryHeaders, "DicionarioCampos", { freezeColumns: 1 });
dictionarySheet.getRange(`C2:C${dictionaryRows.length + 1}`).format.wrapText = true;
dictionarySheet.getRange(`B1:B${dictionaryRows.length + 1}`).format.columnWidth = 22;
dictionarySheet.getRange(`C2:C${dictionaryRows.length + 1}`).format.columnWidth = 58;
dictionarySheet.getRange(`A2:C${dictionaryRows.length + 1}`).format.rowHeight = 64;

summarySheet.showGridLines = false;
summarySheet.mergeCells("A1:H2");
summarySheet.getRange("A1:H2").values = [["Piloto de enriquecimento — Receita Federal"]];
summarySheet.getRange("A1:H2").format = {
  fill: COLORS.navy,
  font: { bold: true, color: COLORS.white, size: 18 },
  verticalAlignment: "center",
  horizontalAlignment: "left",
};
summarySheet.getRange("A3:H3").merge();
summarySheet.getRange("A3:H3").values = [["Primeiras 200 empresas na ordem original • Base Receita 2026-08 • Critério conservador"]];
summarySheet.getRange("A3:H3").format = { fill: COLORS.paleGray, font: { color: COLORS.gray, italic: true } };

summarySheet.getRange("A5:A12").values = [
  ["Empresas analisadas"],
  ["Confirmadas"],
  ["Em revisão manual"],
  ["Não encontradas"],
  ["Aproveitamento confirmado"],
  ["Potencial com revisão"],
  ["Vínculos de sócios/administradores"],
  ["Confirmadas com registro de Simples"],
];
const statusColumnLetter = columnLetter(companyStatusIndex + 1);
const simplesAvailableIndex = companyHeaders.indexOf("Simples - Disponível na base");
const simplesColumnLetter = columnLetter(simplesAvailableIndex + 1);
summarySheet.getRange("B5:B12").formulas = [
  [`=COUNTA('Empresas enriquecidas'!$A$2:$A$${companyLayout.lastRow})`],
  [`=COUNTIF('Empresas enriquecidas'!$${statusColumnLetter}$2:$${statusColumnLetter}$${companyLayout.lastRow},"Confirmado")`],
  [`=COUNTIF('Empresas enriquecidas'!$${statusColumnLetter}$2:$${statusColumnLetter}$${companyLayout.lastRow},"Revisão manual")`],
  [`=COUNTIF('Empresas enriquecidas'!$${statusColumnLetter}$2:$${statusColumnLetter}$${companyLayout.lastRow},"Não encontrado")`],
  ["=B6/B5"],
  ["=(B6+B7)/B5"],
  [`=COUNTA('Sócios e administradores'!$A$2:$A$${data.partners.length + 1})`],
  [`=COUNTIF('Empresas enriquecidas'!$${simplesColumnLetter}$2:$${simplesColumnLetter}$${companyLayout.lastRow},"SIM")`],
];
summarySheet.getRange("A5:B12").format = { borders: { preset: "inside", style: "thin", color: COLORS.border } };
summarySheet.getRange("A5:A12").format = { fill: COLORS.paleGray, font: { bold: true, color: COLORS.navy } };
summarySheet.getRange("B5:B12").format = { fill: COLORS.white, font: { bold: true, color: COLORS.navy, size: 13 }, horizontalAlignment: "right" };
summarySheet.getRange("B9:B10").format.numberFormat = "0.0%";
summarySheet.getRange("A5:A12").format.columnWidth = 34;
summarySheet.getRange("B5:B12").format.columnWidth = 18;

summarySheet.getRange("D5:E8").values = [
  ["Resultado", "Empresas"],
  ["Confirmadas", null],
  ["Revisão manual", null],
  ["Não encontradas", null],
];
summarySheet.getRange("E6:E8").formulas = [["=B6"], ["=B7"], ["=B8"]];
summarySheet.getRange("D5:E5").format = { fill: COLORS.navy, font: { bold: true, color: COLORS.white } };
summarySheet.getRange("D5:E8").format.borders = { preset: "all", style: "thin", color: COLORS.border };
summarySheet.getRange("D5:D8").format.columnWidth = 22;
summarySheet.getRange("E5:E8").format.columnWidth = 14;

const chart = summarySheet.charts.add("bar", summarySheet.getRange("D5:E8"));
chart.title = "Resultado do piloto (empresas)";
chart.hasLegend = false;
chart.xAxis = { axisType: "textAxis" };
chart.yAxis = { numberFormatCode: "0" };
chart.setPosition("D10", "H24");

summarySheet.getRange("A14:B19").values = [
  ["Leitura dos resultados", ""],
  ["Aproveitamento", "Somente confirmados contam como sucesso."],
  ["Revisão", "Casos plausíveis permanecem sem CNPJ automático."],
  ["Sócios", "100 vínculos oficiais dos CNPJs confirmados; PF com CPF mascarado."],
  ["Empresa sócia", "Apenas sinalizada; não foi enriquecida."],
  ["Simples ausente", "Não foi interpretado automaticamente como não optante."],
];
summarySheet.getRange("B17").formulas = [[`=B11&" vínculos oficiais dos CNPJs confirmados; PF com CPF mascarado."`]];
summarySheet.getRange("A14:B14").merge();
summarySheet.getRange("A14:B14").format = { fill: COLORS.blue, font: { bold: true, color: COLORS.white } };
summarySheet.getRange("A15:A19").format = { fill: COLORS.paleBlue, font: { bold: true, color: COLORS.navy } };
summarySheet.getRange("B15:B19").format.wrapText = true;
summarySheet.getRange("B15:B19").format.columnWidth = 48;
summarySheet.getRange("A14:B19").format.borders = { preset: "outside", style: "thin", color: COLORS.border };
summarySheet.freezePanes.freezeRows(3);

const inspections = {};
inspections.summary = (await workbook.inspect({ kind: "table", range: "Resumo!A1:H24", include: "values,formulas", tableMaxRows: 24, tableMaxCols: 8 })).ndjson;
inspections.companies = (await workbook.inspect({ kind: "table", range: `Empresas enriquecidas!A1:L8`, include: "values,formulas", tableMaxRows: 8, tableMaxCols: 12 })).ndjson;
inspections.review = (await workbook.inspect({ kind: "table", range: `Fila de revisão!A1:N8`, include: "values,formulas", tableMaxRows: 8, tableMaxCols: 14 })).ndjson;
console.log(JSON.stringify(inspections));

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errorScan.ndjson);

const previews = [
  ["Resumo", "A1:H24", "resumo.png"],
  ["Empresas enriquecidas", "A1:L12", "empresas_inicio.png"],
  ["Empresas enriquecidas", `${columnLetter(Math.max(1, companyHeaders.length - 25))}1:${companyLayout.lastColumn}10`, "empresas_enriquecidas.png"],
  ["Sócios e administradores", "A1:Q12", "socios.png"],
  ["Fila de revisão", `A1:${reviewLayout.lastColumn}12`, "revisao.png"],
  ["Candidatos e auditoria", "A1:T12", "auditoria.png"],
  ["Dicionário", `A1:C${dictionaryRows.length + 1}`, "dicionario.png"],
];
for (const [sheetName, range, filename] of previews) {
  const preview = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
  await fs.writeFile(`${previewDir}/${filename}`, new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outputDir}/piloto_enriquecimento_receita_200.xlsx`);
console.log(JSON.stringify({ output: `${outputDir}/piloto_enriquecimento_receita_200.xlsx`, sheets: 6, rows: data.summary }));
