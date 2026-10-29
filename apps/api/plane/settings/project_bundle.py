# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Private target settings for the separately authorized project migration Job."""

import os

from .production import *  # noqa

AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_S3_CUSTOM_DOMAIN = None
AWS_LOCATION = ""
PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID = os.environ.get("PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID")
PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY = os.environ.get("PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY")
