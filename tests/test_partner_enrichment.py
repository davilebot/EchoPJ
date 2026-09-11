import sqlite3
import tempfile
import unittest

from service.partner_enrichment import (
    PartnerEnrichmentService,
    PartnerEnrichmentStore,
    minimal_person,
    prioritized_partners,
    valid_cpf,
)


CPFS = ("52998224725", "11144477735", "12345678909", "39053344705")


class FakeLemitClient:
    segment = 20782

    def __init__(self):
        self.company_calls = 0
        self.person_calls = 0

    def company(self, _cnpj):
        self.company_calls += 1
        return {"empresa": {"socios": [
            {"cpf": CPFS[0], "nome": "ADMIN 68", "participacao": 20},
            {"cpf": CPFS[1], "nome": "SOCIO 40", "participacao": 70},
            {"cpf": CPFS[2], "nome": "ADMIN 75", "participacao": 10},
            {"cpf": CPFS[3], "nome": "SOCIO MENOR", "participacao": 90},
            {"cpf": "11222333000181", "nome": "SOCIA PJ"},
        ]}}

    def person(self, cpf):
        self.person_calls += 1
        return {"pessoa": {
            "nome": f"PESSOA {cpf[-2:]}",
            "celulares": [
                {"ddd": "11", "numero": "900000003", "ranking": 3},
                {"ddd": "11", "numero": "900000001", "ranking": 1, "whatsapp": True},
                {"ddd": "11", "numero": "900000002", "ranking": 2},
            ],
            "emails": [
                {"email": "terceiro@example.com", "ranking": 3},
                {"email": "primeiro@example.com", "ranking": 1, "possui_cookie": True},
                {"email": "SEGUNDO@GMAIL.COM", "ranking": 2},
                {"email": "quarto@example.com", "ranking": 4},
            ],
            "fixos": [
                {"ddd": "11", "numero": "33334444", "ranking": 1},
            ],
        }}


class PartnerEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = f"{self.tmp.name}/partners.sqlite"
        self.store = PartnerEnrichmentStore(self.database, "a-test-secret", cache_days=60)
        self.client = FakeLemitClient()
        self.local_partners = [
            {"partner_name": "ADMIN 68", "partner_type": "PESSOA FISICA", "qualification": "Sócio-Administrador", "age_range_code": "7", "age_range": "61 a 70 anos"},
            {"partner_name": "SOCIO 40", "partner_type": "PESSOA FISICA", "qualification": "Sócio", "age_range_code": "4", "age_range": "31 a 40 anos"},
            {"partner_name": "ADMIN 75", "partner_type": "PESSOA FISICA", "qualification": "Administrador", "age_range_code": "8", "age_range": "71 a 80 anos"},
            {"partner_name": "SOCIO MENOR", "partner_type": "PESSOA FISICA", "qualification": "Sócio-Administrador", "age_range_code": "2", "age_range": "13 a 20 anos"},
        ]
        self.service = PartnerEnrichmentService(
            self.store, self.client, lambda _cnpj: {"partners": self.local_partners},
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def job(self, organization_id=1, job_id="job-1"):
        return {"id": job_id, "organization_id": organization_id, "created_by": 10}

    def test_prioritizes_administrator_through_70_and_uses_older_only_as_fallback(self):
        selected = prioritized_partners(self.client.company("x")["empresa"]["socios"], self.local_partners)
        self.assertEqual([item["name"] for item in selected], ["ADMIN 68", "SOCIO 40", "ADMIN 75"])
        self.assertTrue(all(valid_cpf(item["cpf"]) for item in selected))

    def test_limits_and_orders_mobile_phones_and_emails(self):
        person = minimal_person(self.client.person(CPFS[0]))
        self.assertEqual(person["first_name"], "Pessoa")
        self.assertEqual(person["name"], "Pessoa 25")
        self.assertEqual([phone["numero"] for phone in person["phones"]], ["900000001", "900000002"])
        self.assertEqual([phone["value"] for phone in person["phones"]], ["5511900000001", "5511900000002"])
        self.assertEqual(person["fixed_phones"][0]["value"], "551133334444")
        self.assertEqual(
            [email["email"] for email in person["emails"]],
            ["primeiro@example.com", "segundo@gmail.com", "terceiro@example.com"],
        )
        self.assertEqual(
            [email["email"] for email in person["corporate_emails"]],
            ["primeiro@example.com", "terceiro@example.com"],
        )

    def test_global_cache_is_reused_but_access_remains_tenant_scoped(self):
        first = self.service.process_company({"cnpj": "37602227000117"}, self.job(1, "job-1"))
        second = self.service.process_company({"cnpj": "37602227000117"}, self.job(2, "job-2"))
        self.assertEqual(first["provider_requests"], 4)
        self.assertEqual(second["provider_requests"], 1)
        self.assertEqual(second["reused_people"], 3)
        self.assertEqual(self.client.person_calls, 3)
        self.assertEqual(len(self.store.contacts_for_companies(1, ["37602227000117"])["37602227000117"]), 3)
        self.assertEqual(len(self.store.contacts_for_companies(2, ["37602227000117"])["37602227000117"]), 3)
        self.assertEqual(self.store.contacts_for_companies(3, ["37602227000117"]), {})
        connection = sqlite3.connect(self.database)
        cache_rows = connection.execute("SELECT cpf_ciphertext,person_json FROM partner_person_cache").fetchall()
        access_rows = connection.execute("SELECT partner_json FROM partner_enrichment_access").fetchall()
        connection.close()
        serialized = repr(cache_rows + access_rows)
        for cpf in CPFS:
            self.assertNotIn(cpf, serialized)
        self.assertIn("***.", serialized)
        decrypted_cpfs = {
            self.store._fernet.decrypt(ciphertext.encode("ascii")).decode("ascii")
            for ciphertext, _ in cache_rows
        }
        self.assertEqual(
            decrypted_cpfs,
            {"529.982.247-25", "111.444.777-35", "123.456.789-09"},
        )

    def test_cache_older_than_sixty_days_is_refreshed(self):
        self.service.process_company({"cnpj": "37602227000117"}, self.job(1, "job-1"))
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE partner_person_cache SET fetched_at='2020-01-01T00:00:00+00:00'"
            )
        refreshed = self.service.process_company(
            {"cnpj": "37602227000117"}, self.job(2, "job-2"),
        )
        self.assertEqual(refreshed["reused_people"], 0)
        self.assertEqual(refreshed["provider_requests"], 4)
        self.assertEqual(self.client.person_calls, 6)


if __name__ == "__main__":
    unittest.main()
