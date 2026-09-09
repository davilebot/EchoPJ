import unittest

from service.legal import LegalDocuments


class LegalDocumentsTests(unittest.TestCase):
    def configured(self, **updates):
        values = {
            "operator_name": "Echo Teste Ltda.",
            "operator_document": "00.000.000/0001-00",
            "operator_address": "Rua Teste, 100, São Paulo - SP",
            "contact_email": "contato@example.com",
            "privacy_email": "privacidade@example.com",
            "terms_version": "2026-09",
            "privacy_version": "2026-09",
            "effective_date": "2026-09-09",
            "retention_policy": "Dados de conta são mantidos durante o contrato e pelo prazo legal aplicável.",
        }
        values.update(updates)
        return LegalDocuments(**values)

    def test_requires_complete_valid_configuration_before_publication(self):
        self.assertTrue(self.configured().configured)
        for update in (
            {"operator_name": ""}, {"privacy_email": "invalido"},
            {"terms_version": "versão inválida"}, {"effective_date": "09/09/2026"},
            {"retention_policy": ""},
        ):
            self.assertFalse(self.configured(**update).configured)

    def test_acceptance_must_match_both_published_versions(self):
        documents = self.configured()
        self.assertTrue(documents.accepts("2026-09", "2026-09"))
        self.assertFalse(documents.accepts("2026-08", "2026-09"))
        public = documents.public()
        self.assertEqual(public["status"], "published")
        self.assertEqual(public["documents"]["terms"]["url"], "/termos")


if __name__ == "__main__":
    unittest.main()
