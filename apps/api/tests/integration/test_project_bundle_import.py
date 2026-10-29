# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Opt-in synthetic real PostgreSQL/installed-codec/object import tests."""

import base64
import json
from copy import deepcopy
from datetime import datetime, timezone as dt_timezone
import os
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import unittest

import pytest

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from plane.utils.project_bundle import (
    BundleRefused,
    canonical,
    export_project_snapshot,
    plan_project_import,
    import_project_snapshot,
    sha256,
)
from test_project_bundle_export import seed, seed_graph


pytestmark = pytest.mark.skipif(
    os.environ.get("PLANE_RUN_PROJECT_BUNDLE_TESTS") != "1",
    reason="Opt-in synthetic project-bundle fixture only",
)


@unittest.skipUnless(os.environ.get("PLANE_RUN_PROJECT_BUNDLE_TESTS") == "1", "Isolated project-bundle fixture only")
class BundleImportFixture(TransactionTestCase):
    databases = {"default", "target"} if os.environ.get("PLANE_RUN_PROJECT_BUNDLE_TESTS") == "1" else {"default"}

    def setUp(self):
        assert connections["target"].vendor == "postgresql"
        assert connections["target"].settings_dict["NAME"].startswith("test_plane_bundle_")
        self.source_objects = TemporaryDirectory(prefix="source-bundle-private-")
        self.target_objects = TemporaryDirectory(prefix="target-bundle-private-")
        self.addCleanup(self.source_objects.cleanup)
        self.addCleanup(self.target_objects.cleanup)
        self.user, self.workspace, self.project, _, _, self.issue, _ = seed_graph()
        seed("ProjectMember", workspace=self.workspace, project=self.project, member=self.user, role=20, is_active=True)
        seed("IssueSequence", workspace=self.workspace, project=self.project, issue=self.issue, sequence=17)
        self.historical = datetime(2020, 1, 2, 3, 4, 5, 123456, tzinfo=dt_timezone.utc)
        apps.get_model("db", "Issue").all_objects.filter(pk=self.issue.pk).update(
            created_at=self.historical, updated_at=self.historical, created_by=self.user, updated_by=None
        )
        self.payload = b"synthetic-private-target-object-\x00\xff"
        with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
            key = default_storage.save(f"source/{uuid4()}.png", ContentFile(self.payload))
            self.asset = seed(
                "FileAsset",
                workspace=self.workspace,
                project=self.project,
                issue=self.issue,
                entity_type="ISSUE_DESCRIPTION",
                entity_identifier=str(self.issue.pk),
                asset=key,
                size=len(self.payload),
                is_uploaded=True,
            )
            self.bundle = export_project_snapshot(
                "default",
                "https://source.invalid",
                str(self.workspace.pk),
                str(self.project.pk),
                "OBOT",
                dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
            )
        self.target_user = seed(
            "User", _database="target", username=f"target-{uuid4()}", email=f"{uuid4()}@invalid.test", is_bot=True
        )
        self.target_workspace = seed(
            "Workspace", _database="target", name="Synthetic target", slug=f"target-{uuid4()}", owner=self.target_user
        )
        self.target_project = seed(
            "Project", _database="target", workspace=self.target_workspace, name="Empty target", identifier="OBOT"
        )
        seed(
            "ProjectIdentifier",
            _database="target",
            workspace=self.target_workspace,
            project=self.target_project,
            name="OBOT",
        )
        seed(
            "ProjectMember",
            _database="target",
            workspace=self.target_workspace,
            project=self.target_project,
            member=self.target_user,
            role=20,
            is_active=True,
        )
        seed(
            "WorkspaceMember",
            _database="target",
            workspace=self.target_workspace,
            member=self.target_user,
            role=20,
            is_active=True,
        )
        self.unrelated_project = seed(
            "Project", _database="target", workspace=self.target_workspace, name="Unrelated target", identifier="OTHER"
        )
        self.unrelated_issue = seed(
            "Issue",
            _database="target",
            workspace=self.target_workspace,
            project=self.unrelated_project,
            name="Never change",
            sequence_id=17,
        )
        self.target = dict(
            instance="https://target.invalid",
            workspace_id=str(self.target_workspace.pk),
            project_id=str(self.target_project.pk),
            identifier="OBOT",
        )
        self.user_map = {str(self.user.pk): str(self.target_user.pk)}

    def storage(self, location):
        return override_settings(
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {
                        "location": location,
                        "file_permissions_mode": 0o600,
                        "directory_permissions_mode": 0o700,
                    },
                }
            }
        )

    def plan(self):
        return plan_project_import("target", self.bundle, self.target, self.user_map, str(self.target_user.pk))

    def apply(self, approved=None):
        approved = approved or sha256(canonical(self.plan()))
        return import_project_snapshot(
            "target", self.bundle, self.target, self.user_map, str(self.target_user.pk), approved
        )


class ProjectBundleImportTests(BundleImportFixture):
    def test_dry_run_then_atomic_native_import_and_identical_retry(self):
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            intent = self.plan()
            assert not list(os.scandir(self.target_objects.name))
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 0
            first = self.apply(sha256(canonical(intent)))
            second = self.apply(sha256(canonical(intent)))
            assert canonical(first) == canonical(second)
            issue = apps.get_model("db", "Issue").all_objects.using("target").values().get(pk=self.issue.pk)
            assert issue["name"] == "Exact title 雪" and issue["sequence_id"] == 17
            assert issue["created_at"] == self.historical and issue["updated_at"] == self.historical
            assert issue["created_by_id"] == self.target_user.pk and issue["updated_by_id"] is None
            assert issue["workspace_id"] == self.target_workspace.pk and issue["project_id"] == self.target_project.pk
            asset = apps.get_model("db", "FileAsset").all_objects.using("target").get(pk=self.asset.pk)
            assert asset.asset.name.startswith("pb/" + self.target_project.pk.hex + "/")
            assert asset.asset.read() == self.payload
            assert str(self.target_project.pk) in asset.asset_url
            assert apps.get_model("db", "User").objects.using("target").count() == 1
            assert (
                apps.get_model("db", "Issue").all_objects.using("target").get(pk=self.unrelated_issue.pk).name
                == "Never change"
            )
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 1

    def test_wrong_approval_and_native_id_collision_refuse_before_blob_writes(self):
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            with self.assertRaisesRegex(BundleRefused, "^INTENT_APPROVAL_MISMATCH$"):
                self.apply("0" * 64)
            assert not list(os.scandir(self.target_objects.name))
            seed(
                "Issue",
                _database="target",
                id=self.issue.pk,
                workspace=self.target_workspace,
                project=self.unrelated_project,
                name="Collision must survive",
                sequence_id=18,
            )
            with self.assertRaisesRegex(BundleRefused, "^NATIVE_ID_COLLISION$"):
                self.plan()
            assert not list(os.scandir(self.target_objects.name))

    def test_failure_after_native_insert_rolls_back_sql_and_receipt_but_retry_uses_staged_bytes(self):
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            intent = self.plan()

            def fail_after_issue(execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if sql.startswith('INSERT INTO "issues"'):
                    raise RuntimeError("synthetic SQL failure after real issue insert")
                return result

            with (
                connections["target"].execute_wrapper(fail_after_issue),
                self.assertRaisesRegex(RuntimeError, "synthetic SQL failure"),
            ):
                self.apply(sha256(canonical(intent)))
            assert not apps.get_model("db", "Issue").all_objects.using("target").filter(pk=self.issue.pk).exists()
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 0
            assert list(os.scandir(self.target_objects.name))
            assert self.apply(sha256(canonical(intent)))["status"] == "committed"

    def test_concurrent_identical_attempts_converge_and_divergent_retry_refuses(self):
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            approved = sha256(canonical(self.plan()))

            def worker():
                try:
                    return self.apply(approved)
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _value: worker(), range(2)))
            assert canonical(results[0]) == canonical(results[1])
            changed = deepcopy(self.bundle)
            changed["records"][0]["fields"]["name"] = "Divergent synthetic retry"
            with self.assertRaisesRegex(BundleRefused, "^MIGRATION_RUN_CONFLICT$"):
                import_project_snapshot(
                    "target", changed, self.target, self.user_map, str(self.target_user.pk), approved
                )


