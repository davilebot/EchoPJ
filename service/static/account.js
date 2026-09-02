const form = document.querySelector("#account-form");
const message = document.querySelector("#account-message");
const submitButton = document.querySelector("#account-submit");

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
