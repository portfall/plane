# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import uuid
import base64
import requests
import logging
from bs4 import BeautifulSoup

# Django imports
from django.conf import settings
from django.db import transaction

# Module imports
from plane.db.models import FileAsset, Page, Issue
from plane.utils.exception_logger import log_exception
from plane.settings.storage import S3Storage
from celery import shared_task
from celery.exceptions import Retry
from plane.utils.url import normalize_url_path
from plane.utils import page_content

logger = logging.getLogger(__name__)
PAGE_ASSET_RETRY_DELAYS = (10, 30, 90)


def get_entity_id_field(entity_type, entity_id):
    entity_mapping = {
        FileAsset.EntityTypeContext.WORKSPACE_LOGO: {"workspace_id": entity_id},
        FileAsset.EntityTypeContext.PROJECT_COVER: {"project_id": entity_id},
        FileAsset.EntityTypeContext.USER_AVATAR: {"user_id": entity_id},
        FileAsset.EntityTypeContext.USER_COVER: {"user_id": entity_id},
        FileAsset.EntityTypeContext.ISSUE_ATTACHMENT: {"issue_id": entity_id},
        FileAsset.EntityTypeContext.ISSUE_DESCRIPTION: {"issue_id": entity_id},
        FileAsset.EntityTypeContext.PAGE_DESCRIPTION: {"page_id": entity_id},
        FileAsset.EntityTypeContext.COMMENT_DESCRIPTION: {"comment_id": entity_id},
        FileAsset.EntityTypeContext.DRAFT_ISSUE_DESCRIPTION: {"draft_issue_id": entity_id},
    }
    return entity_mapping.get(entity_type, {})


def extract_asset_ids(html, tag):
    try:
        soup = BeautifulSoup(html, "html.parser")
        return [tag.get("src") for tag in soup.find_all(tag) if tag.get("src")]
    except Exception as e:
        log_exception(e)
        return []


def replace_asset_ids(html, tag, duplicated_assets):
    try:
        soup = BeautifulSoup(html, "html.parser")
        for mention_tag in soup.find_all(tag):
            for asset in duplicated_assets:
                if mention_tag.get("src") == asset["old_asset_id"]:
                    mention_tag["src"] = asset["new_asset_id"]
        return str(soup)
    except Exception as e:
        log_exception(e)
        return html


def update_description(entity, duplicated_assets, tag):
    updated_html = replace_asset_ids(entity.description_html, tag, duplicated_assets)
    entity.description_html = updated_html
    entity.save()
    return updated_html


# Get the description binary and description from the live server
def sync_with_external_service(entity_name, description_html):
    try:
        data = {
            "description_html": description_html,
            "variant": "rich" if entity_name == "PAGE" else "document",
        }

        live_url = settings.LIVE_URL
        if not live_url:
            return {}

        url = normalize_url_path(f"{live_url}/convert-document/")

        response = requests.post(url, json=data, headers=None)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException as e:
        log_exception(e)
    return {}


def copy_assets(entity, entity_identifier, project_id, asset_ids, user_id):
    duplicated_assets = []
    workspace = entity.workspace
    storage = S3Storage()
    original_assets = FileAsset.objects.filter(workspace=workspace, project_id=project_id, id__in=asset_ids)

    for original_asset in original_assets:
        destination_key = f"{workspace.id}/{uuid.uuid4().hex}-{original_asset.attributes.get('name')}"
        duplicated_asset = FileAsset.objects.create(
            attributes={
                "name": original_asset.attributes.get("name"),
                "type": original_asset.attributes.get("type"),
                "size": original_asset.attributes.get("size"),
            },
            asset=destination_key,
            size=original_asset.size,
            workspace=workspace,
            created_by_id=user_id,
            entity_type=original_asset.entity_type,
            project_id=project_id,
            storage_metadata=original_asset.storage_metadata,
            **get_entity_id_field(original_asset.entity_type, entity_identifier),
        )
        storage.copy_object(original_asset.asset, destination_key)
        duplicated_assets.append(
            {
                "new_asset_id": str(duplicated_asset.id),
                "old_asset_id": str(original_asset.id),
            }
        )
    if duplicated_assets:
        FileAsset.objects.filter(pk__in=[item["new_asset_id"] for item in duplicated_assets]).update(is_uploaded=True)

    return duplicated_assets


def _page_asset_failed(task, identity, stage, stage_retry):
    logger.error(
        "Page asset task failed",
        extra={
            "task_id": task.request.id,
            "page_id": identity["entity_identifier"],
            "stage": stage,
            "stage_retry": stage_retry,
        },
    )
    raise RuntimeError(f"Page asset {stage} failed.") from None


