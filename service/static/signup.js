const signupForm = document.querySelector("#signup-form");
const signupMessage = document.querySelector("#signup-message");
const signupSubmit = document.querySelector("#signup-submit");
const legalConsent = document.querySelector("#signup-legal-consent");
const legalVersions = document.querySelector("#signup-legal-versions");
let termsVersion = "";
let privacyVersion = "";

function signupFeedback(message, success = false) {
  signupMessage.textContent = message;
  signupMessage.dataset.type = success ? "success" : "";
  signupMessage.classList.remove("hidden");
}

fetch("/api/auth/status").then(async (response) => {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) throw new Error("O cadastro está temporariamente indisponível.");
  const status = await response.json();
  if (!response.ok) throw new Error(status.detail || "Não foi possível carregar o cadastro.");
  const terms = status.legal?.documents?.terms;
  const privacy = status.legal?.documents?.privacy;
  termsVersion = terms?.version || "";
  privacyVersion = privacy?.version || "";
  if (termsVersion && privacyVersion) {
    legalVersions.textContent = `Versões: termos ${termsVersion} · privacidade ${privacyVersion}`;
  } else {
    legalVersions.textContent = "Documentos em preparação";
  }
  if (status.signup_available) {
    legalConsent.disabled = false;
    signupSubmit.disabled = false;
    signupSubmit.textContent = "Criar conta";
    return;
  }
  const blockerMessages = {
    closed: "Novos cadastros ainda não estão abertos. Peça um convite à equipe EchoHub.",
    email: "O cadastro aguarda a configuração do envio do e-mail de confirmação.",
    legal: "O cadastro aguarda a publicação dos Termos de Uso e da Política de Privacidade.",
  };
  signupFeedback(blockerMessages[status.signup_blocker] || "Novos cadastros ainda não estão disponíveis.");
  signupSubmit.textContent = "Cadastro indisponível";
}).catch((error) => {
  legalVersions.textContent = "Documentos indisponíveis";
  signupFeedback(error.message);
  signupSubmit.textContent = "Cadastro indisponível";
});

document.querySelector("#show-signup-password").addEventListener("change", (event) => {
  const type = event.target.checked ? "text" : "password";
  document.querySelector("#signup-password").type = type;
  document.querySelector("#signup-confirmation").type = type;
});

signupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  signupMessage.classList.add("hidden");
  const password = document.querySelector("#signup-password").value;
  if (password !== document.querySelector("#signup-confirmation").value) {
    signupFeedback("As duas senhas precisam ser iguais.");
    document.querySelector("#signup-confirmation").select();
    return;
  }
  if (!legalConsent.checked || !termsVersion || !privacyVersion) {
    signupFeedback("Leia e aceite os documentos atuais para criar a conta.");
    legalConsent.focus();
    return;
  }
  signupSubmit.disabled = true;
  signupSubmit.textContent = "Criando…";
  try {
    const response = await fetch("/api/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.querySelector("#signup-company").value,
        email: document.querySelector("#signup-email").value,
        password,
        accept_terms: legalConsent.checked,
        terms_version: termsVersion,
        privacy_version: privacyVersion,
      }),
    });
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) throw new Error("O cadastro está temporariamente indisponível. Aguarde alguns segundos e tente novamente.");
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Não foi possível criar o cadastro.");
    signupForm.reset();
    signupFeedback(`Enviamos a confirmação para ${payload.email}. Abra o link para ativar seu workspace.`, true);
    signupSubmit.textContent = "E-mail enviado";
  } catch (error) {
    signupFeedback(error.message);
    signupSubmit.disabled = false;
    signupSubmit.textContent = "Criar conta";
  }
});
