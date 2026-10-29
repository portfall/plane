# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Synthetic PostgreSQL closure tests. No source/target deployment credentials."""

from uuid import uuid4
import os
import unittest

import pytest

from django.apps import apps
from django.db import connection
from django.test import TransactionTestCase

from plane.utils.project_bundle import BundleRefused, collect_project_graph, native_schema, export_project_snapshot


pytestmark = pytest.mark.skipif(
    os.environ.get("PLANE_RUN_PROJECT_BUNDLE_TESTS") != "1",
    reason="Opt-in synthetic project-bundle fixture only",
)


def seed(model, **values):
    """Native fixtures avoid save hooks; source audit values are set explicitly."""
    database = values.pop("_database", "default")
    kind = apps.get_model("db", model)
    row = kind(**values)
    kind.objects.using(database).bulk_create([row])
    return row


def seed_graph():
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["NAME"].startswith("test_plane_bundle_")
    user = seed("User", username=f"synthetic-{uuid4()}", email=f"{uuid4()}@invalid.test", is_bot=True)
    workspace = seed("Workspace", name="Synthetic source", slug=f"source-{uuid4()}", owner=user)
    other_workspace = seed("Workspace", name="Synthetic unrelated", slug=f"other-{uuid4()}", owner=user)
    project = seed("Project", workspace=workspace, name="Synthetic OBOT", identifier="OBOT")
    seed("ProjectIdentifier", workspace=workspace, project=project, name="OBOT")
    other = seed("Project", workspace=other_workspace, name="Synthetic unrelated", identifier="OTHER")
    issue = seed(
        "Issue", workspace=workspace, project=project, name="Exact title 雪", sequence_id=17, description_json={}
    )
    foreign = seed("Issue", workspace=other_workspace, project=other, name="Never export unrelated", sequence_id=17)
    return user, workspace, project, other_workspace, other, issue, foreign


