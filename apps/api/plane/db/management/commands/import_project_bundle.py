# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Local target-only native project import, explicitly approved by intent hash."""

from django.core.management.base import BaseCommand, CommandError
from plane.utils.project_bundle import (
    BundleRefused,
    canonical,
    import_project_snapshot,
    plan_project_import,
    publish_bundle_file,
    read_private_json,
    sha256,
)


class Command(BaseCommand):
    help = (
        "Target-only OBOT import. Dry-run first; approval is the exact intent SHA256. "
        "Identity-only documents and audited private objects only; unsupported "
        "references/history/storage refuse. No account creation or source deletion."
    )

    def add_arguments(self, parser):
        for name in (
            "database",
            "bundle",
            "target-instance",
            "target-workspace",
            "target-project",
            "target-identifier",
            "actor-map",
            "acting-admin",
            "output",
        ):
            parser.add_argument("--" + name, required=True)
        parser.add_argument("--baseline-receipt-sha256")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--approve-intent-sha256")

    def handle(self, **options):
        try:
            bundle = read_private_json(options["bundle"])
            mapping = read_private_json(options["actor_map"], limit=16 * 1024 * 1024)
            target = dict(
                instance=options["target_instance"],
                workspace_id=options["target_workspace"],
                project_id=options["target_project"],
                identifier=options["target_identifier"],
            )
            args = (options["database"], bundle, target, mapping, options["acting_admin"])
            if options["dry_run"]:
                result = plan_project_import(*args, baseline_receipt_sha256=options["baseline_receipt_sha256"])
            else:
                result = import_project_snapshot(
                    *args, options["approve_intent_sha256"], baseline_receipt_sha256=options["baseline_receipt_sha256"]
                )
            # A receipt file is a copy of the committed database receipt; it
            # never constitutes a separate filesystem completion marker.
            publish_bundle_file(options["output"], result)
        except BundleRefused as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            canonical(
                dict(format=result["format"], sha256=sha256(canonical(result)), source_removal_authorized=False)
            ).decode()
        )
