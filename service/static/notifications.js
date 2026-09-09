const notificationToggle = document.querySelector("#notification-toggle");
const notificationPanel = document.querySelector("#notification-panel");
const notificationList = document.querySelector("#notification-list");
const notificationBadge = document.querySelector("#notification-badge");
const notificationSummary = document.querySelector("#notification-summary");
let notificationLoading = false;

function notificationEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function notificationDate(value) {
  return value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
}

function updateNotificationCount(count) {
  const unread = Number(count || 0);
  notificationBadge.textContent = unread > 99 ? "99+" : unread;
  notificationBadge.classList.toggle("hidden", unread === 0);
  notificationSummary.textContent = unread ? `${unread} não lida${unread === 1 ? "" : "s"}` : "Tudo em dia";
  notificationToggle.setAttribute("aria-label", unread ? `Abrir notificações, ${unread} não lida${unread === 1 ? "" : "s"}` : "Abrir notificações");
}

function renderNotifications(data) {
  updateNotificationCount(data.unread_count);
  notificationList.innerHTML = data.notifications.length ? data.notifications.map((item) => `<button class="notification-item ${item.read_at ? "" : "unread"}" type="button" data-notification-id="${item.id}" data-notification-tab="${notificationEscape(item.action_tab || "")}"><span class="notification-kind kind-${notificationEscape(item.kind)}" aria-hidden="true"></span><span><strong>${notificationEscape(item.title)}</strong><small>${notificationEscape(item.message)}</small><time>${notificationDate(item.created_at)}</time></span></button>`).join("") : `<div class="notification-empty"><strong>Tudo em dia.</strong><span>Novos alertas aparecerão aqui.</span></div>`;
}

async function notificationResponse(response) {
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Não foi possível carregar as notificações.");
  return payload;
}

async function loadNotifications({ quiet = false } = {}) {
  if (notificationLoading || document.hidden) return;
  notificationLoading = true;
  if (!quiet && !notificationPanel.classList.contains("hidden")) notificationList.innerHTML = `<div class="notification-empty">Atualizando…</div>`;
  try {
    renderNotifications(await notificationResponse(await fetch("/api/notifications")));
  } catch (error) {
    if (!quiet) notificationList.innerHTML = `<div class="notification-empty"><strong>Não foi possível atualizar.</strong><span>${notificationEscape(error.message)}</span></div>`;
  } finally { notificationLoading = false; }
}

notificationToggle.addEventListener("click", () => {
  const opening = notificationPanel.classList.contains("hidden");
  notificationPanel.classList.toggle("hidden", !opening);
  notificationToggle.setAttribute("aria-expanded", String(opening));
  if (opening) loadNotifications();
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".notification-center")) {
    notificationPanel.classList.add("hidden");
    notificationToggle.setAttribute("aria-expanded", "false");
  }
});

notificationList.addEventListener("click", async (event) => {
  const item = event.target.closest("[data-notification-id]");
  if (!item) return;
  try {
    await notificationResponse(await fetch(`/api/notifications/${item.dataset.notificationId}/read`, { method: "POST" }));
    const tab = item.dataset.notificationTab;
    notificationPanel.classList.add("hidden");
    notificationToggle.setAttribute("aria-expanded", "false");
    if (tab === "support" || tab.startsWith("support:")) {
      const organization = new URLSearchParams(location.search).get("organization");
      const target = new URL("/help", location.origin);
      if (organization) target.searchParams.set("organization", organization);
      if (tab.includes(":")) target.searchParams.set("ticket", tab.split(":", 2)[1]);
      location.assign(`${target.pathname}${target.search}`);
      return;
    }
    if (tab) document.querySelector(`.tab-button[data-tab="${CSS.escape(tab)}"]`)?.click();
    await loadNotifications({ quiet: true });
  } catch (_) {}
});

document.querySelector("#notification-read-all").addEventListener("click", async () => {
  try {
    await notificationResponse(await fetch("/api/notifications/read-all", { method: "POST" }));
    await loadNotifications({ quiet: true });
  } catch (_) {}
});

window.echoWorkspace?.then((workspace) => { if (workspace) loadNotifications({ quiet: true }); });
setInterval(() => loadNotifications({ quiet: true }), 30000);
