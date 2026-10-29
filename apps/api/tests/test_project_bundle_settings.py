# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import importlib
import os
import unittest


class ProjectBundleSettingsTest(unittest.TestCase):
    def test_migration_settings_are_private_and_audit_credentials_have_no_fallback(self):
        target = importlib.import_module("plane.settings.project_bundle")
        self.assertIsNone(target.AWS_DEFAULT_ACL)
        self.assertTrue(target.AWS_QUERYSTRING_AUTH)
        self.assertIsNone(target.AWS_S3_CUSTOM_DOMAIN)
        self.assertEqual(target.AWS_LOCATION, "")
        self.assertEqual(target.STORAGES["default"]["BACKEND"], "plane.settings.storage.S3Storage")
        self.assertEqual(target.PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID,
                         os.environ.get("PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID"))
        self.assertEqual(target.PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY,
                         os.environ.get("PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY"))
        if "PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID" not in os.environ:
            self.assertIsNone(target.PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID)
            self.assertIsNone(target.PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY)
        original = importlib.import_module("plane.settings.production")
        self.assertEqual(original.AWS_DEFAULT_ACL, "public-read")
        self.assertFalse(original.AWS_QUERYSTRING_AUTH)

    def test_configured_native_backend_keeps_the_selected_bucket_and_endpoint(self):
        from django.core.files.storage import storages
        from plane.settings.storage import S3Storage

        storage = storages["default"]
        self.assertIs(type(storage), S3Storage)
        self.assertEqual(storage.aws_storage_bucket_name, "synthetic-bucket")
        self.assertEqual(storage.s3_client.meta.endpoint_url, "http://object-store:9000")


if __name__ == "__main__":
    unittest.main()
