const form = document.querySelector("#account-form");
const message = document.querySelector("#account-message");
const submitButton = document.querySelector("#account-submit");
const organizationId = new URLSearchParams(location.search).get("organization");
const privacyMessage = document.querySelector("#privacy-message");
const privacyState = document.querySelector("#privacy-request-state");
const privacyStatusLabels = {
  requested: "Solicitação recebida",
  in_review: "Solicitação em análise",
  waiting_user: "Aguardando suas informações",
  approved: "Exclusão aprovada para processamento",
  canceled: "Solicitação cancelada",
  closed: "Solicitação encerrada",
};
if (organizationId && /^\d+$/.test(organizationId)) {
  document.querySelector("#back-platform").href = `/?organization=${organizationId}`;
  document.querySelector("#manage-organizations").href = `/organizations?organization=${organizationId}`;
}

function showMessage(text, type = "error") {
  message.textContent = text;
  message.dataset.type = type;
  message.classList.remove("hidden");
}

async function loadAccount() {
  const response = await fetch("/api/auth/me");
  if (response.status === 401) {
    window.location.replace("/login?next=/account");
    return;
  }
  const payload = await response.json();
  if (!response.ok) {
    showMessage(payload.detail || "Não foi possível carregar sua conta.");
    return;
  }
  document.querySelector("#account-identifier").value = payload.identifier;
}

function formatPrivacyDate(value) {
  return value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function renderLegalAcceptances(acceptances) {
  const target = document.querySelector("#legal-acceptances");
  const latest = new Map();
  acceptances.forEach((item) => {
    if (!latest.has(item.document_type)) latest.set(item.document_type, item);
  });
  const documents = [
    ["terms", "Termos de Uso", "/termos"],
    ["privacy", "Política de Privacidade", "/privacidade"],
  ];
  target.innerHTML = documents.map(([type, label, href]) => {
    const acceptance = latest.get(type);
    const detail = acceptance
      ? `Versão ${acceptance.document_version} · aceita em ${formatPrivacyDate(acceptance.accepted_at)}`
      : "Sem aceite registrado nesta conta";
    return `<div class="legal-acceptance"><div><strong>${label}</strong><small>${detail}</small></div><a class="text-link" href="${href}" target="_blank" rel="noopener">Ler documento</a></div>`;
  }).join("");
}

async function loadLegalAcceptances() {
  try {
    const response = await fetch("/api/legal/acceptances");
    if (!response.ok) throw new Error();
    renderLegalAcceptances((await response.json()).acceptances || []);
  } catch (_) {
    document.querySelector("#legal-acceptances").innerHTML = '<p class="privacy-note">Não foi possível carregar os documentos aceitos.</p>';
  }
}

function showPrivacyMessage(text, type = "error") {
  privacyMessage.textContent = text;
  privacyMessage.dataset.type = type;
  privacyMessage.classList.remove("hidden");
}

function renderPrivacyRequest(request) {
  privacyState.classList.toggle("hidden", !request);
  if (!request) return;
  const active = ["requested", "in_review", "waiting_user", "approved"].includes(request.status);
  document.querySelector("#privacy-request-title").textContent = privacyStatusLabels[request.status] || request.status;
  document.querySelector("#privacy-request-detail").textContent = `Atualizada em ${formatPrivacyDate(request.updated_at)}${request.resolution_note ? ` · ${request.resolution_note}` : ""}`;
  document.querySelector("#privacy-cancel").classList.toggle("hidden", !active);
  document.querySelector("#privacy-delete").disabled = active;
}

async function loadPrivacy() {
  try {
    const response = await fetch("/api/privacy");
    if (!response.ok) throw new Error("Não foi possível carregar seus controles de privacidade.");
    renderPrivacyRequest((await response.json()).deletion_request);
  } catch (error) {
    showPrivacyMessage(error.message);
  }
}

async function runPrivacyButton(button, busyLabel, action) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = busyLabel;
  privacyMessage.classList.add("hidden");
  try { await action(); }
  catch (error) { showPrivacyMessage(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}

document.querySelector("#privacy-export").addEventListener("click", () => runPrivacyButton(
  document.querySelector("#privacy-export"), "Preparando arquivo…", async () => {
    const password = document.querySelector("#privacy-password").value;
    if (!password) throw new Error("Informe sua senha atual.");
    const response = await fetch("/api/privacy/export", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: password }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Não foi possível exportar seus dados.");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "echopjs-dados-da-conta.json";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    document.querySelector("#privacy-password").value = "";
    showPrivacyMessage("Arquivo preparado com os dados da sua conta.", "success");
  }
));

document.querySelector("#privacy-delete").addEventListener("click", () => runPrivacyButton(
  document.querySelector("#privacy-delete"), "Registrando…", async () => {
    const password = document.querySelector("#privacy-password").value;
    if (!password) throw new Error("Informe sua senha atual.");
    if (!window.confirm("Registrar uma solicitação de exclusão da sua conta? O pedido será analisado antes de qualquer remoção.")) return;
    const response = await fetch("/api/privacy/deletion-requests", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: password, reason: document.querySelector("#privacy-reason").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Não foi possível registrar a solicitação.");
    document.querySelector("#privacy-password").value = "";
    renderPrivacyRequest(payload);
    showPrivacyMessage(payload.created ? "Solicitação registrada para análise." : "Sua solicitação já estava registrada.", "success");
  }
));

document.querySelector("#privacy-cancel").addEventListener("click", () => runPrivacyButton(
  document.querySelector("#privacy-cancel"), "Cancelando…", async () => {
    const response = await fetch("/api/privacy/deletion-requests/current", { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Não foi possível cancelar a solicitação.");
    renderPrivacyRequest(payload);
    showPrivacyMessage("Solicitação cancelada.", "success");
  }
));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.classList.add("hidden");
  const newPassword = document.querySelector("#new-password").value;
  const confirmation = document.querySelector("#confirm-password").value;
  if (newPassword !== confirmation) {
    showMessage("A confirmação não é igual à nova senha.");
    return;
  }
  if (newPassword && newPassword.length < 8) {
    showMessage("A nova senha precisa ter pelo menos 8 caracteres.");
    return;
  }
  submitButton.disabled = true;
  submitButton.textContent = "Salvando…";
  try {
    const body = {
      identifier: document.querySelector("#account-identifier").value,
      current_password: document.querySelector("#current-password").value,
    };
    if (newPassword) body.new_password = newPassword;
    const response = await fetch("/api/auth/account", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Não foi possível atualizar o acesso.");
    document.querySelector("#current-password").value = "";
    document.querySelector("#new-password").value = "";
    document.querySelector("#confirm-password").value = "";
    showMessage("Acesso atualizado. Use o novo e-mail e a nova senha no próximo login.", "success");
  } catch (error) {
    showMessage(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Salvar novo acesso";
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.replace("/login");
});

loadAccount();
loadPrivacy();
loadLegalAcceptances();
