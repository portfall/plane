# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Local native project bundles with bounded, fail-closed content support.

The installed offline codec preserves accepted document bytes unchanged. Native
S3 reads and audited private staging use only the explicitly configured backend.
Actual source acceptance requires its documents and schema to pass these guards.
"""

import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import time


CODEC_NODE = "/usr/local/bin/node"
CODEC_PATH = "/code/project-bundle-codec.mjs"
CODEC_FORMAT = "plane-project-document/1"
CODEC_SUPPORT = "identity-literals/2"
LEGACY_CODEC_FORMAT = "plane-project-legacy-document/1"
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
CODEC_SECONDS = 15
REFUSAL_CODES = frozenset(
    """INVALID_INPUT LIMIT_EXCEEDED INVALID_DOCUMENT INCOMPLETE_HISTORY
UNKNOWN_ROOT UNKNOWN_TYPE UNKNOWN_ATTRIBUTE UNKNOWN_MARK UNSUPPORTED_HISTORY AMBIGUOUS_HISTORY
UNCLASSIFIED_REFERENCE UNMAPPED_REFERENCE REFERENCE_REWRITE_REQUIRED ORIGIN_REWRITE_REQUIRED UNSUPPORTED_HTML""".split()
)

FORK_REVISION = "ed63f2659994df6ccdd7e52a10a844dbfedc3a10"
NATIVE_SCHEMA_SHA256 = "b9de3e49801145ebf79936a064632b928ccf29ad9807687c8307d5afcefe36d6"
PROJECT_KINDS = frozenset(
    """Project ProjectMember ProjectIdentifier ProjectPage Page PageLabel PageLog PageVersion
State Label Issue IssueType ProjectIssueType IssueBlocker IssueRelation IssueMention IssueAssignee
IssueLink IssueAttachment IssueActivity IssueComment IssueLabel IssueSequence IssueSubscriber
IssueReaction CommentReaction IssueVote IssueVersion IssueDescriptionVersion Description DescriptionVersion
Cycle CycleIssue CycleUserProperties Module ModuleIssue ModuleMember ModuleLink ModuleUserProperties
Estimate EstimatePoint ProjectUserProperty IssueView DraftIssue DraftIssueAssignee DraftIssueLabel
DraftIssueModule DraftIssueCycle Intake IntakeIssue ProjectDeployBoard ProjectPublicMember FileAsset DeployBoard
""".split()
)
# These are explicitly out of the content contract; only counts are read.
# UserRecentVisit is per-user navigation history, not project content.
EXCLUDED_PROJECT_KINDS = frozenset({"Notification", "Importer", "UserRecentVisit"})
HISTORY_FIELDS = {
    "IssueVersion": {
        "parent": "Issue",
        "state": "State",
        "estimate_point": "EstimatePoint",
        "type": "IssueType",
        "cycle": "Cycle",
        "assignees": "User",
        "labels": "Label",
        "modules": "Module",
    },
    "Page": {"moved_to_page": "Page", "moved_to_project": "Project"},
}
ENTITY_KINDS = {
    "project": "Project",
    "issue": "Issue",
    "page": "Page",
    "page_mention": "Page",
    "user_mention": "User",
    "cycle": "Cycle",
    "module": "Module",
    "view": "IssueView",
    "intake": "Intake",
    "image": "FileAsset",
    "file": "FileAsset",
    "video": "FileAsset",
}
ASSET_OWNERS = {
    "ISSUE_ATTACHMENT": "Issue",
    "ISSUE_DESCRIPTION": "Issue",
    "COMMENT_DESCRIPTION": "IssueComment",
    "PAGE_DESCRIPTION": "Page",
    "PROJECT_COVER": "Project",
    "DRAFT_ISSUE_ATTACHMENT": "DraftIssue",
    "DRAFT_ISSUE_DESCRIPTION": "DraftIssue",
}


def native_schema(database):
    """Declared exact-fork schema, including native PostgreSQL types and constraints.

    This reads model metadata, not user/credential tables or database contents.
    The target-only receipt model is deliberately outside the source schema.
    """
    from django.apps import apps
    from django.db import connections
    from django.db.migrations.writer import MigrationWriter

    result = {}
    for model in apps.get_app_config("db").get_models():
        if model.__name__ == "ProjectMigrationRun":
            continue
        fields = []
        for field in model._meta.concrete_fields:
            _name, cls, args, kwargs = field.deconstruct()
            fields.append(
                dict(
                    name=field.name,
                    attname=field.attname,
                    column=field.column,
                    type=cls,
                    null=field.null,
                    primary_key=field.primary_key,
                    unique=field.unique,
                    db_type=field.db_type(connections[database]),
                    target=field.related_model._meta.label if field.is_relation else None,
                    args=args,
                    kwargs=kwargs,
                )
            )
        result[model.__name__] = dict(
            table=model._meta.db_table,
            fields=fields,
            constraints=model._meta.constraints,
            unique_together=model._meta.unique_together,
            indexes=model._meta.indexes,
            managers=[manager.name for manager in model._meta.managers],
            many_to_many=[field.deconstruct()[1:] for field in model._meta.local_many_to_many],
        )
    result = json.loads(json.dumps(result, default=lambda value: MigrationWriter.serialize(value)[0]))
    if sha256(canonical(result)) != NATIVE_SCHEMA_SHA256:
        raise BundleRefused("UNREVIEWED_NATIVE_SCHEMA")
    return result


def _all(model, database):
    from django.db import models

    queryset = getattr(model, "all_objects", model._base_manager).using(database)
    # PostgreSQL JSON null and SQL NULL both deserialize to Python None. Carry
    # an explicit SQL-null bit for every native JSON field through all closures.
    return queryset.annotate(
        **{
            "_bundle_sql_null_" + field.attname: models.ExpressionWrapper(
                models.Q(**{field.attname + "__isnull": True}), output_field=models.BooleanField()
            )
            for field in model._meta.concrete_fields
            if isinstance(field, models.JSONField)
        }
    )


def collect_project_graph(database, workspace_id, project_id, identifier):
    """Complete native ownership/FK closure or a precise boundary refusal.

    Source accounts are opaque UUID references. No source User row is queried.
    Workspace definitions are reached forward only; their unrelated consumers
    never become ownership edges. Every known project-bound excluded model is
    counted, and every other excluded project-bound model blocks the export.
    """
    from django.apps import apps
    from django.db import models
    from uuid import UUID

    native_schema(database)
    try:
        workspace_id, project_id = str(UUID(workspace_id)), str(UUID(project_id))
    except (ValueError, TypeError, AttributeError):
        raise BundleRefused("INVALID_PROJECT_IDENTITY") from None
    if identifier != "OBOT":
        raise BundleRefused("INVALID_PROJECT_IDENTITY")
    registry = {model.__name__: model for model in apps.get_app_config("db").get_models()}
    selected = {kind: {} for kind in PROJECT_KINDS}
    actors = set()
    pending = []

    def add(kind, row):
        if kind == "Project" and str(row["id"]) != project_id:
            raise BundleRefused("CROSS_PROJECT_REFERENCE")
        if row.get("project_id") is not None and str(row["project_id"]) != project_id:
            raise BundleRefused("CROSS_PROJECT_REFERENCE")
        if row.get("workspace_id") is not None and str(row["workspace_id"]) != workspace_id:
            raise BundleRefused("CROSS_WORKSPACE_REFERENCE")
        key = str(row["id"])
        if key not in selected[kind]:
            selected[kind][key] = row
            pending.append((kind, row))

    def follow(kind, value):
        if value is None:
            return
        if kind == "User":
            actors.add(str(value))
            return
        if kind == "Workspace":
            if str(value) != workspace_id:
                raise BundleRefused("CROSS_WORKSPACE_REFERENCE")
            return
        if kind not in PROJECT_KINDS:
            raise BundleRefused("UNCLASSIFIED_REFERENCE_MODEL")
        if str(value) in selected[kind]:
            return
        try:
            row = _all(registry[kind], database).values().get(pk=value)
        except registry[kind].DoesNotExist:
            raise BundleRefused("DANGLING_NATIVE_REFERENCE") from None
        add(kind, row)

    root = (
        _all(registry["Project"], database)
        .values()
        .filter(pk=project_id, workspace_id=workspace_id, identifier=identifier)
        .first()
    )
    if root is None:
        raise BundleRefused("PROJECT_IDENTITY_MISMATCH")
    add("Project", root)
    excluded = {}
    for kind, model in registry.items():
        project = next(
            (field for field in model._meta.concrete_fields if field.name == "project" and field.is_relation), None
        )
        if project is None:
            continue
        queryset = _all(model, database).filter(project_id=project_id)
        if kind in PROJECT_KINDS:
            for row in queryset.values():
                add(kind, row)
        else:
            count = queryset.count()
            if count and kind not in EXCLUDED_PROJECT_KINDS:
                raise BundleRefused("UNCLASSIFIED_PROJECT_ROWS:" + kind)
            excluded[kind] = count

    owners = {"Issue", "DraftIssue", "IssueComment", "Description", "Page", "Cycle", "Module", "Intake"}
    while pending:
        kind, row = pending.pop()
        model = registry[kind]
        for field in model._meta.concrete_fields:
            if field.is_relation:
                follow(field.related_model.__name__, row[field.attname])
        for name, target in HISTORY_FIELDS.get(kind, {}).items():
            values = row[name] if isinstance(row[name], list) else [row[name]]
            for value in values:
                follow(target, value)
        if kind == "IssueActivity":
            target = {
                "assignees": "User",
                "labels": "Label",
                "state": "State",
                "parent": "Issue",
                "cycle": "Cycle",
                "modules": "Module",
                "estimate_point": "EstimatePoint",
                "type": "IssueType",
            }.get(row["field"])
            for name in ("old_identifier", "new_identifier"):
                if row[name] is not None:
                    if target is None:
                        raise BundleRefused("UNCLASSIFIED_ACTIVITY_IDENTIFIER")
                    follow(target, row[name])
        if kind in {"PageLog", "DeployBoard"} and row["entity_identifier"] is not None:
            target = ENTITY_KINDS.get(row.get("entity_type")) or ENTITY_KINDS.get(row.get("entity_name"))
            if target is None:
                raise BundleRefused("UNCLASSIFIED_ENTITY_IDENTIFIER")
            follow(target, row["entity_identifier"])
        if kind == "FileAsset":
            target = ASSET_OWNERS.get(row["entity_type"])
            if target is None:
                raise BundleRefused("UNCLASSIFIED_ASSET_ENTITY")
            if row["entity_identifier"]:
                follow(target, row["entity_identifier"])
        if kind == "Page":
            joins = _all(registry["ProjectPage"], database).filter(page_id=row["id"])
            if row["is_global"] or not joins.exists() or joins.exclude(project_id=project_id).exists():
                raise BundleRefused("SHARED_PAGE")
        # Inspect incoming owner edges, including ones whose project column is
        # another project. Filtering them out first would hide a boundary loss.
        if kind in owners or kind in {"Estimate", "Project"}:
            for child_kind in PROJECT_KINDS:
                child = registry[child_kind]
                for field in child._meta.concrete_fields:
                    if not field.is_relation or field.related_model is not model:
                        continue
                    if kind == "Estimate" and child_kind != "EstimatePoint":
                        continue
                    for incoming in _all(child, database).filter(**{field.attname: row["id"]}).values():
                        add(child_kind, incoming)
            # Generic asset/board references have no actual FK constraint.
            for entity_type, owner in ASSET_OWNERS.items():
                if owner == kind:
                    for incoming in (
                        _all(registry["FileAsset"], database)
                        .filter(entity_type=entity_type, entity_identifier=str(row["id"]))
                        .values()
                    ):
                        add("FileAsset", incoming)
            for entity_name, owner in ENTITY_KINDS.items():
                if owner == kind:
                    for incoming in (
                        _all(registry["DeployBoard"], database)
                        .filter(entity_name=entity_name, entity_identifier=row["id"])
                        .values()
                    ):
                        add("DeployBoard", incoming)
    # Integrations with a null/wrong project column can still own an issue or
    # comment FK. Inspect their existence without loading credential payloads.
    for kind, model in registry.items():
        if kind in PROJECT_KINDS or kind == "ProjectMigrationRun":
            continue
        query = models.Q(pk__in=[])
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model.__name__ in owners:
                values = selected[field.related_model.__name__]
                if values:
                    query |= models.Q(**{field.attname + "__in": list(values)})
        count = _all(model, database).filter(query).count()
        if count and kind not in EXCLUDED_PROJECT_KINDS:
            raise BundleRefused("UNCLASSIFIED_PROJECT_ROWS:" + kind)
        if count:
            excluded[kind] = max(count, excluded.get(kind, 0))
    return (
        {kind: [rows[key] for key in sorted(rows)] for kind, rows in sorted(selected.items())},
        actors,
        dict(sorted(excluded.items())),
    )


BUNDLE_FORMAT = "plane-project-bundle/2"
MAX_BUNDLE_BYTES = 512 * 1024 * 1024


def _origin(value):
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.path == ""
            and not parsed.query
            and not parsed.fragment
            and value == f"https://{parsed.netloc}"
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise BundleRefused("INVALID_INSTANCE_ORIGIN")
    return value


def _identity(database, instance, workspace_id, project_id, identifier):
    from django.apps import apps
    from django.conf import settings
    from uuid import UUID

    _origin(instance)
    if getattr(settings, "WEB_URL", None) != instance:
        raise BundleRefused("INSTANCE_ORIGIN_MISMATCH")
    try:
        workspace_id, project_id = str(UUID(workspace_id)), str(UUID(project_id))
    except (ValueError, TypeError, AttributeError):
        raise BundleRefused("INVALID_PROJECT_IDENTITY") from None
    if identifier != "OBOT":
        raise BundleRefused("INVALID_PROJECT_IDENTITY")
    project = apps.get_model("db", "Project")
    if not _all(project, database).filter(pk=project_id, workspace_id=workspace_id, identifier=identifier).exists():
        raise BundleRefused("PROJECT_IDENTITY_MISMATCH")
    workspace = apps.get_model("db", "Workspace")
    slug = _all(workspace, database).values_list("slug", flat=True).get(pk=workspace_id)
    return dict(
        instance=instance, workspace_id=workspace_id, workspace_slug=slug, project_id=project_id, identifier=identifier
    )


def _pack_scalar(value):
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, list):
        return [_pack_scalar(child) for child in value]
    return value


def _read_object(storage, key):
    storage = _unwrap_storage(storage)
    content = bytearray()
    try:
        from django.core.files.storage import FileSystemStorage

        if type(storage) is FileSystemStorage:
            stream = storage.open(key, "rb")
        else:
            client, bucket, _endpoint = _native_s3_client(storage)
            stream = client.get_object(Bucket=bucket, Key=key)["Body"]
        with stream:
            while chunk := stream.read(65536):
                content.extend(chunk)
                if len(content) > MAX_BUNDLE_BYTES:
                    raise BundleRefused("BLOB_TOO_LARGE")
    except BundleRefused:
        raise
    except Exception:
        raise BundleRefused("INACCESSIBLE_OBJECT") from None
    return bytes(content)


def _codec_references(rows, actors, user_map=None, source=None, target=None):
    kinds = {"FileAsset": "asset", "Issue": "issue", "Page": "page", "Project": "project"}
    refs = []
    for model, kind in kinds.items():
        for row in rows.get(model, []):
            original = str(row["id"])
            destination = target["project_id"] if kind == "project" and target else original
            refs.append(dict(kind=kind, source=original, target=destination))
    refs.extend(
        dict(kind="user", source=actor, target=user_map[actor] if user_map else actor) for actor in sorted(actors)
    )
    if source:
        for value in [source["workspace_id"], source["workspace_slug"]]:
            destination = value
            if target:
                destination = target["workspace_id"] if value == source["workspace_id"] else target["workspace_slug"]
            refs.append(dict(kind="workspace", source=value, target=destination))
    return refs


def _check_documents(rows, references, source_origin, target_origin):
    from django.apps import apps
    from django.db import models

    checks = []
    for kind, entries in sorted(rows.items()):
        model = apps.get_model("db", kind)
        fields = {field.attname: field for field in model._meta.concrete_fields}
        for row in entries:
            variant = "document" if kind in {"Page", "PageVersion"} else "rich"
            if "description_binary" in fields and row["description_binary"]:
                checked = inspect_native_document(
                    bytes(row["description_binary"]),
                    variant=variant,
                    source_origin=source_origin,
                    target_origin=target_origin,
                    references=references,
                )
                checks.append(
                    dict(
                        kind=kind,
                        pk=str(row["id"]),
                        field="description_binary",
                        result={key: value for key, value in checked.items() if key != "binary"},
                    )
                )
            pair = None
            for html, document in [
                ("description_html", "description_json"),
                ("description_html", "description_text"),
                ("comment_html", "comment_json"),
            ]:
                if html in fields and document in fields:
                    pair = (html, document)
                    checked = inspect_legacy_document(
                        row[html],
                        row[document],
                        variant=variant,
                        source_origin=source_origin,
                        target_origin=target_origin,
                        references=references,
                    )
                    checks.append(
                        dict(
                            kind=kind,
                            pk=str(row["id"]),
                            field=f"{html}+{document}",
                            result={
                                key: value
                                for key, value in checked.items()
                                if key not in {"html_base64", "json_base64"}
                            },
                        )
                    )
                    break

            # Other JSON fields have no native editor schema. Do not infer
            # meaning from a UUID or retain an authenticated source URL there.
            def opaque(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key.lower() in {
                            "token",
                            "access_token",
                            "refresh_token",
                            "password",
                            "secret",
                            "credentials",
                            "authorization",
                            "cookie",
                        }:
                            raise BundleRefused("CREDENTIAL_SHAPED_PROJECT_CONTENT")
                        opaque(key)
                        opaque(child)
                elif isinstance(value, list):
                    for child in value:
                        opaque(child)
                elif isinstance(value, str):
                    if re.search(r"https?://|(?:^|[\s\"\'])/(?:api/|uploads/)|url\(", value, re.I) or re.search(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value, re.I
                    ):
                        raise BundleRefused("UNCLASSIFIED_JSON_REFERENCE:" + kind)

            for name, field in fields.items():
                value = row[name]
                if name == "attachments" and value:
                    raise BundleRefused("UNSUPPORTED_LEGACY_ATTACHMENT_REFERENCE:" + kind)
                if kind == "Project" and name == "cover_image" and value:
                    raise BundleRefused("UNSUPPORTED_LEGACY_ASSET_URL")
                if (
                    isinstance(value, str)
                    and source_origin.casefold() in value.casefold()
                    and name not in {"url"}
                    and (pair is None or name not in pair)
                    # A link's history follows _native_links' exact rule (rewritten or refused).
                    and not (kind == "IssueActivity" and row.get("field") == "link" and name in {"old_value", "new_value"})
                ):
                    raise BundleRefused("UNCLASSIFIED_SOURCE_URL:" + kind)
                if kind in {"IssueLink", "ModuleLink"} and name == "metadata":
                    _check_link_metadata(kind, row, source_origin)
                elif isinstance(field, models.JSONField) and (pair is None or name not in pair):
                    opaque(row[name])
    return checks


LEGACY_HTML_FIELDS = ("description_html", "comment_html")
LINK_METADATA_KEYS = frozenset({"title", "favicon", "url", "favicon_url", "error"})


def _check_link_metadata(kind, row, source_origin):
    """A link's preview, as Plane's crawler stores it: the link's own URL, an inline favicon and
    literal text. Its url follows the link's rewrite; nothing else may name the source instance."""
    value = row["metadata"]
    if not isinstance(value, dict) or not set(value) <= LINK_METADATA_KEYS:
        raise BundleRefused("UNCLASSIFIED_JSON_REFERENCE:" + kind)
    if "url" in value and value["url"] != row["url"]:
        raise BundleRefused("UNCLASSIFIED_JSON_REFERENCE:" + kind)
    favicon = value.get("favicon")
    if favicon is not None and not (isinstance(favicon, str) and favicon.startswith("data:image/")):
        raise BundleRefused("UNCLASSIFIED_JSON_REFERENCE:" + kind)
    for name in ("title", "favicon_url", "error"):
        text = value.get(name)
        if text is not None and (not isinstance(text, str) or source_origin.casefold() in text.casefold()):
            raise BundleRefused("UNCLASSIFIED_JSON_REFERENCE:" + kind)
    favicon_url = value.get("favicon_url")
    if favicon_url is not None and not favicon_url.startswith("https://"):
        raise BundleRefused("UNCLASSIFIED_JSON_REFERENCE:" + kind)


