import base64
import unittest

from scripts.import_unified_dataset import (
    branch_row,
    direct_validation_is_valid,
    official_download_headers,
)


class UnifiedImportTests(unittest.TestCase):
    def test_official_download_uses_public_share_token(self):
        headers = official_download_headers(
            "https://arquivos.receitafederal.gov.br/public.php/dav/files/public-token/2026-09/Empresas0.zip"
        )
        expected = base64.b64encode(b"public-token:").decode()
        self.assertEqual(headers["Authorization"], f"Basic {expected}")

    def test_branch_pass_keeps_only_branches(self):
        matrix = [""] * 30
        matrix[0:6] = ["12345678", "0001", "95", "1", "MATRIZ", "02"]
        matrix[19] = "SP"
        self.assertIsNone(branch_row(matrix, "2026-09"))

        branch = matrix.copy()
        branch[1] = "0002"
        branch[3] = "2"
        self.assertEqual(branch_row(branch, "2026-09"), ("2026-09", "12345678", True))

    def test_direct_validation_accepts_exact_source_duplicates_and_blank_names(self):
        validation = {
            "source_establishments": 73_193_135,
            "target_establishments": 73_193_135,
            "source_partners": 28_341_092,
            "target_partners": 28_340_800,
            "missing_company_rows": 0,
            "blank_source_legal_names": 16,
            "states": [
                "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
                "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
                "RO", "RR", "RS", "SC", "SE", "SP", "TO",
            ],
            "reference_kinds": [
                "cnae", "country", "legal_nature", "municipality", "qualification",
                "status_reason",
            ],
        }
        self.assertTrue(direct_validation_is_valid(validation))

    def test_direct_validation_rejects_missing_company_join(self):
        validation = {
            "source_establishments": 1,
            "target_establishments": 1,
            "source_partners": 1,
            "target_partners": 1,
            "missing_company_rows": 1,
            "states": [
                "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
                "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
                "RO", "RR", "RS", "SC", "SE", "SP", "TO",
            ],
            "reference_kinds": [
                "cnae", "country", "legal_nature", "municipality", "qualification",
                "status_reason",
            ],
        }
        self.assertFalse(direct_validation_is_valid(validation))


if __name__ == "__main__":
    unittest.main()
