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


if __name__ == "__main__":
    unittest.main()
