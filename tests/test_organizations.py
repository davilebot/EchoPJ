import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from service.auth import AuthStore
from service.jobs import JobStore
from service.models import CustomerWorkspaceRequest, InvitationRequest, OrganizationRequest
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

    def test_internal_organization_name_is_configured_only_once(self):
        configured = self.store.configure_internal_organization(self.org, "EchoHub")
        self.assertEqual(configured["name"], "EchoHub")
        self.store.rename_organization(self.owner, self.org, "EchoHub Operações")
        configured_again = self.store.configure_internal_organization(self.org, "EchoHub")
        self.assertEqual(configured_again["name"], "EchoHub Operações")

    def test_admin_can_create_and_member_cannot(self):
        member = self.invite_user()
        second = self.store.create_organization(self.owner, "Segunda")
        self.assertEqual(len(self.store.organizations_for_user(self.owner)), 2)
        self.assertEqual(len(self.store.organizations_for_user(member["id"])), 1)
        with self.assertRaises(OrganizationError): self.store.create_organization(member["id"], "Forbidden")
        with self.assertRaises(OrganizationError): self.store.organization_for_user(member["id"], second["id"])
        with self.assertRaises(OrganizationError): self.store.organization_team(member["id"], self.org)
        with self.assertRaises(OrganizationError): self.store.create_invitation(member["id"], self.org, "other@example.com", "admin")

    def test_admin_can_provision_customer_workspace_and_owner_invitation_atomically(self):
        provisioned = self.store.provision_customer_workspace(
            self.owner, "Cliente Piloto", "  CLIENTE@Example.com ",
        )
        organization = provisioned["organization"]
        invitation = provisioned["invitation"]
        self.assertEqual(organization["name"], "Cliente Piloto")
        self.assertEqual(organization["created_by"], self.owner)
        self.assertEqual(invitation["email"], "cliente@example.com")
        self.assertEqual(invitation["role"], "admin")
        self.assertEqual(
            self.store.invitation_preview(invitation["token"])["organization_name"],
            "Cliente Piloto",
        )
        actions = {item["action"] for item in self.store.organization_team(self.owner, organization["id"])["audit"]}
        self.assertTrue({"organization.provisioned", "invitation.created"}.issubset(actions))
        with self.assertRaises(OrganizationError):
            self.store.provision_customer_workspace(self.owner, "Inválido", "owner@example.com")

    def test_viewer_role_is_invitable_and_has_read_only_product_permissions(self):
        viewer = self.invite_user("viewer@example.com", role="viewer")
        organization = self.store.organization_for_user(viewer["id"], self.org)
        self.assertEqual(organization["role"], "viewer")
        self.assertTrue(organization["permissions"]["search"])
        self.assertFalse(organization["permissions"]["export"])
        self.assertFalse(organization["permissions"]["manage_library"])
        self.assertFalse(organization["permissions"]["run_jobs"])
        team_viewer = next(item for item in self.store.organization_team(self.owner, self.org)["members"] if item["id"] == viewer["id"])
        self.assertEqual(team_viewer["permissions"], organization["permissions"])
        self.store.change_member(self.owner, self.org, viewer["id"], "member")
        self.assertTrue(self.store.organization_for_user(viewer["id"], self.org)["permissions"]["export"])

    def test_existing_two_role_database_is_upgraded_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "old-auth.sqlite")
            with sqlite3.connect(path) as connection:
                connection.executescript("""
                    CREATE TABLE memberships (
                        organization_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('admin','member')),
                        joined_at TEXT NOT NULL,
                        PRIMARY KEY(organization_id,user_id)
                    );
                    CREATE INDEX idx_memberships_user ON memberships(user_id);
                    CREATE TABLE invitations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        organization_id INTEGER NOT NULL,
                        email TEXT NOT NULL COLLATE NOCASE,
                        role TEXT NOT NULL CHECK(role IN ('admin','member')),
                        token_hash TEXT NOT NULL UNIQUE,
                        created_by INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        accepted_at TEXT,
                        revoked_at TEXT
                    );
                    CREATE INDEX idx_invites_org ON invitations(organization_id);
                """)
            upgraded = AuthStore(path)
            upgraded.bootstrap("owner@example.com", "test-password-long")
            owner = upgraded.authenticate("owner@example.com", "test-password-long")["id"]
            organization_id = upgraded.ensure_initial_organization()
            invite = upgraded.create_invitation(owner, organization_id, "viewer@example.com", "viewer")
            viewer, _ = upgraded.accept_invitation(invite["token"], "test-password-long")
            self.assertEqual(upgraded.organization_for_user(viewer["id"], organization_id)["role"], "viewer")
            with sqlite3.connect(path) as connection:
                definitions = " ".join(row[0] for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name IN ('memberships','invitations')"
                ))
            self.assertIn("'viewer'", definitions)
            upgraded.close()

    def test_activation_summary_counts_members_and_pending_invitations(self):
        self.assertEqual(
            self.store.organization_activation_summary(self.org),
            {"member_count": 1, "pending_invitation_count": 0},
        )
        invite = self.store.create_invitation(self.owner, self.org, "new@example.com", "member")
        self.assertEqual(self.store.organization_activation_summary(self.org)["pending_invitation_count"], 1)
        self.store.accept_invitation(invite["token"], "test-password-long")
        summary = self.store.organization_activation_summary(self.org)
        self.assertEqual(summary["member_count"], 2)
        self.assertEqual(summary["pending_invitation_count"], 0)
        funnel = self.store.admin_product_funnel_base()
        self.assertEqual(funnel[0]["id"], self.org)
        self.assertEqual(funnel[0]["member_count"], 2)
        self.assertIsNotNone(funnel[0]["last_team_activity_at"])

    def test_invite_is_email_bound_one_time_and_only_hash_is_stored(self):
        invite = self.store.create_invitation(self.owner, self.org, "New@Example.com", "member")
        self.assertEqual(self.store.invitation_preview(invite["token"])["email"], "new@example.com")
        with sqlite3.connect(self.path) as connection:
            stored = connection.execute("SELECT token_hash FROM invitations").fetchone()[0]
        self.assertNotEqual(invite["token"], stored)
        user, org = self.store.accept_invitation(
            invite["token"], "new-password-long",
            legal_versions={"terms": "2026-09", "privacy": "2026-09"},
        )
        self.assertEqual(org, self.org)
        self.assertEqual(user["identifier"], "new@example.com")
        acceptances = self.store.legal_acceptances(user["id"])
        self.assertEqual({item["document_type"] for item in acceptances}, {"terms", "privacy"})
        self.assertTrue(all(item["source"] == "invitation" for item in acceptances))
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
        with self.assertRaises(OrganizationError): self.store.change_member(self.owner, self.org, self.owner, "member")
        self.store.transfer_organization_ownership(self.owner, self.org, member["id"])
        self.store.change_member(self.owner, self.org, self.owner, "member")
        self.store.change_member(member["id"], self.org, self.owner)
        with self.assertRaises(OrganizationError): self.store.organization_for_user(self.owner, self.org)
        self.store.ensure_initial_organization()
        self.assertEqual(self.store.organizations_for_user(self.owner), [])

    def test_only_current_owner_can_transfer_to_an_administrator(self):
        member = self.invite_user()
        with self.assertRaises(OrganizationError):
            self.store.transfer_organization_ownership(self.owner, self.org, member["id"])
        self.store.change_member(self.owner, self.org, member["id"], "admin")
        transferred = self.store.transfer_organization_ownership(self.owner, self.org, member["id"])
        self.assertEqual(transferred["created_by"], member["id"])
        team = self.store.organization_team(member["id"], self.org)
        self.assertTrue(next(item for item in team["members"] if item["id"] == member["id"])["is_owner"])
        with self.assertRaises(OrganizationError):
            self.store.transfer_organization_ownership(self.owner, self.org, self.owner)
        self.assertEqual(team["audit"][0]["action"], "organization.owner_transferred")

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
        with self.assertRaises(ValidationError): CustomerWorkspaceRequest(name="Cliente", owner_email="invalido")
        with self.assertRaises(ValidationError): CustomerWorkspaceRequest(name="Cliente", owner_email="cliente@example.com", trial_credits=-1)
        request = CustomerWorkspaceRequest(name="  Cliente Piloto  ", owner_email=" CLIENTE@Example.com ")
        self.assertEqual((request.name, request.owner_email), ("Cliente Piloto", "cliente@example.com"))