# Generated offline by the exact native editor, including retained deleted Yjs structs.
NATIVE_DOCUMENT_FIXTURE = json.loads(
    '{"page":{"binary":"Age1xpaZBgAHAQdkZWZhdWx0AwlwYXJhZ3JhcGgHALXGlpkGAAYEALXGlpkGARxSZXRha'
    "W5lZCBkZWxldGVkIGhpc3Rvcnkg6Zuqh7XGlpkGAAMJY29kZUJsb2NrBwC1xpaZBhwGBAC1xpaZBh0PZ3JhcGggV"
    "EQ7IEEtLT5CKAC1xpaZBhwIbGFuZ3VhZ2UBdwdtZXJtYWlkBMb81JoFAAcBBXRpdGxlAwdoZWFkaW5nBwDG/NSaB"
    'QAGBADG/NSaBQEPRXhhY3QgdGl0bGUg6ZuqKADG/NSaBQAFbGV2ZWwBfQEBtcaWmQYBABw=","before":"Age1x'
    "paZBgAHAQdkZWZhdWx0AwlwYXJhZ3JhcGgHALXGlpkGAAYEALXGlpkGARxSZXRhaW5lZCBkZWxldGVkIGhpc3Rvc"
    "nkg6Zuqh7XGlpkGAAMJY29kZUJsb2NrBwC1xpaZBhwGBAC1xpaZBh0PZ3JhcGggVEQ7IEEtLT5CKAC1xpaZBhwIb"
    "GFuZ3VhZ2UBdwdtZXJtYWlkBMb81JoFAAcBBXRpdGxlAwdoZWFkaW5nBwDG/NSaBQAGBADG/NSaBQEPRXhhY3Qgd"
    'Gl0bGUg6ZuqKADG/NSaBQAFbGV2ZWwBfQEA","contentBinaryEncoded":"Age1xpaZBgAHAQdkZWZhdWx0Awl'
    "wYXJhZ3JhcGgHALXGlpkGAAYEALXGlpkGARxSZXRhaW5lZCBkZWxldGVkIGhpc3Rvcnkg6Zuqh7XGlpkGAAMJY29"
    "kZUJsb2NrBwC1xpaZBhwGBAC1xpaZBh0PZ3JhcGggVEQ7IEEtLT5CKAC1xpaZBhwIbGFuZ3VhZ2UBdwdtZXJtYWl"
    "kBMb81JoFAAcBBXRpdGxlAwdoZWFkaW5nBwDG/NSaBQAGBADG/NSaBQEPRXhhY3QgdGl0bGUg6ZuqKADG/NSaBQA"
    'FbGV2ZWwBfQEBtcaWmQYBABw=","contentJSON":{"type":"doc","content":[{"type":"codeBlock","a'
    'ttrs":{"language":"mermaid"},"content":[{"type":"text","text":"graph TD; A-->B"}]}]},"co'
    'ntentHTML":"<pre><code class=\\"language-mermaid\\">graph TD; A--&gt;B</code></pre>","titl'
    'eHTML":"Exact title 雪"},"rich":{"binary":"AQO5grDtAgAHAQdkZWZhdWx0AwlwYXJhZ3JhcGgHALmCsO'
    '0CAAYEALmCsO0CAQ5FeGFjdCByaWNoIOmbqgA=","contentBinaryEncoded":"AQO5grDtAgAHAQdkZWZhdWx0'
    'AwlwYXJhZ3JhcGgHALmCsO0CAAYEALmCsO0CAQ5FeGFjdCByaWNoIOmbqgA=","contentJSON":{"type":"doc'
    '","content":[{"type":"paragraph","attrs":{"textAlign":null},"content":[{"type":"text","t'
    'ext":"Exact rich 雪"}]}]},"contentHTML":"<p class=\\"editor-paragraph-block\\">Exact rich 雪'
    '</p>"}}'
)


