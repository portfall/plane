# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from plane.db.models import Project, ProjectMember, Issue, FileAsset
from unittest.mock import patch, MagicMock
from plane.bgtasks.copy_s3_object import (
    copy_s3_objects_of_description_and_assets,
    copy_assets,
)
import base64
import json
from celery.exceptions import Retry
from django.db import connection, OperationalError
from django.test.utils import CaptureQueriesContext
from plane.db.models import Page
from plane.utils.page_content import PageContentUnavailable, PageContentTooLarge
from rest_framework.exceptions import ValidationError


ASSET_MAPPING = [
    {
        "old_asset_id": "35e8b958-6ee5-43ce-ae56-fb0e776f421e",
        "new_asset_id": "97988198-274f-4dfe-aa7a-4c0ffc684214",
    }
]


def _page_attempt(payload, retries=0):
    task = copy_s3_objects_of_description_and_assets
    task.push_request(
        id="synthetic-page-asset-task", args=(), kwargs=payload, retries=retries, called_directly=False, is_eager=True
    )
    try:
        return task.run(**payload)
    finally:
        task.pop_request()


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
class TestPageAssetRetry:
    @pytest.fixture
    def page_args(self, workspace, create_user):
        project = Project.objects.create(name="Retry project", identifier="RETRY", workspace=workspace)
        page = Page.objects.create(
            workspace=workspace, owned_by=create_user, name="Page", description_binary=b"initial history"
        )
        return page, dict(
            entity_name="PAGE",
            entity_identifier=str(page.id),
            project_id=str(project.id),
            slug=workspace.slug,
            user_id=str(create_user.id),
        )

    @pytest.fixture
    def prepared(self):
        return dict(
            description_binary=b"prepared history",
            description_html="<p>canonical</p>",
            description_json={"type": "doc"},
        )

    def test_preparation_retry_preserves_json_mapping_and_copies_once(self, page_args, prepared):
        page, payload = page_args
        with (
            patch("plane.bgtasks.copy_s3_object.copy_assets", return_value=ASSET_MAPPING) as copy,
            patch(
                "plane.utils.page_content.prepare_assets",
                create=True,
                side_effect=[PageContentUnavailable(), PageContentUnavailable(), prepared],
            ) as prepare,
            patch(
                "plane.utils.page_content.prepare_pat", side_effect=AssertionError("Full HTML replacement forbidden")
            ),
            patch("plane.utils.page_content.publish_pat") as publish,
        ):
            for attempt, delay in enumerate([10, 30]):
                with pytest.raises(Retry) as retry:
                    _page_attempt(payload, retries=attempt)
                assert retry.value.when == delay
                assert retry.value.sig.args == ()
                payload = json.loads(json.dumps(retry.value.sig.kwargs))
                assert payload["page_stage"] == "prepare"
                assert payload["stage_retry"] == attempt + 1
                assert payload["duplicated_assets"] == ASSET_MAPPING
                page.refresh_from_db()
                assert page.description_binary == b"initial history"
            _page_attempt(payload, retries=2)
        assert copy.call_count == 1
        assert prepare.call_count == 3
        assert all(call.kwargs["duplicated_assets"] == ASSET_MAPPING for call in prepare.call_args_list)
        publish.assert_called_once_with(page.id, b"prepared history")
        page.refresh_from_db()
        assert page.description_binary == b"prepared history"

    def test_publication_retry_reads_latest_commit_without_copy_transform_or_writes(self, page_args, prepared):
        page, payload = page_args

        def publish(page_id, binary):
            assert not connection.in_atomic_block
            assert binary == bytes(Page.objects.get(pk=page_id).description_binary)
            if binary == b"prepared history":
                raise PageContentUnavailable()

        with (
            patch("plane.bgtasks.copy_s3_object.copy_assets", return_value=ASSET_MAPPING) as copy,
            patch("plane.utils.page_content.prepare_assets", return_value=prepared, create=True) as prepare,
            patch(
                "plane.utils.page_content.prepare_pat", side_effect=AssertionError("Full HTML replacement forbidden")
            ),
            patch("plane.utils.page_content.publish_pat", side_effect=publish) as publish_call,
        ):
            with pytest.raises(Retry) as retry:
                _page_attempt(payload)
            payload = json.loads(json.dumps(retry.value.sig.kwargs))
            assert payload["page_stage"] == "publish"
            assert payload["stage_retry"] == 1
            Page.objects.filter(pk=page.pk).update(description_binary=b"newer durable history")
            with CaptureQueriesContext(connection) as queries:
                _page_attempt(payload, retries=1)
        assert copy.call_count == prepare.call_count == 1
        assert publish_call.call_count == 2
        assert not any(query["sql"].lstrip().startswith(("UPDATE", "INSERT", "DELETE")) for query in queries)

    @pytest.mark.parametrize("stage", ["prepare", "publish"])
    def test_exhausted_stage_fails_instead_of_succeeding(self, page_args, stage):
        _, payload = page_args
        payload.update(page_stage=stage, duplicated_assets=ASSET_MAPPING, stage_retry=3)
        with (
            patch("plane.utils.page_content.prepare_assets", side_effect=PageContentUnavailable(), create=True),
            patch("plane.utils.page_content.publish_pat", side_effect=PageContentUnavailable()),
            patch("plane.bgtasks.copy_s3_object.copy_assets") as copy,
            patch.object(copy_s3_objects_of_description_and_assets, "retry") as retry,
        ):
            with pytest.raises(RuntimeError, match="Page asset"):
                _page_attempt(payload, retries=6)
        copy.assert_not_called()
        retry.assert_not_called()

    @pytest.mark.parametrize("error", [ValidationError("Invalid mapping"), PageContentTooLarge()])
    def test_permanent_prepare_failure_does_not_retry(self, page_args, error):
        page, payload = page_args
        payload.update(page_stage="prepare", duplicated_assets=ASSET_MAPPING)
        with (
            patch("plane.utils.page_content.prepare_assets", side_effect=error, create=True),
            patch.object(copy_s3_objects_of_description_and_assets, "retry") as retry,
        ):
            with pytest.raises(RuntimeError, match="Page asset"):
                _page_attempt(payload)
        retry.assert_not_called()
        page.refresh_from_db()
        assert page.description_binary == b"initial history"

    def test_partial_copy_failure_is_terminal_and_sanitized(self, page_args):
        _, payload = page_args
        with (
            patch("plane.bgtasks.copy_s3_object.copy_assets", side_effect=OSError("private storage details")),
            patch.object(copy_s3_objects_of_description_and_assets, "retry") as retry,
        ):
            with pytest.raises(RuntimeError, match="Page asset") as error:
                _page_attempt(payload)
        retry.assert_not_called()
        assert "private storage" not in str(error.value)

    def test_unknown_commit_outcome_is_terminal(self, page_args, prepared):
        _, payload = page_args
        payload.update(page_stage="prepare", duplicated_assets=ASSET_MAPPING)
        with (
            patch("plane.utils.page_content.prepare_assets", return_value=prepared, create=True),
            patch.object(Page, "save", side_effect=OperationalError("unknown commit")),
            patch.object(copy_s3_objects_of_description_and_assets, "retry") as retry,
        ):
            with pytest.raises(RuntimeError, match="Page asset"):
                _page_attempt(payload)
        retry.assert_not_called()

    def test_empty_mapping_finishes_without_live_call(self, page_args):
        _, payload = page_args
        with (
            patch("plane.bgtasks.copy_s3_object.copy_assets", return_value=[]),
            patch("plane.utils.page_content.prepare_assets", create=True) as prepare,
            patch("plane.utils.page_content.publish_pat") as publish,
        ):
            _page_attempt(payload)
        prepare.assert_not_called()
        publish.assert_not_called()

    def test_prepare_without_mapping_never_recopies(self, page_args):
        _, payload = page_args
        payload.update(page_stage="prepare")
        with patch("plane.bgtasks.copy_s3_object.copy_assets") as copy:
            with pytest.raises(RuntimeError, match="Page asset"):
                _page_attempt(payload)
        copy.assert_not_called()


