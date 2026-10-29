# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("db", "0122_alter_draftissue_assignees_alter_issue_assignees_and_more")]

    operations = [
        migrations.CreateModel(
            name="ProjectMigrationRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_instance", models.TextField()),
                ("source_workspace_id", models.UUIDField()),
                ("source_project_id", models.UUIDField()),
                ("target_instance", models.TextField()),
                ("freeze_id", models.UUIDField()),
                ("bundle_sha256", models.CharField(max_length=64)),
                ("intent_sha256", models.CharField(max_length=64)),
                ("receipt_sha256", models.CharField(max_length=64)),
                ("source_actor_map", models.JSONField()),
                ("receipt", models.JSONField()),
                ("completed_at", models.DateTimeField()),
                (
                    "target_workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="project_migration_runs",
                        to="db.workspace",
                    ),
                ),
                (
                    "target_project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="migration_runs", to="db.project"
                    ),
                ),
            ],
            options={
                "db_table": "project_migration_runs",
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "source_instance",
                            "source_workspace_id",
                            "source_project_id",
                            "target_instance",
                            "target_workspace",
                            "target_project",
                            "freeze_id",
                        ),
                        name="project_bundle_unique_source_target_freeze",
                    )
                ],
            },
        ),
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION plane_project_migration_receipt_immutable() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    RAISE EXCEPTION 'project migration receipts are immutable'
                        USING ERRCODE = 'check_violation';
                END $$;
                CREATE TRIGGER project_migration_receipt_immutable
                BEFORE UPDATE OR DELETE ON project_migration_runs
                FOR EACH ROW EXECUTE FUNCTION plane_project_migration_receipt_immutable();
            """,
            reverse_sql="""
                DROP TRIGGER project_migration_receipt_immutable ON project_migration_runs;
                DROP FUNCTION plane_project_migration_receipt_immutable();
            """,
        ),
    ]
