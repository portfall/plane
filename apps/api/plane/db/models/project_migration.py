# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Target-only, append-only project migration receipt; never source content."""

import uuid

from django.core.exceptions import ValidationError
from django.db import models


class ProjectMigrationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_instance = models.TextField()
    source_workspace_id = models.UUIDField()
    source_project_id = models.UUIDField()
    target_instance = models.TextField()
    target_workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.PROTECT, related_name="project_migration_runs"
    )
    target_project = models.ForeignKey("db.Project", on_delete=models.PROTECT, related_name="migration_runs")
    freeze_id = models.UUIDField()
    bundle_sha256 = models.CharField(max_length=64)
    intent_sha256 = models.CharField(max_length=64)
    receipt_sha256 = models.CharField(max_length=64)
    source_actor_map = models.JSONField()
    receipt = models.JSONField()
    completed_at = models.DateTimeField()

    class Meta:
        db_table = "project_migration_runs"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "source_instance",
                    "source_workspace_id",
                    "source_project_id",
                    "target_instance",
                    "target_workspace",
                    "target_project",
                    "freeze_id",
                ],
                name="project_bundle_unique_source_target_freeze",
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Project migration receipts are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Project migration receipts are immutable.")
