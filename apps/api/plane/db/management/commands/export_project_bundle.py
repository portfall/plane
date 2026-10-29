# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Local-only read-only export. No route, target connection, account or deletion."""

from django.core.management.base import BaseCommand, CommandError
from plane.utils.project_bundle import (
    BundleRefused,
    canonical,
    export_project_snapshot,
    publish_bundle_file,
    sha256,
)


class Command(BaseCommand):
    help = "Export one explicit OBOT project under an operator-supplied freeze assertion; unknown content refuses."

    def add_arguments(self, parser):
        for name in (
            "database",
            "source-instance",
            "source-workspace",
            "source-project",
            "source-identifier",
            "freeze-id",
            "frozen-at",
            "output",
        ):
            parser.add_argument("--" + name, required=True)

    def handle(self, **options):
        try:
            bundle = export_project_snapshot(
                options["database"],
                options["source_instance"],
                options["source_workspace"],
                options["source_project"],
                options["source_identifier"],
                dict(id=options["freeze_id"], frozen_at=options["frozen_at"]),
            )
            publish_bundle_file(options["output"], bundle)
        except BundleRefused as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            canonical(
                dict(format=bundle["format"], bundle_sha256=sha256(canonical(bundle)), source_removal_authorized=False)
            ).decode()
        )
