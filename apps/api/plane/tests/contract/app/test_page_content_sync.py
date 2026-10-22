import base64
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from plane.app.serializers.page import PageBinaryUpdateSerializer, PageSerializer
from plane.db.models import Page
from plane.tests.contract.api.test_pages import _create_page, _detail_url, project as _project_fixture

# Re-export the project fixture alongside the shared Page request helpers.
project = _project_fixture


@pytest.mark.contract
@pytest.mark.django_db(transaction=True)
class TestPageContentSync:
    @pytest.mark.parametrize("surface", ["pat", "app"])
    def test_replacement_prepares_fresh_content_then_publishes_after_commit(
        self, request, surface, workspace, project, create_user
    ):
        client = request.getfixturevalue("api_key_client" if surface == "pat" else "session_client")
        page = _create_page(workspace, project, create_user)
        Page.objects.filter(pk=page.pk).update(description_binary=b"current history")
        events = []

        def prepare(page_id, **payload):
            assert connection.in_atomic_block
            assert payload["persisted_binary"] == b"current history"
            events.append("prepare")
            return dict(
                description_binary=b"merged history",
                description_html="<p>normalized</p>",
                description_json={"type": "doc"},
            )

        def publish(page_id, binary):
            assert not connection.in_atomic_block
            assert Page.objects.get(pk=page_id).description_binary == binary == b"merged history"
            events.append("publish")

        with (
            CaptureQueriesContext(connection) as queries,
            patch("plane.utils.page_content.prepare_pat", side_effect=prepare),
            patch("plane.utils.page_content.publish_pat", side_effect=publish),
        ):
            url = (
                _detail_url(workspace, project, page)
                if surface == "pat"
                else (f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/")
            )
            response = client.patch(url, {"description_html": "  <p>replacement</p>  "}, format="json")
        assert response.status_code == 200, response.data
        assert response.data["description_html"] == (
            "  <p>replacement</p>  " if surface == "pat" else "<p>normalized</p>"
        )
        assert events == ["prepare", "publish"]
        assert any('FROM "pages"' in query["sql"] and "FOR UPDATE" in query["sql"] for query in queries)

    def test_prepare_failure_rolls_back_body_and_metadata(self, api_key_client, workspace, project, create_user):
        from plane.utils.page_content import PageContentUnavailable

        page = _create_page(workspace, project, create_user)
        with (
            patch("plane.utils.page_content.prepare_pat", side_effect=PageContentUnavailable()),
            patch("plane.utils.page_content.publish_pat") as publish,
        ):
            response = api_key_client.patch(
                _detail_url(workspace, project, page),
                {"name": "must roll back", "description_html": "<p>must roll back</p>"},
                format="json",
            )
        assert response.status_code == 503
        page.refresh_from_db()
        assert page.name == "Existing page"
        assert page.description_html == "<p>x</p>"
        publish.assert_not_called()

    def test_prepare_size_error_rolls_back_without_discarding_history(
        self, api_key_client, workspace, project, create_user
    ):
        from plane.utils.page_content import PageContentTooLarge

        page = _create_page(workspace, project, create_user)
        Page.objects.filter(pk=page.pk).update(description_binary=b"retained history")
        with (
            patch("plane.utils.page_content.prepare_pat", side_effect=PageContentTooLarge()),
            patch("plane.utils.page_content.publish_pat") as publish,
        ):
            response = api_key_client.patch(
                _detail_url(workspace, project, page),
                {"name": "must roll back", "description_html": "<p>small visible body</p>"},
                format="json",
            )
        assert response.status_code == 413
        page.refresh_from_db()
        assert page.name == "Existing page"
        assert page.description_html == "<p>x</p>"
        assert page.description_binary == b"retained history"
        publish.assert_not_called()

    def test_stale_metadata_serializer_cannot_overwrite_content(self, workspace, project, create_user):
        page = _create_page(workspace, project, create_user)
        Page.objects.filter(pk=page.pk).update(description_binary=b"new history", description_html="<p>new</p>")
        serializer = PageSerializer(page, data={"color": "#123456"}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        page.refresh_from_db()
        assert page.description_binary == b"new history"
        assert page.description_html == "<p>new</p>"
        assert page.color == "#123456"

    def test_snapshot_uses_fresh_binary_and_ignores_client_projections(self, workspace, project, create_user):
        page = _create_page(workspace, project, create_user)
        Page.objects.filter(pk=page.pk).update(description_binary=b"current history")
        serializer = PageBinaryUpdateSerializer(
            page,
            data={
                "description_binary": base64.b64encode(b"offline snapshot").decode(),
                "description_html": "<p>untrusted</p>",
                "description_json": {"untrusted": True},
            },
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors

        def merge(page_id, persisted_binary, incoming_binary):
            assert connection.in_atomic_block
            assert persisted_binary == b"current history"
            assert incoming_binary == b"offline snapshot"
            return dict(
                description_binary=b"merged history", description_html="<p>merged</p>", description_json={"type": "doc"}
            )

        with (
            CaptureQueriesContext(connection) as queries,
            patch("plane.utils.page_content.merge_snapshot", side_effect=merge),
        ):
            serializer.save()
        page.refresh_from_db()
        assert page.description_binary == b"merged history"
        assert page.description_html == "<p>merged</p>"
        assert page.description_json == {"type": "doc"}
        assert any('FROM "pages"' in query["sql"] and "FOR UPDATE" in query["sql"] for query in queries)

    def test_invalid_snapshot_rolls_back_without_trusting_html(self, workspace, project, create_user):
        from rest_framework.exceptions import ValidationError

        page = _create_page(workspace, project, create_user)
        serializer = PageBinaryUpdateSerializer(
            page,
            data={
                "description_binary": base64.b64encode(b"not yjs").decode(),
                "description_html": "<p>untrusted</p>",
            },
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        with patch("plane.utils.page_content.merge_snapshot", side_effect=ValidationError("Invalid snapshot")):
            with pytest.raises(ValidationError):
                serializer.save()
        page.refresh_from_db()
        assert page.description_html == "<p>x</p>"
        assert page.description_binary is None

    @pytest.mark.parametrize("payload", [{}, {"description_html": "<p>x</p>"}, {"description_binary": ""}])
    def test_snapshot_requires_nonempty_binary(self, payload):
        serializer = PageBinaryUpdateSerializer(data=payload, partial=True)
        assert not serializer.is_valid()

    def test_publish_failure_reports_committed_delivery_uncertain(
        self, api_key_client, workspace, project, create_user
    ):
        from plane.utils.page_content import PageContentUnavailable

        page = _create_page(workspace, project, create_user)
        prepared = dict(
            description_binary=b"new history", description_html="<p>new</p>", description_json={"type": "doc"}
        )
        with (
            patch("plane.utils.page_content.prepare_pat", return_value=prepared),
            patch("plane.utils.page_content.publish_pat", side_effect=PageContentUnavailable()),
        ):
            response = api_key_client.patch(
                _detail_url(workspace, project, page), {"description_html": "<p>new</p>"}, format="json"
            )
        assert response.status_code == 503
        assert "committed" in str(response.data).lower()
        page.refresh_from_db()
        assert page.description_binary == b"new history"

    def test_description_get_initializes_canonical_binary(self, session_client, workspace, project, create_user):
        page = _create_page(workspace, project, create_user)
        prepared = dict(
            description_binary=b"initialized history", description_html="<p>x</p>", description_json={"type": "doc"}
        )
        with patch("plane.utils.page_content.prepare_pat", return_value=prepared) as prepare:
            response = session_client.get(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/"
            )
            second_response = session_client.get(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/"
            )
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"initialized history"
        assert b"".join(second_response.streaming_content) == b"initialized history"
        assert prepare.call_count == 1
        assert prepare.call_args.kwargs["replacement_html"] == "<p>x</p>"
        assert prepare.call_args.kwargs["replace_title"] is False
        page.refresh_from_db()
        assert page.description_binary == b"initialized history"

    def test_get_initialization_failure_returns_no_empty_document(
        self, session_client, workspace, project, create_user
    ):
        from plane.utils.page_content import PageContentUnavailable

        page = _create_page(workspace, project, create_user)
        with patch("plane.utils.page_content.prepare_pat", side_effect=PageContentUnavailable()):
            response = session_client.get(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/"
            )
        assert response.status_code == 503
        assert not response.streaming

    def test_page_asset_copy_rebases_on_committed_content_and_keeps_canonical_history(
        self, workspace, project, create_user
    ):
        from plane.bgtasks.copy_s3_object import copy_s3_objects_of_description_and_assets

        page = _create_page(workspace, project, create_user, html='<image-component src="old-id"></image-component>')
        fresh_html = '<p>concurrent committed edit</p><image-component src="old-id"></image-component>'

        def copy_assets(*args):
            Page.objects.filter(pk=page.pk).update(
                name="concurrent title", description_html=fresh_html, description_binary=b"latest history"
            )
            return [{"old_asset_id": "old-id", "new_asset_id": "new-id"}]

        def prepare(page_id, **payload):
            assert connection.in_atomic_block
            assert payload["persisted_binary"] == b"latest history"
            assert payload["previous_html"] == fresh_html
            assert "replacement_html" not in payload
            assert payload["duplicated_assets"] == [{"old_asset_id": "old-id", "new_asset_id": "new-id"}]
            return dict(
                description_binary=b"merged history",
                description_html='<p>trusted canonical body</p><image-component src="new-id"></image-component>',
                description_json={"type": "doc"},
            )

        def publish(page_id, binary):
            assert not connection.in_atomic_block
            assert Page.objects.get(pk=page_id).description_binary == binary == b"merged history"

        with (
            patch("plane.bgtasks.copy_s3_object.copy_assets", side_effect=copy_assets),
            patch("plane.utils.page_content.prepare_assets", side_effect=prepare, create=True),
            patch("plane.utils.page_content.publish_pat", side_effect=publish),
            patch("plane.bgtasks.copy_s3_object.sync_with_external_service") as convert,
        ):
            copy_s3_objects_of_description_and_assets("PAGE", page.id, project.id, workspace.slug, create_user.id)
        page.refresh_from_db()
        assert page.name == "concurrent title"
        assert page.description_binary == b"merged history"
        assert page.description_json == {"type": "doc"}
        convert.assert_not_called()

    @pytest.mark.parametrize("surface", ["pat", "app"])
    @pytest.mark.parametrize("warm_body_changed", [False, True])
    def test_name_only_prepares_title_and_preserves_exact_unchanged_body(
        self, request, surface, warm_body_changed, workspace, project, create_user
    ):
        client = request.getfixturevalue("api_key_client" if surface == "pat" else "session_client")
        raw_html = "  <p>exact body</p>\n"
        original_json = {"type": "doc", "content": [{"type": "paragraph"}]}
        page = _create_page(workspace, project, create_user, html=raw_html)
        Page.objects.filter(pk=page.id).update(description_binary=b"old history", description_json=original_json)
        result_json = (
            {"type": "doc", "content": [{"type": "paragraph", "content": []}]} if warm_body_changed else original_json
        )
        prepared = dict(
            description_binary=b"title history", description_html="<p>canonical body</p>", description_json=result_json
        )
        url = (
            _detail_url(workspace, project, page)
            if surface == "pat"
            else (f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/")
        )

        def publish(page_id, binary):
            assert not connection.in_atomic_block
            assert Page.objects.get(pk=page_id).name == "Renamed"
            assert binary == b"title history"

        with (
            patch("plane.utils.page_content.prepare_pat", return_value=prepared) as prepare,
            patch("plane.utils.page_content.publish_pat", side_effect=publish) as publish_call,
        ):
            response = client.patch(url, {"name": "Renamed"}, format="json")
        assert response.status_code == 200, response.data
        prepare.assert_called_once()
        assert prepare.call_args.kwargs.get("replacement_html") is None
        assert prepare.call_args.kwargs["replace_title"] is True
        assert prepare.call_args.kwargs.get("preserve_loaded_title", False) is (surface == "app")
        publish_call.assert_called_once()
        page.refresh_from_db()
        assert page.description_binary == b"title history"
        assert page.description_json == result_json
        assert page.description_html == ("<p>canonical body</p>" if warm_body_changed else raw_html)

    def test_unchanged_app_name_does_not_prepare_or_publish(self, session_client, workspace, project, create_user):
        page = _create_page(workspace, project, create_user)
        with (
            patch("plane.utils.page_content.prepare_pat") as prepare,
            patch("plane.utils.page_content.publish_pat") as publish,
        ):
            response = session_client.patch(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/",
                {"name": page.name},
                format="json",
            )
        assert response.status_code == 200
        prepare.assert_not_called()
        publish.assert_not_called()

    def test_snapshot_noop_keeps_exact_html_while_merging_history(self, workspace, project, create_user):
        page = _create_page(workspace, project, create_user, html="  <p>exact body</p>\n")
        document = {"type": "doc", "content": [{"type": "paragraph"}]}
        Page.objects.filter(pk=page.pk).update(description_binary=b"fresh history", description_json=document)
        serializer = PageBinaryUpdateSerializer(
            page, data={"description_binary": base64.b64encode(b"offline").decode()}, partial=True
        )
        assert serializer.is_valid(), serializer.errors
        with patch(
            "plane.utils.page_content.merge_snapshot",
            return_value={
                "description_binary": b"merged history",
                "description_html": "<p>normalized</p>",
                "description_json": document,
            },
        ):
            serializer.save()
        page.refresh_from_db()
        assert page.description_binary == b"merged history"
        assert page.description_json == document
        assert page.description_html == "  <p>exact body</p>\n"

    @pytest.mark.parametrize("has_assets", [False, True])
    def test_duplicate_prepares_new_uuid_title_before_save_and_queues_after_commit(
        self, session_client, workspace, project, create_user, has_assets
    ):
        raw_html = "  <p>exact body</p>" + ('<image-component src="old-id"></image-component>' if has_assets else "")
        page = _create_page(workspace, project, create_user, html=raw_html)
        document = {"type": "doc"}
        Page.objects.filter(pk=page.id).update(description_binary=b"source history", description_json=document)
        prepared_ids = []

        def prepare(page_id, **payload):
            assert connection.in_atomic_block
            assert page_id != page.id
            assert not Page.objects.filter(pk=page_id).exists()
            assert payload["persisted_binary"] == b"source history"
            assert payload["title"] == "Existing page (Copy)"
            assert payload.get("replacement_html") is None
            assert payload["replace_title"] is True
            assert not payload.get("preserve_loaded_title", False)
            prepared_ids.append(page_id)
            return dict(
                description_binary=b"clone history", description_html="<p>normalized</p>", description_json=document
            )

        def queue(**payload):
            assert not connection.in_atomic_block
            assert Page.objects.filter(pk=prepared_ids[0]).exists()

        with (
            patch("plane.utils.page_content.prepare_pat", side_effect=prepare),
            patch(
                "plane.app.views.page.base.copy_s3_objects_of_description_and_assets.delay", side_effect=queue
            ) as copy,
            patch("plane.app.views.page.base.page_transaction.delay", side_effect=queue),
        ):
            response = session_client.post(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/duplicate/"
            )
        assert response.status_code == 201, response.data
        clone = Page.objects.get(pk=response.data["id"])
        assert clone.pk == prepared_ids[0]
        assert clone.name == "Existing page (Copy)"
        assert clone.description_binary == b"clone history"
        assert clone.description_html == raw_html
        copy.assert_called_once()
        page.refresh_from_db()
        assert page.name == "Existing page"
        assert page.description_binary == b"source history"
        assert page.description_html == raw_html

    def test_duplicate_bridge_failure_leaves_no_clone_or_tasks(self, session_client, workspace, project, create_user):
        from plane.utils.page_content import PageContentUnavailable

        page = _create_page(workspace, project, create_user)
        with (
            patch("plane.utils.page_content.prepare_pat", side_effect=PageContentUnavailable()),
            patch("plane.app.views.page.base.copy_s3_objects_of_description_and_assets.delay") as copy,
            patch("plane.app.views.page.base.page_transaction.delay") as notify,
        ):
            response = session_client.post(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/duplicate/"
            )
        assert response.status_code == 503
        assert Page.objects.count() == 1
        copy.assert_not_called()
        notify.assert_not_called()
