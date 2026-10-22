"""Bounded, private Plane Live bridge for canonical Page content.

Call prepare/merge while holding the Page row lock. Publish only after the
transaction commits; preparation must never write to a room or call back to DB.
"""

import base64
import binascii
import json
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

import requests
from django.conf import settings
from rest_framework.exceptions import APIException, ValidationError

MAX_CONTENT_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
REQUEST_TIMEOUT = (2, 10)
CONTENT_FIELDS = ("description_binary", "description_html", "description_json")


class PageContentUnavailable(APIException):
    status_code = 503
    default_detail = "Page content service is unavailable. Please try again later."


class PageContentTooLarge(APIException):
    status_code = 413
    default_detail = "Page document exceeds the supported size, including retained collaboration history."


def _encode(binary):
    if len(binary or b"") > MAX_CONTENT_BYTES:
        raise PageContentTooLarge()
    return base64.b64encode(bytes(binary or b"")).decode("ascii")


def _request(page_id, action, payload):
    # The origin is configuration, never request data. Avoid redirects and
    # environment proxies so the shared secret cannot follow another origin.
    try:
        live_url = settings.LIVE_URL or ""
        origin = urlsplit(live_url)
        secret = settings.LIVE_SERVER_SECRET_KEY
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or not secret
        ):
            raise ValueError()
        page_uuid = str(UUID(str(page_id)))
        url = f"{live_url.rstrip('/')}/admin/documents/{page_uuid}/{action}"
        started = monotonic()
        with requests.Session() as session:
            session.trust_env = False
            with session.post(
                url,
                json=payload,
                headers={"live-server-secret-key": secret},
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            ) as response:
                if response.status_code == 413:
                    raise PageContentTooLarge()
                if response.status_code == 400 and action == "merge-snapshot":
                    raise ValidationError({"description_binary": "Invalid document snapshot."})
                if response.status_code == 400 and action == "prepare-assets":
                    raise ValidationError({"duplicated_assets": "Invalid asset replacement request."})
                if response.status_code != 200:
                    raise PageContentUnavailable()
                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise PageContentTooLarge()
                    if monotonic() - started > 15:
                        raise PageContentUnavailable()
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
                if not isinstance(result, dict):
                    raise ValueError()
                return result
    except (ValidationError, PageContentUnavailable, PageContentTooLarge):
        raise
    except (requests.RequestException, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
        # Do not include URLs, response bodies, credentials, or request bodies.
        raise PageContentUnavailable() from None


def _content(result):
    try:
        encoded = result["description_binary"]
        if not isinstance(encoded, str):
            raise ValueError()
        if len(encoded) > ((MAX_CONTENT_BYTES + 2) // 3) * 4:
            raise PageContentTooLarge()
        binary = base64.b64decode(encoded, validate=True)
        html = result["description_html"]
        document = result["description_json"]
        if not binary or not isinstance(html, str) or not isinstance(document, dict):
            raise ValueError()
        if (
            len(binary) > MAX_CONTENT_BYTES
            or len(html.encode("utf-8")) > MAX_CONTENT_BYTES
            or len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_CONTENT_BYTES
        ):
            raise PageContentTooLarge()
        return dict(description_binary=binary, description_html=html, description_json=document)
    except (KeyError, ValueError, TypeError, binascii.Error, RecursionError, OverflowError):
        raise PageContentUnavailable() from None


def prepare_pat(
    page_id,
    *,
    persisted_binary,
    previous_html,
    title,
    replacement_html=None,
    replace_title=False,
    preserve_loaded_title=False,
):
    payload = {
        "persisted_binary": _encode(persisted_binary),
        "previous_html": previous_html,
        "title": title,
        "replace_title": replace_title,
    }
    if replacement_html is not None:
        payload["replacement_html"] = replacement_html
    if preserve_loaded_title:
        payload["preserve_loaded_title"] = True
    return _content(_request(page_id, "prepare-pat", payload))


def prepare_assets(page_id, *, persisted_binary, previous_html, title, duplicated_assets):
    return _content(
        _request(
            page_id,
            "prepare-assets",
            {
                "persisted_binary": _encode(persisted_binary),
                "previous_html": previous_html,
                "title": title,
                "duplicated_assets": duplicated_assets,
            },
        )
    )


def retain_unchanged_html(page, content):
    """Keep exact stored markup when canonical body content did not change."""
    if content["description_json"] == page.description_json:
        content["description_html"] = page.description_html
    return content


def merge_snapshot(page_id, persisted_binary, incoming_binary):
    if not incoming_binary:
        raise ValidationError({"description_binary": "A non-empty document snapshot is required."})
    return _content(
        _request(
            page_id,
            "merge-snapshot",
            {
                "persisted_binary": _encode(persisted_binary),
                "incoming_binary": _encode(incoming_binary),
            },
        )
    )


def publish_pat(page_id, binary):
    try:
        result = _request(page_id, "publish-pat", {"description_binary": _encode(binary)})
        if result.get("status") not in ("published", "not_loaded"):
            raise PageContentUnavailable()
    except PageContentUnavailable:
        raise PageContentUnavailable(
            "Page content was committed, but live delivery is uncertain. Please reload before retrying."
        ) from None
    except PageContentTooLarge:
        raise PageContentTooLarge(
            "Page content was committed, but live delivery exceeded the supported document size."
        ) from None


def publish_saved(serializer):
    """Views call this after their outermost transaction has committed."""
    if getattr(serializer, "content_needs_publish", False):
        publish_pat(serializer.instance.pk, serializer.instance.description_binary)


def ensure_canonical(page):
    """Initialize a fresh, locked Page before returning a description to clients."""
    if not page.description_binary:
        content = prepare_pat(
            page.pk,
            persisted_binary=b"",
            previous_html=page.description_html,
            title=page.name,
            replacement_html=page.description_html,
            replace_title=False,
        )
        for field, value in content.items():
            setattr(page, field, value)
        page.save(update_fields=[*CONTENT_FIELDS, "description_stripped", "updated_at", "updated_by"])
    return page
