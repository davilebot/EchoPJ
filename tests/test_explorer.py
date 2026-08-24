import unittest

from service.explorer import FIELD_GROUPS, cnpj_root_bounds, normalize_cnpj_identifier


class ExplorerTests(unittest.TestCase):
    def test_normalizes_numeric_cnpj(self):
        self.assertEqual(normalize_cnpj_identifier("00.000.000/0001-91"), "00000000000191")

    def test_preserves_alphanumeric_cnpj(self):
        self.assertEqual(normalize_cnpj_identifier("12.ABC.678/00D1-95"), "12ABC67800D195")

    def test_rejects_incomplete_identifier(self):
        with self.assertRaisesRegex(ValueError, "14 caracteres"):
            normalize_cnpj_identifier("123")

    def test_builds_indexed_range_for_matrix_and_branches(self):
        self.assertEqual(
            cnpj_root_bounds("12.345.678/0001-90"),
            ("12345678", "12345678000000", "12345678ZZZZZZ"),
        )

    def test_catalog_covers_the_business_groups(self):
        keys = {group["key"] for group in FIELD_GROUPS}
        self.assertTrue({"establishments", "simples", "partners", "references"} <= keys)


if __name__ == "__main__":
    unittest.main()