def _encode_rows(rows, *, include_objects):
    import base64
    from django.apps import apps
    from django.db import models

    records, objects, blobs = [], [], {}
    total = 0
    for kind, entries in sorted(rows.items()):
        model = apps.get_model("db", kind)
        for row in entries:
            fields = {}
            for field in model._meta.concrete_fields:
                value = row[field.attname]
                if value is not None and isinstance(field, models.BinaryField):
                    payload = bytes(value)
                    digest = sha256(payload)
                    blobs[digest] = base64.b64encode(payload).decode("ascii")
                    fields[field.attname] = {"sha256": digest}
                elif value and isinstance(field, models.FileField):
                    if not include_objects:
                        fields[field.attname] = str(value)
                        continue
                    payload = _read_object(field.storage, str(value))
                    digest = sha256(payload)
                    blobs[digest] = base64.b64encode(payload).decode("ascii")
                    fields[field.attname] = {"sha256": digest, "source_key": str(value)}
                    objects.append(
                        dict(
                            kind=kind,
                            pk=str(row["id"]),
                            field=field.attname,
                            source_key=str(value),
                            asset_uuid=str(row["id"]) if kind == "FileAsset" else None,
                            sha256=digest,
                            size=len(payload),
                        )
                    )
                else:
                    fields[field.attname] = _pack_scalar(value)
            records.append(
                dict(
                    kind=kind,
                    pk=str(row["id"]),
                    fields=fields,
                    json_sql_nulls=sorted(
                        field.attname
                        for field in model._meta.concrete_fields
                        if isinstance(field, models.JSONField) and row["_bundle_sql_null_" + field.attname]
                    ),
                )
            )
    for value in blobs.values():
        total += len(value)
    if total > MAX_BUNDLE_BYTES:
        raise BundleRefused("BUNDLE_TOO_LARGE")
    return records, blobs, objects