@pytest.mark.unit
class TestCopyS3Objects:
    """Test the copy_s3_objects_of_description_and_assets function"""

    @pytest.fixture
    def project(self, create_user, workspace):
        project = Project.objects.create(name="Test Project", identifier="test-project", workspace=workspace)

        ProjectMember.objects.create(project=project, member=create_user)
        return project

    @pytest.fixture
    def issue(self, workspace, project):
        return Issue.objects.create(
            name="Test Issue",
            workspace=workspace,
            project_id=project.id,
            description_html='<div><image-component src="35e8b958-6ee5-43ce-ae56-fb0e776f421e"></image-component><image-component src="97988198-274f-4dfe-aa7a-4c0ffc684214"></image-component></div>',  # noqa: E501
        )

    @pytest.fixture
    def file_asset(self, workspace, project, issue):
        return FileAsset.objects.create(
            issue=issue,
            workspace=workspace,
            project=project,
            asset="workspace1/test-asset-1.jpg",
            attributes={
                "name": "test-asset-1.jpg",
                "size": 100,
                "type": "image/jpeg",
            },
            id="35e8b958-6ee5-43ce-ae56-fb0e776f421e",
            entity_type="ISSUE_DESCRIPTION",
        )

    @pytest.mark.django_db
    @patch("plane.bgtasks.copy_s3_object.S3Storage")
    def test_copy_s3_objects_of_description_and_assets(
        self, mock_s3_storage, create_user, workspace, project, issue, file_asset
    ):
        FileAsset.objects.create(
            issue=issue,
            workspace=workspace,
            project=project,
            asset="workspace1/test-asset-2.pdf",
            attributes={
                "name": "test-asset-2.pdf",
                "size": 100,
                "type": "application/pdf",
            },
            id="97988198-274f-4dfe-aa7a-4c0ffc684214",
            entity_type="ISSUE_DESCRIPTION",
        )

        issue.save()

        # Set up mock S3 storage
        mock_storage_instance = MagicMock()
        mock_s3_storage.return_value = mock_storage_instance

        # Mock the external service call to avoid actual HTTP requests
        with patch("plane.bgtasks.copy_s3_object.sync_with_external_service") as mock_sync:
            mock_sync.return_value = {
                "description": "test description",
                "description_binary": base64.b64encode(b"test binary").decode(),
                "description_json": {"type": "doc", "content": []},
            }

            # Call the actual function (not .delay())
            copy_s3_objects_of_description_and_assets("ISSUE", issue.id, project.id, "test-workspace", create_user.id)

        # Assert that copy_object was called for each asset
        assert mock_storage_instance.copy_object.call_count == 2

        # Get the updated issue and its new assets
        updated_issue = Issue.objects.get(id=issue.id)
        new_assets = FileAsset.objects.filter(
            issue=updated_issue,
            entity_type="ISSUE_DESCRIPTION",
        )

        # Verify new assets were created
        assert new_assets.count() == 4  # 2 original + 2 copied

    @pytest.mark.django_db
    @patch("plane.bgtasks.copy_s3_object.S3Storage")
    def test_copy_assets_successful(self, mock_s3_storage, workspace, project, issue, file_asset):
        """Test successful copying of assets"""
        # Arrange
        mock_storage_instance = MagicMock()
        mock_s3_storage.return_value = mock_storage_instance

        # Act
        result = copy_assets(
            entity=issue,
            entity_identifier=issue.id,
            project_id=project.id,
            asset_ids=[file_asset.id],
            user_id=issue.created_by_id,
        )

        # Assert
        # Verify S3 copy was called
        mock_storage_instance.copy_object.assert_called_once()

        # Verify new asset was created
        assert len(result) == 1
        new_asset_id = result[0]["new_asset_id"]
        new_asset = FileAsset.objects.get(id=new_asset_id)

        # Verify asset properties were copied correctly
        assert new_asset.workspace == workspace
        assert new_asset.project_id == project.id
        assert new_asset.entity_type == file_asset.entity_type
        assert new_asset.attributes == file_asset.attributes
        assert new_asset.size == file_asset.size
        assert new_asset.is_uploaded is True

    @pytest.mark.django_db
    @patch("plane.bgtasks.copy_s3_object.S3Storage")
    def test_copy_assets_empty_asset_ids(self, mock_s3_storage, workspace, project, issue):
        """Test copying with empty asset_ids list"""
        # Arrange
        mock_storage_instance = MagicMock()
        mock_s3_storage.return_value = mock_storage_instance

        # Act
        result = copy_assets(
            entity=issue,
            entity_identifier=issue.id,
            project_id=project.id,
            asset_ids=[],
            user_id=issue.created_by_id,
        )

        # Assert
        assert result == []
        mock_storage_instance.copy_object.assert_not_called()

    @pytest.mark.django_db
    @patch("plane.bgtasks.copy_s3_object.S3Storage")
    def test_copy_assets_nonexistent_asset(self, mock_s3_storage, workspace, project, issue):
        """Test copying with non-existent asset ID"""
        # Arrange
        mock_storage_instance = MagicMock()
        mock_s3_storage.return_value = mock_storage_instance
        non_existent_id = "00000000-0000-0000-0000-000000000000"

        # Act
        result = copy_assets(
            entity=issue,
            entity_identifier=issue.id,
            project_id=project.id,
            asset_ids=[non_existent_id],
            user_id=issue.created_by_id,
        )

        # Assert
        assert result == []
        mock_storage_instance.copy_object.assert_not_called()
