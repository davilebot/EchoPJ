from time import monotonic
from typing import Any

from .repository import Repository
from .website import WebsiteChecker


class MatchingService:
    def __init__(self, repository: Repository, website_checker: WebsiteChecker):
        self.repository = repository
        self.website_checker = website_checker

    def match_items(
        self,
        items: list[dict[str, Any]],
        *,
        active_only: bool = True,
        check_website: bool = False,
    ) -> dict[str, Any]:
        started = monotonic()
        results, version, database_ms = self.repository.match_batch(items, active_only)
        website_ms = 0
        website_evidence = {}
        if check_website:
            unresolved_ids = {result["local_id"] for result in results if result["status"] != "confirmado"}
            unresolved = [item for item in items if item["local_id"] in unresolved_ids and item.get("website")]
            if unresolved:
                website_started = monotonic()
                website_evidence = self.website_checker.check_many(unresolved)
                website_ms = round((monotonic() - website_started) * 1000)
                refined = []
                for item in unresolved:
                    evidence = website_evidence.get(item["local_id"], {})
                    refined.append({
                        **item,
                        "site_cnpjs": evidence.get("cnpjs", []),
                        "site_names": evidence.get("names", []),
                    })
                refined_results, _, refined_database_ms = self.repository.match_batch(refined, active_only)
                database_ms += refined_database_ms
                refined_by_id = {result["local_id"]: result for result in refined_results}
                results = [refined_by_id.get(result["local_id"], result) for result in results]
        for result in results:
            result["website_evidence"] = website_evidence.get(result["local_id"])
        return {
            "results": results,
            "dataset_version": version,
            "timing_ms": {
                "database": database_ms,
                "website": website_ms,
                "total": round((monotonic() - started) * 1000),
            },
        }
