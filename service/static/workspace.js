const workspaceFetch = window.fetch.bind(window);
const requestedWorkspaceOrganization = new URLSearchParams(location.search).get("organization");
let activeWorkspaceOrganization = /^\d+$/.test(requestedWorkspaceOrganization || "") ? requestedWorkspaceOrganization : null;
window.echoCan = () => true;

window.fetch = function workspaceScopedFetch(input, options = {}) {
  const target = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.origin);
  if (activeWorkspaceOrganization && target.origin === location.origin && target.pathname.startsWith("/api/")) {
    const sourceHeaders = options.headers || (typeof Request !== "undefined" && input instanceof Request ? input.headers : undefined);
    const headers = new Headers(sourceHeaders);
    if (!headers.has("X-Organization-Id")) headers.set("X-Organization-Id", activeWorkspaceOrganization);
    return workspaceFetch(input, { ...options, headers });
  }
  return workspaceFetch(input, options);
};

window.echoWorkspace = workspaceFetch("/api/organizations").then(async (response) => {
  if (response.status === 401) { location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`); return null; }
  if (!response.ok) throw new Error("Não foi possível carregar suas organizações. Atualize a página.");
  const data = await response.json();
  const requested = new URLSearchParams(location.search).get("organization");
  const org = requested ? data.organizations.find((item) => String(item.id) === requested) : data.organizations[0];
  if (!org) { location.replace("/organizations"); return null; }
  activeWorkspaceOrganization = String(org.id);
  window.echoCan = (capability) => Boolean(org.permissions?.[capability]);
  const capabilityDatasetNames = { search: "capabilitySearch", export: "capabilityExport", manage_library: "capabilityManageLibrary", run_jobs: "capabilityRunJobs", manage_team: "capabilityManageTeam" };
  Object.entries(capabilityDatasetNames).forEach(([capability, datasetName]) => {
    document.documentElement.dataset[datasetName] = String(window.echoCan(capability));
  });
  const select = document.querySelector("#workspace-organization");
  select.replaceChildren(...data.organizations.map((item) => { const option = document.createElement("option"); option.value = item.id; option.textContent = item.name; return option; }));
  select.value = org.id; select.disabled = false;
  document.querySelector("#organizations-link").href = `/organizations?organization=${org.id}`;
  document.querySelector("#mobile-organizations-link").href = `/organizations?organization=${org.id}`;
  document.querySelector("#mobile-organizations-link").textContent = org.name;
  document.querySelector("#mobile-organizations-link").title = "Organizações e equipe";
  document.querySelector("#account-link").href = `/account?organization=${org.id}`;
  document.querySelector("#help-link").href = `/help?organization=${org.id}`;
  document.querySelector("#topbar-help-link").href = `/help?organization=${org.id}`;
  document.querySelector("#workspace-access-team-link").href = `/organizations?organization=${org.id}`;
  document.querySelector("#workspace-access-notice").classList.toggle("hidden", org.role !== "viewer");
  const creditIndicator = document.querySelector("#credit-indicator strong");
  if (creditIndicator) creditIndicator.textContent = org.billing?.unlimited_credits
    ? "Ilimitados"
    : `${Number(org.billing?.credit_balance || 0).toLocaleString("pt-BR")} disponíveis`;
  document.querySelectorAll("[data-internal-only]").forEach((element) => {
    element.classList.toggle("hidden", !org.billing?.is_internal);
  });
  document.querySelectorAll("[data-internal-admin]").forEach((element) => {
    element.classList.toggle("hidden", !org.billing?.is_internal || org.role !== "admin");
    if (element.id === "admin-link") element.href = `/admin?organization=${org.id}`;
  });
  const url = new URL(location.href); url.searchParams.set("organization", org.id); history.replaceState(null, "", url);
  return { user: data.user, organization: org };
}).catch((error) => {
  const notice = document.createElement("p"); notice.textContent = error.message; notice.setAttribute("role", "alert");
  document.querySelector(".workspace-card").append(notice); return null;
});
document.querySelector("#workspace-organization").addEventListener("change", (event) => {
  // Reload clears every preview/result in memory; each tab retains its own explicit organization.
  location.assign(`/?organization=${event.target.value}`);
});
window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });
