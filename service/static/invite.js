// Fragments are not transmitted in HTTP requests or access logs.
const inviteToken = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname);
const inviteMessage = document.querySelector("#invite-message");
const legalConsent = document.querySelector("#invite-legal-consent");
let legalRequired = false;
let termsVersion = "";
let privacyVersion = "";
function showError(message) { inviteMessage.textContent = message; inviteMessage.classList.remove("hidden"); }
async function invitationApi(action, extra = {}) {
  const response = await fetch(`/api/invitations/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: inviteToken, ...extra }) });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) throw new Error("O convite está temporariamente indisponível. Aguarde alguns segundos e abra o link novamente.");
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
  legalRequired = Boolean(data.legal_acceptance_required);
  if (legalRequired) {
    termsVersion = data.legal?.documents?.terms?.version || "";
    privacyVersion = data.legal?.documents?.privacy?.version || "";
    document.querySelector("#invite-legal-versions").textContent = `Versões: termos ${termsVersion} · privacidade ${privacyVersion}`;
    document.querySelector("#invite-legal-consent-row").classList.remove("hidden");
    legalConsent.disabled = false;
    legalConsent.required = true;
  }
  document.querySelector("#accept-form").classList.remove("hidden");
}
document.querySelector("#show-password").addEventListener("change", (event) => { document.querySelector("#invite-password").type = event.target.checked ? "text" : "password"; });
document.querySelector("#accept-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.target.querySelector("button"); button.disabled = true;
  inviteMessage.classList.add("hidden");
  try {
    if (legalRequired && !legalConsent.checked) throw new Error("Leia e aceite os documentos atuais para entrar na organização.");
    const data = await invitationApi("accept", {
      password: document.querySelector("#invite-password").value,
      accept_terms: legalRequired ? legalConsent.checked : false,
      terms_version: legalRequired ? termsVersion : "",
      privacy_version: legalRequired ? privacyVersion : "",
    });
    document.querySelector("#invite-password").value = "";
    location.replace(`/?organization=${data.organization_id}`);
  } catch (error) { showError(error.message); } finally { button.disabled = false; }
});
preview().catch((error) => { document.querySelector("#invite-description").textContent = "Não foi possível abrir este convite."; showError(error.message); });
