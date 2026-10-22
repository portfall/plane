# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers
import base64
from uuid import uuid4
from django.db import transaction
from rest_framework.exceptions import PermissionDenied

# Module imports
from .base import BaseSerializer
from plane.utils import page_content
from plane.utils.content_validator import validate_html_content
from plane.db.models import (
    Page,
    PageLabel,
    Label,
    ProjectPage,
    Project,
    PageVersion,
)


class PageSerializer(BaseSerializer):
    is_favorite = serializers.BooleanField(read_only=True)
    labels = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(queryset=Label.objects.all()),
        write_only=True,
        required=False,
    )
    # Many to many
    label_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    project_ids = serializers.ListField(child=serializers.UUIDField(), required=False)

    class Meta:
        model = Page
        fields = [
            "id",
            "name",
            "owned_by",
            "access",
            "color",
            "labels",
            "parent",
            "is_favorite",
            "is_locked",
            "archived_at",
            "workspace",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "view_props",
            "logo_props",
            "label_ids",
            "project_ids",
        ]
        read_only_fields = ["workspace", "owned_by"]

    @transaction.atomic
    def create(self, validated_data):
        labels = validated_data.pop("labels", None)
        project_id = self.context["project_id"]
        owned_by_id = self.context["owned_by_id"]
        description_binary = self.context["description_binary"]
        description_html = self.context["description_html"]

        # Get the workspace id from the project
        project = Project.objects.get(pk=project_id)

        page_id = uuid4()
        if description_binary:
            incoming = PageBinaryUpdateSerializer(data={"description_binary": description_binary})
            incoming.is_valid(raise_exception=True)
            content = page_content.merge_snapshot(page_id, b"", incoming.validated_data["description_binary"])
        else:
            content = page_content.prepare_pat(
                page_id,
                persisted_binary=b"",
                previous_html=description_html,
                title=validated_data.get("name", ""),
                replacement_html=description_html,
                replace_title=False,
            )
        if self.context.get("preserve_exact_html"):
            content["description_html"] = description_html

        page = Page.objects.create(
            id=page_id,
            **validated_data,
            **content,
            owned_by_id=owned_by_id,
            workspace_id=project.workspace_id,
        )

        # Create the project page
        ProjectPage.objects.create(
            workspace_id=page.workspace_id,
            project_id=project_id,
            page_id=page.id,
            created_by_id=page.created_by_id,
            updated_by_id=page.updated_by_id,
        )

        # Create page labels
        if labels is not None:
            PageLabel.objects.bulk_create(
                [
                    PageLabel(
                        label=label,
                        page=page,
                        workspace_id=page.workspace_id,
                        created_by_id=page.created_by_id,
                        updated_by_id=page.updated_by_id,
                    )
                    for label in labels
                ],
                batch_size=10,
            )
        return page

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = Page.objects.select_for_update().get(pk=instance.pk)
        request = self.context.get("request")
        if request:
            if instance.access == Page.PRIVATE_ACCESS and instance.owned_by_id != request.user.id:
                raise PermissionDenied()
            if "access" in validated_data and instance.access != validated_data["access"]:
                if instance.owned_by_id != request.user.id:
                    raise serializers.ValidationError(
                        "Access cannot be updated since this page is owned by someone else"
                    )
        if instance.is_locked:
            raise serializers.ValidationError("Page is locked")

        self.content_needs_publish = False
        body_replacement = "description_html" in validated_data
        title_replacement = "name" in validated_data and (
            self.context.get("authoritative_title") or validated_data["name"] != instance.name
        )
        if body_replacement or title_replacement:
            replacement = validated_data.get("description_html")
            content = page_content.prepare_pat(
                instance.pk,
                persisted_binary=instance.description_binary,
                previous_html=instance.description_html,
                title=validated_data.get("name", instance.name),
                replacement_html=replacement,
                replace_title="name" in validated_data if body_replacement else title_replacement,
                preserve_loaded_title=not body_replacement and not self.context.get("authoritative_title", False),
            )
            if not body_replacement:
                page_content.retain_unchanged_html(instance, content)
            elif self.context.get("preserve_exact_html"):
                content["description_html"] = replacement
            validated_data.update(content)
            self.content_needs_publish = True

        labels = validated_data.pop("labels", None)
        if labels is not None:
            PageLabel.objects.filter(page=instance).delete()
            PageLabel.objects.bulk_create(
                [
                    PageLabel(
                        label=label,
                        page=instance,
                        workspace_id=instance.workspace_id,
                        created_by_id=instance.created_by_id,
                        updated_by_id=instance.updated_by_id,
                    )
                    for label in labels
                ],
                batch_size=10,
            )

        for field, value in validated_data.items():
            setattr(instance, field, value)
        fields = {
            field.name
            for field in instance._meta.concrete_fields
            if field.name in validated_data or field.attname in validated_data
        } | {"updated_at", "updated_by"}
        if "description_html" in fields:
            fields.add("description_stripped")
        instance.save(update_fields=fields)
        return instance


