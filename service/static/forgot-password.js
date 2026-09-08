const forgotForm = document.querySelector("#forgot-form");
const forgotMessage = document.querySelector("#forgot-message");
const forgotSubmit = document.querySelector("#forgot-submit");

fetch("/api/auth/status").then((response) => response.json()).then((status) => {
  if (status.password_reset_available) return;
  forgotMessage.textContent = "O envio automático ainda está sendo configurado. Peça ao administrador para recuperar seu acesso.";
  forgotMessage.classList.remove("hidden");
  forgotSubmit.disabled = true;
}).catch(() => {});

forgotForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  forgotMessage.classList.add("hidden");
  forgotMessage.dataset.type = "";
  forgotSubmit.disabled = true;
  forgotSubmit.textContent = "Enviando…";
  try {
    const response = await fetch("/api/auth/password-reset/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier: document.querySelector("#forgot-identifier").value }),
    });
    if (!response.ok) throw new Error("Não foi possível processar a solicitação. Tente novamente.");
    forgotMessage.textContent = "Se este e-mail estiver cadastrado, o link chegará em alguns minutos. Confira também a caixa de spam.";
    forgotMessage.dataset.type = "success";
    forgotMessage.classList.remove("hidden");
    forgotForm.reset();
  } catch (error) {
    forgotMessage.textContent = error.message;
    forgotMessage.classList.remove("hidden");
  } finally {
    forgotSubmit.disabled = false;
    forgotSubmit.textContent = "Enviar link de recuperação";
  }
});
