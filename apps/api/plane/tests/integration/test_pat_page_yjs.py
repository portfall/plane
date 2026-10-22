# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Real DB/API persistence plus Node HTTP/Yjs; not browser/IndexedDB coverage.

Run through apps/live/tests/integration/pat-api.test.ts with explicitly supplied
ephemeral services. No mocks: Celery uses its real in-memory transport, without
workers, since background jobs are outside this content-persistence regression.
"""

import base64
from copy import deepcopy
import ipaddress
import os
from pathlib import Path
from queue import Queue
import re
from threading import Thread
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connection, connections, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from plane.db.models import APIToken, Page, Project, ProjectMember, User, WorkspaceMember


pytestmark = [
    pytest.mark.skipif(
        os.environ.get("PLANE_RUN_DJANGO_CONTENT_TESTS") != "1",
        reason="Opt-in synthetic Django/Live integration only",
    ),
    pytest.mark.django_db(transaction=True),
]

TITLE = "Synthetic PAT integration title"
OLD = "PAT_SYNTHETIC_OLD_BODY"
NEW = "PAT_SYNTHETIC_NEW_BODY"
FINAL = "PAT_SYNTHETIC_FINAL_BODY"
EXACT_NEW_HTML = f" \n<p>{NEW}</p>\n  "
EXACT_FINAL_HTML = f"\n  <p>{FINAL}</p>  \n"
FAILURE_TEST_PATHS = (
    "plane/tests/integration/test_pat_page_yjs.py",
    "plane/tests/contract/api/test_pages.py",
)


class _SafeFailureDiagnostics:
    def __init__(self):
        root = Path(__file__).resolve().parents[3]
        self.paths = {(root / path).resolve(): path for path in FAILURE_TEST_PATHS}
        self.emitted = set()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if not report.failed or call.excinfo is None or len(self.emitted) >= 40:
            return
        source = Path(str(item.path)).resolve()
        relative = self.paths.get(source)
        function = getattr(item, "originalname", None)
        if relative is None or not isinstance(function, str) or not re.fullmatch(r"test_[A-Za-z0-9_]{1,155}", function):
            return
        exception_type = call.excinfo.type.__name__
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", exception_type):
            exception_type = "Exception"
        # Inspect code locations only. Never format exception messages, values,
        # assertion explanations, source text, locals, parameter IDs or URLs.
        line = item.location[1] + 1
        for entry in call.excinfo.traceback:
            if Path(str(entry.path)).resolve() == source:
                line = entry.lineno + 1
        if not isinstance(line, int) or not 1 <= line <= 1_000_000:
            return
        metadata = f"function={function} path={relative} line={line} type={exception_type}"
        if metadata not in self.emitted:
            self.emitted.add(metadata)
            print(f"PAT_YJS_FAILURE: {metadata}", flush=True)


@pytest.fixture(scope="session", autouse=True)
def _safe_failure_diagnostics(request):
    plugin = _SafeFailureDiagnostics()
    request.config.pluginmanager.register(plugin, "pat-yjs-safe-failure-diagnostics")
    yield
    request.config.pluginmanager.unregister(plugin)


def _local_service(value, schemes):
    parsed = urlsplit(value)
    address = ipaddress.ip_address(parsed.hostname or "")
    local = (
        address.is_loopback
        or address in ipaddress.ip_network("172.16.0.0/12")
        or address in ipaddress.ip_network("192.168.144.0/24")
    )
    assert parsed.scheme in schemes and local and not parsed.query and not parsed.fragment, "Invalid test service"
    if parsed.scheme in {"postgres", "postgresql"}:
        assert (
            parsed.username == "plane_test" and parsed.password == "plane_test_only" and parsed.path == "/plane_test"
        ), "Synthetic test database identity required"
    else:
        assert not parsed.username and not parsed.password and parsed.path in {"/", "/0"}, "Test cache required"


@pytest.fixture(scope="session")
def django_db_modify_db_settings(_safe_failure_diagnostics):
    # Validate before pytest creates any DB; never take connection settings from
    # a deployment .env. Each run creates and destroys its own test database.
    database = os.environ.get("PLANE_TEST_DATABASE_URL", "")
    redis = os.environ.get("PLANE_TEST_REDIS_URL", "")
    _local_service(database, {"postgres", "postgresql"})
    _local_service(redis, {"redis"})
    assert os.environ.get("DATABASE_URL") == database, "Test DB configuration mismatch"
    assert os.environ.get("REDIS_URL") == redis, "Test cache configuration mismatch"
    live = urlsplit(settings.LIVE_URL or "")
    assert live.scheme == "http" and live.hostname == "127.0.0.1" and live.port, "Local Live fixture required"
    assert settings.LIVE_SERVER_SECRET_KEY == "synthetic-test-secret", "Synthetic Live secret required"
    assert settings.CELERY_BROKER_URL == "memory://", "Test-only background transport required"
    namespace = f"test_pat_yjs_{uuid4().hex}"
    settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = namespace
    # Session DB setup precedes client/token fixtures and all test requests.
    # Keep the real Redis backend/limiter while isolating this run's cache keys,
    # including the unchanged contract module's fixed-token throttle history.
    cache_settings = deepcopy(settings.CACHES)
    cache_settings["default"]["KEY_PREFIX"] = namespace
    # settings_changed also invalidates any earlier lazy backend resolution;
    # this closes cache clients without deleting or flushing any cache data.
    with override_settings(CACHES=cache_settings):
        yield


@pytest.mark.django_db(transaction=False)
def test_isolated_database_bootstraps_the_pat_contract_session():
    # pytest-django sorts regular DB tests before transactional tests. This
    # module is the first constant target, so bootstrap the shared session DB
    # before the contracts; the warm-room test still uses real transactions.
    assert connection.vendor == "postgresql", "PostgreSQL test database required"
    assert connection.settings_dict["NAME"].startswith("test_pat_yjs_"), "Unique integration test database required"
    assert cache.key_prefix == connection.settings_dict["NAME"], "Cache must share the unique test DB namespace"


@pytest.fixture
def api_token(db, create_user):
    # Override only this module's inherited fixture. Each integration case uses
    # a real, independently rate-limited APIToken through normal authentication.
    return APIToken.objects.create(
        user=create_user,
        label="Synthetic PAT Yjs integration token",
        token=f"pat-yjs-{uuid4().hex}",
    )


@pytest.fixture
def pat_project(workspace, create_user):
    project = Project.objects.create(
        name="Synthetic PAT Yjs project",
        identifier="PYJS",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=project,
        workspace=workspace,
        member=create_user,
        role=20,
        is_active=True,
    )
    return project


def _phase(name):
    # The Node runner reports only this fixed phase, never subprocess bodies.
    print(f"PAT_YJS_PHASE: {name}", flush=True)


def _expect_status(response, expected, message):
    actual = response.status_code
    assert type(actual) is int and 100 <= actual <= 599, "Invalid HTTP response status"
    assert type(expected) is int and 100 <= expected <= 599, "Invalid expected HTTP status"
    if actual != expected:
        # Numeric status codes only: never print response bodies or URLs.
        print(f"PAT_YJS_HTTP_STATUS: actual={actual} expected={expected}", flush=True)
    assert actual == expected, message


def _fixture(page, action, binary=None, **edits):
    payload = {"action": action}
    payload.update(edits)
    if binary is not None:
        payload["description_binary"] = base64.b64encode(bytes(binary)).decode("ascii")
    url = f"{settings.LIVE_URL.rstrip('/')}/__pat-test/documents/{page.id}"
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            url,
            json=payload,
            headers={"live-server-secret-key": "synthetic-test-secret"},
            allow_redirects=False,
            timeout=(2, 10),
        )
    assert response.status_code == 200, "Synthetic Live fixture operation failed"
    return response.json()


def _admin(page, action, **payload):
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            f"{settings.LIVE_URL.rstrip('/')}/admin/documents/{page.id}/{action}",
            json=payload,
            headers={"live-server-secret-key": "synthetic-test-secret"},
            allow_redirects=False,
            timeout=(2, 10),
        )
    assert response.status_code == 200, "Real Node content operation failed"
    return response.json()


def _encoded(binary):
    return base64.b64encode(bytes(binary)).decode("ascii")


def _session(user):
    client = APIClient()
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    assert settings.SESSION_COOKIE_NAME in client.cookies, "Real session cookie required"
    return client


def _member(workspace, project):
    suffix = uuid4().hex
    user = User.objects.create(email=f"synthetic-pat-yjs-{suffix}@example.invalid", username=f"pat_yjs_{suffix}")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=20, is_active=True)
    ProjectMember.objects.create(workspace=workspace, project=project, member=user, role=20, is_active=True)
    return user


def _create(api, workspace, project, html=None, access=Page.PUBLIC_ACCESS, app_client=None):
    pages = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/pages/"
    create_url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/" if app_client else pages
    response = (app_client or api).post(
        create_url,
        {"name": TITLE, "description_html": html if html is not None else f"<p>{OLD}</p>", "access": access},
        format="json",
    )
    _expect_status(response, 201, "Synthetic Page creation failed")
    page = Page.objects.get(pk=response.data["id"])
    assert page.description_binary, "Eager creation must already be canonical"
    app = f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/"
    return page, f"{pages}{page.id}/", app


def _commit_prepared(page, prepared):
    # Real Node pure preparation may run under this real Page lock. Publication
    # is strictly after commit, exactly as in the API persistence contract.
    with transaction.atomic():
        locked = Page.objects.select_for_update().get(pk=page.pk)
        locked.description_binary = base64.b64decode(prepared["description_binary"], validate=True)
        locked.description_html = prepared["description_html"]
        locked.description_json = prepared["description_json"]
        locked.save(
            update_fields=["description_binary", "description_html", "description_json", "description_stripped"]
        )
    _admin(page, "publish-pat", description_binary=prepared["description_binary"])


def _assert_exact_html(api, detail_url, page, html):
    page.refresh_from_db()
    assert page.description_html.encode("utf-8") == html.encode("utf-8"), "DB normalized exact PAT HTML bytes"
    response = api.get(detail_url)
    _expect_status(response, 200, "PAT detail GET failed")
    assert response.data["description_html"].encode("utf-8") == html.encode("utf-8"), "PAT normalized HTML bytes"


def _json_text(node):
    if isinstance(node, dict):
        return node.get("text", "") + "".join(_json_text(child) for child in node.get("content", []))
    return ""


def _assert_content(content, expected):
    assert content["description_html"].count(expected) == 1, "HTML lost or duplicated the current body"
    text = _json_text(content["description_json"])
    assert text.count(expected) == 1, "JSON lost or duplicated the current body"
    for obsolete in {OLD, NEW, FINAL} - {expected}:
        assert obsolete not in content["description_html"], "HTML resurrected an older body"
        assert obsolete not in text, "JSON resurrected an older body"


def _db_content(page):
    page.refresh_from_db()
    assert page.description_binary, "Page lost canonical Yjs history"
    return {"description_html": page.description_html, "description_json": page.description_json}


def _description(client, url, page):
    response = client.get(url)
    assert response.status_code == 200, "Session description GET failed"
    binary = b"".join(response.streaming_content)
    page.refresh_from_db()
    assert binary == bytes(page.description_binary), "Description GET did not return persisted canonical state"
    return binary


def test_pat_overwrite_survives_warm_publish_stale_saves_and_reopen(
    api_key_client, workspace, pat_project, create_user
):
    assert connection.vendor == "postgresql", "This integration requires actual PostgreSQL row locks"
    assert connection.settings_dict["NAME"].startswith("test_pat_yjs_"), "Unique synthetic test DB required"
    session = APIClient()
    session.force_login(create_user, backend="django.contrib.auth.backends.ModelBackend")
    assert settings.SESSION_COOKIE_NAME in session.cookies, "Real session cookie was not created"
    pages_url = f"/api/v1/workspaces/{workspace.slug}/projects/{pat_project.id}/pages/"

    _phase("cold_create")
    created = api_key_client.post(
        pages_url,
        {"name": TITLE, "description_html": f"<p>{OLD}</p>", "access": Page.PUBLIC_ACCESS},
        format="json",
    )
    assert created.status_code == 201, "PAT Page creation failed"
    page = Page.objects.get(pk=created.data["id"])
    _assert_content(_db_content(page), OLD)
    detail_url = f"{pages_url}{page.id}/"
    description_url = f"/api/workspaces/{workspace.slug}/projects/{pat_project.id}/pages/{page.id}/description/"

    _phase("warm_load")
    old_snapshot = _description(session, description_url, page)
    warm = _fixture(page, "load", old_snapshot)
    _assert_content(warm, OLD)
    assert warm["title"] == TITLE, "Initial collaborative title differs from Page title"

    _phase("warm_pat_patch")
    updated = api_key_client.patch(detail_url, {"description_html": EXACT_NEW_HTML}, format="json")
    assert updated.status_code == 200, "Warm PAT body overwrite failed"
    _assert_content(_db_content(page), NEW)
    _assert_exact_html(api_key_client, detail_url, page, EXACT_NEW_HTML)
    warm = _fixture(page, "inspect")
    _assert_content(warm, NEW)
    assert warm["description_json"] == page.description_json, "Published JSON differs from durable JSON"
    assert warm["title"] == page.name == TITLE, "Body overwrite changed the title"
    _assert_content(_fixture(page, "replay", old_snapshot), NEW)

    _phase("stale_session_saves")
    for _ in range(3):
        response = session.patch(
            description_url,
            {
                "description_binary": base64.b64encode(old_snapshot).decode("ascii"),
                "description_html": f"<p>{OLD}</p>",
                "description_json": {"type": "doc", "content": [{"type": "text", "text": OLD}]},
            },
            format="json",
        )
        assert response.status_code == 200, "Stale full snapshot was not accepted and merged"
        _assert_content(_db_content(page), NEW)
        _assert_exact_html(api_key_client, detail_url, page, EXACT_NEW_HTML)
    first_replacement_snapshot = _description(session, description_url, page)

    _phase("delayed_snapshot_order")
    updated = api_key_client.patch(detail_url, {"description_html": EXACT_FINAL_HTML}, format="json")
    assert updated.status_code == 200, "Second warm PAT overwrite failed"
    _assert_content(_fixture(page, "inspect"), FINAL)
    for stale in [first_replacement_snapshot, old_snapshot, first_replacement_snapshot]:
        response = session.patch(
            description_url,
            {
                "description_binary": base64.b64encode(stale).decode("ascii"),
            },
            format="json",
        )
        assert response.status_code == 200, "Delayed stale snapshot was not merged"
        _assert_content(_db_content(page), FINAL)
        _assert_exact_html(api_key_client, detail_url, page, EXACT_FINAL_HTML)
        _assert_content(_fixture(page, "replay", stale), FINAL)

    _phase("durable_reopen")
    canonical = _description(session, description_url, page)
    reopened = _fixture(page, "reopen", canonical)
    _assert_content(reopened, FINAL)
    assert reopened["description_json"] == page.description_json, "Reopened JSON differs from persisted JSON"
    assert reopened["description_html"] == f'<p class="editor-paragraph-block">{FINAL}</p>', "Wrong canonical render"
    _assert_exact_html(api_key_client, detail_url, page, EXACT_FINAL_HTML)
    assert reopened["title"] == page.name == TITLE, "Reopening changed the collaborative title"
    assert base64.b64decode(reopened["description_binary"]), "Reopening lost the binary document"
    # Hocuspocus may GC deleted payloads while retaining their IDs/delete sets;
    # replay behavior, rather than byte identity, proves history survived.
    _assert_content(_fixture(page, "replay", old_snapshot), FINAL)
    _assert_content(_fixture(page, "replay", first_replacement_snapshot), FINAL)
    print("PAT_YJS_RESULT: passed", flush=True)


@pytest.mark.parametrize("route,warm", [("pat", False), ("pat", True), ("app", False)])
def test_name_only_rename_keeps_exact_html_body_ids_and_replacement_history(
    api_key_client, workspace, pat_project, create_user, route, warm
):
    _phase("name_only_rename")
    session = _session(create_user)
    page, detail, app = _create(
        api_key_client, workspace, pat_project, app_client=session if route == "app" else None
    )
    old = _description(session, f"{app}description/", page)
    response = api_key_client.patch(detail, {"description_html": EXACT_NEW_HTML}, format="json")
    _expect_status(response, 200, "Replacement before rename failed")
    canonical = _description(session, f"{app}description/", page)
    before = _fixture(page, "load" if warm else "decode", canonical)
    expected_json = deepcopy(page.description_json)
    for attempt in range(3):
        name = f"Synthetic {route} rename {attempt}"
        response = (api_key_client if route == "pat" else session).patch(
            detail if route == "pat" else app, {"name": name}, format="json"
        )
        assert response.status_code == 200, "Name-only Page rename failed"
        page.refresh_from_db()
        assert page.name == name
        _assert_exact_html(api_key_client, detail, page, EXACT_NEW_HTML)
        decoded = _fixture(page, "decode", page.description_binary)
        assert decoded["title"] == name, "Cold or warm rename left a stale collaborative title"
        assert decoded["description_json"] == expected_json, "Name-only rename changed the body"
        assert decoded["body_ids"] == before["body_ids"], "Name-only rename replaced body history"
        if warm:
            assert _fixture(page, "inspect")["title"] == name, "Warm room did not receive rename"
        for stale in [old, canonical]:
            saved = session.patch(
                f"{app}description/", {"description_binary": _encoded(stale)}, format="json"
            )
            assert saved.status_code == 200, "Stale save after rename failed"
            _assert_exact_html(api_key_client, detail, page, EXACT_NEW_HTML)
            _assert_content(_db_content(page), NEW)
        current = _description(session, f"{app}description/", page)
        reopened = _fixture(page, "reopen", current)
        assert reopened["title"] == name
        assert reopened["description_json"] == expected_json
        assert reopened["description_html"] == f'<p class="editor-paragraph-block">{NEW}</p>'
        assert reopened["body_ids"] == before["body_ids"]
        for stale in [canonical, old, old]:
            replayed = _fixture(page, "replay", stale)
            _assert_content(replayed, NEW)
            assert replayed["title"] == name
            assert replayed["body_ids"] == before["body_ids"]
        if not warm:
            assert _fixture(page, "unload")["status"] == "unloaded"


def test_warm_app_name_callback_preserves_the_loaded_title_and_unsaved_body(
    api_key_client, workspace, pat_project, create_user
):
    _phase("warm_app_title")
    page, detail, app = _create(api_key_client, workspace, pat_project)
    session = _session(create_user)
    old = _description(session, f"{app}description/", page)
    _fixture(page, "load", old)
    warm = _fixture(page, "warm-edit", warm_html="<p>UNSAVED_WARM_TEXT</p>", warm_title="User's loaded title")
    response = session.patch(app, {"name": "Delayed app title callback"}, format="json")
    assert response.status_code == 200, "Warm app title callback failed"
    loaded = _fixture(page, "inspect")
    assert loaded["title"] == warm["title"], "App callback replaced the user's loaded title"
    assert loaded["body_ids"] == warm["body_ids"], "App title callback replaced body IDs"
    assert loaded["description_json"] == warm["description_json"], "App title callback changed unsaved body"
    canonical = _description(session, f"{app}description/", page)
    assert page.name == "Delayed app title callback"
    assert page.description_json == warm["description_json"], "Warm content was not retained in canonical state"
    reopened = _fixture(page, "reopen", canonical)
    assert reopened["title"] == warm["title"]
    assert reopened["body_ids"] == warm["body_ids"]
    assert _fixture(page, "replay", old)["description_json"] == warm["description_json"]
    assert api_key_client.get(detail).status_code == 200


@pytest.mark.parametrize("with_image", [False, True])
def test_duplicate_open_retains_source_history_with_a_new_title_without_mutating_source_room(
    api_key_client, workspace, pat_project, create_user, with_image
):
    _phase("duplicate_open")
    session = _session(create_user)
    page, detail, app = _create(api_key_client, workspace, pat_project)
    old = _description(session, f"{app}description/", page)
    image = f'<image-component id="{uuid4()}" src="{uuid4()}" width="42px"></image-component>' if with_image else ""
    response = api_key_client.patch(detail, {"description_html": f"<p>{NEW}</p>{image}"}, format="json")
    assert response.status_code == 200
    canonical = _description(session, f"{app}description/", page)
    source = _fixture(page, "load", canonical)
    response = session.post(f"{app}duplicate/", {}, format="json")
    assert response.status_code == 201, "App duplicate failed"
    copied = Page.objects.get(pk=response.data["id"])
    assert copied.pk != page.pk, "Duplicate reused source UUID"
    assert copied.name == f"{TITLE} (Copy)"
    assert copied.description_json == page.description_json, "Duplicate changed body JSON"
    copied_app = f"/api/workspaces/{workspace.slug}/projects/{pat_project.id}/pages/{copied.id}/"
    opened = _fixture(copied, "load", _description(session, f"{copied_app}description/", copied))
    assert opened["title"] == copied.name, "Opening a duplicate showed the source title"
    assert opened["body_ids"] == source["body_ids"], "Duplicate reseeded source body history"
    assert opened["description_json"] == source["description_json"]
    for snapshot in [old, canonical, old]:
        replayed = _fixture(copied, "replay", snapshot)
        _assert_content(replayed, NEW)
        assert replayed["title"] == copied.name
        assert replayed["body_ids"] == source["body_ids"]
    assert _fixture(page, "inspect") == source, "Preparing duplicate mutated the old room"
    page.refresh_from_db()
    assert bytes(page.description_binary) == canonical, "Duplicate mutated source persistence"


def test_private_replacement_downloaded_by_another_member_cannot_leak_deleted_payloads(
    api_key_client, workspace, pat_project, create_user
):
    _phase("private_to_public")
    marker = "PAT_SYNTHETIC_PRIVATE_雪_NEVER_PUBLISH"
    owner = _session(create_user)
    member = _session(_member(workspace, pat_project))
    page, detail, app = _create(api_key_client, workspace, pat_project, f"<p>{marker}</p>", Page.PRIVATE_ACCESS)
    url = f"{app}description/"
    private_content = (bytes(page.description_binary), page.description_html, deepcopy(page.description_json))
    denied = member.get(url)
    # ProjectPagePermission re-reads access and denies the non-owner before
    # the owner/public description queryset runs.
    assert denied.status_code == 403, "Private download must deny access through the permission guard"
    assert not getattr(denied, "streaming", False), "Denied download returned a binary stream"
    assert marker.encode("utf-8") not in denied.content, "Denied download exposed private content"
    page.refresh_from_db()
    assert (bytes(page.description_binary), page.description_html, page.description_json) == private_content, (
        "Denied private download changed Page content"
    )
    old = _description(owner, url, page)
    assert marker.encode("utf-8") in old, "Privacy fixture must contain the real deleted marker"
    _fixture(page, "load", old)
    response = api_key_client.patch(detail, {"description_html": EXACT_NEW_HTML}, format="json")
    assert response.status_code == 200
    private_replacement = _description(owner, url, page)
    response = api_key_client.patch(detail, {"access": Page.PUBLIC_ACCESS}, format="json")
    assert response.status_code == 200, "Making synthetic Page public failed"
    for stale in [old, private_replacement, old]:
        response = owner.patch(url, {"description_binary": _encoded(stale)}, format="json")
        assert response.status_code == 200
        downloaded = _description(member, url, page)
        assert marker.encode("utf-8") not in downloaded, "Public binary leaked deleted private payload bytes"
        reopened = _fixture(page, "reopen", downloaded)
        _assert_content(reopened, NEW)
        replayed = _fixture(page, "replay", stale)
        _assert_content(replayed, NEW)
        assert marker.encode("utf-8") not in base64.b64decode(replayed["description_binary"]), "Replay leaked payload"
        assert marker not in replayed["description_html"]
        _assert_exact_html(api_key_client, detail, page, EXACT_NEW_HTML)


@pytest.mark.parametrize("transition", ["replacement", "locked", "private"])
def test_waiting_stale_session_writer_rechecks_committed_canonical_and_permissions(
    api_key_client, workspace, pat_project, create_user, transition
):
    _phase("row_lock_contention")
    page, detail, app = _create(api_key_client, workspace, pat_project)
    owner = _session(create_user)
    writer = _session(_member(workspace, pat_project))
    stale = _description(owner, f"{app}description/", page)
    loaded_before = _fixture(page, "load", stale)
    pids = Queue(maxsize=1)
    results = Queue(maxsize=1)

    def stale_writer():
        # Django's connections are thread-local: this is a genuinely separate
        # PostgreSQL session using a real app session cookie, not a mocked lock.
        try:
            client = APIClient()
            client.cookies = deepcopy(writer.cookies)
            with transaction.atomic():
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '8s'")
                    cursor.execute("SET LOCAL statement_timeout = '12s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                response = client.patch(
                    f"{app}description/",
                    {
                        "description_binary": _encoded(stale),
                        "description_html": f"<p>{OLD}</p>",
                        "description_json": {"type": "doc", "content": [{"type": "text", "text": OLD}]},
                    },
                    format="json",
                )
                status_code = response.status_code
            results.put(("response", status_code))
        except Exception as error:
            # Never let pytest reduce a thread exception to a warning, and
            # never print exception values that can contain connection URLs.
            results.put(("exception", type(error).__name__))
        finally:
            connections.close_all()

    worker = Thread(target=stale_writer, name="synthetic-pat-stale-writer", daemon=True)
    writer_pid = None
    try:
        with transaction.atomic():
            locked = Page.objects.select_for_update().get(pk=page.pk)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '8s'")
                cursor.execute("SET LOCAL statement_timeout = '12s'")
                cursor.execute("SELECT pg_backend_pid()")
                holder_pid = cursor.fetchone()[0]
            worker.start()
            writer_pid = pids.get(timeout=4)
            assert writer_pid != holder_pid, "Contention needs two actual database connections"
            deadline = monotonic() + 4
            blocked = False
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [holder_pid, writer_pid])
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                sleep(0.02)
            assert blocked, "Stale session PATCH never demonstrably waited on the Page row lock"

            if transition == "replacement":
                # This actual HTTP preparation happens after proving the other
                # connection is blocked. No scheduling network mock is needed.
                prepared = _admin(
                    page,
                    "prepare-pat",
                    persisted_binary=_encoded(locked.description_binary),
                    previous_html=locked.description_html,
                    title=locked.name,
                    replacement_html=EXACT_NEW_HTML,
                    replace_title=False,
                    preserve_loaded_title=False,
                )
                assert _fixture(page, "inspect") == loaded_before, "Prepare mutated room"
                locked.description_binary = base64.b64decode(prepared["description_binary"], validate=True)
                locked.description_html = EXACT_NEW_HTML
                locked.description_json = prepared["description_json"]
                locked.save(
                    update_fields=["description_binary", "description_html", "description_json", "description_stripped"]
                )
            else:
                if transition == "locked":
                    locked.is_locked = True
                    locked.save(update_fields=["is_locked"])
                else:
                    locked.access = Page.PRIVATE_ACCESS
                    locked.save(update_fields=["access"])
                committed_content = (
                    bytes(locked.description_binary), locked.description_html, deepcopy(locked.description_json)
                )
        # The row lock is released here; the waiting writer must read the new
        # canonical state (or reject newly locked/private access) before saving.
        worker.join(timeout=25)
    finally:
        if worker.is_alive() and writer_pid is not None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_cancel_backend(%s)", [writer_pid])
            worker.join(timeout=15)
        assert not worker.is_alive(), "Bounded stale writer did not clean up its database connection"
    outcome, value = results.get(timeout=1)
    assert outcome == "response", f"Stale writer raised a captured {value}"
    page.refresh_from_db()
    if transition != "replacement":
        # The post-lock permission recheck re-reads access and rejects the
        # non-owner with 403 before the private description queryset runs.
        assert value == (400 if transition == "locked" else 403), "Writer ignored lock/privacy changed while waiting"
        assert (bytes(page.description_binary), page.description_html, page.description_json) == committed_content, (
            "Rejected waiting writer changed Page content"
        )
        assert _fixture(page, "inspect") == loaded_before, "Rejected write changed room"
        return

    assert value == 200, "Waiting stale writer could not resume after replacement commit"
    _assert_content(_db_content(page), NEW)
    _assert_exact_html(api_key_client, detail, page, EXACT_NEW_HTML)
    persisted = _description(owner, f"{app}description/", page)
    decoded = _fixture(page, "decode", persisted)
    _assert_content(decoded, NEW)
    assert decoded["description_html"] == f'<p class="editor-paragraph-block">{NEW}</p>'
    assert decoded["description_json"] == prepared["description_json"]
    _admin(page, "publish-pat", description_binary=_encoded(persisted))
    _assert_content(_fixture(page, "inspect"), NEW)
    _assert_content(_fixture(page, "reopen", persisted), NEW)
    for snapshot in [stale, persisted, stale]:
        _assert_content(_fixture(page, "replay", snapshot), NEW)


def _images(node):
    found = []
    if isinstance(node, dict):
        if node.get("type") == "imageComponent":
            found.append(node)
        for child in node.get("content", []):
            found.extend(_images(child))
    return found


def test_asset_only_prepare_committed_publish_and_old_replay_preserve_unsaved_warm_edits(
    api_key_client, workspace, pat_project, create_user
):
    _phase("asset_only_prepare")
    old_asset, new_asset, unrelated_asset = str(uuid4()), str(uuid4()), str(uuid4())
    image_id, warm_image_id, unrelated_id = str(uuid4()), str(uuid4()), str(uuid4())
    html = (
        f'<p>{NEW}</p><image-component id="{image_id}" src="{old_asset}" '
        f'width="42px" height="21px" alignment="right" alt="{old_asset}" status="uploaded"></image-component>'
        f'<image-component id="{unrelated_id}" src="{unrelated_asset}" width="88px"></image-component>'
    )
    page, detail, app = _create(api_key_client, workspace, pat_project, html)
    session = _session(create_user)
    persisted = _description(session, f"{app}description/", page)
    _fixture(page, "load", persisted)
    warm = _fixture(
        page,
        "warm-edit",
        warm_html=(
            f'<p>UNSAVED_ASSET_WARM_TEXT</p><image-component id="{warm_image_id}" src="{old_asset}" '
            'width="73px" alignment="left"></image-component>'
        ),
        warm_title="Unsaved asset warm title",
    )
    assert "UNSAVED_ASSET_WARM_TEXT" not in _json_text(page.description_json), "Warm edit was already saved"
    assert len(_images(warm["description_json"])) == 3, "Image fixture did not parse as imageComponent"
    prepared = _admin(
        page,
        "prepare-assets",
        persisted_binary=_encoded(persisted),
        previous_html=page.description_html,
        title=page.name,
        duplicated_assets=[{"old_asset_id": old_asset, "new_asset_id": new_asset}],
    )
    assert _fixture(page, "inspect") == warm, "Pure asset preparation mutated loaded state"
    page.refresh_from_db()
    assert bytes(page.description_binary) == persisted, "Pure asset preparation wrote persistence"
    expected = deepcopy(warm["description_json"])
    for image in _images(expected):
        if image["attrs"]["src"] == old_asset:
            image["attrs"]["src"] = new_asset
    assert prepared["description_json"] == expected, "Asset operation changed text or unrelated image attributes"
    decoded = _fixture(page, "decode", base64.b64decode(prepared["description_binary"]))
    assert decoded["title"] == warm["title"], "Asset preparation changed unsaved title"
    assert decoded["body_ids"] == warm["body_ids"], "Asset preparation replaced body IDs"
    _commit_prepared(page, prepared)
    assert _fixture(page, "inspect")["description_json"] == expected
    canonical = _description(session, f"{app}description/", page)
    _assert_exact_html(api_key_client, detail, page, prepared["description_html"])
    for state in [
        _fixture(page, "reopen", canonical),
        _fixture(page, "replay", persisted),
        _fixture(page, "replay", base64.b64decode(warm["description_binary"])),
        _fixture(page, "replay", persisted),
    ]:
        assert state["description_json"] == expected, "Old asset snapshot undid the targeted replacement"
        assert state["body_ids"] == warm["body_ids"], "Asset history lost warm body IDs"
        assert state["title"] == warm["title"]
        assert _json_text(state["description_json"]).count("UNSAVED_ASSET_WARM_TEXT") == 1


@pytest.mark.parametrize("clear_body", [False, True])
def test_http_prepare_distinguishes_omitted_body_from_explicit_empty_body(
    api_key_client, workspace, pat_project, create_user, clear_body
):
    _phase("optional_body_prepare")
    page, _, app = _create(api_key_client, workspace, pat_project)
    snapshot = _description(_session(create_user), f"{app}description/", page)
    before = _fixture(page, "load", snapshot)
    payload = dict(
        persisted_binary=_encoded(snapshot),
        previous_html=page.description_html,
        title="Explicit new title",
        replace_title=True,
        preserve_loaded_title=False,
    )
    if clear_body:
        payload["replacement_html"] = ""
    prepared = _admin(page, "prepare-pat", **payload)
    assert _fixture(page, "inspect") == before, "Preparation was not pure"
    decoded = _fixture(page, "decode", base64.b64decode(prepared["description_binary"]))
    assert decoded["title"] == "Explicit new title"
    if clear_body:
        assert _json_text(decoded["description_json"]) == "", "Explicit empty replacement did not clear body"
    else:
        assert decoded["description_json"] == before["description_json"], "Omitted replacement changed body"
        assert decoded["body_ids"] == before["body_ids"], "Title-only prepare replaced body history"
    _commit_prepared(page, prepared)
    replayed = _fixture(page, "replay", snapshot)
    assert replayed["description_json"] == decoded["description_json"]
    assert replayed["title"] == "Explicit new title"
