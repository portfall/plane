# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Real offline process-boundary tests; no Django, database or deployment env.

Run in an isolated Node/Python container with the built codec installed at
/code/project-bundle-codec.mjs. Fault-worker tests require a separate container
with the synthetic fault artifact at that same path and an explicit opt-in.
"""

import base64
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[2] / "plane/utils/project_bundle.py"
SPEC = importlib.util.spec_from_file_location("isolated_project_bundle", MODULE)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)

# Produced by the exact native editor's document HTML codec, not a Python fake.
BINARY = base64.b64decode(
    "AgOrkr3vBgAHAQdkZWZhdWx0AwlwYXJhZ3JhcGgHAKuSve8GAAYEAKuSve8GAQpFeGFjdCBib2R5"
    "BP6BqhkABwEFdGl0bGUDB2hlYWRpbmcHAP6BqhkABgQA/oGqGQEPRXhhY3QgdGl0bGUg6ZuqKAD+gaoZAAVsZXZlbAF9AQA="
)


def inspect(**overrides):
    values = dict(
        variant="document",
        source_origin="https://source.invalid",
        target_origin="https://target.invalid",
        references=[],
    )
    values.update(overrides)
    return bundle.inspect_native_document(BINARY, **values)


@unittest.skipUnless(
    os.environ.get("PLANE_RUN_PROJECT_BUNDLE_CODEC_TESTS") == "1"
    and os.environ.get("PLANE_BUNDLE_FAULT_FIXTURE") != "1",
    "Opt-in installed offline codec fixture",
)
class TestInstalledNativeCodec(unittest.TestCase):
    def test_exact_bytes_hash_and_codec_schema_are_verified_across_real_process(self):
        result = inspect()
        self.assertEqual(result["support"], "identity-only/1")
        self.assertEqual(base64.b64decode(result["binary"]), BINARY)
        self.assertEqual(result["before_sha256"], bundle.sha256(BINARY))
        self.assertEqual(result["after_sha256"], bundle.sha256(BINARY))
        self.assertEqual(result["census"]["roots"], ["default", "title"])

    def test_child_receives_no_poisoned_node_options_or_source_credentials(self):
        with patch.dict(
            os.environ,
            {
                "NODE_OPTIONS": "--this-synthetic-option-must-not-reach-node",
                "AWS_ACCESS_KEY_ID": "SYNTHETIC_NEVER_FORWARD",
            },
        ):
            self.assertEqual(inspect()["status"], "accepted")

    def test_native_refusal_has_only_a_bounded_diagnostic_code(self):
        with self.assertRaisesRegex(bundle.BundleRefused, "^UNKNOWN_ROOT$"):
            inspect(variant="rich")

    def test_size_limit_refuses_before_starting_a_process(self):
        with self.assertRaisesRegex(bundle.BundleRefused, "^LIMIT_EXCEEDED$"):
            bundle.inspect_native_document(
                b"x" * (bundle.MAX_DOCUMENT_BYTES + 1),
                variant="document",
                source_origin="https://source.invalid",
                target_origin="https://target.invalid",
                references=[],
            )


@unittest.skipUnless(os.environ.get("PLANE_BUNDLE_FAULT_FIXTURE") == "1", "Opt-in isolated fault artifact")
class TestInstalledInvalidCodec(unittest.TestCase):
    def test_corrupt_output_is_refused_without_reflecting_private_payload(self):
        with self.assertRaisesRegex(bundle.BundleRefused, "^INVALID_CODEC_RESPONSE$"):
            inspect(source_origin="https://corrupt.invalid")

    def test_stdout_has_a_hard_read_limit(self):
        with self.assertRaisesRegex(bundle.BundleRefused, "^CODEC_OUTPUT_LIMIT$"):
            inspect(source_origin="https://stdout.invalid")

    def test_stderr_has_a_hard_read_limit(self):
        with self.assertRaisesRegex(bundle.BundleRefused, "^CODEC_OUTPUT_LIMIT$"):
            inspect(source_origin="https://stderr.invalid")

    def test_child_wall_time_is_bounded(self):
        with self.assertRaisesRegex(bundle.BundleRefused, "^CODEC_TIMEOUT$"):
            inspect(source_origin="https://timeout.invalid")


if __name__ == "__main__":
    unittest.main()