def seed_complete_graph(test):
    """Every classified native content model, with real historical representations."""
    common = dict(workspace=test.workspace, project=test.project)
    user, issue = test.user, test.issue

    def row(kind, **kwargs):
        return seed(kind, **dict(common, **kwargs))

    page_data, rich = NATIVE_DOCUMENT_FIXTURE["page"], NATIVE_DOCUMENT_FIXTURE["rich"]
    rich_fields = dict(
        description_binary=base64.b64decode(rich["binary"]),
        description_json=rich["contentJSON"],
        description_html=rich["contentHTML"],
    )
    page_fields = dict(
        description_binary=base64.b64decode(page_data["binary"]),
        description_json=page_data["contentJSON"],
        description_html=page_data["contentHTML"],
    )
    state = row("State", name="Historical done", group="completed", default=True)
    estimate = row("Estimate", name="Exact estimate")
    point = row("EstimatePoint", estimate=estimate, key=3, value="Three")
    label = row("Label", name="Exact label")
    issue_type = seed("IssueType", workspace=test.workspace, name="Exact native type")
    row("ProjectIssueType", issue_type=issue_type)
    apps.get_model("db", "Project").all_objects.filter(pk=test.project.pk).update(
        estimate=estimate, default_state=state
    )
    apps.get_model("db", "Issue").all_objects.filter(pk=issue.pk).update(
        state=state, type=issue_type, estimate_point=point, **rich_fields
    )
    second = row("Issue", name="Deleted child", parent=issue, sequence_id=18, **rich_fields)
    apps.get_model("db", "Issue").all_objects.filter(pk=second.pk).update(
        deleted_at=test.historical, archived_at=test.historical.date()
    )
    row("IssueSequence", issue=second, sequence=18, deleted=True)
    row("IssueBlocker", block=issue, blocked_by=second)
    row("IssueRelation", issue=issue, related_issue=second, relation_type="relates_to")
    row("IssueMention", issue=issue, mention=user)
    row("IssueAssignee", issue=issue, assignee=user)
    row("IssueSubscriber", issue=issue, subscriber=user)
    row("IssueLabel", issue=issue, label=label)
    row("IssueLink", issue=issue, title="External reference", url="https://example.invalid/reference")
    activity = row(
        "IssueActivity",
        issue=issue,
        actor=user,
        field="state",
        old_identifier=state.pk,
        new_identifier=state.pk,
        old_value="Historical done",
        new_value="Historical done",
    )
    description = row("Description", **rich_fields)
    row("DescriptionVersion", description=description, **rich_fields)
    parent_comment = row(
        "IssueComment",
        issue=issue,
        actor=user,
        description=description,
        comment_html=rich["contentHTML"],
        comment_json=rich["contentJSON"],
        access="INTERNAL",
    )
    child_comment = row(
        "IssueComment",
        issue=issue,
        actor=user,
        parent=parent_comment,
        edited_at=test.historical,
        comment_html=" <p>Edited child 雪</p>\n",
        comment_json={},
    )
    row("IssueReaction", issue=issue, actor=user, reaction="🚀")
    row("CommentReaction", comment=child_comment, actor=user, reaction="👍")
    row("IssueVote", issue=issue, actor=user, vote=-1)
    cycle = row("Cycle", name="Exact cycle", owned_by=user)
    row("CycleIssue", cycle=cycle, issue=issue)
    row("CycleUserProperties", cycle=cycle, user=user)
    module = row("Module", name="Exact module", lead=user)
    row("ModuleIssue", module=module, issue=issue)
    row("ModuleMember", module=module, member=user)
    row("ModuleLink", module=module, title="External plain link", url="https://example.invalid/reference")
    row("ModuleUserProperties", module=module, user=user)
    row("ProjectUserProperty", user=user)
    row("IssueView", name="Exact view", owned_by=user, query={})
    draft = row(
        "DraftIssue",
        parent=issue,
        state=state,
        type=issue_type,
        estimate_point=point,
        name="Private draft",
        **rich_fields,
    )
    row("DraftIssueAssignee", draft_issue=draft, assignee=user)
    row("DraftIssueLabel", draft_issue=draft, label=label)
    row("DraftIssueModule", draft_issue=draft, module=module)
    row("DraftIssueCycle", draft_issue=draft, cycle=cycle)
    intake = row("Intake", name="Private intake", is_default=True)
    row("IntakeIssue", intake=intake, issue=issue, duplicate_to=second)
    row("ProjectDeployBoard", intake=intake)
    row("DeployBoard", entity_name="issue", entity_identifier=issue.pk, intake=intake)
    row(
        "IssueVersion",
        issue=issue,
        owned_by=user,
        activity=activity,
        parent=second.pk,
        state=state.pk,
        estimate_point=point.pk,
        type=issue_type.pk,
        cycle=cycle.pk,
        modules=[module.pk],
        labels=[label.pk],
        assignees=[user.pk],
        name="Historical title",
        sequence_id=17,
    )
    row("IssueDescriptionVersion", issue=issue, owned_by=user, **rich_fields)
    parent = seed(
        "Page",
        workspace=test.workspace,
        name="Exact private parent",
        owned_by=user,
        access=1,
        is_locked=True,
        **page_fields,
    )
    child = seed(
        "Page",
        workspace=test.workspace,
        name="Exact private child",
        parent=parent,
        owned_by=user,
        access=1,
        **page_fields,
    )
    row("ProjectPage", page=parent)
    row("ProjectPage", page=child)
    row(
        "IssueLink",
        issue=issue,
        title="Native private Page association",
        url=f"https://source.invalid/{test.workspace.slug}/projects/{test.project.pk}/pages/{child.pk}",
    )
    seed("PageLabel", workspace=test.workspace, page=child, label=label)
    seed(
        "PageLog",
        workspace=test.workspace,
        page=child,
        entity_name="issue",
        entity_type="issue",
        entity_identifier=issue.pk,
    )
    seed("PageVersion", workspace=test.workspace, page=child, owned_by=user, **page_fields)
    # Active public membership must already exist on the destination.
    row("ProjectPublicMember", member=user)
    seed(
        "ProjectPublicMember",
        _database="target",
        workspace=test.target_workspace,
        project=test.target_project,
        member=test.target_user,
    )
    with test.storage(test.source_objects.name):
        for entity, owner, name in [
            ("ISSUE_ATTACHMENT", issue, "issue"),
            ("COMMENT_DESCRIPTION", parent_comment, "comment"),
            ("PAGE_DESCRIPTION", child, "page"),
            ("PROJECT_COVER", test.project, "project"),
            ("DRAFT_ISSUE_ATTACHMENT", draft, "draft_issue"),
            ("DRAFT_ISSUE_DESCRIPTION", draft, "draft_issue"),
        ]:
            key = default_storage.save(f"source/{uuid4()}.bin", ContentFile(test.payload + entity.encode()))
            row(
                "FileAsset",
                **{name: owner} if name != "project" else {},
                entity_type=entity,
                entity_identifier=str(owner.pk),
                asset=key,
                is_uploaded=True,
            )
        key = default_storage.save(f"source/{uuid4()}.bin", ContentFile(b"legacy-attachment-private"))
        row("IssueAttachment", issue=issue, asset=key)
    return child


