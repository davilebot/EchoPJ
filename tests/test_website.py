import unittest

from service.website import extract_evidence


class WebsiteEvidenceTests(unittest.TestCase):
    def test_extracts_only_checksum_valid_cnpj(self):
        html = """
        <html><body><footer>CNPJ 11.222.333/0001-81</footer>
        <a href='/privacidade'>Privacidade</a>
        <p>Referência inválida 11.222.333/0001-80</p></body></html>
        """
        cnpjs, _, links = extract_evidence("https://example.com", html)
        self.assertEqual(cnpjs, ["11222333000181"])
        self.assertEqual(links, ["https://example.com/privacidade"])

    def test_extracts_json_ld_name(self):
        html = '<script type="application/ld+json">{"legalName":"Empresa Exemplo Ltda","taxID":"11.222.333/0001-81"}</script>'
        cnpjs, names, _ = extract_evidence("https://example.com", html)
        self.assertIn("Empresa Exemplo Ltda", names)
        self.assertEqual(cnpjs, ["11222333000181"])


if __name__ == "__main__":
    unittest.main()