def export_project_snapshot(database, instance, workspace_id, project_id, identifier, freeze):
    """One read-only repeatable-read native graph and exact object census.

    The freeze assertion is supplied by the operator; this command cannot freeze
    Live or prove a source-wide writer barrier. It never connects to a target.
    """
    from django.db import connections, transaction
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone
    from uuid import UUID

    capture_started_at = timezone.now()
    if not isinstance(freeze, dict) or set(freeze) != {"id", "frozen_at"} or not isinstance(freeze["frozen_at"], str):
        raise BundleRefused("INVALID_FREEZE_ASSERTION")
    try:
        if (
            str(UUID(freeze["id"])) != freeze["id"]
            or parse_datetime(freeze["frozen_at"]).utcoffset() is None
            or parse_datetime(freeze["frozen_at"]) > capture_started_at
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise BundleRefused("INVALID_FREEZE_ASSERTION") from None
    connection = connections[database]
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise BundleRefused("OUTERMOST_POSTGRESQL_TRANSACTION_REQUIRED")
    with transaction.atomic(using=database, durable=True):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        profile = schema_profile(database)
        source = _identity(database, instance, workspace_id, project_id, identifier)
        rows, actors, excluded = collect_project_graph(database, workspace_id, project_id, identifier)
        checks = _check_documents(rows, _codec_references(rows, actors, source=source), instance, instance)
        checks.extend(_native_links(rows, source, source)[0])
        records, blobs, objects = _encode_rows(rows, include_objects=True)
        bundle = dict(
            format=BUNDLE_FORMAT,
            fork_revision=FORK_REVISION,
            schema_sha256=NATIVE_SCHEMA_SHA256,
            source=source,
            freeze=freeze,
            capture=dict(started_at=capture_started_at.isoformat(), completed_at=timezone.now().isoformat()),
            schema_profile=profile,
            codec_support=CODEC_SUPPORT,
            records=records,
            actors=sorted(actors),
            blobs=blobs,
            objects=objects,
            content_checks=checks,
            census={kind: len(entries) for kind, entries in sorted(rows.items())},
            excluded_census=excluded,
        )
        if len(records) > 100000 or len(blobs) > 100000:
            raise BundleRefused("INVALID_RECORD_CENSUS")
        if len(canonical(bundle)) > MAX_BUNDLE_BYTES:
            raise BundleRefused("BUNDLE_TOO_LARGE")
        return bundle


def publish_bundle_file(path, value):
    """Publish one private, fsynced artifact without overwriting a divergent retry."""
    from pathlib import Path
    import tempfile

    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise BundleRefused("OUTPUT_DIRECTORY_NOT_PRIVATE")
    payload = canonical(value) + b"\n"
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".project-bundle-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise BundleRefused("OUTPUT_ARTIFACT_CONFLICT") from None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


class BundleRefused(ValueError):
    """A bounded diagnostic code; never contains document or subprocess content."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BundleRefused("INVALID_CODEC_RESPONSE")
            result[key] = value
        return result

    def constant(_value):
        raise BundleRefused("INVALID_CODEC_RESPONSE")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise BundleRefused("INVALID_CODEC_RESPONSE") from None


def _codec_process(payload, document_bytes):
    """Fixed installed executable; cap JS heap, pipes and wall time independently.

    The heap limit is 128 + 16 MiB per document MiB, capped at 256 MiB.
    This bounds V8's heap, not the process's total RSS; the pinned runtime's
    container memory limit is an additional parent-owned deployment control.
    """
    heap_mib = min(256, 128 + 16 * math.ceil(document_bytes / (1024 * 1024)))
    args = [CODEC_NODE, f"--max-old-space-size={heap_mib}", CODEC_PATH]
    process = None
    selector = selectors.DefaultSelector()
    output = bytearray()
    diagnostics = bytearray()
    deadline = time.monotonic() + CODEC_SECONDS
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env={},
            close_fds=True,
            start_new_session=True,
        )
        for stream, event, role in (
            (process.stdin, selectors.EVENT_WRITE, "input"),
            (process.stdout, selectors.EVENT_READ, "output"),
            (process.stderr, selectors.EVENT_READ, "diagnostic"),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event, role)
        pending = memoryview(payload)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BundleRefused("CODEC_TIMEOUT")
            for key, _event in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                if key.data == "input":
                    try:
                        written = os.write(stream.fileno(), pending[:65536])
                        pending = pending[written:]
                    except BrokenPipeError:
                        pending = pending[len(pending) :]
                    if not pending:
                        selector.unregister(stream)
                        stream.close()
                else:
                    chunk = os.read(stream.fileno(), 65536)
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    destination = output if key.data == "output" else diagnostics
                    limit = MAX_RESPONSE_BYTES if key.data == "output" else 4096
                    if len(destination) + len(chunk) > limit:
                        raise BundleRefused("CODEC_OUTPUT_LIMIT")
                    destination.extend(chunk)
        returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if diagnostics or returncode not in (0, 2):
            raise BundleRefused("CODEC_PROCESS_FAILED")
        return returncode, bytes(output)
    except (OSError, subprocess.SubprocessError):
        raise BundleRefused("CODEC_PROCESS_FAILED") from None
    finally:
        selector.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def inspect_native_document(binary, *, variant, source_origin, target_origin, references):
    """Inspect one native binary using the fixed offline protocol; never writes.

    Any changed embedded native identity/origin or unknown retained shape refuses.
    Original and returned bytes must agree, independently of the codec's claim.
    """
    import base64

    if not isinstance(binary, bytes) or not binary:
        raise BundleRefused("INVALID_INPUT")
    if len(binary) > MAX_DOCUMENT_BYTES:
        raise BundleRefused("LIMIT_EXCEEDED")
    request = dict(
        format=CODEC_FORMAT,
        variant=variant,
        binary=base64.b64encode(binary).decode("ascii"),
        source_origin=source_origin,
        target_origin=target_origin,
        references=references,
    )
    try:
        payload = canonical(request)
    except (TypeError, ValueError, RecursionError):
        raise BundleRefused("INVALID_INPUT") from None
    if len(payload) > MAX_REQUEST_BYTES:
        raise BundleRefused("LIMIT_EXCEEDED")
    returncode, raw = _codec_process(payload, len(binary))
    result = _json(raw)
    if returncode == 2:
        if (
            isinstance(result, dict)
            and set(result) == {"format", "status", "code"}
            and result["format"] == CODEC_FORMAT
            and result["status"] == "refused"
            and result["code"] in REFUSAL_CODES
        ):
            raise BundleRefused(result["code"])
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    keys = {
        "format",
        "status",
        "support",
        "variant",
        "binary",
        "before_sha256",
        "after_sha256",
        "schema_sha256",
        "references",
        "census",
    }
    if (
        not isinstance(result, dict)
        or set(result) != keys
        or result["format"] != CODEC_FORMAT
        or result["status"] != "accepted"
        or result["support"] != CODEC_SUPPORT
        or result["variant"] != variant
        or result["binary"] != request["binary"]
        or result["before_sha256"] != sha256(binary)
        or result["after_sha256"] != sha256(binary)
        or not isinstance(result["schema_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["schema_sha256"])
    ):
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    if not isinstance(result["references"], list) or len(result["references"]) > 20000:
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    known = {(ref["kind"], ref["source"]): ref["target"] for ref in references}
    for ref in result["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"kind", "source", "target", "location", "retained"}
            or not isinstance(ref["kind"], str)
            or not isinstance(ref["source"], str)
            or ref["target"] != ref["source"]
            or known.get((ref["kind"], ref["source"])) != ref["target"]
            or type(ref["retained"]) is not bool
            or not isinstance(ref["location"], str)
            or not re.fullmatch(r"[0-9]+:[0-9]+", ref["location"])
        ):
            raise BundleRefused("INVALID_CODEC_RESPONSE")
    census = result["census"]
    if (
        not isinstance(census, dict)
        or set(census) != {"roots", "structs", "retained_structs", "gc_structs"}
        or not isinstance(census["roots"], list)
        or census["roots"] != sorted(set(census["roots"]))
        or not set(census["roots"]) <= ({"default", "title"} if variant == "document" else {"default"})
        or any(
            type(census[key]) is not int or not 0 <= census[key] <= 100000
            for key in ("structs", "retained_structs", "gc_structs")
        )
    ):
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    return result


def inspect_legacy_document(html, document, *, variant, source_origin, target_origin, references):
    """Inspect original legacy field bytes with the native parser and raw DOM guard."""
    import base64

    if html is not None and not isinstance(html, str):
        raise BundleRefused("UNSUPPORTED_LEGACY_HTML")
    try:
        html_bytes = None if html is None else html.encode("utf-8")
        json_bytes = canonical(document)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise BundleRefused("INVALID_LEGACY_DOCUMENT") from None
    size = len(html_bytes or b"") + len(json_bytes)
    if size > MAX_DOCUMENT_BYTES:
        raise BundleRefused("LIMIT_EXCEEDED")
    html_base64 = None if html_bytes is None else base64.b64encode(html_bytes).decode("ascii")
    json_base64 = base64.b64encode(json_bytes).decode("ascii")
    payload = canonical(
        dict(
            format=LEGACY_CODEC_FORMAT,
            variant=variant,
            html_base64=html_base64,
            json_base64=json_base64,
            source_origin=source_origin,
            target_origin=target_origin,
            references=references,
        )
    )
    if len(payload) > MAX_REQUEST_BYTES:
        raise BundleRefused("LIMIT_EXCEEDED")
    returncode, raw = _codec_process(payload, size)
    result = _json(raw)
    if returncode == 2:
        if (
            isinstance(result, dict)
            and set(result) == {"format", "status", "code"}
            and result["format"] == CODEC_FORMAT
            and result["status"] == "refused"
            and result["code"] in REFUSAL_CODES
        ):
            raise BundleRefused(result["code"])
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    expected = {
        "format",
        "status",
        "support",
        "variant",
        "html_base64",
        "json_base64",
        "html_sha256",
        "json_sha256",
        "schema_sha256",
        "references",
        "census",
    }
    if (
        not isinstance(result, dict)
        or set(result) != expected
        or result["format"] != LEGACY_CODEC_FORMAT
        or result["status"] != "accepted"
        or result["support"] != CODEC_SUPPORT
        or result["variant"] != variant
        or result["html_base64"] != html_base64
        or result["json_base64"] != json_base64
        or result["html_sha256"] != (None if html_bytes is None else sha256(html_bytes))
        or result["json_sha256"] != sha256(json_bytes)
        or not isinstance(result["schema_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["schema_sha256"])
    ):
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    if not isinstance(result["references"], list) or len(result["references"]) > 20000:
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    known = {(ref["kind"], ref["source"]): ref["target"] for ref in references}
    for ref in result["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"kind", "source", "target", "location", "retained"}
            or not isinstance(ref["kind"], str)
            or not isinstance(ref["source"], str)
            or ref["target"] != ref["source"]
            or known.get((ref["kind"], ref["source"])) != ref["target"]
            or ref["retained"] is not False
            or not isinstance(ref["location"], str)
            or len(ref["location"]) > 2048
            or not ref["location"].startswith(("html/", "json/"))
        ):
            raise BundleRefused("INVALID_CODEC_RESPONSE")
    census = result["census"]
    if (
        not isinstance(census, dict)
        or set(census) != {"elements", "nodes"}
        or any(type(value) is not int or not 0 <= value <= 100000 for value in census.values())
    ):
        raise BundleRefused("INVALID_CODEC_RESPONSE")
    return result


def _decode_bundle(database, bundle):
    """Validate the complete v2 envelope without a source connection or storage read."""
    import base64
    from django.apps import apps
    from django.db import models
    from django.core.exceptions import ValidationError
    from uuid import UUID

    native_schema(database)
    keys = {
        "format",
        "fork_revision",
        "schema_sha256",
        "source",
        "freeze",
        "codec_support",
        "records",
        "actors",
        "blobs",
        "objects",
        "content_checks",
        "census",
        "excluded_census",
        "schema_profile",
        "capture",
    }
    if (
        not isinstance(bundle, dict)
        or set(bundle) != keys
        or bundle["format"] != BUNDLE_FORMAT
        or bundle["fork_revision"] != FORK_REVISION
        or bundle["schema_sha256"] != NATIVE_SCHEMA_SHA256
        or bundle["codec_support"] != CODEC_SUPPORT
    ):
        raise BundleRefused("INVALID_BUNDLE_SCHEMA")
    if bundle["schema_profile"] != schema_profile(database):
        raise BundleRefused("DATABASE_SCHEMA_PROFILE_MISMATCH")
    if len(canonical(bundle)) > MAX_BUNDLE_BYTES:
        raise BundleRefused("BUNDLE_TOO_LARGE")
    source = bundle["source"]
    if not isinstance(source, dict) or set(source) != {
        "instance",
        "workspace_id",
        "workspace_slug",
        "project_id",
        "identifier",
    }:
        raise BundleRefused("INVALID_PROJECT_IDENTITY")
    _origin(source["instance"])
    if source["identifier"] != "OBOT" or not isinstance(source["workspace_slug"], str) or not source["workspace_slug"]:
        raise BundleRefused("INVALID_PROJECT_IDENTITY")
    try:
        for name in ("workspace_id", "project_id"):
            if str(UUID(source[name])) != source[name]:
                raise ValueError()
        from django.utils.dateparse import parse_datetime

        if (
            set(bundle["freeze"]) != {"id", "frozen_at"}
            or str(UUID(bundle["freeze"]["id"])) != bundle["freeze"]["id"]
            or parse_datetime(bundle["freeze"]["frozen_at"]).utcoffset() is None
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise BundleRefused("INVALID_FREEZE_ASSERTION") from None
    try:
        captured = bundle["capture"]
        started, completed = parse_datetime(captured["started_at"]), parse_datetime(captured["completed_at"])
        if (
            set(captured) != {"started_at", "completed_at"}
            or started.utcoffset() is None
            or completed.utcoffset() is None
            or not parse_datetime(bundle["freeze"]["frozen_at"]) <= started <= completed
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise BundleRefused("INVALID_CAPTURE_BARRIER") from None
    blobs = {}
    if not isinstance(bundle["blobs"], dict) or len(bundle["blobs"]) > 100000:
        raise BundleRefused("INVALID_BLOB_CENSUS")
    for digest, encoded in bundle["blobs"].items():
        try:
            payload = base64.b64decode(encoded, validate=True)
            if sha256(payload) != digest or base64.b64encode(payload).decode("ascii") != encoded:
                raise ValueError()
        except (ValueError, TypeError):
            raise BundleRefused("INVALID_BLOB_HASH") from None
        blobs[digest] = payload
    if not isinstance(bundle["records"], list) or len(bundle["records"]) > 100000:
        raise BundleRefused("INVALID_RECORD_CENSUS")
    rows = {kind: [] for kind in sorted(PROJECT_KINDS)}
    seen, used_blobs, expected_objects = set(), set(), []
    for record in bundle["records"]:
        if (
            not isinstance(record, dict)
            or set(record) != {"kind", "pk", "fields", "json_sql_nulls"}
            or record["kind"] not in PROJECT_KINDS
        ):
            raise BundleRefused("INVALID_NATIVE_RECORD")
        kind, fields = record["kind"], record["fields"]
        model = apps.get_model("db", kind)
        if not isinstance(fields, dict) or set(fields) != {field.attname for field in model._meta.concrete_fields}:
            raise BundleRefused("INVALID_NATIVE_FIELDS")
        nulls = record["json_sql_nulls"]
        json_fields = {
            field.attname: field for field in model._meta.concrete_fields if isinstance(field, models.JSONField)
        }
        if (
            not isinstance(nulls, list)
            or any(
                not isinstance(name, str)
                or name not in json_fields
                or not json_fields[name].null
                or fields[name] is not None
                for name in nulls
            )
            or nulls != sorted(set(nulls))
        ):
            raise BundleRefused("INVALID_NATIVE_JSON_NULL_CENSUS")
        row = {"_bundle_sql_null_" + name: name in nulls for name in json_fields}
        for field in model._meta.concrete_fields:
            value = fields[field.attname]
            if value is None:
                if not field.null and not (isinstance(field, models.JSONField) and field.attname not in nulls):
                    raise BundleRefused("INVALID_NATIVE_NULL")
            elif isinstance(field, models.BinaryField):
                if not isinstance(value, dict) or set(value) != {"sha256"} or value["sha256"] not in blobs:
                    raise BundleRefused("INVALID_BLOB_CENSUS")
                used_blobs.add(value["sha256"])
                value = blobs[value["sha256"]]
            elif isinstance(field, models.FileField) and value:
                if (
                    not isinstance(value, dict)
                    or set(value) != {"sha256", "source_key"}
                    or value["sha256"] not in blobs
                ):
                    raise BundleRefused("INVALID_OBJECT_PROVENANCE")
                digest, key = value["sha256"], value["source_key"]
                if (
                    not isinstance(key, str)
                    or not key
                    or key.startswith("/")
                    or ".." in key.split("/")
                    or len(key) > field.max_length
                ):
                    raise BundleRefused("INVALID_OBJECT_PROVENANCE")
                used_blobs.add(digest)
                expected_objects.append(
                    dict(
                        kind=kind,
                        pk=record["pk"],
                        field=field.attname,
                        source_key=key,
                        asset_uuid=record["pk"] if kind == "FileAsset" else None,
                        sha256=digest,
                        size=len(blobs[digest]),
                    )
                )
                value = key
            elif isinstance(field, models.JSONField):
                canonical(value)
            else:
                try:
                    value = field.to_python(value)
                except (ValueError, TypeError, ValidationError):
                    # Native ValidationError messages can include content.
                    raise BundleRefused("INVALID_NATIVE_VALUE") from None
                if canonical(_pack_scalar(value)) != canonical(fields[field.attname]):
                    raise BundleRefused("NONCANONICAL_NATIVE_VALUE")
            row[field.attname] = value
        if record["pk"] != str(row["id"]) or (kind, record["pk"]) in seen:
            raise BundleRefused("INVALID_NATIVE_PRIMARY_KEY")
        seen.add((kind, record["pk"]))
        rows[kind].append(row)
    if used_blobs != set(blobs) or canonical(expected_objects) != canonical(bundle["objects"]):
        raise BundleRefused("INVALID_OBJECT_CENSUS")
    for entries in rows.values():
        entries.sort(key=lambda row: str(row["id"]))
    if bundle["census"] != {kind: len(entries) for kind, entries in rows.items()}:
        raise BundleRefused("INVALID_RECORD_CENSUS")
    actors = set()
    index = {kind: {str(row["id"]): row for row in entries} for kind, entries in rows.items()}
    if len(rows["Project"]) != 1 or str(rows["Project"][0]["id"]) != source["project_id"]:
        raise BundleRefused("PROJECT_IDENTITY_MISMATCH")
    reached, edges = set(), {}

    def reference(owner, kind, value):
        if value is None:
            return
        key = str(value)
        if kind == "User":
            actors.add(key)
        elif kind == "Workspace":
            if key != source["workspace_id"]:
                raise BundleRefused("CROSS_WORKSPACE_REFERENCE")
        elif kind not in index or key not in index[kind]:
            raise BundleRefused("DANGLING_NATIVE_REFERENCE")
        else:
            edges.setdefault(owner, set()).add((kind, key))
            if kind in {
                "Project",
                "Issue",
                "DraftIssue",
                "IssueComment",
                "Description",
                "Page",
                "Cycle",
                "Module",
                "Intake",
            } or (kind == "Estimate" and owner[0] == "EstimatePoint"):
                edges.setdefault((kind, key), set()).add(owner)

    for kind, entries in rows.items():
        for row in entries:
            owner = (kind, str(row["id"]))
            if row.get("project_id") is not None:
                if str(row["project_id"]) != source["project_id"]:
                    raise BundleRefused("CROSS_PROJECT_REFERENCE")
                reached.add(owner)
            for field in apps.get_model("db", kind)._meta.concrete_fields:
                if field.is_relation:
                    reference(owner, field.related_model.__name__, row[field.attname])
            for name, target in HISTORY_FIELDS.get(kind, {}).items():
                for value in row[name] if isinstance(row[name], list) else [row[name]]:
                    reference(owner, target, value)
            if kind == "IssueActivity":
                target = {
                    "assignees": "User",
                    "labels": "Label",
                    "state": "State",
                    "parent": "Issue",
                    "cycle": "Cycle",
                    "modules": "Module",
                    "estimate_point": "EstimatePoint",
                    "type": "IssueType",
                }.get(row["field"])
                for name in ("old_identifier", "new_identifier"):
                    if row[name] is not None:
                        if target is None:
                            raise BundleRefused("UNCLASSIFIED_ACTIVITY_IDENTIFIER")
                        reference(owner, target, row[name])
            if kind in {"PageLog", "DeployBoard"} and row["entity_identifier"] is not None:
                target = ENTITY_KINDS.get(row.get("entity_type")) or ENTITY_KINDS.get(row.get("entity_name"))
                if target is None:
                    raise BundleRefused("UNCLASSIFIED_ENTITY_IDENTIFIER")
                reference(owner, target, row["entity_identifier"])
            if kind == "FileAsset":
                target = ASSET_OWNERS.get(row["entity_type"])
                if target is None:
                    raise BundleRefused("UNCLASSIFIED_ASSET_ENTITY")
                if row["entity_identifier"]:
                    reference(owner, target, row["entity_identifier"])
            if kind == "Page":
                if row["is_global"] or not any(str(join["page_id"]) == owner[1] for join in rows["ProjectPage"]):
                    raise BundleRefused("SHARED_PAGE")
    reached.add(("Project", source["project_id"]))
    while True:
        expanded = reached | set().union(*(edges.get(owner, set()) for owner in reached))
        if expanded == reached:
            break
        reached = expanded
    if reached != seen or bundle["actors"] != sorted(actors):
        raise BundleRefused("INVALID_GRAPH_CLOSURE")
    if not isinstance(bundle["excluded_census"], dict) or any(
        type(count) is not int or count < 0 for count in bundle["excluded_census"].values()
    ):
        raise BundleRefused("INVALID_EXCLUDED_CENSUS")
    if any(count and kind not in EXCLUDED_PROJECT_KINDS for kind, count in bundle["excluded_census"].items()):
        raise BundleRefused("UNCLASSIFIED_PROJECT_ROWS")
    checks = _check_documents(
        rows, _codec_references(rows, actors, source=source), source["instance"], source["instance"]
    )
    checks.extend(_native_links(rows, source, source)[0])
    if canonical(checks) != canonical(bundle["content_checks"]):
        raise BundleRefused("CONTENT_CHECK_MISMATCH")
    return rows, actors, blobs


def _row_manifest(records):
    return [
        dict(
            kind=row["kind"],
            pk=row["pk"],
            sha256=sha256(canonical(dict(fields=row["fields"], json_sql_nulls=row["json_sql_nulls"]))),
        )
        for row in sorted(records, key=lambda row: (row["kind"], row["pk"]))
    ]


def _target_inventory(database, target):
    rows, actors, excluded = collect_project_graph(database, target["workspace_id"], target["project_id"], "OBOT")
    records, _blobs, _objects = _encode_rows(rows, include_objects=False)
    return rows, _row_manifest(records), excluded


def _verify_staged_objects(objects, expected_proof=None):
    from django.apps import apps

    if expected_proof is not None and _storage_proof(objects) != expected_proof:
        raise BundleRefused("STORAGE_PRIVACY_PROOF_MISMATCH")
    for item in objects:
        storage = apps.get_model("db", item["kind"])._meta.get_field(item["field"]).storage
        storage = _private_storage(storage)
        from django.core.files.storage import FileSystemStorage

        if type(storage) is FileSystemStorage:
            _private_path(storage, item["target_key"])
        payload = _read_object(storage, item["target_key"])
        if len(payload) != item["size"] or sha256(payload) != item["sha256"]:
            raise BundleRefused("TARGET_BLOB_MISMATCH")


def _verify_receipt(database, row, target):
    receipt = row.receipt
    if (
        receipt.get("format") != "plane-project-migration-receipt/2"
        or receipt.get("status") != "committed"
        or receipt.get("run_id") != str(row.pk)
        or receipt.get("source_removal_authorized") is not False
        or receipt.get("target") != target
        or receipt.get("completed_at") != row.completed_at.isoformat()
        or receipt.get("freeze", {}).get("id") != str(row.freeze_id)
        or receipt.get("source", {}).get("instance") != row.source_instance
        or receipt.get("source", {}).get("workspace_id") != str(row.source_workspace_id)
        or receipt.get("source", {}).get("project_id") != str(row.source_project_id)
        or sha256(canonical(receipt)) != row.receipt_sha256
        or receipt.get("intent_sha256") != row.intent_sha256
        or receipt.get("bundle_sha256") != row.bundle_sha256
        or receipt.get("actor_map") != row.source_actor_map
    ):
        raise BundleRefused("RECEIPT_MISMATCH")
    _rows, manifest, excluded = _target_inventory(database, target)
    if manifest != receipt.get("target_rows") or excluded != receipt.get("excluded_census"):
        raise BundleRefused("TARGET_READBACK_MISMATCH")
    _verify_staged_objects(receipt["objects"], receipt["storage_proof"])
    return receipt


def _run_query(database, bundle, target):
    from django.apps import apps

    source = bundle["source"]
    return (
        apps.get_model("db", "ProjectMigrationRun")
        .objects.using(database)
        .filter(
            source_instance=source["instance"],
            source_workspace_id=source["workspace_id"],
            source_project_id=source["project_id"],
            target_instance=target["instance"],
            target_workspace_id=target["workspace_id"],
            target_project_id=target["project_id"],
            freeze_id=bundle["freeze"]["id"],
        )
    )


def _unwrap_storage(storage):
    from django.conf import settings

    # Resolve the native lazy backend only after requiring explicit ordinary
    # credentials. Native S3Storage.__init__ otherwise permits SDK discovery.
    if hasattr(type(storage), "_setup"):
        if settings.STORAGES.get("default", {}).get("BACKEND") == "plane.settings.storage.S3Storage":
            if not (
                all(
                    os.environ.get(name)
                    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_BUCKET_NAME")
                )
                and (os.environ.get("AWS_S3_ENDPOINT_URL") or os.environ.get("MINIO_ENDPOINT_URL"))
            ):
                raise BundleRefused("EXPLICIT_NATIVE_S3_CONFIGURATION_REQUIRED")
        storage._setup()
        storage = storage._wrapped
    return storage


def _private_storage(storage):
    from django.core.files.storage import FileSystemStorage
    from django.conf import settings

    storage = _unwrap_storage(storage)
    if type(storage) is FileSystemStorage:
        if storage.file_permissions_mode != 0o600 or storage.directory_permissions_mode != 0o700:
            raise BundleRefused("STORAGE_PERMISSIONS_NOT_PRIVATE")
    else:
        _native_s3_client(storage)
        if (
            getattr(settings, "AWS_DEFAULT_ACL", None) is not None
            or getattr(settings, "AWS_QUERYSTRING_AUTH", True) is not True
            or getattr(settings, "AWS_S3_CUSTOM_DOMAIN", None)
            or getattr(settings, "AWS_LOCATION", "")
            or getattr(settings, "AWS_S3_OBJECT_PARAMETERS", {})
        ):
            raise BundleRefused("PUBLIC_OR_UNREVIEWED_S3_CONFIGURATION")
    return storage


def _native_s3_client(storage, *, audit=False, unsigned=False):
    """The exact native backend/endpoint/bucket; no generic caller adapter.

    Native S3Storage does not initialize inherited django-storages connection
    state. Bind its actual own configuration and use a bounded low-level client.
    The migration-only audit identity may have scoped Admin IAM; this code calls
    only get_bucket_policy with it, never an administrative write.
    """
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    from django.conf import settings
    from plane.settings.storage import S3Storage
    from urllib.parse import urlsplit

    if type(storage) is not S3Storage:
        raise BundleRefused("UNPROVEN_PRIVATE_STORAGE_BACKEND")
    endpoint, bucket = storage.aws_s3_endpoint_url, storage.aws_storage_bucket_name
    try:
        parsed = urlsplit(endpoint)
        if (
            not endpoint
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or endpoint.rstrip("/") != getattr(settings, "AWS_S3_ENDPOINT_URL", "").rstrip("/")
            or not bucket
            or bucket != settings.AWS_STORAGE_BUCKET_NAME
            or getattr(settings, "AWS_S3_VERIFY", True) is False
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise BundleRefused("EXPLICIT_NATIVE_S3_CONFIGURATION_REQUIRED") from None
    access, secret = storage.aws_access_key_id, storage.aws_secret_access_key
    if not access or not secret:
        raise BundleRefused("EXPLICIT_NATIVE_S3_CONFIGURATION_REQUIRED")
    if audit:
        access = getattr(settings, "PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID", None)
        secret = getattr(settings, "PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY", None)
        if not access or not secret or access == storage.aws_access_key_id:
            raise BundleRefused("SEPARATE_MIGRATION_S3_AUDIT_IDENTITY_REQUIRED")
    config = Config(
        signature_version=UNSIGNED if unsigned else "s3v4",
        connect_timeout=3,
        read_timeout=10,
        retries={"max_attempts": 0},
        s3={"addressing_style": "path"},
        proxies={},
    )
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=storage.aws_region or "us-east-1",
        aws_access_key_id=None if unsigned else access,
        aws_secret_access_key=None if unsigned else secret,
        config=config,
        verify=True,
    )
    return client, bucket, endpoint.rstrip("/")


def _s3_privacy_audit(storage, objects):
    from botocore.exceptions import ClientError

    audit, bucket, endpoint = _native_s3_client(storage, audit=True)
    try:
        audit.get_bucket_policy(Bucket=bucket)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "NoSuchBucketPolicy":
            raise BundleRefused("S3_BUCKET_POLICY_AUDIT_REFUSED") from None
    except Exception:
        raise BundleRefused("S3_BUCKET_POLICY_AUDIT_REFUSED") from None
    else:
        raise BundleRefused("S3_BUCKET_POLICY_PRESENT")
    anonymous, _bucket, _endpoint = _native_s3_client(storage, unsigned=True)
    for item in objects:
        for method, parameters in [
            ("head_object", dict(Key=item["target_key"])),
            ("get_object", dict(Key=item["target_key"], Range="bytes=0-0")),
            ("list_objects_v2", dict(Prefix=item["target_key"], MaxKeys=1)),
        ]:
            try:
                response = getattr(anonymous, method)(Bucket=bucket, **parameters)
                if "Body" in response:
                    response["Body"].close()
            except ClientError as error:
                if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 403:
                    continue
                raise BundleRefused("S3_ANONYMOUS_PRIVACY_PROBE_REFUSED") from None
            except Exception:
                raise BundleRefused("S3_ANONYMOUS_PRIVACY_PROBE_REFUSED") from None
            raise BundleRefused("S3_ANONYMOUS_ACCESS_NOT_DENIED")
    return dict(
        algorithm="plane-native-private-s3/1",
        endpoint=endpoint,
        bucket=bucket,
        policy="NoSuchBucketPolicy",
        anonymous_head=403,
        anonymous_range_get=403,
        anonymous_prefix_list=403,
        conditional_create="IfNoneMatch:*;no-ACL",
        audit_method="get_bucket_policy",
    )


def _storage_proof(objects):
    from django.apps import apps
    from django.core.files.storage import FileSystemStorage

    result = []
    for kind, field in sorted({(item["kind"], item["field"]) for item in objects}):
        storage = _private_storage(apps.get_model("db", kind)._meta.get_field(field).storage)
        if type(storage) is FileSystemStorage:
            proof = dict(
                algorithm="plane-private-filesystem/1",
                location_sha256=sha256(storage.location.encode()),
                file_mode="0600",
                directory_mode="0700",
            )
        else:
            proof = _s3_privacy_audit(
                storage, [item for item in objects if (item["kind"], item["field"]) == (kind, field)]
            )
        result.append(dict(kind=kind, field=field, proof=proof))
    return result


def _stage_s3_object(storage, item, payload):
    from botocore.exceptions import ClientError

    client, bucket, _endpoint = _native_s3_client(storage)
    try:
        client.put_object(
            Bucket=bucket, Key=item["target_key"], Body=payload, ContentLength=len(payload), IfNoneMatch="*"
        )
    except ClientError as error:
        # 409 is an ambiguous race, never a reason to retry an overwrite. Both
        # collision outcomes proceed only to full immutable-key byte readback.
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") not in {409, 412}:
            raise BundleRefused("S3_CONDITIONAL_STAGE_FAILED") from None
    except Exception:
        raise BundleRefused("S3_CONDITIONAL_STAGE_FAILED") from None


def _private_path(storage, key):
    from pathlib import Path

    root = Path(storage.location)
    path = root / key
    if not path.is_relative_to(root) or ".." in key.split("/"):
        raise BundleRefused("INVALID_TARGET_OBJECT_KEY")
    for item in [root, *list(path.parents)[: len(path.parts) - len(root.parts) - 1], path]:
        if item.is_symlink() or (item.exists() and item.stat().st_mode & 0o077):
            raise BundleRefused("STORAGE_PERMISSIONS_NOT_PRIVATE")
    return path


def _stage_objects(objects, blobs):
    """Fsync then exclusive link; a crash cannot publish a partial final object."""
    from pathlib import Path
    import tempfile
    from django.apps import apps

    for item in objects:
        storage = _private_storage(apps.get_model("db", item["kind"])._meta.get_field(item["field"]).storage)
        from django.core.files.storage import FileSystemStorage

        if type(storage) is not FileSystemStorage:
            _stage_s3_object(storage, item, blobs[item["sha256"]])
            continue
        root = Path(storage.location)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = root
        for part in item["target_key"].split("/")[:-1]:
            directory = directory / part
            directory.mkdir(mode=0o700, exist_ok=True)
        path = _private_path(storage, item["target_key"])
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".project-bundle-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(blobs[item["sha256"]])
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                _private_path(storage, item["target_key"])
        finally:
            os.unlink(temporary)
        # Include every newly created parent directory entry, not just the file.
        for parent in [directory, *directory.parents]:
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if parent == root.parent:
                break
    _verify_staged_objects(objects)


def _actor_map(database, actors, user_map, administrator, target):
    from django.apps import apps
    from uuid import UUID

    if not isinstance(user_map, dict) or set(user_map) != actors:
        raise BundleRefused("INCOMPLETE_ACTOR_MAP")
    try:
        if str(UUID(administrator)) != administrator or any(str(UUID(value)) != value for value in user_map.values()):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise BundleRefused("INVALID_ACTOR_MAP") from None
    users = apps.get_model("db", "User").objects.using(database)
    requested = set(user_map.values()) | {administrator}
    if set(map(str, users.filter(pk__in=requested).values_list("pk", flat=True))) != requested:
        raise BundleRefused("MISSING_EXISTING_TARGET_ACTOR")
    for kind, filters in [
        ("WorkspaceMember", dict(workspace_id=target["workspace_id"])),
        ("ProjectMember", dict(project_id=target["project_id"])),
    ]:
        if (
            not _all(apps.get_model("db", kind), database)
            .filter(**filters, member_id=administrator, role=20, is_active=True, deleted_at__isnull=True)
            .exists()
        ):
            raise BundleRefused("EXISTING_TARGET_ADMIN_REQUIRED")


def _mapped_rows(rows, actors, source, target, user_map):
    from copy import deepcopy
    from django.apps import apps

    def remap(kind, value):
        if value is None:
            return None
        if kind == "User":
            return user_map[str(value)]
        if kind == "Workspace":
            return target["workspace_id"]
        if kind == "Project":
            return target["project_id"]
        return value

    result = deepcopy(rows)
    audit = []
    for kind, entries in result.items():
        for row in entries:
            original = str(row["id"])
            for field in apps.get_model("db", kind)._meta.concrete_fields:
                if field.is_relation:
                    value = row[field.attname]
                    if field.related_model.__name__ == "User" and value is not None:
                        audit.append(
                            dict(
                                kind=kind,
                                pk=original,
                                field=field.attname,
                                source_actor=str(value),
                                target_actor=user_map[str(value)],
                            )
                        )
                    row[field.attname] = remap(field.related_model.__name__, value)
            if kind == "Project":
                row["id"] = target["project_id"]
            for name, reference_kind in HISTORY_FIELDS.get(kind, {}).items():
                value = row[name]
                row[name] = (
                    [remap(reference_kind, child) for child in value]
                    if isinstance(value, list)
                    else remap(reference_kind, value)
                )
                if reference_kind == "User":
                    audit.append(dict(kind=kind, pk=original, field=name, source_actors=_pack_scalar(value)))
            if kind == "IssueActivity":
                reference_kind = {
                    "assignees": "User",
                    "parent": "Issue",
                    "state": "State",
                    "labels": "Label",
                    "modules": "Module",
                    "cycle": "Cycle",
                    "type": "IssueType",
                    "estimate_point": "EstimatePoint",
                }.get(row["field"])
                for name in ("old_identifier", "new_identifier"):
                    if reference_kind:
                        if reference_kind == "User" and row[name] is not None:
                            audit.append(
                                dict(
                                    kind=kind,
                                    pk=original,
                                    field=name,
                                    source_actor=str(row[name]),
                                    target_actor=user_map[str(row[name])],
                                )
                            )
                        row[name] = remap(reference_kind, row[name])
            if kind in {"PageLog", "DeployBoard"}:
                reference_kind = ENTITY_KINDS.get(row.get("entity_type")) or ENTITY_KINDS.get(row.get("entity_name"))
                if reference_kind:
                    row["entity_identifier"] = remap(reference_kind, row["entity_identifier"])
            if kind == "FileAsset":
                row["entity_identifier"] = remap(ASSET_OWNERS[row["entity_type"]], row["entity_identifier"])
    return result, audit


def _check_memberships(database, rows, target):
    from django.apps import apps

    for kind in ("ProjectMember", "ProjectPublicMember"):
        active = set()
        for row in rows[kind]:
            if row["deleted_at"] is None and row.get("is_active", True):
                member = str(row["member_id"])
                if member in active:
                    raise BundleRefused("AMBIGUOUS_MAPPED_MEMBERSHIP")
                active.add(member)
                query = _all(apps.get_model("db", kind), database).filter(
                    project_id=target["project_id"], member_id=member, deleted_at__isnull=True
                )
                if "is_active" in row:
                    query = query.filter(is_active=True)
                if "role" in row:
                    query = query.filter(role=row["role"])
                if not query.exists():
                    raise BundleRefused("MEMBERSHIP_ACTIVATION_REFUSED")


def _exclusive_rows(database, current):
    """No external native FK may depend on rows scheduled for replacement."""
    from django.apps import apps

    selected = {kind: {str(row["id"]) for row in entries} for kind, entries in current.items() if kind != "Project"}
    for model in apps.get_app_config("db").get_models():
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model.__name__ in selected:
                ids = selected[field.related_model.__name__]
                if (
                    ids
                    and _all(model, database)
                    .filter(**{field.attname + "__in": ids})
                    .exclude(pk__in=selected.get(model.__name__, set()))
                    .exists()
                ):
                    raise BundleRefused("EXTERNAL_TARGET_REFERENCE")


def _prepare_import(database, bundle, target, user_map, administrator, baseline_receipt_sha256):
    from django.apps import apps

    rows, actors, blobs = _decode_bundle(database, bundle)
    if not isinstance(target, dict) or set(target) != {"instance", "workspace_id", "project_id", "identifier"}:
        raise BundleRefused("INVALID_PROJECT_IDENTITY")
    target = _identity(
        database,
        **dict(
            instance=target["instance"],
            workspace_id=target["workspace_id"],
            project_id=target["project_id"],
            identifier=target["identifier"],
        ),
    )
    _actor_map(database, actors, user_map, administrator, target)
    source = bundle["source"]
    if all(source[name] == target[name] for name in ("instance", "workspace_id", "project_id")):
        raise BundleRefused("SOURCE_DESTINATION_IDENTITY_COLLISION")
    # Inspect ORIGINAL documents against the complete destination identity map.
    # No re-encoding/current-JSON fallback can erase retained references.
    links, link_values = _native_links(rows, source, target)
    # Documents are inspected as they will be written: original, except rewritten anchors.
    checked = {
        kind: [
            {
                **row,
                **{
                    name: link_values[(kind, str(row["id"]), name)]
                    for name in LEGACY_HTML_FIELDS
                    if (kind, str(row["id"]), name) in link_values
                },
            }
            for row in entries
        ]
        for kind, entries in rows.items()
    }
    checks = _check_documents(
        checked, _codec_references(rows, actors, user_map, source, target), source["instance"], target["instance"]
    )
    checks.extend(links)
    mapped, audit = _mapped_rows(rows, actors, source, target, user_map)
    for kind in ("IssueLink", "ModuleLink"):
        for row in mapped[kind]:
            row["url"] = link_values[(kind, str(row["id"]))]
            # The crawler's preview records the same URL (_check_link_metadata requires equality).
            if isinstance(row.get("metadata"), dict) and "url" in row["metadata"]:
                row["metadata"] = {**row["metadata"], "url": row["url"]}
    for kind, entries in mapped.items():
        for row in entries:
            for name in ("old_value", "new_value", *LEGACY_HTML_FIELDS):
                key = (kind, str(row["id"]), name)
                if key in link_values:
                    row[name] = link_values[key]
    current, current_manifest, excluded = _target_inventory(database, target)
    previous = (
        apps.get_model("db", "ProjectMigrationRun")
        .objects.using(database)
        .filter(target_project_id=target["project_id"])
        .order_by("-completed_at", "id")
        .first()
    )
    if previous is not None:
        if baseline_receipt_sha256 != previous.receipt_sha256:
            raise BundleRefused("BASELINE_RECEIPT_REQUIRED")
        baseline = _verify_receipt(database, previous, target)
        if baseline["source"] != source:
            raise BundleRefused("BASELINE_SOURCE_MISMATCH")
    else:
        if baseline_receipt_sha256 is not None:
            raise BundleRefused("BASELINE_RECEIPT_MISMATCH")
        baseline = None
        if any(
            entries
            for kind, entries in current.items()
            if kind
            not in {
                "Project",
                "ProjectIdentifier",
                "ProjectMember",
                "ProjectPublicMember",
                "State",
                "ProjectUserProperty",
            }
        ):
            raise BundleRefused("TARGET_NOT_EMPTY")
        _check_bootstrap(current, user_map, administrator)
        root = current["Project"][0]
        if any(
            root[name]
            for name in (
                "description",
                "description_html",
                "description_text",
                "cover_image_asset_id",
                "estimate_id",
                "archived_at",
                "deleted_at",
            )
        ):
            raise BundleRefused("TARGET_NOT_EMPTY")
    _check_memberships(database, mapped, target)
    # Every pre-existing active member must remain represented; no implicit revoke.
    desired_members = {
        str(row["member_id"]) for row in mapped["ProjectMember"] if row["deleted_at"] is None and row["is_active"]
    }
    if administrator not in desired_members:
        raise BundleRefused("SOURCE_MUST_REPRESENT_EXISTING_TARGET_ADMIN")
    if baseline is None and any(
        str(row["member_id"]) not in desired_members
        for row in current["ProjectMember"]
        if row["deleted_at"] is None and row["is_active"]
    ):
        raise BundleRefused("UNMAPPED_EXISTING_TARGET_MEMBERSHIP")
    if baseline is None:
        desired_public = {str(row["member_id"]) for row in mapped["ProjectPublicMember"] if row["deleted_at"] is None}
        if any(
            str(row["member_id"]) not in desired_public
            for row in current["ProjectPublicMember"]
            if row["deleted_at"] is None
        ):
            raise BundleRefused("UNMAPPED_EXISTING_TARGET_PUBLIC_MEMBERSHIP")
    _exclusive_rows(database, current)
    for kind, entries in mapped.items():
        allowed = {str(row["id"]) for row in current[kind]}
        ids = [row["id"] for row in entries]
        if ids and _all(apps.get_model("db", kind), database).filter(pk__in=ids).exclude(pk__in=allowed).exists():
            raise BundleRefused("NATIVE_ID_COLLISION")
    _check_native_uniqueness(database, mapped, current)
    objects = []
    for item in bundle["objects"]:
        item = dict(item, target_key="pb/" + target["project_id"].replace("-", "") + "/" + item["sha256"])
        field = apps.get_model("db", item["kind"])._meta.get_field(item["field"])
        _private_storage(field.storage)
        if len(item["target_key"]) > field.max_length:
            raise BundleRefused("TARGET_OBJECT_KEY_TOO_LONG")
        for row in mapped[item["kind"]]:
            if str(row["id"]) == (target["project_id"] if item["kind"] == "Project" else item["pk"]):
                row[item["field"]] = item["target_key"]
        objects.append(item)
    records, _binary, _objects = _encode_rows(mapped, include_objects=False)
    manifest = _row_manifest(records)
    prior_source = (
        {} if baseline is None else {(row["kind"], row["pk"]): row["sha256"] for row in baseline["source_rows"]}
    )
    source_rows = _row_manifest(bundle["records"])
    next_source = {(row["kind"], row["pk"]): row["sha256"] for row in source_rows}
    delta = {"additions": [], "updates": [], "removals": []}
    for key in sorted(set(prior_source) | set(next_source)):
        if key not in prior_source:
            delta["additions"].append(dict(kind=key[0], pk=key[1], after_sha256=next_source[key]))
        elif key not in next_source:
            delta["removals"].append(dict(kind=key[0], pk=key[1], before_sha256=prior_source[key]))
        elif prior_source[key] != next_source[key]:
            delta["updates"].append(
                dict(kind=key[0], pk=key[1], before_sha256=prior_source[key], after_sha256=next_source[key])
            )
    intent = dict(
        format="plane-project-import-intent/2",
        bundle_sha256=sha256(canonical(bundle)),
        source=source,
        target=target,
        freeze=bundle["freeze"],
        capture=bundle["capture"],
        schema_sha256=NATIVE_SCHEMA_SHA256,
        schema_profile=bundle["schema_profile"],
        codec_support=CODEC_SUPPORT,
        actor_map=user_map,
        administrator=administrator,
        source_actor_locations=audit,
        source_rows=source_rows,
        target_rows=manifest,
        replaced_target_rows=current_manifest,
        excluded_census=excluded,
        objects=objects,
        storage_proof=_storage_proof(objects),
        content_checks=checks,
        delta=delta,
        baseline_receipt_sha256=baseline_receipt_sha256,
        source_removal_authorized=False,
    )
    return intent, mapped, current, blobs


def plan_project_import(database, bundle, target, user_map, administrator, baseline_receipt_sha256=None):
    """Read-only dry-run. Return the exact reviewed intent; create no target blob."""
    from django.db import connections, transaction

    connection = connections[database]
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise BundleRefused("OUTERMOST_POSTGRESQL_TRANSACTION_REQUIRED")
    with transaction.atomic(using=database, durable=True):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return _prepare_import(database, bundle, target, user_map, administrator, baseline_receipt_sha256)[0]


def _native_sql(cursor, connection, kind, row, *, update=False):
    """Raw native fields, never Model.save/pre_save/signals/Celery/Live hooks."""
    from django.apps import apps

    model = apps.get_model("db", kind)
    fields = [field for field in model._meta.concrete_fields if not update or not field.primary_key]
    quoted = connection.ops.quote_name
    from django.db import models

    values = [
        field.get_db_prep_value(None, connection)
        if isinstance(field, models.JSONField)
        and row[field.attname] is None
        and row.get("_bundle_sql_null_" + field.attname) is False
        else field.get_db_prep_save(row[field.attname], connection)
        for field in fields
    ]
    if update:
        cursor.execute(
            "UPDATE "
            + quoted(model._meta.db_table)
            + " SET "
            + ",".join(quoted(field.column) + "=%s" for field in fields)
            + " WHERE "
            + quoted(model._meta.pk.column)
            + "=%s",
            values + [row["id"]],
        )
    else:
        cursor.execute(
            "INSERT INTO "
            + quoted(model._meta.db_table)
            + " ("
            + ",".join(quoted(field.column) for field in fields)
            + ") VALUES ("
            + ",".join(["%s"] * len(fields))
            + ")",
            values,
        )


def import_project_snapshot(
    database, bundle, target, user_map, administrator, approved_intent_sha256, baseline_receipt_sha256=None
):
    """Target-only durable transaction. Stage private bytes before native SQL.

    Identity-only documents, audited private staging and exact reviewed native
    schema are the defined support boundary. A receipt is never source removal
    authorization and does not certify actual-data/cutover/restore readiness.
    """
    from django.apps import apps
    from django.db import connections, transaction
    from django.utils import timezone
    from plane.utils.uuid import convert_uuid_to_integer
    from uuid import UUID, uuid4

    connection = connections[database]
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise BundleRefused("OUTERMOST_POSTGRESQL_TRANSACTION_REQUIRED")
    if not isinstance(approved_intent_sha256, str) or not re.fullmatch("[0-9a-f]{64}", approved_intent_sha256):
        raise BundleRefused("INTENT_APPROVAL_MISMATCH")
    # Validate bounded envelope before touching target rows/locks.
    if (
        not isinstance(bundle, dict)
        or not isinstance(bundle.get("source"), dict)
        or not isinstance(bundle.get("freeze"), dict)
    ):
        raise BundleRefused("INVALID_BUNDLE_SCHEMA")
    identity = _identity(
        database, target["instance"], target["workspace_id"], target["project_id"], target["identifier"]
    )
    with transaction.atomic(using=database, durable=True):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [convert_uuid_to_integer(UUID(identity["project_id"]))])
            list(
                _all(apps.get_model("db", "Workspace"), database)
                .select_for_update()
                .filter(pk=identity["workspace_id"])
            )
            list(_all(apps.get_model("db", "Project"), database).select_for_update().filter(pk=identity["project_id"]))
            existing = _run_query(database, bundle, identity).select_for_update().first()
            if existing is not None:
                if (
                    existing.bundle_sha256 != sha256(canonical(bundle))
                    or existing.intent_sha256 != approved_intent_sha256
                    or existing.source_actor_map != user_map
                    or existing.receipt.get("administrator") != administrator
                    or existing.receipt.get("baseline_receipt_sha256") != baseline_receipt_sha256
                ):
                    raise BundleRefused("MIGRATION_RUN_CONFLICT")
                return _verify_receipt(database, existing, identity)
            # Lock existing closure rows, including Pages/versions/workspace
            # definitions; their incoming FK writers cannot race replacement.
            current, _manifest, _excluded = _target_inventory(database, identity)
            for kind, entries in current.items():
                if entries:
                    list(
                        _all(apps.get_model("db", kind), database)
                        .select_for_update()
                        .filter(pk__in=[row["id"] for row in entries])
                    )
            intent, mapped, current, blobs = _prepare_import(
                database, bundle, target, user_map, administrator, baseline_receipt_sha256
            )
            if sha256(canonical(intent)) != approved_intent_sha256:
                raise BundleRefused("INTENT_APPROVAL_MISMATCH")
            _stage_objects(intent["objects"], blobs)
            _verify_staged_objects(intent["objects"], intent["storage_proof"])
            cursor.execute('LOCK TABLE "project_identifiers" IN SHARE ROW EXCLUSIVE MODE')
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            for kind, entries in current.items():
                if kind == "Project" or not entries:
                    continue
                model = apps.get_model("db", kind)
                cursor.execute(
                    "DELETE FROM "
                    + connection.ops.quote_name(model._meta.db_table)
                    + " WHERE "
                    + connection.ops.quote_name(model._meta.pk.column)
                    + " = ANY(%s)",
                    [[row["id"] for row in entries]],
                )
            for kind, entries in mapped.items():
                for row in entries:
                    _native_sql(cursor, connection, kind, row, update=(kind == "Project"))
            # Transactional restart (setval is deliberately avoided: it does not
            # roll back). This is the only native integer-PK table in the graph.
            cursor.execute("SELECT pg_get_serial_sequence('project_identifiers', 'id')")
            sequence = cursor.fetchone()[0]
            cursor.execute("SELECT last_value FROM " + connection.ops.quote_name(sequence.split(".")[-1]))
            last_value = cursor.fetchone()[0]
            cursor.execute('SELECT COALESCE(MAX(id), 0) FROM "project_identifiers"')
            restart = max(last_value, cursor.fetchone()[0]) + 1
            cursor.execute(
                "ALTER SEQUENCE " + connection.ops.quote_name(sequence.split(".")[-1]) + " RESTART WITH " + str(restart)
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            _actual, actual_manifest, actual_excluded = _target_inventory(database, identity)
            if actual_manifest != intent["target_rows"] or actual_excluded != intent["excluded_census"]:
                raise BundleRefused("TARGET_READBACK_MISMATCH")
            _verify_staged_objects(intent["objects"], intent["storage_proof"])
            completed_at = timezone.now()
            receipt = dict(
                intent,
                format="plane-project-migration-receipt/2",
                status="committed",
                intent_sha256=approved_intent_sha256,
                completed_at=completed_at.isoformat(),
                run_id=str(uuid4()),
            )
            model = apps.get_model("db", "ProjectMigrationRun")
            row = dict(
                id=receipt["run_id"],
                source_instance=identity["instance"],
                source_workspace_id=identity["workspace_id"],
                source_project_id=identity["project_id"],
                target_instance=identity["instance"],
                target_workspace_id=identity["workspace_id"],
                target_project_id=identity["project_id"],
                freeze_id=bundle["freeze"]["id"],
                bundle_sha256=intent["bundle_sha256"],
                intent_sha256=approved_intent_sha256,
                receipt_sha256=sha256(canonical(receipt)),
                source_actor_map=user_map,
                receipt=receipt,
                completed_at=completed_at,
            )
            row.update(
                source_instance=bundle["source"]["instance"],
                source_workspace_id=bundle["source"]["workspace_id"],
                source_project_id=bundle["source"]["project_id"],
            )
            _native_sql(cursor, connection, model.__name__, row)
        return receipt


def read_private_json(path, *, limit=MAX_BUNDLE_BYTES):
    """One bounded local artifact, no symlinks, duplicate keys or public mode."""
    import stat

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_size > limit:
                raise BundleRefused("INVALID_PRIVATE_ARTIFACT")
            payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise BundleRefused("BUNDLE_TOO_LARGE")
        return _json(payload)
    except OSError:
        raise BundleRefused("INACCESSIBLE_PRIVATE_ARTIFACT") from None


def schema_profile(database):
    """Version-qualified actual catalog fingerprint, excluding the target receipt.

    Algorithm 2 hashes exact columns, PostgreSQL native defaults, constraints and
    indexes for all 92 declared native tables. No application table rows are read.
    Matching Python metadata alone cannot conceal a drifted database column.
    """
    import django
    from django.apps import apps
    from django.conf import settings
    from django.db import connections

    connection = connections[database]
    if not settings.USE_TZ:
        raise BundleRefused("NATIVE_UTC_TIMESTAMP_SETTINGS_REQUIRED")
    native_schema(database)
    registry = {
        model._meta.db_table: model
        for model in apps.get_app_config("db").get_models()
        if model.__name__ != "ProjectMigrationRun"
    }
    catalog = {}
    with connection.cursor() as cursor:
        allowed_apps = {"db", "auth", "contenttypes", "sessions", "django_celery_beat"}
        allowed_tables = {
            model._meta.db_table
            for model in apps.get_models(include_auto_created=True)
            if model._meta.app_label in allowed_apps
        } | {"django_migrations"}
        # Native production installs these instance/license metadata models.
        # Classify only their exact known model/table pairs. No payload rows are
        # queried or exported, and future license models/tables still refuse.
        license_metadata = {
            "license.Instance": "instances",
            "license.InstanceAdmin": "instance_admins",
            "license.InstanceConfiguration": "instance_configurations",
            "license.ChangeLog": "changelogs",
        }
        allowed_tables.update(
            model._meta.db_table
            for model in apps.get_models()
            if license_metadata.get(model._meta.label) == model._meta.db_table
        )
        tables = sorted(connection.introspection.table_names(cursor))
        if set(tables) - allowed_tables:
            raise BundleRefused("UNCLASSIFIED_DATABASE_TABLE")
        cursor.execute("SELECT to_jsonb(d) FROM pg_database d WHERE datname=current_database()")
        properties = cursor.fetchone()[0]
        if isinstance(properties, str):
            properties = json.loads(properties)
        locale = {
            key: properties[key]
            for key in (
                "encoding",
                "datlocprovider",
                "datcollate",
                "datctype",
                "daticulocale",
                "datlocale",
                "daticurules",
                "datcollversion",
            )
            if key in properties
        }
        for table, model in sorted(registry.items()):
            cursor.execute(
                """SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
                pg_get_expr(d.adbin,d.adrelid), a.attidentity, a.attgenerated, a.attcollation::regcollation::text
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",
                [table],
            )
            columns = cursor.fetchall()
            expected = [
                (
                    field.column,
                    re.sub(r"\[[0-9]+\]", "[]", field.db_type(connection).replace("varchar", "character varying")),
                    not field.null,
                )
                for field in model._meta.concrete_fields
            ]
            if sorted((name, datatype, required) for name, datatype, required, *_rest in columns) != sorted(expected):
                raise BundleRefused("DATABASE_COLUMN_DRIFT:" + model.__name__)
            cursor.execute(
                "SELECT conname, contype, condeferrable, condeferred, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE conrelid=to_regclass(%s) ORDER BY conname",
                [table],
            )
            constraints = cursor.fetchall()
            cursor.execute(
                "SELECT indexname,indexdef FROM pg_indexes "
                "WHERE schemaname=current_schema() AND tablename=%s ORDER BY indexname",
                [table],
            )
            catalog[table] = dict(columns=columns, constraints=constraints, indexes=cursor.fetchall())
    return dict(
        algorithm="plane-django-meta-pg-catalog/2",
        django_version=django.get_version(),
        database_locale=locale,
        table_inventory_sha256=sha256(canonical([table for table in tables if table != "project_migration_runs"])),
        postgresql_major=connection.pg_version // 10000,
        native_schema_sha256=NATIVE_SCHEMA_SHA256,
        catalog_sha256=sha256(canonical(catalog)),
    )


