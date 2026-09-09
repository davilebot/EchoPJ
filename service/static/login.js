const form = document.querySelector("#login-form");
const message = document.querySelector("#login-message");
const submitButton = document.querySelector("#login-submit");

async function responsePayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  throw new Error(response.ok
    ? "O servidor respondeu em um formato inesperado. Recarregue a página e tente novamente."
    : "O acesso está temporariamente indisponível. Aguarde alguns segundos e tente novamente.");
}

fetch("/api/auth/status").then(responsePayload).then((status) => {
  document.querySelector("#signup-prompt").classList.toggle("hidden", !status.signup_available);
  document.querySelector("#invite-prompt").classList.toggle("hidden", status.signup_available);
}).catch(() => {});

function safeNextPath() {
  const requested = new URLSearchParams(window.location.search).get("next") || "/";
  const target = new URL(requested, window.location.origin);
  return requested.startsWith("/") && target.origin === window.location.origin ? target.pathname + target.search : "/";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.classList.add("hidden");
  submitButton.disabled = true;
  submitButton.textContent = "Entrando…";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        identifier: document.querySelector("#login-identifier").value,
        password: document.querySelector("#login-password").value,
      }),
    });
    const payload = await responsePayload(response);
    if (!response.ok) throw new Error(payload.detail || "Não foi possível entrar.");
    window.location.replace(safeNextPath());
  } catch (error) {
    message.textContent = error.message;
    message.classList.remove("hidden");
    document.querySelector("#login-password").select();
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Entrar no EchoPJs";
  }
});
