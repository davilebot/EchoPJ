import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from plataforma_receita.rfb_importer import ImportSettings, QueueAndLoadGate, read_semicolon_zip
from plataforma_receita.rfb_layout import (
    company_details_row,
    establishment_details_row,
    partner_row,
    simples_row,
)
from plataforma_receita.rfb_manifest import (
    import_plan,
    kind_from_filename,
    manifest_from_links,
    parse_manifest,
)


class LayoutTests(unittest.TestCase):
    def test_company_parser_keeps_only_missing_fields(self):
        row = ["12345678", "EMPRESA X", "2062", "49", "1.234,56", "03", "UNIAO"]
        self.assertEqual(
            company_details_row(row, "2026-08"),
            ("2026-08", "12345678", "2062", "49", "UNIAO"),
        )

    def test_establishment_parser_keeps_all_complementary_fields(self):
        row = [""] * 30
        row[0:3] = ["12345678", "0001", "95"]
        row[3] = "1"
        row[7:10] = ["01", "LISBOA", "149"]
        row[21:30] = ["11", "33334444", "11", "55556666", "11", "77778888", "x@example.com", "ESPECIAL", "20260824"]
        parsed = establishment_details_row(row, "2026-08")
        self.assertEqual(parsed[1], "12345678000195")
        self.assertEqual(parsed[2:6], ("1", "01", "LISBOA", "149"))
        self.assertEqual(parsed[-3:], ("x@example.com", "ESPECIAL", "2026-08-24"))

    def test_alphanumeric_cnpj_is_preserved(self):
        row = [""] * 30
        row[0:3] = ["12ABC678", "00D1", "95"]
        parsed = establishment_details_row(row, "2026-08")
        self.assertEqual(parsed[1], "12ABC67800D195")

    def test_simples_parser_distinguishes_false_from_unknown(self):
        self.assertEqual(
            simples_row(["12345678", "S", "20200101", "", "N", "", ""], "2026-08"),
            ("2026-08", "12345678", True, "2020-01-01", None, False, None, None),
        )
        self.assertIsNone(simples_row(["12345678", "", "", "", "", "", ""], "2026-08")[2])

    def test_partner_parser_keeps_public_masked_document_and_age_range(self):
        row = [
            "12345678", "2", "MARIA TESTE", "***123456**", "49", "20200101",
            "", "***987654**", "REPRESENTANTE", "05", "6",
        ]
        parsed = partner_row(row, "2026-08")
        self.assertEqual(parsed[4], "PESSOA FISICA")
        self.assertEqual(parsed[5], "MARIA TESTE")
        self.assertEqual(parsed[6], "***123456**")
        self.assertEqual(parsed[-1], "51 A 60 ANOS")
        self.assertEqual(len(parsed[1]), 32)


class ManifestTests(unittest.TestCase):
    def test_filename_classification(self):
        self.assertEqual(kind_from_filename("Empresas0.zip"), "companies")
        self.assertEqual(kind_from_filename("Estabelecimentos9.zip"), "establishments")
        self.assertEqual(kind_from_filename("Socios1.zip"), "partners")
        self.assertEqual(kind_from_filename("Simples.zip"), "simples")
        self.assertEqual(kind_from_filename("Qualificacoes.zip"), "reference_qualifications")

    def test_complete_manifest_and_plan(self):
        payload = {
            "version": "2026-08",
            "source_url": "https://example.test/2026-08/",
            "files": [
                {"name": "Empresas0.zip", "url": "https://example.test/Empresas0.zip", "size": 10},
                {"name": "Estabelecimentos0.zip", "url": "https://example.test/Estabelecimentos0.zip", "size": 20},
                {"name": "Socios0.zip", "url": "https://example.test/Socios0.zip", "size": 30},
                {"name": "Simples.zip", "url": "https://example.test/Simples.zip", "size": 40},
            ],
        }
        plan = import_plan(parse_manifest(payload))
        self.assertEqual(plan["known_download_bytes"], 100)
        self.assertEqual(plan["unknown_size_files"], 0)

    def test_manifest_rejects_missing_partners(self):
        payload = {
            "version": "2026-08",
            "source_url": "https://example.test/2026-08/",
            "files": [
                {"name": "Empresas0.zip", "url": "https://example.test/Empresas0.zip"},
                {"name": "Estabelecimentos0.zip", "url": "https://example.test/Estabelecimentos0.zip"},
                {"name": "Simples.zip", "url": "https://example.test/Simples.zip"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "partners"):
            parse_manifest(payload)

    def test_manifest_can_be_built_from_directory_links(self):
        manifest = manifest_from_links(
            "2026-08",
            "https://example.test/2026-08/",
            [
                "Empresas0.zip", "Estabelecimentos0.zip", "Socios0.zip", "Simples.zip",
                "Cnaes.zip", "../", "layout.pdf",
            ],
        )
        self.assertEqual(len(manifest.files), 5)
        self.assertTrue(all(item.url.startswith(manifest.source_url) for item in manifest.files))


class SafetyTests(unittest.TestCase):
    def test_zip_reader_streams_latin1_and_removes_nul(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("sample.csv", '"1";"Jo\x00s\xe9"\r\n'.encode("latin-1"))
            self.assertEqual(list(read_semicolon_zip(path)), [["1", "José"]])

    def test_gate_blocks_queued_or_running_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE jobs (status text)")
                connection.execute("INSERT INTO jobs(status) VALUES ('running'),('queued'),('completed')")
            settings = ImportSettings(
                jobs_database=database,
                stable_idle_seconds=0,
                max_load_per_cpu=100,
            )
            with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)):
                reasons = QueueAndLoadGate(settings).reasons()
            self.assertTrue(any("2 consulta" in reason for reason in reasons))

    def test_gate_fails_closed_when_jobs_database_is_missing(self):
        settings = ImportSettings(
            jobs_database=Path("/missing/jobs.sqlite"),
            max_load_per_cpu=100,
        )
        with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)):
            self.assertIn("banco da fila nao encontrado", QueueAndLoadGate(settings).reasons())


if __name__ == "__main__":
    unittest.main()
