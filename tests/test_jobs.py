import tempfile
import time
import unittest
from pathlib import Path

from service.jobs import JobRunner, JobStore


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
            exported = store.export_csv(job["id"]).decode("utf-8")
            self.assertIn("Company Name", exported)
            self.assertIn("Empresa 1", exported)
            self.assertIn("11222333000181", exported)
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


if __name__ == "__main__":
    unittest.main()
