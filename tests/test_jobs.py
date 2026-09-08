import csv
import io
import tempfile
import time
import unittest
from pathlib import Path

from service.jobs import JobRunner, JobStore, export_companies_csv


class JobTests(unittest.TestCase):
    def test_job_runs_one_item_at_a_time_and_exports_source(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(str(Path(directory) / "jobs.sqlite"))
            calls = []

            def match_one(items, *, active_only, check_website):
                self.assertEqual(len(items), 1)
                calls.append(items[0]["local_id"])
                status = "confirmado" if items[0]["local_id"] == "2" else "nao_encontrado"
                selected = {
                    "cnpj": "11222333000181",
                    "legal_name": "EMPRESA TESTE LTDA",
                    "trade_name": "EMPRESA TESTE",
                    "score": 95,
                } if status == "confirmado" else None
                return {
                    "results": [{
                        "local_id": items[0]["local_id"],
                        "status": status,
                        "confidence": 5 if selected else 0,
                        "selected": selected,
                        "candidates": [],
                    }],
                    "dataset_version": "2026-08",
                    "timing_ms": {"database": 10, "website": 0, "total": 10},
                }

            job = store.create_job(
                "teste.csv",
                [
                    {"local_id": "2", "name": "Empresa 1", "uf": "SP", "source": {"Company Name": "Empresa 1"}},
                    {"local_id": "3", "name": "Empresa 2", "uf": "SP", "source": {"Company Name": "Empresa 2"}},
                ],
                active_only=True,
                check_website=False,
            )
            runner = JobRunner(store, match_one)
            runner.start()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                current = store.get_job(job["id"])
                if current["status"] == "completed":
                    break
                time.sleep(0.02)
            runner.stop()

            current = store.get_job(job["id"])
            self.assertEqual(calls, ["2", "3"])
            self.assertEqual(current["processed"], 2)
            self.assertEqual(current["confirmed"], 1)
            self.assertEqual(current["not_found"], 1)
            self.assertEqual(store.selected_cnpjs(job["id"]), ["11222333000181"])
            def company_lookup(cnpjs):
                self.assertEqual(cnpjs, ["11222333000181"])
                return {"11222333000181": {
                    "cnpj": "11222333000181",
                    "cnpj_root": "11222333",
                    "legal_name": "EMPRESA TESTE LTDA",
                    "trade_name": "EMPRESA TESTE",
                    "registration_status": "ATIVA",
                    "company_size": "MICRO EMPRESA",
                    "is_simples": True,
                    "is_mei": False,
                    "partner_count": 2,
                    "dataset_version": "2026-08",
                    "partners": [
                        {
                            "partner_name": "SOCIO UM",
                            "age_range": "31 a 40 anos",
                            "partner_document": "***123456**",
                            "qualification": "Sócio-Administrador",
                        },
                        {
                            "partner_name": "SOCIO DOIS",
                            "age_range": "41 a 50 anos",
                            "partner_document": "***987654**",
                            "qualification": "Sócio",
                        },
                    ],
                }}

            exported = store.export_csv(job["id"], company_lookup).decode("utf-8")
            self.assertIn("Company Name", exported)
            self.assertIn("Empresa 1", exported)
            self.assertIn("11222333000181", exported)
            csv_rows = list(csv.reader(io.StringIO(exported.lstrip("\ufeff"))))
            headers, first_result = csv_rows[0], csv_rows[1]
            self.assertIn("Capital Social", headers)
            self.assertIn("Simples", headers)
            self.assertIn("Sócio 1", headers)
            self.assertIn("Faixa Etária 1", headers)
            self.assertIn("Sócio 2", headers)
            self.assertEqual(first_result[headers.index("Porte")], "MICRO EMPRESA")
            self.assertEqual(first_result[headers.index("Sócio 1")], "SOCIO UM")
            self.assertEqual(first_result[headers.index("Faixa Etária 2")], "41 a 50 anos")
            self.assertEqual(first_result[headers.index("CPF/CNPJ Público 2")], "***987654**")
            store.close()

    def test_running_job_is_resumed_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "jobs.sqlite")
            first = JobStore(path)
            job = first.create_job(
                "retomar.csv",
                [{"local_id": "2", "name": "Empresa", "uf": "SP", "source": {}}],
                active_only=True,
                check_website=False,
            )
            claimed = first.claim_next_job()
            self.assertEqual(claimed["status"], "running")
            first.close()

            reopened = JobStore(path)
            self.assertEqual(reopened.get_job(job["id"])["status"], "queued")
            reopened.close()

    def test_exports_neutralize_spreadsheet_formulas(self):
        content = export_companies_csv([{
            "cnpj": "11222333000181",
            "legal_name": '=HYPERLINK("https://attacker.example")',
            "trade_name": "+SUM(1,1)",
            "partners": [{"partner_name": "@malicious"}],
        }]).decode("utf-8")
        rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
        headers, company = rows
        self.assertTrue(company[headers.index("Razão Social")].startswith("'="))
        self.assertTrue(company[headers.index("Nome Fantasia")].startswith("'+"))
        self.assertTrue(company[headers.index("Sócio 1")].startswith("'@"))

    def test_selected_cnpjs_respects_job_organization(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(str(Path(directory) / "jobs.sqlite"))
            job = store.create_job(
                "tenant.csv", [], active_only=True, check_website=False, organization_id=10,
            )
            self.assertEqual(store.selected_cnpjs(job["id"], organization_id=10), [])
            self.assertIsNone(store.selected_cnpjs(job["id"], organization_id=11))
            self.assertEqual(store.active_job_count(organization_id=10), 1)
            self.assertEqual(store.active_job_count(organization_id=11), 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
