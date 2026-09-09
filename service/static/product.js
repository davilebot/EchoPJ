const signupActions = document.querySelectorAll("[data-signup-cta]");

fetch("/api/auth/status").then((response) => response.ok ? response.json() : null).then((status) => {
  signupActions.forEach((action) => {
    action.href = status?.signup_available ? "/signup" : "/login";
    action.textContent = status?.signup_available ? "Começar avaliação" : "Acessar plataforma";
  });
}).catch(() => {});

fetch("/health").then((response) => response.ok ? response.json() : null).then((health) => {
  if (health?.dataset_version) document.querySelector("#public-dataset").textContent = health.dataset_version;
}).catch(() => {});
