// Fragments are not transmitted in HTTP requests or access logs.
const inviteToken = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname);
const inviteMessage = document.querySelector("#invite-message");
function showError(message) { inviteMessage.textContent = message; inviteMessage.classList.remove("hidden"); }
async function invitationApi(action, extra = {}) {
  const response = await fetch(`/api/invitations/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: inviteToken, ...extra }) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Confira o convite e a senha informados.");
  return data;
}
async function preview() {
  if (!inviteToken) throw new Error("Abra o link completo enviado pelo administrador. Se já atualizou esta página, abra o convite novamente.");
  const data = await invitationApi("preview");
  const roleName = { admin: "administrador", member: "membro", viewer: "acesso de consulta" }[data.role] || "membro";
  document.querySelector("#invite-description").textContent = `Participe de ${data.organization_name} com ${roleName}.`;
  document.querySelector("#invited-email").value = data.email;
  document.querySelector("#accept-form").classList.remove("hidden");
}
document.querySelector("#show-password").addEventListener("change", (event) => { document.querySelector("#invite-password").type = event.target.checked ? "text" : "password"; });
document.querySelector("#accept-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.target.querySelector("button"); button.disabled = true;
  inviteMessage.classList.add("hidden");
  try {
    const data = await invitationApi("accept", { password: document.querySelector("#invite-password").value });
    document.querySelector("#invite-password").value = "";
    location.replace(`/?organization=${data.organization_id}`);
  } catch (error) { showError(error.message); } finally { button.disabled = false; }
});
preview().catch((error) => { document.querySelector("#invite-description").textContent = "Não foi possível abrir este convite."; showError(error.message); });
