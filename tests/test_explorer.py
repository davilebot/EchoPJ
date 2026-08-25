import unittest

from service.explorer import (
    FIELD_GROUPS,
    RELATION_CATALOG,
    RELATION_CATALOG_BY_NAME,
    cnpj_root_bounds,
    normalize_cnpj_identifier,
)


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

    def test_database_catalog_includes_tables_and_current_views(self):
        self.assertIn("rfb_establishments", RELATION_CATALOG_BY_NAME)
        self.assertIn("rfb_partners", RELATION_CATALOG_BY_NAME)
        self.assertIn("rfb_current_partners", RELATION_CATALOG_BY_NAME)
        self.assertTrue(RELATION_CATALOG_BY_NAME["rfb_current_partners"]["recommended"])

    def test_database_catalog_hides_staging_and_state_partitions(self):
        names = {relation["name"] for relation in RELATION_CATALOG}
        self.assertFalse(any(name.endswith("_stage") for name in names))
        self.assertFalse(any(name.startswith("rfb_establishments_") for name in names))


if __name__ == "__main__":
    unittest.main()