def _native_links(rows, source, target):
    """Only the exact native Page route in standalone URL fields may change.

    Bound to apps/web/core/store/pages/project-page.ts::redirect_url, the
    existing project Page route and the work item route /<slug>/browse/<ID>-<n>/.
    Beyond URL fields, only an exact double-quoted href to one of those routes in a
    legacy (HTML-only) document changes; JSON and Yjs history are never edited.
    Signed/credential-bearing URLs, unknown native routes and foreign Pages or work
    items refuse instead of retaining an authenticated source link.
    """
    import re
    from urllib.parse import urlsplit
    from uuid import UUID

    checks, values = [], {}
    pages = {str(row["id"]) for row in rows["Page"]}
    sequences = {str(row["sequence_id"]) for row in rows.get("Issue", [])}

    def rewrite(original, kind):
        try:
            parsed = urlsplit(original)
            if (
                parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.scheme not in {"", "https"}
            ):
                raise ValueError()
            internal = (
                not parsed.netloc or parsed.netloc.casefold() == urlsplit(source["instance"]).netloc.casefold()
            )
            if parsed.netloc and not parsed.scheme:
                raise ValueError()
            if internal:
                pieces = parsed.path.split("/")
                trailing = pieces[-1] == ""
                if trailing:
                    pieces = pieces[:-1]
                prefix, issue = source["identifier"] + "-", pieces[3] if len(pieces) == 4 else ""
                if pieces[:3] == ["", source["workspace_slug"], "browse"] and len(pieces) == 4:
                    if not (
                        issue.startswith(prefix)
                        and issue[len(prefix) :].isdigit()
                        and issue[len(prefix) :] in sequences
                    ):
                        raise ValueError()
                    route = "/browse/" + target["identifier"] + "-" + issue[len(prefix) :]
                    support = "native-issue-url/1"
                elif (
                    len(pieces) != 6
                    or pieces[:5] != ["", source["workspace_slug"], "projects", source["project_id"], "pages"]
                    or str(UUID(pieces[5])) != pieces[5]
                    or pieces[5] not in pages
                ):
                    raise ValueError()
                else:
                    route = "/projects/" + target["project_id"] + "/pages/" + pieces[5]
                    support = "native-page-url/1"
                rewritten = (
                    (target["instance"] if parsed.netloc else "")
                    + "/"
                    + target["workspace_slug"]
                    + route
                    + ("/" if trailing else "")
                )
            else:
                if not parsed.hostname or source["instance"].casefold() in original.casefold():
                    raise ValueError()
                rewritten = original
                support = "external-plain-url/1"
        except (ValueError, TypeError):
            raise BundleRefused("UNSUPPORTED_NATIVE_LINK:" + kind) from None
        return support, rewritten

    def record(kind, pk, field, original, support, rewritten):
        checks.append(
            dict(
                kind=kind,
                pk=pk,
                field=field,
                result=dict(
                    support=support,
                    before_sha256=sha256(original.encode()),
                    after_sha256=sha256(rewritten.encode()),
                    original=original,
                    target=rewritten,
                ),
            )
        )

    for kind in ("IssueLink", "ModuleLink"):
        for row in rows[kind]:
            original = row["url"]
            if not isinstance(original, str) or not original:
                raise BundleRefused("UNSUPPORTED_NATIVE_LINK")
            support, rewritten = rewrite(original, kind)
            values[(kind, str(row["id"]))] = rewritten
            record(kind, str(row["id"]), "url", original, support, rewritten)
    # A link's activity history records the same URL; it follows exactly the same rule, so the
    # history never keeps a link to the source instance and never changes anything else.
    for row in rows.get("IssueActivity", []):
        if row.get("field") != "link":
            continue
        for name in ("old_value", "new_value"):
            original = row.get(name)
            if not isinstance(original, str) or not original or ("://" not in original and not original.startswith("/")):
                continue
            support, rewritten = rewrite(original, "IssueActivity")
            values[("IssueActivity", str(row["id"]), name)] = rewritten
            record("IssueActivity", str(row["id"]), name, original, support, rewritten)
    # A legacy document's anchors to the source instance follow the same rule, one exact
    # href="..." at a time; the codec then refuses any other mention of the source.
    netloc = urlsplit(source["instance"]).netloc.casefold()
    for kind, entries in sorted(rows.items()):
        for row in entries:
            for name in LEGACY_HTML_FIELDS:
                html = row.get(name)
                if not isinstance(html, str) or netloc not in html.casefold():
                    continue
                updated = html
                for original in dict.fromkeys(re.findall(r'href="([^"]*)"', html)):
                    if urlsplit(original).netloc.casefold() != netloc:
                        continue
                    support, rewritten = rewrite(original, kind)
                    updated = updated.replace(f'href="{original}"', f'href="{rewritten}"')
                    record(kind, str(row["id"]), name + "#href", original, support, rewritten)
                if updated != html:
                    values[(kind, str(row["id"]), name)] = updated
    return checks, values


