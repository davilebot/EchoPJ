import unittest
from decimal import Decimal

from pydantic import ValidationError

from service.models import CompanyLookupRequest, CompanySearchRequest
from service.search import (
    SearchCapabilities,
    SearchCapabilityUnavailable,
    build_search_query,
)


class SearchModelTests(unittest.TestCase):
    def test_normalizes_filters(self):
        request = CompanySearchRequest(
            ufs=["sp", "SP"],
            cnae="62.01-5/01",
            postal_code_prefix="13010-",
            registration_statuses=["ativa"],
        )
        self.assertEqual(request.ufs, ["SP"])
        self.assertEqual(request.cnae, "6201501")
        self.assertEqual(request.postal_code_prefix, "13010")
        self.assertEqual(request.registration_statuses, ["ATIVA"])

    def test_rejects_inverted_ranges(self):
        with self.assertRaises(ValidationError):
            CompanySearchRequest(share_capital_min=Decimal("100"), share_capital_max=Decimal("50"))
        with self.assertRaises(ValidationError):
            CompanySearchRequest(opened_from="2026-01-01", opened_to="2025-01-01")
        with self.assertRaises(ValidationError):
            CompanySearchRequest(cnae="62", cnae_scope="any")
        with self.assertRaises(ValidationError):
            CompanySearchRequest(active_branch_count_min=5, active_branch_count_max=2)

    def test_bulk_lookup_keeps_order_and_removes_exact_duplicates(self):
        request = CompanyLookupRequest(cnpjs=["00.000.000/0001-91", "11.222.333/0001-81", "00.000.000/0001-91"])
        self.assertEqual(request.cnpjs, ["00.000.000/0001-91", "11.222.333/0001-81"])


class SearchSqlTests(unittest.TestCase):
    def test_defaults_to_active_and_applies_region_cnae_and_limit(self):
        filters = CompanySearchRequest(region="SE", cnae="62", limit=500).model_dump()
        sql, parameters = build_search_query(filters, SearchCapabilities())
        self.assertIn("e.is_active", sql)
        self.assertIn("e.uf=ANY(%s)", sql)
        self.assertIn("e.primary_cnae LIKE %s", sql)
        self.assertEqual(parameters[0], ["ES", "MG", "RJ", "SP"])
        self.assertEqual(parameters[1], "62%")
        self.assertEqual(parameters[-1], 501)

    def test_exact_cnae_uses_equality(self):
        filters = CompanySearchRequest(cnae="6201501").model_dump()
        sql, parameters = build_search_query(filters, SearchCapabilities())
        self.assertIn("e.primary_cnae=%s", sql)
        self.assertEqual(parameters[-2], "6201501")

    def test_complete_cnae_can_include_secondary_activities(self):
        filters = CompanySearchRequest(cnae="6201501", cnae_scope="any").model_dump()
        sql, parameters = build_search_query(filters, SearchCapabilities())
        self.assertIn("e.primary_cnae=%s OR e.secondary_cnaes @> ARRAY[%s]::text[]", sql)
        self.assertEqual(parameters[-3:-1], ["6201501", "6201501"])

    def test_status_selection_replaces_active_default(self):
        filters = CompanySearchRequest(registration_statuses=["BAIXADA"]).model_dump()
        sql, parameters = build_search_query(filters, SearchCapabilities())
        self.assertIn("e.registration_status=ANY(%s)", sql)
        self.assertNotIn("e.is_active", sql)
        self.assertEqual(parameters[0], ["BAIXADA"])

    def test_explicit_active_status_keeps_partial_indexes_available(self):
        filters = CompanySearchRequest(registration_statuses=["ATIVA"]).model_dump()
        sql, _ = build_search_query(filters, SearchCapabilities())
        self.assertIn("e.is_active", sql)
        self.assertNotIn("e.registration_status=ANY", sql)

    def test_auxiliary_filter_is_blocked_until_matching_dataset_exists(self):
        filters = CompanySearchRequest(simples=True).model_dump()
        with self.assertRaises(SearchCapabilityUnavailable):
            build_search_query(filters, SearchCapabilities())

    def test_auxiliary_filters_add_safe_joins(self):
        filters = CompanySearchRequest(
            simples=True,
            mei=False,
            legal_nature_code="2062",
            branch_type="1",
            has_email=True,
        ).model_dump()
        sql, parameters = build_search_query(
            filters,
            SearchCapabilities(simples=True, company_details=True, establishment_details=True),
        )
        self.assertIn("LEFT JOIN rfb_simples", sql)
        self.assertIn("LEFT JOIN rfb_company_details", sql)
        self.assertIn("LEFT JOIN rfb_establishment_details", sql)
        self.assertIn("s.dataset_version=e.dataset_version", sql)
        self.assertIn("nullif(x.email,'') IS NOT NULL", sql)
        self.assertIn(True, parameters)
        self.assertIn(False, parameters)

    def test_inactive_search_restores_fields_summarized_by_old_import(self):
        filters = CompanySearchRequest(
            registration_statuses=["BAIXADA"],
            cnae="6201501",
            share_capital_min=100,
        ).model_dump()
        sql, _ = build_search_query(
            filters,
            SearchCapabilities(company_details=True, establishment_details=True),
        )
        self.assertIn("coalesce(e.primary_cnae,x.primary_cnae)=%s", sql)
        self.assertIn("coalesce(e.share_capital,c.share_capital)>=%s", sql)
        self.assertIn("coalesce(e.opened_at,x.opened_at) AS opened_at", sql)

    def test_region_and_incompatible_uf_are_rejected(self):
        filters = CompanySearchRequest(region="S", ufs=["SP"]).model_dump()
        with self.assertRaisesRegex(ValueError, "regiao"):
            build_search_query(filters, SearchCapabilities())

    def test_branch_count_filter_uses_precomputed_summary(self):
        filters = CompanySearchRequest(
            active_branch_count_min=2,
            active_branch_count_max=20,
        ).model_dump()
        sql, parameters = build_search_query(
            filters, SearchCapabilities(branch_counts=True)
        )
        self.assertIn("LEFT JOIN rfb_company_branch_counts", sql)
        self.assertIn("coalesce(b.active_branch_count,0)>=%s", sql)
        self.assertIn("coalesce(b.active_branch_count,0)<=%s", sql)
        self.assertEqual(parameters[-3:-1], [2, 20])


if __name__ == "__main__":
    unittest.main()
