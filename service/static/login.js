const form = document.querySelector("#login-form");
const message = document.querySelector("#login-message");
const submitButton = document.querySelector("#login-submit");

fetch("/api/auth/status").then((response) => response.json()).then((status) => {
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
    const payload = await response.json();
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