@unittest.skipUnless(os.environ.get("PLANE_RUN_PROJECT_BUNDLE_TESTS") == "1", "Isolated project-bundle fixture only")
class ProjectBundleClosureTests(TransactionTestCase):
    def setUp(self):
        self.graph = seed_graph()

    def test_exact_native_schema_and_deleted_history_are_in_the_project_census(self):
        graph = self.graph
        user, workspace, project, _, _, issue, foreign = graph
        page = seed("Page", workspace=workspace, name="Private Page", owned_by=user, access=1)
        seed("ProjectPage", workspace=workspace, project=project, page=page)
        deleted = seed("Issue", workspace=workspace, project=project, name="Deleted issue", sequence_id=18)
        from django.utils import timezone

        apps.get_model("db", "Issue").all_objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())
        rows, actors, _excluded = collect_project_graph("default", str(workspace.pk), str(project.pk), "OBOT")
        assert {str(row["id"]) for row in rows["Issue"]} == {str(issue.pk), str(deleted.pk)}
        assert str(foreign.pk) not in {str(row["id"]) for row in rows["Issue"]}
        assert str(user.pk) in actors
        assert len(native_schema("default")) == 92
        assert rows["Page"][0]["access"] == 1

    def test_incoming_cross_project_relation_is_refused_without_exporting_foreign_content(self):
        graph = self.graph
        _, workspace, project, other_workspace, other, issue, foreign = graph
        seed(
            "IssueRelation",
            workspace=other_workspace,
            project=other,
            issue=foreign,
            related_issue=issue,
            relation_type="relates_to",
        )
        with self.assertRaisesRegex(BundleRefused, "^CROSS_PROJECT_REFERENCE$"):
            collect_project_graph("default", str(workspace.pk), str(project.pk), "OBOT")

    def test_shared_page_or_shared_ancestor_refuses_the_entire_graph(self):
        graph = self.graph
        user, workspace, project, _, other, _, _ = graph
        parent = seed("Page", workspace=workspace, name="Shared ancestor", owned_by=user)
        page = seed("Page", workspace=workspace, name="Child", owned_by=user, parent=parent)
        seed("ProjectPage", workspace=workspace, project=project, page=page)
        seed("ProjectPage", workspace=workspace, project=project, page=parent)
        seed("ProjectPage", workspace=workspace, project=other, page=parent)
        with self.assertRaisesRegex(BundleRefused, "^SHARED_PAGE$"):
            collect_project_graph("default", str(workspace.pk), str(project.pk), "OBOT")

    def test_project_bound_invites_block_even_though_their_payload_is_never_exported(self):
        graph = self.graph
        _, workspace, project, _, _, _, _ = graph
        seed(
            "ProjectMemberInvite",
            workspace=workspace,
            project=project,
            email="SYNTHETIC_NEVER_EXPORT@invalid.test",
            token="SYNTHETIC_NEVER_EXPORT",
        )
        with self.assertRaisesRegex(BundleRefused, "^UNCLASSIFIED_PROJECT_ROWS:ProjectMemberInvite$"):
            collect_project_graph("default", str(workspace.pk), str(project.pk), "OBOT")

    def test_workspace_labels_are_selected_through_project_closure(self):
        graph = self.graph
        _, workspace, project, _, _, issue, _ = graph
        used = seed("Label", workspace=workspace, project=None, name="Reachable")
        unused = seed("Label", workspace=workspace, project=None, name="Unrelated")
        seed("IssueLabel", workspace=workspace, project=project, issue=issue, label=used)
        rows, _actors, _excluded = collect_project_graph("default", str(workspace.pk), str(project.pk), "OBOT")
        assert [str(row["id"]) for row in rows["Label"]] == [str(used.pk)]
        assert str(unused.pk) not in {str(row["id"]) for row in rows["Label"]}

    def test_read_only_snapshot_preserves_integer_keys_audit_and_actual_private_object_bytes(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        from django.utils import timezone
        from datetime import datetime, timezone as dt_timezone
        from plane.utils.project_bundle import sha256, canonical

        user, workspace, project, _, _, issue, _ = self.graph
        historical = datetime(2020, 1, 2, 3, 4, 5, 123456, tzinfo=dt_timezone.utc)
        apps.get_model("db", "Issue").all_objects.filter(pk=issue.pk).update(
            created_at=historical,
            updated_at=historical,
            created_by=user,
            updated_by=None,
            archived_at=historical.date(),
        )
        payload = b"synthetic-private-project-object-\x00\xff"
        key = default_storage.save(f"source/{uuid4()}.png", ContentFile(payload))
        asset = seed(
            "FileAsset",
            workspace=workspace,
            project=project,
            issue=issue,
            entity_type="ISSUE_DESCRIPTION",
            entity_identifier=str(issue.pk),
            asset=key,
            size=len(payload),
            is_uploaded=True,
        )
        freeze = dict(id=str(uuid4()), frozen_at=timezone.now().isoformat())
        first = export_project_snapshot(
            "default", "https://source.invalid", str(workspace.pk), str(project.pk), "OBOT", freeze
        )
        second = export_project_snapshot(
            "default", "https://source.invalid", str(workspace.pk), str(project.pk), "OBOT", freeze
        )
        assert canonical({key: value for key, value in first.items() if key != "capture"}) == canonical(
            {key: value for key, value in second.items() if key != "capture"}
        )
        record = next(row for row in first["records"] if row["kind"] == "Issue" and row["pk"] == str(issue.pk))
        assert record["fields"]["name"] == "Exact title 雪"
        assert record["fields"]["sequence_id"] == 17
        assert record["fields"]["created_at"] == historical.isoformat()
        assert record["fields"]["updated_by_id"] is None
        assert any(row["kind"] == "ProjectIdentifier" and type(row["fields"]["id"]) is int for row in first["records"])
        assert first["objects"] == [
            dict(
                kind="FileAsset",
                pk=str(asset.pk),
                field="asset",
                source_key=key,
                asset_uuid=str(asset.pk),
                sha256=sha256(payload),
                size=len(payload),
            )
        ]
        assert first["blobs"][sha256(payload)]
