const isPrivacy = location.pathname === "/privacidade";
const selectedDocument = isPrivacy ? "privacy" : "terms";
const title = isPrivacy ? "Política de Privacidade" : "Termos de Uso";

document.title = `${title} — EchoPJs`;
document.querySelector("#document-title").textContent = title;
document.querySelector("#document-intro").textContent = isPrivacy
  ? "Como protegemos os dados de contas, equipes e operações no EchoPJs."
  : "As regras para criar uma conta, contratar créditos e usar o EchoPJs.";
document.querySelector(`#${selectedDocument}-document`).classList.remove("hidden");
document.querySelector(`#${selectedDocument}-navigation`).classList.remove("hidden");
document.querySelector(`#${isPrivacy ? "terms" : "privacy"}-navigation`).classList.add("hidden");

function setText(selector, value, fallback = "") {
  document.querySelectorAll(selector).forEach((element) => {
    element.textContent = value || fallback;
    element.classList.toggle("hidden", !value && !fallback);
  });
}

function setEmail(selector, value, fallback) {
  document.querySelectorAll(selector).forEach((element) => {
    element.textContent = value || fallback;
    element.removeAttribute("href");
    if (value) element.href = `mailto:${value}`;
  });
}

fetch("/api/legal/documents").then(async (response) => {
  if (!response.ok) throw new Error("Não foi possível carregar as informações do documento.");
  return response.json();
}).then((legal) => {
  const documentMeta = legal.documents[selectedDocument];
  document.querySelector("#document-status").textContent = legal.configured ? "Publicado" : "Em preparação";
  document.querySelector("#document-version").textContent = documentMeta.version || "Rascunho";
  document.querySelector("#document-date").textContent = legal.effective_date
    ? new Date(`${legal.effective_date}T12:00:00`).toLocaleDateString("pt-BR")
    : "A definir";
  document.querySelector("#draft-banner").classList.toggle("hidden", legal.configured);
  setText("[data-operator-name]", legal.company.name, "Empresa operadora em configuração");
  setText("[data-operator-document]", legal.company.document);
  setText("[data-operator-address]", legal.company.address);
  setEmail("[data-contact-email]", legal.company.contact_email, "Contato comercial em configuração");
  setEmail("[data-privacy-email]", legal.company.privacy_email, "Contato de privacidade em configuração");
  if (legal.retention_policy) document.querySelector("#retention-policy").textContent = legal.retention_policy;
}).catch((error) => {
  document.querySelector("#document-status").textContent = "Indisponível";
  document.querySelector("#draft-banner").classList.remove("hidden");
  document.querySelector("#draft-banner span").textContent = error.message;
});

document.querySelector("#theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("echopjs-theme", next); } catch (_) {}
});
