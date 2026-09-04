import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from service.auth import AuthStore
from service.jobs import JobStore
from service.models import InvitationRequest, OrganizationRequest
from service.organizations import OrganizationError
from pydantic import ValidationError


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "auth.sqlite")
        self.store = AuthStore(self.path)
        self.store.bootstrap("owner@example.com", "test-password-long")
        self.owner = self.store.authenticate("owner@example.com", "test-password-long")["id"]
        self.org = self.store.ensure_initial_organization()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def invite_user(self, email="member@example.com", role="member", org=None):
        invitation = self.store.create_invitation(self.owner, org or self.org, email, role)
        user, _ = self.store.accept_invitation(invitation["token"], "test-password-long")
        return user

    def test_migration_is_idempotent_and_preserves_credentials(self):
        self.assertEqual(self.org, self.store.ensure_initial_organization())
        self.assertEqual(self.store.organizations_for_user(self.owner)[0]["role"], "admin")
        self.assertEqual(len(self.store.organizations_for_user(self.owner)), 1)
        self.assertIsNotNone(self.store.authenticate("owner@example.com", "test-password-long"))

    def test_admin_can_create_and_member_cannot(self):
        member = self.invite_user()
        second = self.store.create_organization(self.owner, "Segunda")
        self.assertEqual(len(self.store.organizations_for_user(self.owner)), 2)
        self.assertEqual(len(self.store.organizations_for_user(member["id"])), 1)
        with self.assertRaises(OrganizationError): self.store.create_organization(member["id"], "Forbidden")
        with self.assertRaises(OrganizationError): self.store.organization_for_user(member["id"], second["id"])
        with self.assertRaises(OrganizationError): self.store.organization_team(member["id"], self.org)
        with self.assertRaises(OrganizationError): self.store.create_invitation(member["id"], self.org, "other@example.com", "admin")

    def test_invite_is_email_bound_one_time_and_only_hash_is_stored(self):
        invite = self.store.create_invitation(self.owner, self.org, "New@Example.com", "member")
        self.assertEqual(self.store.invitation_preview(invite["token"])["email"], "new@example.com")
        with sqlite3.connect(self.path) as connection:
            stored = connection.execute("SELECT token_hash FROM invitations").fetchone()[0]
        self.assertNotEqual(invite["token"], stored)
        user, org = self.store.accept_invitation(invite["token"], "new-password-long")
        self.assertEqual(org, self.org)
        self.assertEqual(user["identifier"], "new@example.com")
        self.assertIsNotNone(self.store.authenticate("new@example.com", "new-password-long"))
        with self.assertRaises(OrganizationError): self.store.accept_invitation(invite["token"], "new-password-long")
        team = self.store.organization_team(self.owner, self.org)
        self.assertNotIn("token_hash", team["invitations"][0])
        self.assertNotIn("password_hash", str(team))

    def test_expired_revoked_and_replaced_invites_fail(self):
        old = self.store.create_invitation(self.owner, self.org, "new@example.com", "member")
        fresh = self.store.create_invitation(self.owner, self.org, "new@example.com", "member")
        with self.assertRaises(OrganizationError): self.store.invitation_preview(old["token"])
        self.store.revoke_invitation(self.owner, self.org, fresh["id"])
        with self.assertRaises(OrganizationError): self.store.accept_invitation(fresh["token"], "test-password-long")
        expired = self.store.create_invitation(self.owner, self.org, "new@example.com", "member")
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE invitations SET expires_at='2000-01-01' WHERE id=?", (expired["id"],))
        with self.assertRaises(OrganizationError): self.store.invitation_preview(expired["token"])

    def test_existing_account_requires_its_password_never_overwritten(self):
        user = self.invite_user()
        second = self.store.create_organization(self.owner, "Second")
        invite = self.store.create_invitation(self.owner, second["id"], user["identifier"], "admin")
        with self.assertRaises(OrganizationError): self.store.accept_invitation(invite["token"], "attacker-new-password")
        accepted, _ = self.store.accept_invitation(invite["token"], "test-password-long")
        self.assertEqual(accepted["id"], user["id"])
        self.assertEqual(len(self.store.organizations_for_user(user["id"])), 2)
        self.assertIsNone(self.store.authenticate(user["identifier"], "attacker-new-password"))

    def test_last_admin_protected_and_removed_member_loses_access(self):
        member = self.invite_user()
        with self.assertRaises(OrganizationError): self.store.change_member(self.owner, self.org, self.owner)
        with self.assertRaises(OrganizationError): self.store.change_member(self.owner, self.org, self.owner, "member")
        self.store.change_member(self.owner, self.org, member["id"], "admin")
        self.store.change_member(self.owner, self.org, self.owner, "member")
        self.store.change_member(member["id"], self.org, self.owner)
        with self.assertRaises(OrganizationError): self.store.organization_for_user(self.owner, self.org)
        self.store.ensure_initial_organization()
        self.assertEqual(self.store.organizations_for_user(self.owner), [])

    def test_demoting_inviter_revokes_pending_invitations(self):
        admin = self.invite_user(role="admin")
        invite = self.store.create_invitation(admin["id"], self.org, "next@example.com", "admin")
        self.store.change_member(self.owner, self.org, admin["id"], "member")
        with self.assertRaises(OrganizationError): self.store.accept_invitation(invite["token"], "test-password-long")

    def test_invite_acceptance_is_atomic_across_connections(self):
        invite = self.store.create_invitation(self.owner, self.org, "race@example.com", "member")
        other = AuthStore(self.path)
        def accept(store):
            try: store.accept_invitation(invite["token"], "test-password-long"); return True
            except OrganizationError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(accept, [self.store, other])), [False, True])
        other.close()

    def test_last_admin_rule_atomic_across_connections(self):
        admin = self.invite_user(role="admin")
        other = AuthStore(self.path)
        def demote(args):
            store, actor = args
            try: store.change_member(actor, self.org, actor, "member"); return True
            except OrganizationError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(demote, [(self.store, self.owner), (other, admin["id"])])), [False, True])
        other.close()

    def test_jobs_and_exports_are_isolated_and_legacy_migration_is_idempotent(self):
        jobs = JobStore(str(Path(self.tmp.name) / "jobs.sqlite"))
        legacy = jobs.create_job("old.csv", [], active_only=True, check_website=False)
        jobs.assign_legacy_organization(self.org)
        second = self.store.create_organization(self.owner, "Second")["id"]
        new = jobs.create_job("new.csv", [], active_only=True, check_website=False, organization_id=second, created_by=self.owner)
        jobs.assign_legacy_organization(self.org)
        self.assertEqual([j["id"] for j in jobs.list_jobs(organization_id=self.org)], [legacy["id"]])
        self.assertEqual([j["id"] for j in jobs.list_jobs(organization_id=second)], [new["id"]])
        self.assertIsNone(jobs.get_job(legacy["id"], organization_id=second))
        self.assertIsNone(jobs.export_csv(legacy["id"], organization_id=second))
        self.assertIsNotNone(jobs.export_csv(legacy["id"], organization_id=self.org))
        jobs.close()

    def test_request_validation(self):
        for email in ("invalid", "foo@bar", "foo\r\n@bar.com"):
            with self.assertRaises(ValidationError): InvitationRequest(email=email)
        with self.assertRaises(ValidationError): OrganizationRequest(name="   ")
        self.assertEqual(OrganizationRequest(name="  Minha equipe  ").name, "Minha equipe")