def _check_native_uniqueness(database, rows, current):
    """Reject mapped join/name/anchor collisions before private blob creation."""
    from django.apps import apps
    from django.db import models

    for kind, entries in rows.items():
        model = apps.get_model("db", kind)
        rules = [(tuple(names), None) for names in model._meta.unique_together]
        rules.extend(
            ((field.name,), None) for field in model._meta.concrete_fields if field.unique and not field.primary_key
        )
        for constraint in model._meta.constraints:
            if (
                not isinstance(constraint, models.UniqueConstraint)
                or constraint.expressions
                or constraint.nulls_distinct is False
            ):
                raise BundleRefused("UNCLASSIFIED_NATIVE_CONSTRAINT")
            condition = constraint.condition
            if condition is not None:
                if (
                    condition.negated
                    or condition.connector != "AND"
                    or any(
                        not isinstance(child, tuple) or not child[0].endswith("__isnull") or type(child[1]) is not bool
                        for child in condition.children
                    )
                ):
                    raise BundleRefused("UNCLASSIFIED_NATIVE_CONSTRAINT")
            rules.append((constraint.fields, condition))
        for names, condition in rules:
            known = set()
            for row in entries:
                if condition is not None:
                    if any(
                        (row[model._meta.get_field(name[:-8]).attname] is None) != required
                        for name, required in condition.children
                    ):
                        continue
                query = {
                    model._meta.get_field(name).attname: row[model._meta.get_field(name).attname] for name in names
                }
                if any(value is None for value in query.values()):
                    continue  # PostgreSQL's native NULLS DISTINCT behavior.
                identity = canonical({key: _pack_scalar(value) for key, value in query.items()})
                if identity in known:
                    raise BundleRefused("MAPPED_NATIVE_UNIQUE_COLLISION")
                known.add(identity)
                existing = (
                    _all(model, database).filter(**query).exclude(pk__in=[entry["id"] for entry in current[kind]])
                )
                if condition is not None:
                    existing = existing.filter(condition)
                if existing.exists():
                    raise BundleRefused("TARGET_NATIVE_UNIQUE_COLLISION")


