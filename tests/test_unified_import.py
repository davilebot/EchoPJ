import base64
import unittest

from scripts.import_unified_dataset import branch_row, official_download_headers


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


if __name__ == "__main__":
    unittest.main()
