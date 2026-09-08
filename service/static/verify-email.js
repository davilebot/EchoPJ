const verificationToken = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname);
const verifyTitle = document.querySelector("#verify-title");
const verifyDescription = document.querySelector("#verify-description");
const verifyMessage = document.querySelector("#verify-message");

async function verifySignup() {
  if (!verificationToken) throw new Error("Abra o link completo enviado ao seu e-mail ou solicite um novo cadastro.");
  const response = await fetch("/api/auth/signup/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: verificationToken }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Não foi possível confirmar o cadastro.");
  verifyTitle.textContent = "Workspace criado.";
  verifyDescription.textContent = "Seu e-mail foi confirmado e sua empresa já está pronta.";
  verifyMessage.textContent = "Abrindo o EchoPJs…";
  verifyMessage.dataset.type = "success";
  verifyMessage.classList.remove("hidden");
  setTimeout(() => location.replace(`/?organization=${payload.organization_id}`), 700);
}

verifySignup().catch((error) => {
  verifyTitle.textContent = "Não foi possível confirmar.";
  verifyDescription.textContent = "O link pode ter expirado ou já ter sido utilizado.";
  verifyMessage.textContent = error.message;
  verifyMessage.classList.remove("hidden");
});