class CompleteProjectBundleTests(BundleImportFixture):
    def test_complete_native_graph_fields_documents_and_source_target_isolation(self):
        page = seed_complete_graph(self)
        with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):

            def read_only_no_accounts(execute, sql, params, many, context):
                assert not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                assert 'FROM "users"' not in sql and 'FROM "accounts"' not in sql
                return execute(sql, params, many, context)

            with connections["default"].execute_wrapper(read_only_no_accounts):
                self.bundle = export_project_snapshot(
                    "default",
                    "https://source.invalid",
                    str(self.workspace.pk),
                    str(self.project.pk),
                    "OBOT",
                    dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
                )
        assert {kind for kind, count in self.bundle["census"].items() if count} == set(self.bundle["census"])
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):

            def no_source_query(*_args):
                raise AssertionError("Importer accessed source connection")

            from unittest.mock import patch

            with (
                connections["default"].execute_wrapper(no_source_query),
                patch("django.db.models.Model.save", side_effect=AssertionError("native save side effect")),
            ):
                receipt = self.apply()
            assert receipt["codec_support"] == "identity-only/1"
            target_page = apps.get_model("db", "Page").all_objects.using("target").values().get(pk=page.pk)
            assert bytes(target_page["description_binary"]) == base64.b64decode(
                NATIVE_DOCUMENT_FIXTURE["page"]["binary"]
            )
            assert target_page["description_html"] == NATIVE_DOCUMENT_FIXTURE["page"]["contentHTML"]
            assert target_page["description_json"] == NATIVE_DOCUMENT_FIXTURE["page"]["contentJSON"]
            assert b"Retained deleted history" in bytes(target_page["description_binary"])
            assert target_page["access"] == 1 and target_page["parent_id"] is not None
            for record in self.bundle["records"]:
                kind = record["kind"]
                model = apps.get_model("db", kind)
                pk = self.target_project.pk if kind == "Project" else record["pk"]
                actual = getattr(model, "all_objects", model._base_manager).using("target").values().get(pk=pk)
                for field in model._meta.concrete_fields:
                    expected = record["fields"][field.attname]
                    if field.is_relation and expected is not None:
                        expected = {
                            "User": self.user_map.get(str(expected)),
                            "Workspace": str(self.target_workspace.pk),
                            "Project": str(self.target_project.pk),
                        }.get(field.related_model.__name__, expected)
                    if field.primary_key and kind == "Project":
                        expected = str(self.target_project.pk)
                    if (
                        kind == "FileAsset"
                        and field.attname == "entity_identifier"
                        and expected == str(self.project.pk)
                    ):
                        expected = str(self.target_project.pk)
                    from django.db import models

                    if isinstance(field, models.BinaryField) and expected is not None:
                        assert bytes(actual[field.attname]) == base64.b64decode(
                            self.bundle["blobs"][expected["sha256"]]
                        )
                    elif isinstance(field, models.FileField) and expected:
                        item = next(
                            item
                            for item in receipt["objects"]
                            if item["kind"] == kind and item["pk"] == record["pk"] and item["field"] == field.attname
                        )
                        assert actual[field.attname] == item["target_key"]
                    elif kind in {"IssueLink", "ModuleLink"} and field.attname == "url":
                        checked = next(
                            item
                            for item in receipt["content_checks"]
                            if item["kind"] == kind and item["pk"] == record["pk"] and item["field"] == "url"
                        )
                        assert actual[field.attname] == checked["result"]["target"]
                        if expected.startswith("https://source.invalid/"):
                            assert actual[field.attname].startswith(
                                f"https://target.invalid/{self.target_workspace.slug}/projects/{self.target_project.pk}/pages/"
                            )
                    elif kind == "IssueVersion" and field.attname == "assignees":
                        assert list(map(str, actual[field.attname])) == [str(self.target_user.pk)]
                    elif isinstance(field, models.JSONField):
                        assert canonical(actual[field.attname]) == canonical(expected)
                    else:
                        from plane.utils.project_bundle import _pack_scalar

                        assert canonical(_pack_scalar(actual[field.attname])) == canonical(expected), (
                            kind,
                            field.attname,
                        )
            output = os.environ.get("PLANE_BUNDLE_TEST_OUTPUT")
            if output:
                from pathlib import Path

                Path(output, "stored-native-document.json").write_text(
                    json.dumps(
                        dict(
                            binary=base64.b64encode(bytes(target_page["description_binary"])).decode(),
                            fixture=NATIVE_DOCUMENT_FIXTURE["page"],
                        )
                    )
                )


