import unittest

from plataforma_receita.matcher import decide
from plataforma_receita.normalization import valid_cnpj


class MatcherTests(unittest.TestCase):
    def test_cnpj_checksum(self):
        self.assertTrue(valid_cnpj("11.222.333/0001-81"))
        self.assertFalse(valid_cnpj("11.222.333/0001-80"))

    def test_cep_alone_does_not_confirm(self):
        item = {"row_number": 1, "company_name": "Empresa Alfa", "city": "Sao Paulo", "postal_code": "01000-000", "address": "Rua A 10", "site_cnpjs": [], "site_names": []}
        candidate = {"cnpj": "11222333000181", "legal_name": "Empresa Beta Ltda", "trade_name": "Beta", "municipality": "SAO PAULO", "postal_code": "01000000", "address": "Rua B 99"}
        result = decide(item, [candidate])
        self.assertNotEqual(result["status"], "confirmado")

    def test_strong_name_and_location_can_confirm(self):
        item = {"row_number": 1, "company_name": "Alpha Tecnologia", "city": "Campinas", "postal_code": "13010000", "address": "Rua Brasil 100", "site_cnpjs": [], "site_names": []}
        candidate = {"cnpj": "11222333000181", "legal_name": "Alpha Tecnologia Ltda", "trade_name": "Alpha Tecnologia", "municipality": "CAMPINAS", "postal_code": "13010000", "address": "RUA BRASIL 100"}
        result = decide(item, [candidate])
        self.assertEqual(result["status"], "confirmado")

    def test_generic_home_title_does_not_match_company(self):
        item = {"row_number": 1, "company_name": "OpenX", "city": "Sao Paulo", "postal_code": "04719002", "street": "Rua Verbo Divino 2001", "site_cnpjs": [], "site_names": ["Home - OpenX", "Home", "Contato"]}
        candidate = {"cnpj": "10213831000103", "legal_name": "ASSOCIACAO BRASILEIRA DE OUT OF HOME - ABOOH", "trade_name": "ABOOH", "municipality": "SAO PAULO", "postal_code": "04719002", "address": "RUA VERBO DIVINO 2001"}
        result = decide(item, [candidate])
        self.assertNotEqual(result["status"], "confirmado")

    def test_single_generic_site_word_does_not_confirm(self):
        item = {"row_number": 1, "company_name": "Ellemento", "city": "Sao Paulo", "postal_code": "01311100", "street": "Avenida Paulista 807", "site_cnpjs": [], "site_names": ["Politica de Privacidade de Dados"]}
        candidate = {"cnpj": "08111612000163", "legal_name": "MURASHIGE PROCESSAMENTO DE DADOS LTDA", "trade_name": None, "municipality": "SAO PAULO", "postal_code": "01311100", "address": "AVENIDA PAULISTA 807"}
        result = decide(item, [candidate])
        self.assertNotEqual(result["status"], "confirmado")


if __name__ == "__main__":
    unittest.main()
