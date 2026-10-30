# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Offline tests for the project bundle's link rules (no database)."""

import unittest
from uuid import uuid4

from plane.utils.project_bundle import BundleRefused, _check_link_metadata, _native_links

SOURCE = dict(
    instance="https://source.invalid",
    workspace_slug="source",
    project_id=str(uuid4()),
    identifier="OBOT",
)
TARGET = dict(
    instance="https://target.invalid",
    workspace_slug="target",
    project_id=str(uuid4()),
    identifier="OBOT",
)
PAGE = str(uuid4())


def graph(**extra):
    rows = {"Page": [{"id": PAGE}], "Issue": [{"id": uuid4(), "sequence_id": 7}], "IssueLink": [], "ModuleLink": []}
    rows.update(extra)
    return rows


class NativeLinkTests(unittest.TestCase):
    def test_page_and_work_item_routes_follow_the_target_and_external_links_stay(self):
        page = f"{SOURCE['instance']}/source/projects/{SOURCE['project_id']}/pages/{PAGE}/"
        rows = graph(
            IssueLink=[
                {"id": "l1", "url": page},
                {"id": "l2", "url": "https://source.invalid/source/browse/OBOT-7/"},
                {"id": "l3", "url": "https://forge.example.invalid/org/repo/pulls/7"},
            ]
        )
        checks, values = _native_links(rows, SOURCE, TARGET)
        self.assertEqual(values[("IssueLink", "l1")], f"https://target.invalid/target/projects/{TARGET['project_id']}/pages/{PAGE}/")
        self.assertEqual(values[("IssueLink", "l2")], "https://target.invalid/target/browse/OBOT-7/")
        self.assertEqual(values[("IssueLink", "l3")], "https://forge.example.invalid/org/repo/pulls/7")
        self.assertEqual(
            [check["result"]["support"] for check in checks],
            ["native-page-url/1", "native-issue-url/1", "external-plain-url/1"],
        )

    def test_unknown_routes_work_items_and_pages_refuse(self):
        for url in [
            "https://source.invalid/source/browse/OBOT-8/",
            "https://source.invalid/source/browse/OTHER-7/",
            "https://source.invalid/other/browse/OBOT-7/",
            f"https://source.invalid/source/projects/{SOURCE['project_id']}/pages/{uuid4()}/",
            f"https://source.invalid/source/projects/{SOURCE['project_id']}/issues/",
            "https://source.invalid/source/browse/OBOT-7/?token=x",
        ]:
            with self.subTest(url), self.assertRaises(BundleRefused):
                _native_links(graph(IssueLink=[{"id": "l1", "url": url}]), SOURCE, TARGET)

    def test_link_activity_and_legacy_anchors_are_rewritten_exactly(self):
        html = (
            '<p><a href="https://source.invalid/source/browse/OBOT-7/">OBOT-7</a> and '
            f'<a href="https://source.invalid/source/projects/{SOURCE["project_id"]}/pages/{PAGE}/">Page</a> '
            'stay as text: source.invalid; <a href="https://forge.example.invalid/x">forge</a></p>'
        )
        rows = graph(
            Page=[{"id": PAGE, "description_html": html}],
            IssueActivity=[{"id": "a1", "field": "link", "old_value": None, "new_value": "https://source.invalid/source/browse/OBOT-7/"}],
        )
        checks, values = _native_links(rows, SOURCE, TARGET)
        self.assertEqual(values[("IssueActivity", "a1", "new_value")], "https://target.invalid/target/browse/OBOT-7/")
        self.assertEqual(
            values[("Page", PAGE, "description_html")],
            '<p><a href="https://target.invalid/target/browse/OBOT-7/">OBOT-7</a> and '
            f'<a href="https://target.invalid/target/projects/{TARGET["project_id"]}/pages/{PAGE}/">Page</a> '
            'stay as text: source.invalid; <a href="https://forge.example.invalid/x">forge</a></p>',
        )
        self.assertEqual(sum(check["field"] == "description_html#href" for check in checks), 2)
        with self.assertRaises(BundleRefused):
            _native_links(
                graph(Page=[{"id": PAGE, "description_html": '<a href="https://source.invalid/source/settings/">x</a>'}]),
                SOURCE,
                TARGET,
            )


class LinkMetadataTests(unittest.TestCase):
    def test_crawler_preview_is_accepted_only_in_its_own_shape(self):
        url = "https://source.invalid/source/browse/OBOT-7/"
        preview = {"url": url, "title": None, "favicon": "data:image/svg+xml;base64,AA==", "favicon_url": None}
        _check_link_metadata("IssueLink", {"url": url, "metadata": preview}, SOURCE["instance"])
        _check_link_metadata("IssueLink", {"url": url, "metadata": {}}, SOURCE["instance"])
        for change in [
            {"url": "https://elsewhere.invalid/"},
            {"favicon": "https://cdn.invalid/favicon.ico"},
            {"favicon_url": "http://cdn.invalid/favicon.ico"},
            {"title": "Moved from https://source.invalid/x"},
            {"future": "opaque"},
        ]:
            with self.subTest(change), self.assertRaises(BundleRefused):
                _check_link_metadata("IssueLink", {"url": url, "metadata": {**preview, **change}}, SOURCE["instance"])


if __name__ == "__main__":
    unittest.main()