class ProjectBundleFailureAndDeltaTests(BundleImportFixture):
    def test_final_snapshot_reconciles_exact_add_update_remove_without_source_or_blob_deletion(self):
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            baseline = self.apply()
        with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
            apps.get_model("db", "Issue").all_objects.filter(pk=self.issue.pk).update(
                name="Final title", archived_at=self.historical.date()
            )
            with connections["default"].cursor() as cursor:
                cursor.execute('DELETE FROM "file_assets" WHERE id=%s', [self.asset.pk])
            added = seed("Issue", workspace=self.workspace, project=self.project, name="Final addition", sequence_id=18)
            seed("IssueSequence", workspace=self.workspace, project=self.project, issue=added, sequence=18)
            final = export_project_snapshot(
                "default",
                "https://source.invalid",
                str(self.workspace.pk),
                str(self.project.pk),
                "OBOT",
                dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
            )
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            args = ("target", final, self.target, self.user_map, str(self.target_user.pk))
            with self.assertRaisesRegex(BundleRefused, "^BASELINE_RECEIPT_REQUIRED$"):
                plan_project_import(*args)
            baseline_hash = sha256(canonical(baseline))
            intent = plan_project_import(*args, baseline_receipt_sha256=baseline_hash)
            assert any(
                row["kind"] == "FileAsset" and row["pk"] == str(self.asset.pk) for row in intent["delta"]["removals"]
            )
            assert any(row["kind"] == "Issue" and row["pk"] == str(added.pk) for row in intent["delta"]["additions"])
            assert any(row["kind"] == "Issue" and row["pk"] == str(self.issue.pk) for row in intent["delta"]["updates"])
            receipt = import_project_snapshot(*args, sha256(canonical(intent)), baseline_receipt_sha256=baseline_hash)
            assert receipt["delta"] == intent["delta"]
            assert apps.get_model("db", "Issue").all_objects.using("target").get(pk=self.issue.pk).name == "Final title"
            assert not apps.get_model("db", "FileAsset").all_objects.using("target").filter(pk=self.asset.pk).exists()
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 2
            # Final reconciliation never deletes source/staged object bytes.
            assert default_storage.exists(baseline["objects"][0]["target_key"])
            assert self.unrelated_issue.pk in apps.get_model("db", "Issue").all_objects.using("target").values_list(
                "pk", flat=True
            )

    def test_commit_failure_rolls_back_rows_receipt_and_transactional_counter_restart(self):
        from unittest.mock import patch
        from django.db import DatabaseError

        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            intent = self.plan()

            def counter():
                with connections["target"].cursor() as cursor:
                    cursor.execute("SELECT pg_get_serial_sequence('project_identifiers', 'id')")
                    sequence = cursor.fetchone()[0].split(".")[-1]
                    cursor.execute("SELECT last_value,is_called FROM " + connections["target"].ops.quote_name(sequence))
                    return cursor.fetchone()

            before = counter()
            with patch.object(connections["target"], "commit", side_effect=DatabaseError("synthetic commit failure")):
                with self.assertRaisesRegex(DatabaseError, "synthetic commit failure"):
                    self.apply(sha256(canonical(intent)))
            assert counter() == before
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 0
            assert not apps.get_model("db", "Issue").all_objects.using("target").filter(pk=self.issue.pk).exists()
            assert self.apply(sha256(canonical(intent)))["status"] == "committed"

    def test_real_process_exit_mid_transaction_leaves_no_receipt_and_identical_retry_succeeds(self):
        import multiprocessing

        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            approved = sha256(canonical(self.plan()))
            connections.close_all()

            def child():
                def exit_after_issue(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if sql.startswith('INSERT INTO "issues"'):
                        os._exit(73)
                    return result

                with connections["target"].execute_wrapper(exit_after_issue):
                    self.apply(approved)
                os._exit(74)

            process = multiprocessing.get_context("fork").Process(target=child)
            process.start()
            process.join(timeout=30)
            if process.is_alive():
                process.kill()
                process.join()
                self.fail("Synthetic crash fixture exceeded its bounded deadline")
            assert process.exitcode == 73
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 0
            assert not apps.get_model("db", "Issue").all_objects.using("target").filter(pk=self.issue.pk).exists()
            assert list(os.scandir(self.target_objects.name))
            assert self.apply(approved)["status"] == "committed"

    def test_immutable_receipt_and_corrupt_or_missing_referenced_bytes_never_report_completion(self):
        from django.db import IntegrityError, transaction
        from pathlib import Path

        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            approved = sha256(canonical(self.plan()))
            receipt = self.apply(approved)
            runs = apps.get_model("db", "ProjectMigrationRun").objects.using("target")
            with self.assertRaises(IntegrityError), transaction.atomic(using="target"):
                runs.update(bundle_sha256="0" * 64)
            with self.assertRaises(IntegrityError), transaction.atomic(using="target"):
                with connections["target"].cursor() as cursor:
                    cursor.execute("DELETE FROM project_migration_runs")
            path = Path(self.target_objects.name, receipt["objects"][0]["target_key"])
            path.write_bytes(b"Corrupt synthetic target bytes")
            with self.assertRaisesRegex(BundleRefused, "^TARGET_BLOB_MISMATCH$"):
                self.apply(approved)
            path.unlink()
            with self.assertRaisesRegex(BundleRefused, "^INACCESSIBLE_OBJECT$"):
                self.apply(approved)
            assert runs.count() == 1

    def test_extra_target_rows_and_changed_native_history_or_legacy_markup_refuse_before_staging(self):
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            seed(
                "Issue",
                _database="target",
                workspace=self.target_workspace,
                project=self.target_project,
                name="Do not replace",
                sequence_id=1,
            )
            with self.assertRaisesRegex(BundleRefused, "^TARGET_NOT_EMPTY$"):
                self.plan()
            assert not list(os.scandir(self.target_objects.name))
        # Unknown source markup is also refused by actual export, preserving raw source fields.
        apps.get_model("db", "Issue").all_objects.filter(pk=self.issue.pk).update(
            description_html='<p future="opaque">Original</p>'
        )
        with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
            with self.assertRaisesRegex(BundleRefused, "^UNKNOWN_ATTRIBUTE$"):
                export_project_snapshot(
                    "default",
                    "https://source.invalid",
                    str(self.workspace.pk),
                    str(self.project.pk),
                    "OBOT",
                    dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
                )


@unittest.skipUnless(os.environ.get("PLANE_RUN_PROJECT_BUNDLE_S3_TESTS") == "1", "Isolated real SeaweedFS fixture only")
class NativeStorageFixture(BundleImportFixture):
    def native_storage(self, **overrides):
        values = dict(
            STORAGES={"default": {"BACKEND": "plane.settings.storage.S3Storage"}},
            AWS_STORAGE_BUCKET_NAME="plane-bundle-fixture",
            AWS_S3_ENDPOINT_URL="http://plane-bundle-store:9000",
            AWS_DEFAULT_ACL=None,
            AWS_QUERYSTRING_AUTH=True,
            AWS_S3_CUSTOM_DOMAIN=None,
            AWS_LOCATION="",
            AWS_S3_OBJECT_PARAMETERS={},
            PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID="synthetic-bundle-audit",
            PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY="synthetic-bundle-audit-secret-only",
        )
        values.update(overrides)
        return override_settings(**values)

    def native_environment(self):
        from unittest.mock import patch

        return patch.dict(
            os.environ,
            dict(
                AWS_ACCESS_KEY_ID="synthetic-bundle-app",
                AWS_SECRET_ACCESS_KEY="synthetic-bundle-app-secret-only",
                AWS_S3_BUCKET_NAME="plane-bundle-fixture",
                AWS_S3_ENDPOINT_URL="http://plane-bundle-store:9000",
                AWS_REGION="us-east-1",
                USE_MINIO="1",
            ),
        )


@unittest.skipUnless(os.environ.get("PLANE_RUN_PROJECT_BUNDLE_S3_TESTS") == "1", "Isolated real SeaweedFS fixture only")
class ProjectBundleNativeS3Tests(NativeStorageFixture):
    def test_actual_native_s3_source_reads_and_private_target_conditional_receipt_retry(self):
        from plane.utils.project_bundle import _native_s3_client, _unwrap_storage

        with self.native_environment(), self.native_storage(), override_settings(WEB_URL="https://source.invalid"):
            client, bucket, _endpoint = _native_s3_client(_unwrap_storage(default_storage))
            source_key = "synthetic-source/" + uuid4().hex
            client.put_object(Bucket=bucket, Key=source_key, Body=self.payload, IfNoneMatch="*")
            apps.get_model("db", "FileAsset").all_objects.filter(pk=self.asset.pk).update(asset=source_key)
            # A source read needs no bootstrap credential and cannot call policy mutation.
            with override_settings(
                PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID=None, PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY=None
            ):
                self.bundle = export_project_snapshot(
                    "default",
                    "https://source.invalid",
                    str(self.workspace.pk),
                    str(self.project.pk),
                    "OBOT",
                    dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
                )
            with override_settings(WEB_URL="https://target.invalid"):
                intent = self.plan()
                assert intent["storage_proof"][0]["proof"]["policy"] == "NoSuchBucketPolicy"
                prefix = "pb/" + self.target_project.pk.hex + "/"
                assert client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("KeyCount", 0) == 0
                receipt = self.apply(sha256(canonical(intent)))
                assert canonical(self.apply(sha256(canonical(intent)))) == canonical(receipt)
                key = receipt["objects"][0]["target_key"]
                assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == self.payload
                assert client.get_object(Bucket=bucket, Key=source_key)["Body"].read() == self.payload
                assert receipt["storage_proof"][0]["proof"]["conditional_create"] == "IfNoneMatch:*;no-ACL"

    def test_missing_audit_or_configured_public_acl_refuses_without_object_or_sql_mutation(self):
        with self.native_environment(), self.native_storage(), override_settings(WEB_URL="https://target.invalid"):
            with override_settings(
                PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID=None, PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY=None
            ):
                with self.assertRaisesRegex(BundleRefused, "^SEPARATE_MIGRATION_S3_AUDIT_IDENTITY_REQUIRED$"):
                    self.plan()
            with override_settings(AWS_DEFAULT_ACL="public-read"):
                with self.assertRaisesRegex(BundleRefused, "^PUBLIC_OR_UNREVIEWED_S3_CONFIGURATION$"):
                    self.plan()
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 0
            from plane.utils.project_bundle import _native_s3_client, _unwrap_storage

            client, bucket, _endpoint = _native_s3_client(_unwrap_storage(default_storage))
            assert (
                client.list_objects_v2(Bucket=bucket, Prefix="pb/" + self.target_project.pk.hex + "/").get(
                    "KeyCount", 0
                )
                == 0
            )

    def test_existing_wrong_bytes_are_not_overwritten_by_conditional_put(self):
        from plane.utils.project_bundle import _native_s3_client, _unwrap_storage

        with self.native_environment(), self.native_storage(), override_settings(WEB_URL="https://target.invalid"):
            client, bucket, _endpoint = _native_s3_client(_unwrap_storage(default_storage))
            intent = self.plan()
            key = intent["objects"][0]["target_key"]
            wrong = b"synthetic existing bytes must survive"
            client.put_object(Bucket=bucket, Key=key, Body=wrong, IfNoneMatch="*")
            with self.assertRaisesRegex(BundleRefused, "^TARGET_BLOB_MISMATCH$"):
                self.apply(sha256(canonical(intent)))
            assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == wrong
            assert apps.get_model("db", "ProjectMigrationRun").objects.using("target").count() == 0
            assert not apps.get_model("db", "Issue").all_objects.using("target").filter(pk=self.issue.pk).exists()


class ProjectBundleProvenanceTests(BundleImportFixture):
    def test_json_null_and_sql_null_remain_distinct_in_native_rows_and_hashes(self):
        from django.db import models

        apps.get_model("db", "Issue").all_objects.filter(pk=self.issue.pk).update(
            description_json=models.Value(None, output_field=models.JSONField())
        )
        apps.get_model("db", "Project").all_objects.filter(pk=self.project.pk).update(
            description_text=models.Value(None, output_field=models.JSONField())
        )
        with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
            self.bundle = export_project_snapshot(
                "default",
                "https://source.invalid",
                str(self.workspace.pk),
                str(self.project.pk),
                "OBOT",
                dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
            )
        record = next(row for row in self.bundle["records"] if row["kind"] == "Project")
        assert record["fields"]["description_html"] is None and record["fields"]["description_text"] is None
        assert "description_html" in record["json_sql_nulls"] and "description_text" not in record["json_sql_nulls"]
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            receipt = self.apply()
            with connections["target"].cursor() as cursor:
                cursor.execute(
                    "SELECT description_html IS NULL, description_text IS NULL, "
                    "description_text = 'null'::jsonb FROM projects WHERE id=%s",
                    [self.target_project.pk],
                )
                assert cursor.fetchone() == (True, False, True)
                cursor.execute(
                    "SELECT description_json IS NULL, description_json = 'null'::jsonb FROM issues WHERE id=%s",
                    [self.issue.pk],
                )
                assert cursor.fetchone() == (False, True)
            assert receipt["status"] == "committed"

    def test_many_to_one_actor_map_rejects_native_join_unique_collision_before_staging(self):
        other_actor = seed("User", username=f"historical-{uuid4()}", email=f"{uuid4()}@invalid.test", is_bot=True)
        for actor in (self.user, other_actor):
            seed("IssueAssignee", workspace=self.workspace, project=self.project, issue=self.issue, assignee=actor)
        with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
            self.bundle = export_project_snapshot(
                "default",
                "https://source.invalid",
                str(self.workspace.pk),
                str(self.project.pk),
                "OBOT",
                dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
            )
        self.user_map[str(other_actor.pk)] = str(self.target_user.pk)
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            with self.assertRaisesRegex(BundleRefused, "^MAPPED_NATIVE_UNIQUE_COLLISION$"):
                self.plan()
            assert not list(os.scandir(self.target_objects.name))


@unittest.skipUnless(os.environ.get("PLANE_RUN_PROJECT_BUNDLE_S3_TESTS") == "1", "Isolated real SeaweedFS fixture only")
class ProjectBundleNativeEndpointTests(NativeStorageFixture):
    def test_explicit_legacy_minio_endpoint_is_the_same_native_bound_source_target_contract(self):
        from unittest.mock import patch
        from plane.settings.storage import S3Storage
        from plane.utils.project_bundle import _native_s3_client

        endpoint = "http://plane-bundle-store:9000"
        with self.native_environment(), patch.dict(os.environ, {"MINIO_ENDPOINT_URL": endpoint}), self.native_storage():
            del os.environ["AWS_S3_ENDPOINT_URL"]
            # Confirm the actual unchanged native constructor's own resolution first.
            native = S3Storage()
            assert native.aws_s3_endpoint_url == endpoint
            client, bucket, resolved = _native_s3_client(native)
            assert resolved == endpoint
            key = "synthetic-source/" + uuid4().hex
            client.put_object(Bucket=bucket, Key=key, Body=self.payload, IfNoneMatch="*")
            apps.get_model("db", "FileAsset").all_objects.filter(pk=self.asset.pk).update(asset=key)
            with override_settings(WEB_URL="https://source.invalid"):
                self.bundle = export_project_snapshot(
                    "default",
                    "https://source.invalid",
                    str(self.workspace.pk),
                    str(self.project.pk),
                    "OBOT",
                    dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
                )
            with override_settings(WEB_URL="https://target.invalid"):
                assert self.apply()["status"] == "committed"


class ProjectBundleCommandAndCatalogTests(BundleImportFixture):
    def test_actual_commands_private_artifacts_and_postcommit_receipt_file_retry(self):
        from io import StringIO
        from pathlib import Path
        from django.core.management import call_command, CommandError
        from plane.utils.project_bundle import publish_bundle_file, read_private_json

        with TemporaryDirectory(prefix="private-command-bundle-") as directory:
            root = Path(directory)
            bundle_path, mapping_path = root / "bundle.json", root / "map.json"
            intent_path, receipt_path = root / "intent.json", root / "receipt.json"
            output = StringIO()
            with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
                call_command(
                    "export_project_bundle",
                    database="default",
                    source_instance="https://source.invalid",
                    source_workspace=str(self.workspace.pk),
                    source_project=str(self.project.pk),
                    source_identifier="OBOT",
                    freeze_id=str(uuid4()),
                    frozen_at=timezone.now().isoformat(),
                    output=str(bundle_path),
                    stdout=output,
                )
            publish_bundle_file(mapping_path, self.user_map)
            options = dict(
                database="target",
                bundle=str(bundle_path),
                target_instance=self.target["instance"],
                target_workspace=self.target["workspace_id"],
                target_project=self.target["project_id"],
                target_identifier="OBOT",
                actor_map=str(mapping_path),
                acting_admin=str(self.target_user.pk),
            )
            with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
                call_command("import_project_bundle", **options, dry_run=True, output=str(intent_path), stdout=output)
                intent = read_private_json(intent_path)
                approved = sha256(canonical(intent))
                # A failed filesystem publication after SQL commit cannot create
                # a second run or turn its file into an independent receipt.
                publish_bundle_file(receipt_path, {"synthetic": "conflicting file"})
                with self.assertRaisesRegex(CommandError, "^OUTPUT_ARTIFACT_CONFLICT$"):
                    call_command(
                        "import_project_bundle",
                        **options,
                        approve_intent_sha256=approved,
                        output=str(receipt_path),
                        stdout=output,
                    )
                runs = apps.get_model("db", "ProjectMigrationRun").objects.using("target")
                assert runs.count() == 1
                corrected = root / "committed-receipt.json"
                call_command(
                    "import_project_bundle",
                    **options,
                    approve_intent_sha256=approved,
                    output=str(corrected),
                    stdout=output,
                )
                assert canonical(read_private_json(corrected)) == canonical(runs.get().receipt)
                assert corrected.stat().st_mode & 0o777 == 0o600
                assert "synthetic-private-target-object" not in output.getvalue()

    def test_actual_pg_unknown_table_and_column_are_not_hidden_by_python_meta(self):
        from plane.utils.project_bundle import schema_profile

        with connections["default"].cursor() as cursor:
            cursor.execute(
                "CREATE TABLE synthetic_unclassified_project_content "
                "(id uuid, project_id uuid REFERENCES projects(id), opaque text)"
            )
            try:
                with self.assertRaisesRegex(BundleRefused, "^UNCLASSIFIED_DATABASE_TABLE$"):
                    schema_profile("default")
            finally:
                cursor.execute("DROP TABLE synthetic_unclassified_project_content")
            cursor.execute("ALTER TABLE issues ADD COLUMN synthetic_unclassified_content text")
            try:
                with self.assertRaisesRegex(BundleRefused, "^DATABASE_COLUMN_DRIFT:Issue$"):
                    schema_profile("default")
            finally:
                cursor.execute("ALTER TABLE issues DROP COLUMN synthetic_unclassified_content")

    @unittest.skipUnless(
        os.environ.get("PLANE_RUN_PROJECT_BUNDLE_FULL_APPS_TESTS") == "1",
        "Exact production INSTALLED_APPS and migrated PostgreSQL fixture only",
    )
    def test_full_production_app_inventory_allows_only_known_license_metadata_without_reading_rows(self):
        import re
        from django.conf import settings
        from django.db import transaction
        from plane.settings.production import INSTALLED_APPS
        from plane.utils.project_bundle import schema_profile

        assert settings.INSTALLED_APPS == INSTALLED_APPS
        license_tables = {"instances", "instance_admins", "instance_configurations", "changelogs"}
        assert {model._meta.db_table for model in apps.get_app_config("license").get_models()} == license_tables
        configuration = apps.get_model("license", "InstanceConfiguration")
        marker = "SYNTHETIC_LICENSE_PAYLOAD_NEVER_EXPORT"
        configuration.objects.using("default").bulk_create(
            [configuration(key="synthetic-project-bundle-audit", value=marker, category="test-only", is_encrypted=True)]
        )

        def forbid_license_payload_read(execute, sql, params, many, context):
            # Catalog names/metadata may be read, but not any excluded row payload.
            for table in license_tables:
                assert re.search(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+"?' + table + r'"?\b', sql, re.I) is None
            return execute(sql, params, many, context)

        with connections["default"].execute_wrapper(forbid_license_payload_read):
            profile = schema_profile("default")
            with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
                bundle = export_project_snapshot(
                    "default",
                    "https://source.invalid",
                    str(self.workspace.pk),
                    str(self.project.pk),
                    "OBOT",
                    dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
                )
            assert bundle["schema_profile"] == profile
            assert marker.encode() not in canonical(bundle)
            assert {row["kind"] for row in bundle["records"]}.isdisjoint(
                {"Instance", "InstanceAdmin", "InstanceConfiguration", "ChangeLog"}
            )
            tables = connections["default"].introspection.table_names()
            assert license_tables.issubset(tables)
            assert profile["table_inventory_sha256"] == sha256(
                canonical(sorted(table for table in tables if table != "project_migration_runs"))
            )
            # A source can omit the target-only receipt table without changing its profile.
            # Roll back the fixture-only schema move, including its temporary schema.
            with transaction.atomic(using="default"):
                with connections["default"].cursor() as cursor:
                    cursor.execute("CREATE SCHEMA synthetic_project_bundle_receipt")
                    cursor.execute("ALTER TABLE project_migration_runs SET SCHEMA synthetic_project_bundle_receipt")
                assert schema_profile("default") == profile
                transaction.set_rollback(True, using="default")
            assert schema_profile("default") == profile

    def test_native_default_states_and_admin_properties_are_reviewed_bootstrap_not_unrelated_content(self):
        from plane.db.models.state import DEFAULT_STATES

        for definition in DEFAULT_STATES:
            seed(
                "State",
                _database="target",
                workspace=self.target_workspace,
                project=self.target_project,
                created_by=self.target_user,
                **definition,
            )
        properties = seed(
            "ProjectUserProperty",
            _database="target",
            workspace=self.target_workspace,
            project=self.target_project,
            user=self.target_user,
        )
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            intent = self.plan()
            assert len([row for row in intent["replaced_target_rows"] if row["kind"] == "State"]) == 6
            assert any(
                row["kind"] == "ProjectUserProperty" and row["pk"] == str(properties.pk)
                for row in intent["replaced_target_rows"]
            )
            assert self.apply(sha256(canonical(intent)))["status"] == "committed"
            assert (
                apps.get_model("db", "State").all_objects.using("target").filter(project=self.target_project).count()
                == 0
            )

    def test_wrong_schema_freeze_actor_map_and_integer_pk_collision_refuse_before_staging(self):
        with self.storage(self.target_objects.name), override_settings(WEB_URL="https://target.invalid"):
            original = self.bundle
            self.bundle = deepcopy(original)
            self.bundle["schema_profile"]["django_version"] = "unreviewed"
            with self.assertRaisesRegex(BundleRefused, "^DATABASE_SCHEMA_PROFILE_MISMATCH$"):
                self.plan()
            self.bundle = deepcopy(original)
            self.bundle["freeze"]["id"] = "invalid-freeze"
            with self.assertRaisesRegex(BundleRefused, "^INVALID_FREEZE_ASSERTION$"):
                self.plan()
            self.bundle = deepcopy(original)
            self.bundle["blobs"][next(iter(self.bundle["blobs"]))] = "not-base64"
            with self.assertRaisesRegex(BundleRefused, "^INVALID_BLOB_HASH$"):
                self.plan()
            self.bundle = original
            with self.assertRaisesRegex(BundleRefused, "^INCOMPLETE_ACTOR_MAP$"):
                plan_project_import("target", self.bundle, self.target, {}, str(self.target_user.pk))
            identifiers = apps.get_model("db", "ProjectIdentifier")
            source_pk = next(
                row["fields"]["id"] for row in self.bundle["records"] if row["kind"] == "ProjectIdentifier"
            )
            identifiers.all_objects.using("target").filter(project=self.target_project).update(id=source_pk + 100000)
            seed(
                "ProjectIdentifier",
                _database="target",
                id=source_pk,
                workspace=self.target_workspace,
                project=self.unrelated_project,
                name="OTHER",
            )
            with self.assertRaisesRegex(BundleRefused, "^NATIVE_ID_COLLISION$"):
                self.plan()
            assert not list(os.scandir(self.target_objects.name))


@unittest.skipUnless(
    os.environ.get("PLANE_RUN_PROJECT_BUNDLE_RESTORE_TESTS") == "1",
    "Isolated PostgreSQL15.19 and second real S3 service only",
)
class ProjectBundleBackupRestoreTests(NativeStorageFixture):
    databases = (
        {"default", "target", "restore"}
        if os.environ.get("PLANE_RUN_PROJECT_BUNDLE_RESTORE_TESTS") == "1"
        else {"default"}
    )

    def test_full_native_database_and_private_object_backup_restore_independently_reexports_identically(self):
        from pathlib import Path
        import subprocess
        from unittest.mock import patch
        from plane.utils.project_bundle import _native_s3_client, _unwrap_storage, _read_object

        assert connections["target"].pg_version == 150019
        assert connections["restore"].pg_version == 150019
        assert connections["restore"].settings_dict["NAME"].startswith("test_plane_bundle_960c865f_")
        seed_complete_graph(self)
        with self.storage(self.source_objects.name), override_settings(WEB_URL="https://source.invalid"):
            self.bundle = export_project_snapshot(
                "default",
                "https://source.invalid",
                str(self.workspace.pk),
                str(self.project.pk),
                "OBOT",
                dict(id=str(uuid4()), frozen_at=timezone.now().isoformat()),
            )
        with TemporaryDirectory(prefix="synthetic-native-backup-") as directory, self.native_environment():
            root = Path(directory)
            with self.native_storage(), override_settings(WEB_URL="https://target.invalid"):
                receipt = self.apply()
                freeze = dict(id=str(uuid4()), frozen_at=timezone.now().isoformat())
                before = export_project_snapshot(
                    "target",
                    self.target["instance"],
                    self.target["workspace_id"],
                    self.target["project_id"],
                    "OBOT",
                    freeze,
                )
                for item in receipt["objects"]:
                    payload = _read_object(_unwrap_storage(default_storage), item["target_key"])
                    assert sha256(payload) == item["sha256"]
                    path = root / item["sha256"]
                    path.write_bytes(payload)
                    path.chmod(0o600)
                database = connections["target"].settings_dict
                environment = {
                    "PGPASSWORD": database["PASSWORD"],
                    "PGCONNECT_TIMEOUT": "3",
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                }
                dump = root / "target.dump"
                result = subprocess.run(
                    [
                        "/usr/local/bin/pg_dump",
                        "--no-password",
                        "--host",
                        database["HOST"],
                        "--port",
                        str(database["PORT"]),
                        "--username",
                        database["USER"],
                        "--dbname",
                        database["NAME"],
                        "--format",
                        "custom",
                        "--file",
                        str(dump),
                    ],
                    env=environment,
                    capture_output=True,
                    timeout=60,
                )
                assert result.returncode == 0, result.stderr[:1000]
                dump.chmod(0o600)
                dump_version = (
                    subprocess.run(["/usr/local/bin/pg_dump", "--version"], capture_output=True, check=True)
                    .stdout.decode()
                    .strip()
                )
                assert dump_version == "pg_dump (PostgreSQL) 15.19"
            restored_database = connections["restore"].settings_dict
            connections["restore"].close()
            result = subprocess.run(
                [
                    "/usr/local/bin/pg_restore",
                    "--no-password",
                    "--host",
                    restored_database["HOST"],
                    "--port",
                    str(restored_database["PORT"]),
                    "--username",
                    restored_database["USER"],
                    "--dbname",
                    restored_database["NAME"],
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-privileges",
                    "--single-transaction",
                    str(dump),
                ],
                env=environment,
                capture_output=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stderr[:1000]
            restored_endpoint = "http://plane-bundle-restore-store:9000"
            with (
                patch.dict(os.environ, {"AWS_S3_ENDPOINT_URL": restored_endpoint}),
                self.native_storage(AWS_S3_ENDPOINT_URL=restored_endpoint),
                override_settings(WEB_URL="https://target.invalid"),
            ):
                client, bucket, endpoint = _native_s3_client(_unwrap_storage(default_storage))
                assert endpoint == restored_endpoint
                prefix = "pb/" + self.target_project.pk.hex + "/"
                assert client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("KeyCount", 0) == 0
                for item in receipt["objects"]:
                    payload = (root / item["sha256"]).read_bytes()
                    assert sha256(payload) == item["sha256"]
                    client.put_object(
                        Bucket=bucket, Key=item["target_key"], Body=payload, ContentLength=len(payload), IfNoneMatch="*"
                    )
                after = export_project_snapshot(
                    "restore",
                    self.target["instance"],
                    self.target["workspace_id"],
                    self.target["project_id"],
                    "OBOT",
                    freeze,
                )
                before_logical = {key: value for key, value in before.items() if key != "capture"}
                after_logical = {key: value for key, value in after.items() if key != "capture"}
                assert canonical(before_logical) == canonical(after_logical)
                runs = apps.get_model("db", "ProjectMigrationRun").objects.using("restore")
                assert canonical(runs.get().receipt) == canonical(receipt)
                from plane.utils.project_bundle import _storage_proof

                assert _storage_proof(receipt["objects"])[0]["proof"]["policy"] == "NoSuchBucketPolicy"
                restore_receipt = dict(
                    format="plane-project-restore-test/1",
                    runtime_acceptance=False,
                    bundle_sha256=sha256(canonical(self.bundle)),
                    transaction_receipt_sha256=sha256(canonical(receipt)),
                    database_backup_sha256=sha256(dump.read_bytes()),
                    object_backup_manifest_sha256=sha256(canonical(receipt["objects"])),
                    before_native_sha256=sha256(canonical(before_logical)),
                    restored_native_sha256=sha256(canonical(after_logical)),
                    pg_dump_version=dump_version,
                    source_pg_version=connections["default"].pg_version,
                    target_pg_version=connections["target"].pg_version,
                    restored_pg_version=connections["restore"].pg_version,
                )
                output = os.environ.get("PLANE_BUNDLE_TEST_OUTPUT")
                if output:
                    from plane.utils.project_bundle import publish_bundle_file

                    publish_bundle_file(Path(output) / "synthetic-restore-receipt.json", restore_receipt)
