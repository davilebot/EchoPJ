const grid = document.querySelector("#plans-grid");
const state = document.querySelector("#billing-state");
const signupAction = document.querySelector("[data-signup-cta]");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function money(cents) {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(cents) / 100);
}

function cycleLabel(cycle) {
  return { WEEKLY: "/semana", MONTHLY: "/mês", QUARTERLY: "/trimestre", SEMIANNUALLY: "/semestre", YEARLY: "/ano" }[cycle] || "";
}

function planCard(offer, enabled) {
  const checkout = enabled
    ? `<a class="plan-action" href="/login?next=${encodeURIComponent('/?tab=billing')}">Escolher ${escapeHtml(offer.name)}</a>`
    : `<span class="plan-action disabled">Disponível em breve</span>`;
  return `<article class="plan-card ${offer.highlighted ? "highlighted" : ""}">
    ${offer.highlighted ? '<span class="recommended">MAIS ESCOLHIDO</span>' : ""}
    <span class="plan-kind">${offer.kind === "subscription" ? "ASSINATURA" : "PACOTE AVULSO"}</span>
    <h3>${escapeHtml(offer.name)}</h3><p>${escapeHtml(offer.description)}</p>
    <div class="plan-price"><strong>${money(offer.price_cents)}</strong><small>${cycleLabel(offer.cycle)}</small></div>
    <div class="plan-credit"><b>${Number(offer.credits).toLocaleString("pt-BR")}</b> créditos${offer.cycle ? " por ciclo" : ""}</div>
    <ul>${offer.features.map((feature) => `<li>✓ ${escapeHtml(feature)}</li>`).join("")}</ul>${checkout}
  </article>`;
}

fetch("/api/billing/catalog").then(async (response) => {
  if (!response.ok) throw new Error("Não foi possível carregar as condições agora.");
  const catalog = await response.json();
  state.textContent = catalog.enabled ? `Checkout seguro · ${catalog.payment_methods.join(" e ")}` : "Lançamento comercial em preparação";
  grid.innerHTML = catalog.offers.length
    ? catalog.offers.map((offer) => planCard(offer, catalog.enabled)).join("")
    : `<article class="catalog-pending"><span>CATÁLOGO EM PREPARAÇÃO</span><h3>Os planos comerciais serão publicados aqui.</h3><p>A plataforma já está disponível para acessos autorizados. Preços, volumes de créditos e condições serão exibidos antes da abertura do checkout.</p><a href="/login">Entrar na plataforma</a></article>`;
}).catch((error) => {
  state.textContent = "Catálogo indisponível";
  grid.innerHTML = `<article class="catalog-pending"><h3>Não foi possível carregar os planos.</h3><p>${escapeHtml(error.message)}</p><button type="button" onclick="location.reload()">Tentar novamente</button></article>`;
});

fetch("/api/auth/status").then((response) => response.ok ? response.json() : null).then((status) => {
  signupAction.href = status?.signup_available ? "/signup" : "/login";
  signupAction.textContent = status?.signup_available ? "Começar avaliação" : "Acessar plataforma";
}).catch(() => {});