def _retry_page_asset(task, identity, stage, duplicated_assets, stage_retry):
    if stage_retry >= len(PAGE_ASSET_RETRY_DELAYS) or task.request.retries >= task.max_retries:
        _page_asset_failed(task, identity, stage, stage_retry)
    try:
        raise task.retry(
            args=(),
            kwargs={
                **identity,
                "page_stage": stage,
                "duplicated_assets": duplicated_assets,
                "stage_retry": stage_retry + 1,
            },
            countdown=PAGE_ASSET_RETRY_DELAYS[stage_retry],
            exc=page_content.PageContentUnavailable(),
        )
    except Retry:
        raise
    except Exception:
        # A broker failure must not expose connection credentials or turn into
        # a successful result. There is no durable outbox in this workflow.
        _page_asset_failed(task, identity, stage, stage_retry)


def _copy_page_assets(task, identity, page_stage, duplicated_assets, stage_retry):
    if page_stage not in ("copy", "prepare", "publish") or type(stage_retry) is not int or not 0 <= stage_retry <= 3:
        _page_asset_failed(task, identity, "state", 0)

    if page_stage == "copy":
        if duplicated_assets is not None or stage_retry:
            _page_asset_failed(task, identity, "state", stage_retry)
        try:
            entity = Page.objects.get(pk=identity["entity_identifier"])
            asset_ids = extract_asset_ids(entity.description_html, "image-component")
            duplicated_assets = copy_assets(
                entity, identity["entity_identifier"], identity["project_id"], asset_ids, identity["user_id"]
            )
        except Exception:
            # A partially completed S3 copy is not safe to repeat automatically.
            _page_asset_failed(task, identity, "copy", stage_retry)
        page_stage = "prepare"

    if page_stage == "prepare":
        if not isinstance(duplicated_assets, list):
            _page_asset_failed(task, identity, "state", stage_retry)
        if not duplicated_assets:
            return
        try:
            with transaction.atomic(durable=True):
                entity = Page.objects.select_for_update().get(pk=identity["entity_identifier"])
                content = page_content.prepare_assets(
                    entity.pk,
                    persisted_binary=entity.description_binary,
                    previous_html=entity.description_html,
                    title=entity.name,
                    duplicated_assets=duplicated_assets,
                )
                page_content.retain_unchanged_html(entity, content)
                for field, value in content.items():
                    setattr(entity, field, value)
                entity.save(
                    update_fields=[*page_content.CONTENT_FIELDS, "description_stripped", "updated_at", "updated_by"]
                )
        except page_content.PageContentUnavailable:
            # The row lock is released and preparation rolled back before
            # retrying with the same completed S3 mapping.
            _retry_page_asset(task, identity, "prepare", duplicated_assets, stage_retry)
        except Exception:
            # Includes permanent 400/413 and ambiguous database commit errors.
            _page_asset_failed(task, identity, "prepare", stage_retry)
        page_stage = "publish"
        stage_retry = 0

    try:
        # This stage cannot copy assets, transform content, or write the DB.
        # Read the latest durable state rather than queueing a large snapshot.
        entity = Page.objects.get(pk=identity["entity_identifier"])
        if not entity.description_binary:
            raise ValueError()
        page_content.publish_pat(entity.pk, entity.description_binary)
    except page_content.PageContentUnavailable:
        _retry_page_asset(task, identity, "publish", None, stage_retry)
    except Exception:
        _page_asset_failed(task, identity, "publish", stage_retry)


@shared_task(bind=True, max_retries=6)
def copy_s3_objects_of_description_and_assets(
    self,
    entity_name,
    entity_identifier,
    project_id,
    slug,
    user_id,
    *,
    page_stage="copy",
    duplicated_assets=None,
    stage_retry=0,
):
    """
    Step 1: Extract asset ids from the description_html of the entity
    Step 2: Duplicate the assets
    Step 3: Update the description_html of the entity with the new asset ids (change the src of img tag)
    Step 4: Request the live server to generate the description_binary and description for the entity

    """
    if entity_name == "PAGE":
        identity = dict(
            entity_name="PAGE",
            entity_identifier=str(entity_identifier),
            project_id=str(project_id),
            slug=slug,
            user_id=str(user_id),
        )
        return _copy_page_assets(self, identity, page_stage, duplicated_assets, stage_retry)

    try:
        model_class = {"PAGE": Page, "ISSUE": Issue}.get(entity_name)
        if not model_class:
            raise ValueError(f"Unsupported entity_name: {entity_name}")

        entity = model_class.objects.get(id=entity_identifier)
        asset_ids = extract_asset_ids(entity.description_html, "image-component")

        duplicated_assets = copy_assets(entity, entity_identifier, project_id, asset_ids, user_id)

        updated_html = update_description(entity, duplicated_assets, "image-component")

        external_data = sync_with_external_service(entity_name, updated_html)

        if external_data:
            entity.description_json = external_data.get("description_json")
            entity.description_binary = base64.b64decode(external_data.get("description_binary"))
            entity.save()

        return
    except Exception as e:
        log_exception(e)
        return []
