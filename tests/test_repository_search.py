import unittest
from contextlib import contextmanager

from psycopg.errors import QueryCanceled

from service.repository import Repository
from service.search import SearchCapabilities


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Connection:
    def __init__(self, observed_sql):
        self.observed_sql = observed_sql

    def execute(self, statement, parameters=None):
        query = str(statement)
        self.observed_sql.append(query)
        if "set_config('statement_timeout'" in query:
            return _Rows([])
        state = parameters[0]
        if "count(*) AS total_count" in query:
            return _Rows([{"total_count": ord(state[0])}])
        rows = [
            {
                "cnpj": f"{index:012d}{position:02d}",
                "cnpj_root": f"{index:08d}",
                "uf": state,
            }
            for position, index in enumerate(range(ord(state[0]) * 100, ord(state[0]) * 100 + 4))
        ]
        return _Rows(rows)


class _Pool:
    def __init__(self):
        self.observed_sql = []

    @contextmanager
    def connection(self):
        yield _Connection(self.observed_sql)


class _FallbackConnection(_Connection):
    def execute(self, statement, parameters=None):
        query = str(statement)
        self.observed_sql.append(query)
        if "set_config('statement_timeout'" in query:
            return _Rows([])
        if "count(*) AS total_count" in query:
            if parameters[0] == "SP":
                raise QueryCanceled("statement timeout")
            return _Rows([{"total_count": 1}])
        return _Rows([])


class _FallbackPool(_Pool):
    @contextmanager
    def connection(self):
        yield _FallbackConnection(self.observed_sql)


class RepositorySearchTests(unittest.TestCase):
    def test_broad_multi_region_search_returns_a_bounded_partial_result_without_counting(self):
        repository = Repository.__new__(Repository)
        repository.pool = _Pool()
        repository.database_workers = 8
        repository.statement_timeout_ms = 1800
        repository.search_capabilities = lambda: SearchCapabilities()

        results, _, _, has_more, total_count = repository.search_companies({
            "regions": ["S", "SE"],
            "ufs": [],
            "registration_statuses": ["ATIVA"],
            "cnaes": ["5611201"],
            "limit": 3,
        })

        self.assertEqual(len(results), 3)
        self.assertTrue(has_more)
        self.assertIsNone(total_count)
        self.assertFalse(any("count(*)" in query.lower() for query in repository.pool.observed_sql))

    def test_exact_count_sums_state_partitions(self):
        repository = Repository.__new__(Repository)
        repository.pool = _Pool()
        repository.database_workers = 8
        repository.statement_timeout_ms = 1800
        repository.search_capabilities = lambda: SearchCapabilities()

        total, _, _ = repository.count_companies({
            "regions": ["S", "SE"],
            "ufs": [],
            "registration_statuses": ["ATIVA"],
            "cnaes": ["5611201"],
            "limit": 10000,
        })

        self.assertEqual(total, sum(ord(state[0]) for state in ("ES", "MG", "PR", "RJ", "RS", "SC", "SP")))
        self.assertEqual(
            sum("count(*) AS total_count" in query for query in repository.pool.observed_sql),
            7,
        )

    def test_exact_count_splits_a_timed_out_state_into_cnpj_ranges(self):
        repository = Repository.__new__(Repository)
        repository.pool = _FallbackPool()
        repository.database_workers = 8
        repository.statement_timeout_ms = 1800
        repository.search_capabilities = lambda: SearchCapabilities()

        total, _, _ = repository.count_companies({
            "regions": ["SE"],
            "ufs": [],
            "registration_statuses": ["ATIVA"],
            "cnaes": ["5611201"],
            "limit": 10000,
        })

        self.assertEqual(total, 13)


if __name__ == "__main__":
    unittest.main()
