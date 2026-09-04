"""Public deployment checks. Creates only a short diagnostic session, deleted in finally."""
import json
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service.auth import AuthStore
from service.config import get_settings


def main():
    settings = get_settings()
    store = AuthStore(settings.auth_database_path, session_days=1 / 24)
    with sqlite3.connect(settings.auth_database_path) as connection:
        owner_id = connection.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
    token, _ = store.create_session(owner_id)
    base = settings.app_public_url.rstrip("/")
    checks = {}

    def request(path, authenticated=True):
        headers = {"Cookie": f"echopjs_session={token}"} if authenticated else {}
        try:
            with urlopen(Request(base + path, headers=headers), timeout=30) as response:
                return response.status, response.read(), response.geturl()
        except HTTPError as error:
            return error.code, b"", ""

    try:
        status, content, url = request("/organizations", False)
        checks["unauthenticated_redirect"] = status == 200 and "/login?" in url
        status, content, _ = request("/api/organizations")
        orgs = json.loads(content)
        checks["owner_admin"] = status == 200 and orgs["can_create"] and orgs["organizations"][0]["role"] == "admin"
        org_id = orgs["organizations"][0]["id"]
        checks["organization_page"] = b'org-select' in request("/organizations")[1]
        checks["team_api"] = request(f"/api/organizations/{org_id}")[0] == 200
        checks["foreign_org_blocked"] = request("/api/jobs?organization_id=999999999")[0] == 403
        status, content, _ = request(f"/api/jobs?organization_id={org_id}")
        jobs = json.loads(content)["jobs"]
        checks["history_scoped"] = status == 200 and all(j["organization_id"] == org_id for j in jobs)
        with sqlite3.connect(settings.job_database_path) as connection:
            checks["legacy_jobs_assigned"] = connection.execute("SELECT count(*) FROM jobs WHERE organization_id IS NULL").fetchone()[0] == 0
        if jobs:
            job = min(jobs, key=lambda j: j["total"])
            status, content, _ = request(f"/api/jobs/{job['id']}/export.csv?organization_id={org_id}")
            checks["existing_export"] = status == 200 and "CNPJ" in content.decode("utf-8-sig").splitlines()[0]
        for name in ("organizations.js", "invite.js", "workspace.js"):
            checks[name] = request(f"/static/{name}", False)[0] == 200
        checks["invite_page"] = request("/invite", False)[0] == 200
        checks["company_lookup"] = request("/api/explorer/companies/12484145000194")[0] == 200
        checks["health"] = request("/health", False)[0] == 200
        print(json.dumps({"checks": checks, "passed": all(checks.values()), "visible_jobs": len(jobs), "email_delivery_available": orgs["email_delivery_available"]}))
        if not all(checks.values()):
            raise SystemExit(1)
    finally:
        store.delete_session(token)
        store.close()


if __name__ == "__main__":
    main()
