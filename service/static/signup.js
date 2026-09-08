const signupForm = document.querySelector("#signup-form");
const signupMessage = document.querySelector("#signup-message");
const signupSubmit = document.querySelector("#signup-submit");

function signupFeedback(message, success = false) {
  signupMessage.textContent = message;
  signupMessage.dataset.type = success ? "success" : "";
  signupMessage.classList.remove("hidden");
}

fetch("/api/auth/status").then((response) => response.json()).then((status) => {
  if (status.signup_available) return;
  signupFeedback("Novos cadastros ainda não estão abertos. Peça um convite à equipe EchoHub.");
  signupSubmit.disabled = true;
}).catch(() => {});

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
      }),
    });
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
