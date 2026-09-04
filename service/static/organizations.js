const $ = (selector) => document.querySelector(selector);
let currentOrg = null;
let currentUser = null;
const roleNames = { admin: "Administrador", member: "Membro" };
const statusNames = { pending: "Pendente", accepted: "Aceito", revoked: "Cancelado", expired: "Expirado" };
function notify(text, success = false) {
  $("#org-message").textContent = text;
  $("#org-message").dataset.type = success ? "success" : "error";
  $("#org-message").classList.remove("hidden");
}
async function api(path, method = "GET", body) {
  const response = await fetch(path, { method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
  if (response.status === 401) { location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`); throw new Error("Entre novamente."); }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Confira os campos informados e tente novamente.");
  return data;
}
function textElement(tag, text, className = "") {
  const element = document.createElement(tag); element.textContent = text; element.className = className; return element;
}
function actionButton(text, action) {
  const button = textElement("button", text, "secondary-button"); button.type = "button";
  button.addEventListener("click", async () => { button.disabled = true; try { await action(); } catch (error) { notify(error.message); } finally { button.disabled = false; } });
  return button;
}
function row(title, subtitle) {
  const item = textElement("div", "", "team-row"); const info = textElement("div", "", "team-info");
  info.append(textElement("strong", title), textElement("small", subtitle)); item.append(info); return item;
}
async function loadTeam() {
  if (!currentOrg || currentOrg.role !== "admin") return;
  const data = await api(`/api/organizations/${currentOrg.id}`);
  $("#members-list").replaceChildren();
  for (const member of data.members) {
    const item = row(member.identifier + (member.id === currentUser.id ? " (você)" : ""), roleNames[member.role]);
    const actions = textElement("div", "", "org-actions");
    const role = member.role === "admin" ? "member" : "admin";
    actions.append(actionButton(role === "admin" ? "Tornar administrador" : "Tornar membro", async () => {
      if (!confirm(`Alterar a permissão de ${member.identifier} para ${roleNames[role]}?`)) return;
      await api(`/api/organizations/${currentOrg.id}/members/${member.id}`, "PUT", { role });
      if (member.id === currentUser.id) location.reload(); else { await loadTeam(); notify("Permissão atualizada.", true); }
    }), actionButton("Remover", async () => {
      if (!confirm(`Remover ${member.identifier} desta organização? As consultas da organização serão preservadas.`)) return;
      await api(`/api/organizations/${currentOrg.id}/members/${member.id}`, "DELETE");
      if (member.id === currentUser.id) location.href = "/organizations"; else { await loadTeam(); notify("Acesso removido desta organização.", true); }
    }));
    item.append(actions); $("#members-list").append(item);
  }
  $("#invitations-list").replaceChildren();
  for (const invite of data.invitations) {
    const item = row(invite.email, `${roleNames[invite.role]} · ${statusNames[invite.status]} · validade ${new Date(invite.expires_at).toLocaleDateString("pt-BR")}`);
    if (invite.status === "pending") item.append(actionButton("Cancelar convite", async () => {
      if (!confirm(`Cancelar o convite de ${invite.email}?`)) return;
      await api(`/api/organizations/${currentOrg.id}/invitations/${invite.id}`, "DELETE");
      $("#invite-result").classList.add("hidden"); await loadTeam(); notify("Convite cancelado.", true);
    }));
    $("#invitations-list").append(item);
  }
  if (!data.invitations.length) $("#invitations-list").append(textElement("p", "Nenhum convite criado ainda.", "muted"));
  const auditNames = { "organization.migrated": "Organização inicial criada", "organization.created": "Organização criada", "organization.renamed": "Organização renomeada", "member.removed": "Membro removido", "member.role_changed": "Permissão alterada", "invitation.created": "Convite criado", "invitation.revoked": "Convite cancelado", "invitation.accepted": "Convite aceito" };
  $("#audit-list").replaceChildren(...data.audit.map((event) => row(auditNames[event.action] || event.action, `${event.actor} · ${new Date(event.created_at).toLocaleString("pt-BR")}${event.target ? " · " + event.target : ""}`)));
}
async function loadOrganizations() {
  const data = await api("/api/organizations"); currentUser = data.user;
  const selected = new URLSearchParams(location.search).get("organization");
  currentOrg = data.organizations.find((org) => String(org.id) === selected) || (!selected ? data.organizations[0] : null);
  $("#org-select").replaceChildren(...data.organizations.map((org) => { const option = textElement("option", org.name); option.value = org.id; return option; }));
  $("#org-select").disabled = !data.organizations.length;
  $("#create-panel").classList.toggle("hidden", !data.can_create);
  if (!currentOrg) {
    notify(data.organizations.length ? "Você não tem acesso à organização solicitada. Selecione uma das suas organizações." : "Sua conta ainda não está em uma organização. Peça um convite a um administrador."); return;
  }
  $("#org-select").value = currentOrg.id;
  $("#org-role").textContent = `Seu acesso: ${roleNames[currentOrg.role]}. ${currentOrg.role === "member" ? "Peça ao administrador alterações na equipe." : ""}`;
  $("#org-name").value = currentOrg.name;
  $("#back-platform").href = `/?organization=${currentOrg.id}`;
  $("#team-panel").classList.toggle("hidden", currentOrg.role !== "admin");
  $("#send-email").disabled = !data.email_delivery_available; $("#send-email").checked = data.email_delivery_available;
  $("#email-status").textContent = data.email_delivery_available ? "O convite poderá ser enviado automaticamente ao e-mail informado." : "Envio automático ainda não configurado. Gere o link e compartilhe por e-mail ou mensagem.";
  await loadTeam();
}
function submit(selector, action) {
  $(selector).addEventListener("submit", async (event) => {
    event.preventDefault(); const button = event.target.querySelector('button[type="submit"]'); button.disabled = true;
    try { await action(); } catch (error) { notify(error.message); } finally { button.disabled = false; }
  });
}
$("#org-select").addEventListener("change", (event) => { location.href = `/organizations?organization=${event.target.value}`; });
submit("#create-org-form", async () => { const org = await api("/api/organizations", "POST", { name: $("#new-org-name").value }); location.href = `/organizations?organization=${org.id}`; });
submit("#rename-form", async () => { await api(`/api/organizations/${currentOrg.id}`, "PUT", { name: $("#org-name").value }); await loadOrganizations(); notify("Nome da organização atualizado.", true); });
submit("#invite-form", async () => {
  const data = await api(`/api/organizations/${currentOrg.id}/invitations`, "POST", { email: $("#invite-email").value, role: $("#invite-role").value, send_email: $("#send-email").checked });
  $("#invite-link").value = data.link;
  $("#invite-result-title").textContent = data.delivery === "sent" ? "Convite enviado por e-mail." : data.delivery === "failed" ? "O e-mail não foi enviado. Compartilhe o link abaixo." : "Convite pronto para compartilhar.";
  $("#email-invite").href = `mailto:${encodeURIComponent(data.email)}?subject=${encodeURIComponent("Convite para o EchoPJs")}&body=${encodeURIComponent(`Você foi convidado(a) para ${currentOrg.name}. Acesse ${data.link}\nO link é válido por 7 dias.`)}`;
  $("#invite-result").classList.remove("hidden"); $("#copy-invite").textContent = "Copiar convite";
  await loadTeam(); notify("Convite gerado. Um convite anterior para o mesmo e-mail foi substituído, se existia.", true);
});
$("#copy-invite").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#invite-link").value); $("#copy-invite").textContent = "Copiado!"; }
  catch (_) { $("#invite-link").select(); notify("Selecione o link e use Copiar no seu navegador."); }
});
loadOrganizations().catch((error) => notify(error.message));
