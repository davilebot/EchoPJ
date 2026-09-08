const resetToken = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname);
const resetForm = document.querySelector("#reset-form");
const resetMessage = document.querySelector("#reset-message");
const resetSubmit = document.querySelector("#reset-submit");

function resetError(message) {
  resetMessage.textContent = message;
  resetMessage.dataset.type = "";
  resetMessage.classList.remove("hidden");
}

if (!resetToken) {
  resetError("Abra o link completo enviado ao seu e-mail ou solicite um novo.");
  resetSubmit.disabled = true;
}

document.querySelector("#show-reset-password").addEventListener("change", (event) => {
  const type = event.target.checked ? "text" : "password";
  document.querySelector("#reset-password").type = type;
  document.querySelector("#reset-confirmation").type = type;
});

resetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!resetToken) return;
  resetMessage.classList.add("hidden");
  const password = document.querySelector("#reset-password").value;
  const confirmation = document.querySelector("#reset-confirmation").value;
  if (password !== confirmation) {
    resetError("As duas senhas precisam ser iguais.");
    document.querySelector("#reset-confirmation").select();
    return;
  }
  resetSubmit.disabled = true;
  resetSubmit.textContent = "Salvando…";
  try {
    const response = await fetch("/api/auth/password-reset/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: resetToken, new_password: password }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Não foi possível redefinir a senha.");
    resetForm.reset();
    resetMessage.textContent = "Senha alterada. Abrindo seu workspace…";
    resetMessage.dataset.type = "success";
    resetMessage.classList.remove("hidden");
    setTimeout(() => location.replace("/"), 700);
  } catch (error) {
    resetError(error.message);
    resetSubmit.disabled = false;
    resetSubmit.textContent = "Salvar nova senha";
  }
});
