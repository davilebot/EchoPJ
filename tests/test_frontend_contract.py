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
            "search-templates",
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

    def test_internal_admin_shows_product_funnel_and_customer_activity(self):
        html = (STATIC_DIR / "admin.html").read_text(encoding="utf-8")
        script = (STATIC_DIR / "admin.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "auth.css").read_text(encoding="utf-8")
        self.assertIn('id="admin-funnel-stages"', html)
        self.assertIn('data-funnel="activated"', html)
        self.assertIn("last_activity_at", script)
        self.assertIn("renderFunnel(data.funnel)", script)
        self.assertIn(".admin-funnel-stages", styles)

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


if __name__ == "__main__":
    unittest.main()