class PageDetailSerializer(PageSerializer):
    description_html = serializers.CharField(allow_blank=True, trim_whitespace=False)

    def validate_description_html(self, value):
        valid, error, sanitized = validate_html_content(value)
        if not valid:
            raise serializers.ValidationError(error)
        return sanitized if sanitized is not None else value

    class Meta(PageSerializer.Meta):
        fields = PageSerializer.Meta.fields + ["description_html"]


class PageVersionSerializer(BaseSerializer):
    class Meta:
        model = PageVersion
        fields = [
            "id",
            "workspace",
            "page",
            "last_saved_at",
            "owned_by",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "page"]


class PageVersionDetailSerializer(BaseSerializer):
    class Meta:
        model = PageVersion
        fields = [
            "id",
            "workspace",
            "page",
            "last_saved_at",
            "description_binary",
            "description_html",
            "description_json",
            "owned_by",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "page"]


class PageBinaryUpdateSerializer(serializers.Serializer):
    """Serializer for updating page binary description with validation"""

    description_binary = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=((page_content.MAX_CONTENT_BYTES + 2) // 3) * 4,
    )
    description_html = serializers.CharField(read_only=True)
    description_json = serializers.JSONField(read_only=True)

    def validate(self, attrs):
        if not attrs.get("description_binary"):
            raise serializers.ValidationError({"description_binary": "A non-empty document snapshot is required."})
        return attrs

    def validate_description_binary(self, value):
        """Validate the base64-encoded binary data"""
        if not value:
            return value

        try:
            # Decode the base64 data
            binary_data = base64.b64decode(value, validate=True)

            # Live validates actual Yjs structure. Text-pattern heuristics
            # reject valid documents containing code or URLs.
            if len(binary_data) > page_content.MAX_CONTENT_BYTES:
                raise serializers.ValidationError("Document exceeds the maximum size.")

            return binary_data
        except Exception as e:
            if isinstance(e, serializers.ValidationError):
                raise
            raise serializers.ValidationError("Failed to decode base64 data")

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = Page.objects.select_for_update().get(pk=instance.pk)
        if instance.is_locked or instance.archived_at:
            raise serializers.ValidationError("Page is locked or archived")
        request = self.context.get("request")
        if request and instance.access == Page.PRIVATE_ACCESS and instance.owned_by_id != request.user.id:
            raise PermissionDenied()
        self.previous_description_html = instance.description_html
        content = page_content.merge_snapshot(
            instance.pk, instance.description_binary, validated_data["description_binary"]
        )
        page_content.retain_unchanged_html(instance, content)
        for field, value in content.items():
            setattr(instance, field, value)
        instance.save(update_fields=[*page_content.CONTENT_FIELDS, "description_stripped", "updated_at", "updated_by"])
        return instance
