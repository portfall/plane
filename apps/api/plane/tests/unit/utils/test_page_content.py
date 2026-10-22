import base64
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests
from rest_framework.exceptions import ValidationError

from plane.utils import page_content


@pytest.fixture
def bridge(settings):
    settings.LIVE_URL = "http://live:3000/live/"
    settings.LIVE_SERVER_SECRET_KEY = "synthetic-test-secret"
    with patch.object(page_content.requests, "Session") as factory:
        session = factory.return_value.__enter__.return_value
        response = session.post.return_value.__enter__.return_value
        response.status_code = 200
        yield session, response


def _respond(response, payload):
    response.iter_content.return_value = [json.dumps(payload).encode()]


@pytest.mark.unit
def test_private_bridge_has_fixed_url_credentials_and_bounds(bridge):
    session, response = bridge
    page_id = uuid4()
    _respond(
        response,
        {
            "description_binary": base64.b64encode(b"canonical").decode(),
            "description_html": "<p>merged</p>",
            "description_json": {"type": "doc"},
        },
    )
    result = page_content.merge_snapshot(page_id, b"persisted", b"incoming")
    assert result["description_binary"] == b"canonical"
    assert session.trust_env is False
    args, kwargs = session.post.call_args
    assert args == (f"http://live:3000/live/admin/documents/{page_id}/merge-snapshot",)
    assert kwargs["headers"] == {"live-server-secret-key": "synthetic-test-secret"}
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (2, 10)
    assert kwargs["json"] == {
        "persisted_binary": base64.b64encode(b"persisted").decode(),
        "incoming_binary": base64.b64encode(b"incoming").decode(),
    }


@pytest.mark.unit
@pytest.mark.parametrize("code", [301, 302, 401, 500, 503])
def test_bridge_failure_is_generic_and_does_not_follow_redirects(bridge, code):
    session, response = bridge
    response.status_code = code
    with pytest.raises(page_content.PageContentUnavailable) as error:
        page_content.merge_snapshot(uuid4(), b"persisted", b"incoming")
    assert "synthetic-test-secret" not in str(error.value)
    assert session.post.call_count == 1


@pytest.mark.unit
def test_bridge_invalid_snapshot_maps_to_400(bridge):
    _, response = bridge
    response.status_code = 400
    with pytest.raises(ValidationError):
        page_content.merge_snapshot(uuid4(), b"persisted", b"invalid")


@pytest.mark.unit
def test_bridge_size_error_is_413_and_does_not_suggest_retry(bridge):
    _, response = bridge
    response.status_code = 413
    with pytest.raises(page_content.PageContentTooLarge) as error:
        page_content.merge_snapshot(uuid4(), b"persisted", b"incoming")
    assert error.value.status_code == 413
    assert "history" in str(error.value)
    assert "retry" not in str(error.value).lower()


@pytest.mark.unit
def test_bridge_timeout_is_generic(bridge):
    session, _ = bridge
    session.post.side_effect = requests.Timeout("sensitive request details")
    with pytest.raises(page_content.PageContentUnavailable) as error:
        page_content.merge_snapshot(uuid4(), b"persisted", b"incoming")
    assert "sensitive" not in str(error.value)


@pytest.mark.unit
@pytest.mark.parametrize("binary", ["", "!!bad!!", "YQ==\n"])
def test_bridge_rejects_malformed_canonical_result(bridge, binary):
    _, response = bridge
    _respond(response, {"description_binary": binary, "description_html": "<p>x</p>", "description_json": {}})
    with pytest.raises(page_content.PageContentUnavailable):
        page_content.merge_snapshot(uuid4(), b"persisted", b"incoming")


@pytest.mark.unit
@pytest.mark.parametrize("state", ["published", "not_loaded"])
def test_publish_accepts_loaded_and_cold_acknowledgement(bridge, state):
    _, response = bridge
    _respond(response, {"status": state})
    page_content.publish_pat(uuid4(), b"persisted")


@pytest.mark.unit
def test_publish_failure_reports_committed_content(bridge):
    _, response = bridge
    response.status_code = 503
    with pytest.raises(page_content.PageContentUnavailable, match="committed"):
        page_content.publish_pat(uuid4(), b"persisted")


@pytest.mark.unit
@pytest.mark.parametrize("replacement", [None, ""])
@pytest.mark.parametrize("preserve_loaded_title", [False, True])
def test_prepare_title_only_omits_body_but_empty_string_clears(bridge, replacement, preserve_loaded_title):
    session, response = bridge
    _respond(
        response,
        {
            "description_binary": base64.b64encode(b"canonical").decode(),
            "description_html": "<p>body</p>",
            "description_json": {"type": "doc"},
        },
    )
    page_content.prepare_pat(
        uuid4(),
        persisted_binary=b"previous",
        previous_html="<p>body</p>",
        title="New title",
        replacement_html=replacement,
        replace_title=True,
        preserve_loaded_title=preserve_loaded_title,
    )
    payload = session.post.call_args.kwargs["json"]
    assert ("replacement_html" in payload) is (replacement is not None)
    if replacement is not None:
        assert payload["replacement_html"] == ""
    assert payload.get("preserve_loaded_title", False) is preserve_loaded_title


@pytest.mark.unit
def test_prepare_assets_uses_bounded_private_bridge(bridge):
    session, response = bridge
    mapping = [{"old_asset_id": str(uuid4()), "new_asset_id": str(uuid4())}]
    _respond(
        response,
        {
            "description_binary": base64.b64encode(b"canonical").decode(),
            "description_html": "<p>body</p>",
            "description_json": {"type": "doc"},
        },
    )
    page_id = uuid4()
    result = page_content.prepare_assets(
        page_id,
        persisted_binary=b"previous",
        previous_html="<p>body</p>",
        title="Title",
        duplicated_assets=mapping,
    )
    assert result["description_binary"] == b"canonical"
    assert session.post.call_args.args == (f"http://live:3000/live/admin/documents/{page_id}/prepare-assets",)
    assert session.post.call_args.kwargs["json"] == {
        "persisted_binary": base64.b64encode(b"previous").decode(),
        "previous_html": "<p>body</p>",
        "title": "Title",
        "duplicated_assets": mapping,
    }
    assert session.post.call_args.kwargs["allow_redirects"] is False
    assert session.trust_env is False


@pytest.mark.unit
def test_prepare_assets_bad_request_is_permanent_400(bridge):
    _, response = bridge
    response.status_code = 400
    with pytest.raises(ValidationError):
        page_content.prepare_assets(
            uuid4(),
            persisted_binary=b"previous",
            previous_html="<p>body</p>",
            title="Title",
            duplicated_assets=[],
        )
