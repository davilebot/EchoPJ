import unittest

from service.explorer import FIELD_GROUPS, normalize_cnpj_identifier


class ExplorerTests(unittest.TestCase):
    def test_normalizes_numeric_cnpj(self):
        self.assertEqual(normalize_cnpj_identifier("00.000.000/0001-91"), "00000000000191")

    def test_preserves_alphanumeric_cnpj(self):
        self.assertEqual(normalize_cnpj_identifier("12.ABC.678/00D1-95"), "12ABC67800D195")

    def test_rejects_incomplete_identifier(self):
        with self.assertRaisesRegex(ValueError, "14 caracteres"):
            normalize_cnpj_identifier("123")

    def test_catalog_covers_the_business_groups(self):
        keys = {group["key"] for group in FIELD_GROUPS}
        self.assertTrue({"establishments", "simples", "partners", "references"} <= keys)


if __name__ == "__main__":
    unittest.main()
