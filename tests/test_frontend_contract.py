import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "service" / "static"


class InterfaceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tabs = []

    def handle_starttag(self, _, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if values.get("data-tab"):
            self.tabs.append(values["data-tab"])


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        cls.parser = InterfaceParser()
        cls.parser.feed(cls.html)

    def test_keeps_unique_ids_and_all_javascript_contracts(self):
        duplicates = [key for key, count in Counter(self.parser.ids).items() if count > 1]
        self.assertEqual(duplicates, [])
        required = {
            "match-form", "batch-form", "company-search-form", "bulk-cnpj-form",
            "explorer-cnpj-form", "history-list", "explorer-overview",
            "schema-catalog", "relation-preview", "theme-toggle",
            "lists-grid", "saved-searches-grid", "billing-summary", "credit-indicator",
            "save-search-dialog", "save-list-dialog", "create-list-dialog", "saas-overview",
            "notification-toggle", "notification-panel", "notification-list", "notification-badge",
            "search-templates", "workspace-access-notice",
        }
        self.assertTrue(required.issubset(set(self.parser.ids)))

    def test_every_navigation_item_has_a_panel(self):
        ids = set(self.parser.ids)
        self.assertEqual(len(self.parser.tabs), 9)
        self.assertTrue(all(f"{tab}-tab" in ids for tab in self.parser.tabs))

    def test_search_navigation_is_grouped_and_saas_library_is_visible(self):
        self.assertIn('class="nav-group-title"', self.html)
        self.assertIn("Buscar empresas", self.html)
        for label in ("Busca com filtros", "Consultar CNPJs", "Identificar CNPJ", "Listas", "Buscas salvas", "Plano e créditos"):
            self.assertIn(label, self.html)

    def test_echo_brand_assets_and_themes_are_present(self):
        self.assertIn("EchoPJs", self.html)
        self.assertTrue((STATIC_DIR / "assets" / "echo-wordmark-dark.png").is_file())
        self.assertTrue((STATIC_DIR / "assets" / "echo-wordmark-light.png").is_file())
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn(':root[data-theme="dark"]', styles)
        self.assertIn("--accent: #3bfde7", styles)

    def test_company_search_uses_grouped_filters_and_custom_selection_ui(self):
        self.assertIn("filter-form-header", self.html)
        self.assertGreaterEqual(self.html.count('class="filter-group filter-grid"'), 5)
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".search-form { display: grid; grid-template-columns: minmax(0, 1fr)", styles)
        self.assertIn("select { appearance: none", styles)
        self.assertIn(".multi-picker-trigger::after", styles)

    def test_downloads_use_server_credit_enforcement(self):
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/credits/estimate"', script)
        self.assertIn('downloadCsvResponse("/api/exports/companies"', script)
        self.assertIn('downloadCsvResponse("/api/exports/cnpj-lookup"', script)
        self.assertNotIn("downloadCompleteCompanyCsv", script)

    def test_search_keeps_selection_actions_visible_and_explains_credit_use(self):
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        action_bar = script.index('id="selection-action-bar"')
        results_table = script.index('<div class="table-wrap"><table>', action_bar)
        self.assertLess(action_bar, results_table)
        self.assertIn('id="selection-credit-summary"', script)
        self.assertIn("searchSelectionEstimateRequest", script)
        self.assertIn('companySearchForm.setAttribute("aria-busy", "true")', script)
        self.assertIn(".selection-action-bar { position: sticky", styles)

    def test_internal_admin_entry_is_role_gated(self):
        workspace = (STATIC_DIR / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("data-internal-admin", self.html)
        self.assertIn('org.role !== "admin"', workspace)

    def test_dashboard_contains_contextual_onboarding(self):
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn("Prepare seu workspace", script)
        self.assertIn("pending_invitation_count", script)
        self.assertIn(".onboarding-steps", styles)

    def test_workspace_fetches_are_scoped_to_selected_organization(self):
        workspace = (STATIC_DIR / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("window.fetch = function workspaceScopedFetch", workspace)
        self.assertIn('headers.set("X-Organization-Id", activeWorkspaceOrganization)', workspace)
        self.assertIn("activeWorkspaceOrganization = String(org.id)", workspace)

    def test_notification_center_is_user_and_workspace_aware(self):
        script = (STATIC_DIR / "notifications.js").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/notifications")', script)
        self.assertIn("/api/notifications/read-all", script)
        self.assertIn("data-notification-id", script)
        self.assertIn('tab.startsWith("support:")', script)

    def test_internal_admin_shows_product_funnel_and_customer_activity(self):
        html = (STATIC_DIR / "admin.html").read_text(encoding="utf-8")
        script = (STATIC_DIR / "admin.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "auth.css").read_text(encoding="utf-8")
        self.assertIn('id="admin-funnel-stages"', html)
        self.assertIn('data-funnel="activated"', html)
        self.assertIn("last_activity_at", script)
        self.assertIn("renderFunnel(data.funnel)", script)
        self.assertIn(".admin-funnel-stages", styles)

    def test_billing_catalog_checkout_and_admin_audit_have_complete_ui(self):
        plans = (STATIC_DIR / "plans.html").read_text(encoding="utf-8")
        plans_script = (STATIC_DIR / "plans.js").read_text(encoding="utf-8")
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        admin = (STATIC_DIR / "admin.html").read_text(encoding="utf-8")
        admin_script = (STATIC_DIR / "admin.js").read_text(encoding="utf-8")
        self.assertIn('id="plans-grid"', plans)
        self.assertIn('fetch("/api/billing/catalog")', plans_script)
        self.assertIn('fetch("/api/auth/status")', plans_script)
        self.assertIn('fetch("/api/billing/checkouts"', script)
        self.assertIn('data-billing-plan=', script)
        self.assertIn('id="admin-billing-events"', admin)
        self.assertIn('id="admin-orders"', admin)
        self.assertIn("renderBillingEvents", admin_script)
        self.assertIn('id="admin-operations-grid"', admin)
        self.assertIn("renderOperations", admin_script)
        self.assertIn('/api/admin/operations', admin_script)

    def test_public_product_page_explains_the_complete_customer_workflow(self):
        html = (STATIC_DIR / "product.html").read_text(encoding="utf-8")
        script = (STATIC_DIR / "product.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "product.css").read_text(encoding="utf-8")
        for text in ("Busca com filtros", "Consulta de CNPJs", "Identificação de CNPJ", "Buscas salvas", "Listas", "Administrador", "Membro", "Consulta"):
            self.assertIn(text, html)
        self.assertGreaterEqual(html.count("data-signup-cta"), 3)
        self.assertIn('fetch("/api/auth/status")', script)
        self.assertIn('fetch("/health")', script)
        self.assertIn("@media(max-width:680px)", styles)

    def test_search_and_list_templates_start_from_editable_examples(self):
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertEqual(self.html.count("data-search-template="), 3)
        self.assertIn("supportedTemplateFilters", script)
        self.assertIn("applySearchTemplate", script)
        self.assertIn("listTemplates", script)
        self.assertIn("data-list-template", script)
        self.assertIn(".search-template-grid", styles)
        self.assertIn(".list-template-grid", styles)

    def test_help_center_is_contextual_searchable_and_workspace_aware(self):
        html = (STATIC_DIR / "help.html").read_text(encoding="utf-8")
        script = (STATIC_DIR / "help.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "help.css").read_text(encoding="utf-8")
        workspace = (STATIC_DIR / "workspace.js").read_text(encoding="utf-8")
        self.assertIn('id="help-search"', html)
        self.assertIn('id="copy-diagnostic"', html)
        self.assertGreaterEqual(html.count('class="help-topic"'), 6)
        self.assertIn("X-Organization-Id", script)
        self.assertIn("data-platform-tab", script)
        self.assertIn(".quick-help-grid", styles)
        self.assertIn('document.querySelector("#help-link").href', workspace)

    def test_support_center_has_customer_thread_and_internal_queue(self):
        help_html = (STATIC_DIR / "help.html").read_text(encoding="utf-8")
        help_script = (STATIC_DIR / "help.js").read_text(encoding="utf-8")
        help_styles = (STATIC_DIR / "help.css").read_text(encoding="utf-8")
        admin_html = (STATIC_DIR / "admin.html").read_text(encoding="utf-8")
        admin_script = (STATIC_DIR / "admin.js").read_text(encoding="utf-8")
        self.assertIn('id="support-form"', help_html)
        self.assertIn('id="support-tickets"', help_html)
        self.assertIn('id="support-dialog"', help_html)
        self.assertIn('api("/api/support/tickets"', help_script)
        self.assertIn("requestedSupportTicket", help_script)
        self.assertIn("support-message", help_styles)
        self.assertIn('id="admin-support-tickets"', admin_html)
        self.assertIn('id="admin-support-dialog"', admin_html)
        self.assertIn("/api/admin/support/tickets", admin_script)

    def test_account_privacy_center_exports_data_and_tracks_deletion_requests(self):
        account = (STATIC_DIR / "account.html").read_text(encoding="utf-8")
        account_script = (STATIC_DIR / "account.js").read_text(encoding="utf-8")
        admin = (STATIC_DIR / "admin.html").read_text(encoding="utf-8")
        admin_script = (STATIC_DIR / "admin.js").read_text(encoding="utf-8")
        self.assertIn('id="privacy-export"', account)
        self.assertIn('id="privacy-delete"', account)
        self.assertIn('fetch("/api/privacy/export"', account_script)
        self.assertIn("/api/privacy/deletion-requests", account_script)
        self.assertIn('id="admin-privacy-requests"', admin)
        self.assertIn("/api/admin/privacy/requests", admin_script)

    def test_platform_accepts_safe_deep_links_to_known_tabs(self):
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('new URLSearchParams(location.search).get("tab")', script)
        self.assertIn('button.dataset.tab === requestedInitialTab', script)

    def test_viewer_permissions_are_visible_and_hide_protected_actions(self):
        organizations = (STATIC_DIR / "organizations.html").read_text(encoding="utf-8")
        organization_script = (STATIC_DIR / "organizations.js").read_text(encoding="utf-8")
        workspace = (STATIC_DIR / "workspace.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('<option value="viewer">', organizations)
        self.assertIn('viewer: "Consulta"', organization_script)
        self.assertIn("capabilityManageLibrary", workspace)
        self.assertIn('org.role !== "viewer"', workspace)
        self.assertIn('data-capability-manage-library="false"', styles)
        for capability in ('data-capability="manage-library"', 'data-capability="export"', 'data-capability="run-jobs"'):
            self.assertIn(capability, self.html + script)


if __name__ == "__main__":
    unittest.main()
