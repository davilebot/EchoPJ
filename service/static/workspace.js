const workspaceFetch = window.fetch.bind(window);
window.echoWorkspace = workspaceFetch("/api/organizations").then(async (response) => {
  if (response.status === 401) { location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`); return null; }
  if (!response.ok) throw new Error("Não foi possível carregar suas organizações. Atualize a página.");
  const data = await response.json();
  const requested = new URLSearchParams(location.search).get("organization");
  const org = requested ? data.organizations.find((item) => String(item.id) === requested) : data.organizations[0];
  if (!org) { location.replace("/organizations"); return null; }
  const select = document.querySelector("#workspace-organization");
  select.replaceChildren(...data.organizations.map((item) => { const option = document.createElement("option"); option.value = item.id; option.textContent = item.name; return option; }));
  select.value = org.id; select.disabled = false;
  document.querySelector("#organizations-link").href = `/organizations?organization=${org.id}`;
  document.querySelector("#mobile-organizations-link").href = `/organizations?organization=${org.id}`;
  document.querySelector("#mobile-organizations-link").textContent = org.name;
  document.querySelector("#mobile-organizations-link").title = "Organizações e equipe";
  document.querySelector("#account-link").href = `/account?organization=${org.id}`;
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