def _check_bootstrap(current, user_map, administrator):
    """Recognize only exact native empty-project defaults, reviewed in the intent."""
    from django.apps import apps
    from plane.db.models.state import DEFAULT_STATES

    expected = {value["name"]: value for value in DEFAULT_STATES}
    states = current["State"]
    if states:
        if len(states) != len(expected) or {row["name"] for row in states} != set(expected):
            raise BundleRefused("TARGET_STATES_NOT_NATIVE_BOOTSTRAP")
        for row in states:
            wanted = dict(
                description="",
                slug="",
                is_triage=False,
                default=False,
                external_source=None,
                external_id=None,
                deleted_at=None,
                updated_by_id=None,
            )
            wanted.update(expected[row["name"]])
            if any(_pack_scalar(row[name]) != value for name, value in wanted.items()):
                raise BundleRefused("TARGET_STATES_NOT_NATIVE_BOOTSTRAP")
            if row["created_by_id"] is not None and str(row["created_by_id"]) != administrator:
                raise BundleRefused("TARGET_STATES_NOT_NATIVE_BOOTSTRAP")
    root = current["Project"][0]
    if root["default_state_id"] is not None and not any(
        str(row["id"]) == str(root["default_state_id"]) and row["default"] for row in states
    ):
        raise BundleRefused("TARGET_STATES_NOT_NATIVE_BOOTSTRAP")
    model = apps.get_model("db", "ProjectUserProperty")
    for row in current["ProjectUserProperty"]:
        if str(row["user_id"]) not in set(user_map.values()) or row["deleted_at"] is not None:
            raise BundleRefused("TARGET_PROPERTIES_NOT_NATIVE_BOOTSTRAP")
        for field in model._meta.concrete_fields:
            if field.get_internal_type() == "JSONField" and (
                row["_bundle_sql_null_" + field.attname] or row[field.attname] != field.get_default()
            ):
                raise BundleRefused("TARGET_PROPERTIES_NOT_NATIVE_BOOTSTRAP")
