# Plane JavaScript dependency refresh

Checked 2026-10-28. Base: `ed63f2659994df6ccdd7e52a10a844dbfedc3a10`, branch `feat/portfall-node-dependency-refresh`. This is an uncommitted unit for caller integration, not a record of deployed state.

## Outcome

Production audit: **80 records → 1 moderate** over 1007 resolved dependencies. Full audit: **108 records → 1 moderate + 1 low** over 1880 dependencies. Neither has critical/high records. Both final audit commands still exit **1**; the remaining records are visible, not ignored. Tiptap's range includes a fixed official v2 release; deepmerge's upstream version carries the explicit local patch below. Registry records alone do not prove application exploitability.

The unit refreshes direct/transitive dependencies, keeps Tiptap on v2, patches one MIT library, and replaces Zod's removed `.errors` alias with `.issues` at exactly two controller sites. Existing repair/Page/CRDT assertions are unchanged. Five new tests cover actual dependency behavior and malformed conversion input. Root `package.json`, Node engine, and pnpm package-manager integrity pin are unchanged.

This checkout covers ed63. The caller identified historical web source `a85d78db295d3a5aa135a7d7755cc6e9bf902f5d` and frontend commits `5d156ed68a` and `a85d78db29`. The caller must preserve those later Mermaid viewer/centering features and integrate the separately frozen five-file codec unit, then rerun integrated tests/builds. This worker did not merge those commits or edit codec-owned paths.

## Dependency choices

The version tables below are exact lock inventories. All primary npm/upstream/advisory links were checked 2026-10-28.

- All catalogued Tiptap packages are 2.27.3. Core and PM overrides remove the old peer-resolved PM v3 copy. No v3 editor/schema migration was introduced.
- ProseMirror model is unified at 1.25.12. The first updated graph mixed 1.25.3 and 1.25.12, yielding incompatible Node/Slice/schema types. The override fixes the graph; removing only the task-generated shared `packages/typescript-config/.turbo/tsconfig.tsbuildinfo` cleared stale type paths. No editor source changes/casts were needed.
- Undici 7, fast-uri 3, js-yaml 4 and selector-parser 6/7 overrides stay branch-specific. No undici 6-to-7 override was added. KaTeX 0.16→0.18.2, TOML 4→5.0.1 and webpack-dev-middleware 6→7.4.6 are necessary transitive security upgrades; builds pass, but visual/Storybook acceptance remains separate.
- Official npm has no patched Zod v3. Live/shared-state use 4.6.7. The remaining first-pass v3 consumers, eslint-plugin-react-hooks 7.0.1 and zod-validation-error 4.0.2, explicitly accept `^3.25.0 || ^4.0.0`; the override selects their supported v4 alternative. The two controller substitutions preserve HTTP 400 and `message/context.validationErrors`; upstream validation wording can differ between majors.
- Mature Router 7.18.4, Storybook 10.6.2, qs 6.16.1 and baseline-browser-mapping 2.11.0 avoid fresh-tag exceptions. An experimental first resolution chose younger tags and pnpm automatically added loose-mode exemptions. Those tags/exemptions were discarded and resolution restarted from the original lock. The final workspace has no `minimumReleaseAgeExclude` block and frozen installation passes the normal policy.

## Remaining audit records

### Tiptap: official fixed v2, stale range

[GHSA-cp6q-959q-f8rh](https://github.com/advisories/GHSA-cp6q-959q-f8rh) lists `>=2.0.0-alpha.0 <3.30.4`. Official npm's v2-latest is 2.27.3; its [source at git head 0ed4a30a6cda23799a01881f67d01ad218a2ac6d](https://github.com/ueberdosis/tiptap/blob/0ed4a30a6cda23799a01881f67d01ad218a2ac6d/packages/core/src/utilities/mergeAttributes.ts) defines an own data property for `__proto__`, matching the v3 fix. Source and all three executable distributions were inspected, and the downloaded [official tarball](https://registry.npmjs.org/@tiptap/core/-/core-2.27.3.tgz) independently verified against npm integrity.

Before upgrading, the new test reproduced inherited `src`/`onerror`, and actual `@tiptap/html`/ProseMirror serialization emitted `<img class="first" src="invalid://canary" onerror="alert(1)">`. Now the same assertions pass: ordinary prototype, no inherited attacker fields, no injected HTML attributes, and normal class merging. This proves the library fix, not an exploit against every Plane input path or browser acceptance. `tiptap-v2-fix-evidence.json` retains source/executable excerpts and hashes; identities are appended below.

### deepmerge: backport, no fabricated release

[GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4) and [upstream issue #273](https://github.com/TehShrike/deepmerge/issues/273) concern return-object prototype poisoning. Audit metadata proposes `>=4.3.2`, but official npm has no such publication; latest is 4.3.1. GitHub's advisory says no patched release.

`patches/deepmerge@4.3.1.patch` backports the contributor fix in upstream [PR #274](https://github.com/TehShrike/deepmerge/pull/274), commit [c5ad573121574db1c625b0aabcc6114ed7df6c5a](https://github.com/cristianfloresRC/deepmerge/commit/c5ad573121574db1c625b0aabcc6114ed7df6c5a). It rejects `__proto__` in both copy loops in `index.js`, `dist/cjs.js` and `dist/umd.js`, marking the backport in each. This is a contributor patch, not a claimed upstream release; MIT license text is retained.

Patch SHA-256 and pnpm `patchedDependencies` hash are both `d3836103c6c78f7829b85509a7f71ee8b0ff4fa769b163d15ab740d9c3c0b1b0`. Every selected deepmerge snapshot uses that hash. The regression reproduced both target-copy and null-prototype-target poisoning on unpatched 4.3.1; now it passes while ordinary nested/array merges work. It resolves the actual remaining consumer: UI → Storybook React webpack → builder-webpack5 → fork-ts-checker-webpack-plugin. New sanitize-html removes deepmerge from production. The remaining full-audit low record is this patched dev-tool chain.

## Tooling and exact build recipe

All installs/builds use official `node:22.23.4-alpine3.24@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914`, Node 22.23.4, and pnpm 11.3.0 with the unchanged packageManager SHA-512 pin. Host Node 22.13.1 was not used. No host-global packages, credentials, registry writes or deployment operations were used.

Actual host runner: `/tmp/plane-node-deps-20261028/run`; pnpm shim: `/tmp/plane-node-deps-20261028/bin/pnpm`; evidence root: `/tmp/plane-node-deps-20261028/evidence/`. Task directories/config files were created before execution; npm configs are empty. Corepack shims were created only under `/task/bin` with `corepack enable --install-directory /task/bin`. The task pnpm shim is:

```sh
#!/bin/sh
exec corepack pnpm --config.store-dir=/task/store "$@"
```

The runner executes this Docker recipe (line breaks added):

```sh
env -i PATH=/usr/bin:/bin \
  DOCKER_CONFIG=/tmp/plane-node-deps-20261028/docker \
  docker run --rm -i --user 1000:1000 \
  -e PATH=/task/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  -e HOME=/task/home \
  -e NPM_CONFIG_USERCONFIG=/task/empty.npmrc \
  -e NPM_CONFIG_GLOBALCONFIG=/task/empty-global.npmrc \
  -e npm_config_registry=https://registry.npmjs.org \
  -e COREPACK_HOME=/task/corepack \
  -e pnpm_config_verify_deps_before_run=error \
  -e npm_config_store_dir=/task/store \
  -e CI=true -e HUSKY=0 \
  -v /tmp/plane-node-deps-20261028:/task \
  -v /home/rclod/code/github.com/rclod/portfall/.worktrees/plane-node-dependencies:/src \
  -w /src \
  node:22.23.4-alpine3.24@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 \
  sh -c '<command below>'
```

The shim explicitly selects the task store, while `pnpm_config_verify_deps_before_run=error` prevents automatic installation during verification. The exact final build command, including web, is:

```sh
pnpm install --frozen-lockfile
pnpm exec turbo run build --filter=live... --filter=web --filter=admin --filter=space --concurrency=2
```

The original-lock frontend baseline command was `pnpm exec turbo run build --filter=web --filter=admin --filter=space --concurrency=2`.

## Verification receipts

Log names below are relative to the evidence root. Both raw audit JSON results are retained; nonzero exits were not swallowed or promoted to clean scans.

| Check / command | Observed result | Evidence |
| --- | --- | --- |
| Original `pnpm install --frozen-lockfile` | PASS | `baseline-install.log` |
| Original `pnpm audit --prod --json` | Exit 1; 2 critical/38 high/29 moderate/11 low, 985 dependencies; matches supplied authoritative audit | `baseline-prod-audit.json` |
| Original `pnpm audit --json` | Exit 1; 5 critical/50 high/38 moderate/15 low, 1812 dependencies | `baseline-full-audit.json` |
| Original `pnpm exec turbo run build --filter=live... --concurrency=2` | PASS, 10 tasks, including editor/Live | `baseline-live-build.log` |
| Original frontend build command above | PASS, 13 tasks/7 cache hits; admin, space, web actually built | `baseline-frontends-build.log` |
| Original configured Live tests, excluding newly added security tests | PASS, 86 passed/2 intentionally skipped | `baseline-live-test-configured.log` |
| New four dependency tests on original graph | Expected RED: real Tiptap HTML injection, Zod walks 1000 rejected items despite `.max(2)`, two deepmerge poisoning cases | `security-red.log` |
| Unchanged malformed repair test after Zod4, before source substitutions | Expected RED: `undefined.map` at removed `.errors` | `zod-compat-red.log` |
| New malformed conversion test before source substitution | Expected RED: same removed alias in convert controller | `zod-convert-red.log` |
| Final nonfrozen resolution then frozen installation | PASS | `upgrade-install.log`, `frozen-install.log` |
| Final build command above | PASS, 16 tasks/11 cache hits; editor, Live, admin, space, web actually built | `final-build.log` |
| `pnpm --filter @plane/editor check:types`; `pnpm --filter live check:types` | Both PASS | `final-editor-types.log`, `final-live-types.log` |
| `API_BASE_URL=http://api.invalid LIVE_SERVER_SECRET_KEY=synthetic-test-only pnpm --filter live test` | PASS, 91 passed/2 intentionally skipped, 11 files | `final-live-test.log` |
| Native sharp create/resize/PNG encode/decode/metadata assertions | PASS; sharp 0.35.5/libvips 8.18.7, 8×12 PNG | `sharp-runtime.log` |
| `pnpm exec oxfmt --check` and `pnpm exec oxlint` on the two controllers and two added test files | PASS, zero lint warnings/errors | `format-check.log`, `lint-check.log` |
| Final `pnpm audit --prod --json`; `pnpm audit --json` | Both exit 1, residuals documented above | `final-prod-audit.json`, `final-full-audit.json`, `final-audit-exits.txt` |
| `pnpm peers check` | Exit 1, same three baseline peer warnings below | `peers.log` |

The original suite without environment values failed existing env validation; synthetic API URL/secret above resolves that runner prerequisite. The two skipped integration tests require a real Plane API/token; none was supplied. Initial tooling/cache failures were corrected, not counted as application failures or passing checks.

The existing ed63 Page/repair/history cases passed. The caller's separate codec/legacy/history freeze and later frontend commits need a fresh integrated run. These results do not establish browser behavior, visual Mermaid rendering, Storybook startup, live acceptance, NGINX/source-offer/proxy behavior or deployed data correctness.

## Remaining gates

Three `pnpm peers check` warnings are unchanged in the old/new lock: typography 0.5.19 lacks a resolved Tailwind peer; react-masonry-component 6.3.0 and react-color's react-popper 1.3.11 declare support only through React17, while the application uses React18.3.1. They were not hidden. Broad UI replacement is outside the approved two-site source adaptation. Successful builds do not establish vendor support for these historical combinations.

This is not artifact/license clearance. The graph changes 205 names/versions and introduces 18 transitive names; inventory follows. The caller owns final SBOM/license review, notices/source publication, source-to-image mapping, integration/review/commit/PR, root images, browser/live checks and deployment. Sharp/libvips LGPL remains a separate REVIEW item. ADR-0090 at handbook commit `7aca0d3` permits only the private internal Plane fork, not unrelated transitive licenses or customer distribution.


## Exact security and compatibility target versions

| Package / official metadata | Base graph | Refreshed graph |
| --- | --- | --- |
| [@react-router/dev](https://registry.npmjs.org/@react-router/dev) | 7.17.0 | 7.18.4 |
| [@react-router/node](https://registry.npmjs.org/@react-router/node) | 7.18.1 | 7.18.4 |
| [@react-router/serve](https://registry.npmjs.org/@react-router/serve) | 7.18.1 | 7.18.4 |
| [react-router](https://registry.npmjs.org/react-router) | 7.18.1 | 7.18.4 |
| [@tiptap/core](https://registry.npmjs.org/@tiptap/core) | 2.26.3 | 2.27.3 |
| [@tiptap/pm](https://registry.npmjs.org/@tiptap/pm) | 2.26.1, 3.6.6 | 2.27.3 |
| [axios](https://registry.npmjs.org/axios) | 1.18.1 | 1.21.0 |
| [dompurify](https://registry.npmjs.org/dompurify) | 3.4.15 | 3.4.17 |
| [sharp](https://registry.npmjs.org/sharp) | 0.35.3 | 0.35.5 |
| [sanitize-html](https://registry.npmjs.org/sanitize-html) | 2.17.5 | 2.18.1 |
| [prosemirror-view](https://registry.npmjs.org/prosemirror-view) | 1.40.0 | 1.42.8 |
| [prosemirror-model](https://registry.npmjs.org/prosemirror-model) | 1.25.3 | 1.25.12 |
| [undici](https://registry.npmjs.org/undici) | 7.29.0 | 7.30.1 |
| [fast-uri](https://registry.npmjs.org/fast-uri) | 3.1.5 | 3.1.9 |
| [qs](https://registry.npmjs.org/qs) | 6.15.2 | 6.16.1 |
| [nanoid](https://registry.npmjs.org/nanoid) | 3.3.8 | 3.3.20 |
| [proxy-addr](https://registry.npmjs.org/proxy-addr) | 2.0.7 | 2.0.8 |
| [compression](https://registry.npmjs.org/compression) | 1.8.1 | 1.8.2 |
| [markdown-it](https://registry.npmjs.org/markdown-it) | 14.2.0 | 14.3.3 |
| [brace-expansion](https://registry.npmjs.org/brace-expansion) | 5.0.7 | 5.0.12 |
| [katex](https://registry.npmjs.org/katex) | 0.16.47 | 0.18.2 |
| [source-map-js](https://registry.npmjs.org/source-map-js) | 1.2.1 | 1.2.2 |
| [follow-redirects](https://registry.npmjs.org/follow-redirects) | 1.16.0 | 1.16.2 |
| [body-parser](https://registry.npmjs.org/body-parser) | 1.20.6 | 1.20.9 |
| [morgan](https://registry.npmjs.org/morgan) | 1.11.0 | 1.12.1 |
| [zod](https://registry.npmjs.org/zod) | 3.25.76 | 4.6.7 |
| [vite](https://registry.npmjs.org/vite) | 8.0.16 | 8.1.6 |
| [vitest](https://registry.npmjs.org/vitest) | 4.1.8 | 4.1.12 |
| [@vitest/coverage-v8](https://registry.npmjs.org/@vitest/coverage-v8) | 4.1.8 | 4.1.12 |
| [storybook](https://registry.npmjs.org/storybook) | 10.4.6 | 10.6.2 |
| [js-yaml](https://registry.npmjs.org/js-yaml) | 4.3.0 | 4.3.2 |
| [browserslist](https://registry.npmjs.org/browserslist) | 4.28.1 | 4.29.5 |
| [baseline-browser-mapping](https://registry.npmjs.org/baseline-browser-mapping) | 2.9.19 | 2.11.0 |
| [tinypool](https://registry.npmjs.org/tinypool) | 2.1.0 | 2.2.0 |
| [toml](https://registry.npmjs.org/toml) | 4.1.1 | 5.0.1 |
| [postcss-selector-parser](https://registry.npmjs.org/postcss-selector-parser) | 6.0.10, 6.1.2, 7.1.0 | 6.1.5, 7.1.7 |
| [serialize-javascript](https://registry.npmjs.org/serialize-javascript) | 7.0.5 | absent |
| [webpack-dev-middleware](https://registry.npmjs.org/webpack-dev-middleware) | 6.1.3 | 7.4.6 |
| [deepmerge](https://registry.npmjs.org/deepmerge) | 4.3.1 | 4.3.1 + hash-bound patch |

## Tiptap official package identities

- npm integrity, recomputed: `sha512-a5LfRbLpfaGI3hbL/LPHUYHI0I+FQHdSHsy8L4YnVIuu3hXcm3QZgkWbpEGf8hCz8krk6zEiu0+iFOjTySU2FA==`.
- Tarball SHA-256: `a0ec99121a9f7e1dd8d476926596bb79423bff64860d3b6a230e6ed682398a7c`.
- `src/utilities/mergeAttributes.ts` SHA-256: `3bd0c4db0aeb05f6d1c12e6e22cb3c4ec0704f2b85168bfcf578c2c0840708ce`.
- `dist/index.js` SHA-256: `bad8f5a54dfabd943298de64dac3aa716ca0d37fb65a07ce11ff71841f2d4b6a`.
- `dist/index.cjs` SHA-256: `95efb562d6b684daa8b423f850cf565b24193479e53f8b074b2cc578b2b86c69`.
- `dist/index.umd.js` SHA-256: `112ad205852419772ce962d0e9824b1eeafa0a9915abfa73fe0170fc48b50997`.

## Complete baseline advisory disposition

All 108 full-audit records are retained here, including the production subset. Ranges are verbatim registry metadata; full registry-reported finding paths remain in the raw JSON. “Upgraded” means the affected versions are absent from the final lock, not that an application exploit was established.

| Package | Severity | Affected range | Registry patched range | Primary advisory | Final disposition |
| --- | --- | --- | --- | --- | --- |
| brace-expansion | high | `>=4.0.0 <5.0.8` | `>=5.0.8` | [GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg) | Upgraded: 5.0.12 |
| brace-expansion | high | `>=4.0.0 <5.0.9` | `>=5.0.9` | [GHSA-rgw5-rvv9-x895](https://github.com/advisories/GHSA-rgw5-rvv9-x895) | Upgraded: 5.0.12 |
| js-yaml | high | `>=4.0.0 <4.3.1` | `>=4.3.1` | [GHSA-5p4m-2wfm-xmqj](https://github.com/advisories/GHSA-5p4m-2wfm-xmqj) | Upgraded: 4.3.2 |
| react-router | high | `>=7.12.0 <7.18.2` | `>=7.18.2` | [GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2) | Upgraded: 7.18.4 |
| nanoid | high | `<3.3.16` | `>=3.3.16` | [GHSA-28wg-ghj8-5hjv](https://github.com/advisories/GHSA-28wg-ghj8-5hjv) | Upgraded: 3.3.20 |
| nanoid | high | `<3.3.18` | `>=3.3.18` | [GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8) | Upgraded: 3.3.20 |
| postcss-selector-parser | low | `>=7.1.0 <7.1.3` | `>=7.1.3` | [GHSA-w9m9-85wc-3x92](https://github.com/advisories/GHSA-w9m9-85wc-3x92) | Upgraded: 6.1.5, 7.1.7 |
| postcss-selector-parser | low | `>=6.1.0 <6.1.3` | `>=6.1.3` | [GHSA-w9m9-85wc-3x92](https://github.com/advisories/GHSA-w9m9-85wc-3x92) | Upgraded: 6.1.5, 7.1.7 |
| browserslist | high | `<=4.28.6` | `>=4.28.7` | [GHSA-c83g-rgw3-j3cx](https://github.com/advisories/GHSA-c83g-rgw3-j3cx) | Upgraded: 4.29.5 |
| browserslist | high | `<=4.28.6` | `>=4.28.7` | [GHSA-73wf-gq98-2v4g](https://github.com/advisories/GHSA-73wf-gq98-2v4g) | Upgraded: 4.29.5 |
| nanoid | high | `<3.3.12` | `>=3.3.12` | [GHSA-xwg4-73v4-xw9w](https://github.com/advisories/GHSA-xwg4-73v4-xw9w) | Upgraded: 3.3.20 |
| sanitize-html | moderate | `>=1.9.0 <=2.17.6` | `>=2.17.7` | [GHSA-g8qq-57p8-ggw5](https://github.com/advisories/GHSA-g8qq-57p8-ggw5) | Upgraded: 2.18.1 |
| qs | moderate | `>=6.14.2 <=6.15.3` | `>=6.15.4` | [GHSA-x5fp-wj9c-mxmx](https://github.com/advisories/GHSA-x5fp-wj9c-mxmx) | Upgraded: 6.16.1 |
| qs | moderate | `>=2.2.5 <6.16.0` | `>=6.16.0` | [GHSA-4mjr-xmp4-gh2g](https://github.com/advisories/GHSA-4mjr-xmp4-gh2g) | Upgraded: 6.16.1 |
| @tiptap/core | moderate | `>=2.0.0-alpha.0 <3.30.4` | `>=3.30.4` | [GHSA-cp6q-959q-f8rh](https://github.com/advisories/GHSA-cp6q-959q-f8rh) | Official 2.27.3 fix verified; stale range remains |
| fast-uri | high | `>=3.1.3 <3.1.6` | `>=3.1.6` | [GHSA-5jgf-p345-68v8](https://github.com/advisories/GHSA-5jgf-p345-68v8) | Upgraded: 3.1.9 |
| fast-uri | high | `>=3.0.0 <3.1.6` | `>=3.1.6` | [GHSA-f65p-4m7j-42xc](https://github.com/advisories/GHSA-f65p-4m7j-42xc) | Upgraded: 3.1.9 |
| fast-uri | high | `>=3.1.2 <3.1.6` | `>=3.1.6` | [GHSA-fph4-wmhf-6fwf](https://github.com/advisories/GHSA-fph4-wmhf-6fwf) | Upgraded: 3.1.9 |
| fast-uri | high | `>=3.0.0 <3.1.6` | `>=3.1.6` | [GHSA-jqff-g426-hqxp](https://github.com/advisories/GHSA-jqff-g426-hqxp) | Upgraded: 3.1.9 |
| sanitize-html | moderate | `<=2.17.5` | `>=2.17.6` | [GHSA-jxwj-j7wr-gfrw](https://github.com/advisories/GHSA-jxwj-j7wr-gfrw) | Upgraded: 2.18.1 |
| toml | high | `<4.2.0` | `>=4.2.0` | [GHSA-82x6-q7mm-w9cf](https://github.com/advisories/GHSA-82x6-q7mm-w9cf) | Upgraded: 5.0.1 |
| toml | high | `<4.1.2` | `>=4.1.2` | [GHSA-v5mp-jgw5-2x6j](https://github.com/advisories/GHSA-v5mp-jgw5-2x6j) | Upgraded: 5.0.1 |
| vitest | moderate | `>=2.1.0 <4.1.11` | `>=4.1.11` | [GHSA-82fw-gwwq-j7x9](https://github.com/advisories/GHSA-82fw-gwwq-j7x9) | Upgraded: 4.1.12 |
| @vitest/mocker | moderate | `>=2.1.0 <4.1.11` | `>=4.1.11` | [GHSA-82fw-gwwq-j7x9](https://github.com/advisories/GHSA-82fw-gwwq-j7x9) | Upgraded: 4.1.12 |
| baseline-browser-mapping | moderate | `>=2.0.0 <2.11.0` | `>=2.11.0` | [GHSA-w5vr-8v7q-w6rv](https://github.com/advisories/GHSA-w5vr-8v7q-w6rv) | Upgraded: 2.11.0 |
| sharp | high | `<0.35.4` | `>=0.35.4` | [GHSA-rgj7-g3m4-5g8c](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c) | Upgraded: 0.35.5 |
| js-yaml | high | `>=4.0.0 <4.3.2` | `>=4.3.2` | [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh) | Upgraded: 4.3.2 |
| morgan | moderate | `<1.12.0` | `>=1.12.0` | [GHSA-jxfw-x594-9x9m](https://github.com/advisories/GHSA-jxfw-x594-9x9m) | Upgraded: 1.12.1 |
| undici | moderate | `>=7.28.0 <7.29.1` | `>=7.29.1` | [GHSA-3wwx-pv8p-q78v](https://github.com/advisories/GHSA-3wwx-pv8p-q78v) | Upgraded: 7.30.1 |
| morgan | moderate | `<1.12.1` | `>=1.12.1` | [GHSA-9f6g-j8ch-79g4](https://github.com/advisories/GHSA-9f6g-j8ch-79g4) | Upgraded: 1.12.1 |
| fast-uri | high | `>=3.0.0 <3.1.7` | `>=3.1.7` | [GHSA-qw65-cvwx-89v3](https://github.com/advisories/GHSA-qw65-cvwx-89v3) | Upgraded: 3.1.9 |
| webpack-dev-middleware | high | `<7.4.5` | `>=7.4.5` | [GHSA-g84c-rxfj-3j2c](https://github.com/advisories/GHSA-g84c-rxfj-3j2c) | Upgraded: 7.4.6 |
| undici | moderate | `>=7.11.0 <7.29.1` | `>=7.29.1` | [GHSA-pmjh-fq2x-6v4x](https://github.com/advisories/GHSA-pmjh-fq2x-6v4x) | Upgraded: 7.30.1 |
| undici | low | `>=7.0.0 <7.29.1` | `>=7.29.1` | [GHSA-r53p-7pc4-xj5r](https://github.com/advisories/GHSA-r53p-7pc4-xj5r) | Upgraded: 7.30.1 |
| undici | high | `>=7.0.0 <7.29.1` | `>=7.29.1` | [GHSA-rfgv-xxqx-mfg5](https://github.com/advisories/GHSA-rfgv-xxqx-mfg5) | Upgraded: 7.30.1 |
| undici | moderate | `>=7.15.0 <7.29.1` | `>=7.29.1` | [GHSA-3xpg-4rpp-hhhm](https://github.com/advisories/GHSA-3xpg-4rpp-hhhm) | Upgraded: 7.30.1 |
| undici | moderate | `>=7.0.0 <7.29.1` | `>=7.29.1` | [GHSA-2jfj-6hjv-fm6j](https://github.com/advisories/GHSA-2jfj-6hjv-fm6j) | Upgraded: 7.30.1 |
| undici | low | `>=7.1.0 <7.29.1` | `>=7.29.1` | [GHSA-2gqq-gqf2-x968](https://github.com/advisories/GHSA-2gqq-gqf2-x968) | Upgraded: 7.30.1 |
| undici | high | `>=7.24.1 <7.29.1` | `>=7.29.1` | [GHSA-w293-vg96-wgc3](https://github.com/advisories/GHSA-w293-vg96-wgc3) | Upgraded: 7.30.1 |
| undici | low | `>=7.0.0 <7.29.1` | `>=7.29.1` | [GHSA-8436-99hf-9mmv](https://github.com/advisories/GHSA-8436-99hf-9mmv) | Upgraded: 7.30.1 |
| undici | moderate | `>=7.0.0 <7.29.1` | `>=7.29.1` | [GHSA-rx4f-c7p8-82vq](https://github.com/advisories/GHSA-rx4f-c7p8-82vq) | Upgraded: 7.30.1 |
| fast-uri | moderate | `>=3.0.0 <3.1.8` | `>=3.1.8` | [GHSA-hrr3-gc8f-f4qj](https://github.com/advisories/GHSA-hrr3-gc8f-f4qj) | Upgraded: 3.1.9 |
| brace-expansion | moderate | `>=4.0.0 <5.0.12` | `>=5.0.12` | [GHSA-q2hr-2g5m-vwhr](https://github.com/advisories/GHSA-q2hr-2g5m-vwhr) | Upgraded: 5.0.12 |
| brace-expansion | high | `>=4.0.0 <5.0.11` | `>=5.0.11` | [GHSA-qhr7-859c-m2p7](https://github.com/advisories/GHSA-qhr7-859c-m2p7) | Upgraded: 5.0.12 |
| brace-expansion | high | `>=4.0.0 <5.0.10` | `>=5.0.10` | [GHSA-6j4f-fj2g-mc7p](https://github.com/advisories/GHSA-6j4f-fj2g-mc7p) | Upgraded: 5.0.12 |
| axios | moderate | `>=1.7.0 <1.20.0` | `>=1.20.0` | [GHSA-vh66-26gq-q6x8](https://github.com/advisories/GHSA-vh66-26gq-q6x8) | Upgraded: 1.21.0 |
| axios | moderate | `>=1.0.0 <1.20.0` | `>=1.20.0` | [GHSA-9fr6-4gfg-395g](https://github.com/advisories/GHSA-9fr6-4gfg-395g) | Upgraded: 1.21.0 |
| axios | high | `>=1.16.1 <1.20.0` | `>=1.20.0` | [GHSA-c29m-xwm3-cm6r](https://github.com/advisories/GHSA-c29m-xwm3-cm6r) | Upgraded: 1.21.0 |
| axios | high | `>=1.15.0 <1.20.0` | `>=1.20.0` | [GHSA-mghh-pgcx-3jjj](https://github.com/advisories/GHSA-mghh-pgcx-3jjj) | Upgraded: 1.21.0 |
| axios | high | `>=1.15.1 <1.20.0` | `>=1.20.0` | [GHSA-x97p-jq2g-jp4f](https://github.com/advisories/GHSA-x97p-jq2g-jp4f) | Upgraded: 1.21.0 |
| axios | high | `>=1.13.0 <1.20.0` | `>=1.20.0` | [GHSA-3pq3-5fj3-cg6v](https://github.com/advisories/GHSA-3pq3-5fj3-cg6v) | Upgraded: 1.21.0 |
| axios | high | `>=1.13.0 <1.20.0` | `>=1.20.0` | [GHSA-542g-h47m-68v8](https://github.com/advisories/GHSA-542g-h47m-68v8) | Upgraded: 1.21.0 |
| dompurify | low | `>=3.4.13 <=3.4.15` | `>=3.4.16` | [GHSA-p98j-92pf-mc4p](https://github.com/advisories/GHSA-p98j-92pf-mc4p) | Upgraded: 3.4.17 |
| axios | moderate | `>=1.0.0 <1.20.0` | `>=1.20.0` | [GHSA-j8rh-479h-cp32](https://github.com/advisories/GHSA-j8rh-479h-cp32) | Upgraded: 1.21.0 |
| axios | moderate | `>=1.12.0 <1.20.0` | `>=1.20.0` | [GHSA-4hqw-qxg8-jxx2](https://github.com/advisories/GHSA-4hqw-qxg8-jxx2) | Upgraded: 1.21.0 |
| axios | high | `>=1.15.2 <1.20.0` | `>=1.20.0` | [GHSA-m8m8-qj5v-23w3](https://github.com/advisories/GHSA-m8m8-qj5v-23w3) | Upgraded: 1.21.0 |
| axios | moderate | `>=1.15.0 <1.20.0` | `>=1.20.0` | [GHSA-44g4-m2mj-wpvx](https://github.com/advisories/GHSA-44g4-m2mj-wpvx) | Upgraded: 1.21.0 |
| axios | high | `>=1.17.0 <1.20.0` | `>=1.20.0` | [GHSA-r4gj-5m52-g5wh](https://github.com/advisories/GHSA-r4gj-5m52-g5wh) | Upgraded: 1.21.0 |
| dompurify | low | `<=3.4.15` | `>=3.4.16` | [GHSA-6688-9rhm-gjv2](https://github.com/advisories/GHSA-6688-9rhm-gjv2) | Upgraded: 3.4.17 |
| katex | low | `>=0.11.0 <0.18.2` | `>=0.18.2` | [GHSA-238p-pmpm-9mq7](https://github.com/advisories/GHSA-238p-pmpm-9mq7) | Upgraded: 0.18.2 |
| source-map-js | high | `>=1.0.0 <1.2.2` | `>=1.2.2` | [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q) | Upgraded: 1.2.2 |
| proxy-addr | critical | `>=1.1.0 <2.0.8` | `>=2.0.8` | [GHSA-jqcg-44mw-7w3h](https://github.com/advisories/GHSA-jqcg-44mw-7w3h) | Upgraded: 2.0.8 |
| compression | high | `<1.8.2` | `>=1.8.2` | [GHSA-vc2v-76pw-4v95](https://github.com/advisories/GHSA-vc2v-76pw-4v95) | Upgraded: 1.8.2 |
| tinypool | critical | `<=2.1.0` | `>=2.1.1` | [GHSA-5gmw-xhrv-c9v3](https://github.com/advisories/GHSA-5gmw-xhrv-c9v3) | Upgraded: 2.2.0 |
| tinypool | critical | `<2.1.2` | `>=2.1.2` | [GHSA-85c8-ppgw-ccpr](https://github.com/advisories/GHSA-85c8-ppgw-ccpr) | Upgraded: 2.2.0 |
| prosemirror-view | high | `<1.42.3` | `>=1.42.3` | [GHSA-c8x8-7fp4-3x9w](https://github.com/advisories/GHSA-c8x8-7fp4-3x9w) | Upgraded: 1.42.8 |
| sharp | high | `<0.35.5` | `>=0.35.5` | [GHSA-wq5f-xc86-pv6w](https://github.com/advisories/GHSA-wq5f-xc86-pv6w) | Upgraded: 0.35.5 |
| markdown-it | high | `<14.3.2` | `>=14.3.2` | [GHSA-r7fv-28h4-cvq7](https://github.com/advisories/GHSA-r7fv-28h4-cvq7) | Upgraded: 14.3.3 |
| follow-redirects | high | `>=1.16.0 <1.16.1` | `>=1.16.1` | [GHSA-q3jh-wrc6-jgq8](https://github.com/advisories/GHSA-q3jh-wrc6-jgq8) | Upgraded: 1.16.2 |
| follow-redirects | high | `>=1.16.0 <1.16.1` | `>=1.16.1` | [GHSA-8r9p-f939-6h3c](https://github.com/advisories/GHSA-8r9p-f939-6h3c) | Upgraded: 1.16.2 |
| sanitize-html | moderate | `<2.18.0` | `>=2.18.0` | [GHSA-x3q4-9hxx-gx8m](https://github.com/advisories/GHSA-x3q4-9hxx-gx8m) | Upgraded: 2.18.1 |
| vite | moderate | `>=8.0.0 <=8.1.5` | `>=8.1.6` | [GHSA-698v-4r3x-3vcv](https://github.com/advisories/GHSA-698v-4r3x-3vcv) | Upgraded: 8.1.6 |
| sanitize-html | moderate | `<2.18.0` | `>=2.18.0` | [GHSA-cv27-6wvh-8x7j](https://github.com/advisories/GHSA-cv27-6wvh-8x7j) | Upgraded: 2.18.1 |
| markdown-it | low | `<14.3.3` | `>=14.3.3` | [GHSA-vfgh-jcmw-g37q](https://github.com/advisories/GHSA-vfgh-jcmw-g37q) | Upgraded: 14.3.3 |
| vite | moderate | `>=8.0.0 <=8.1.5` | `>=8.1.6` | [GHSA-r825-w8mm-5pxw](https://github.com/advisories/GHSA-r825-w8mm-5pxw) | Upgraded: 8.1.6 |
| vite | high | `>=8.0.0 <=8.1.5` | `>=8.1.6` | [GHSA-587h-7wmj-h5pm](https://github.com/advisories/GHSA-587h-7wmj-h5pm) | Upgraded: 8.1.6 |
| markdown-it | high | `>=14.1.0 <14.3.3` | `>=14.3.3` | [GHSA-gfr2-rv44-r23c](https://github.com/advisories/GHSA-gfr2-rv44-r23c) | Upgraded: 14.3.3 |
| vite | low | `>=8.0.0 <=8.1.5` | `>=8.1.6` | [GHSA-9jrq-w75r-8gcw](https://github.com/advisories/GHSA-9jrq-w75r-8gcw) | Upgraded: 8.1.6 |
| vite | moderate | `>=8.0.0 <=8.1.5` | `>=8.1.6` | [GHSA-rq7h-c2jc-7f22](https://github.com/advisories/GHSA-rq7h-c2jc-7f22) | Upgraded: 8.1.6 |
| storybook | critical | `>=10.0.0 <10.6.2` | `>=10.6.2` | [GHSA-f2ph-cmg5-wxc8](https://github.com/advisories/GHSA-f2ph-cmg5-wxc8) | Upgraded: 10.6.2 |
| @vitest/mocker | moderate | `>=2.1.0-beta.7 <4.1.12` | `>=4.1.12` | [GHSA-qxg6-89mg-rqv8](https://github.com/advisories/GHSA-qxg6-89mg-rqv8) | Upgraded: 4.1.12 |
| toml | high | `<=5.0.0` | `>=5.0.1` | [GHSA-4qx8-w7c3-pj3p](https://github.com/advisories/GHSA-4qx8-w7c3-pj3p) | Upgraded: 5.0.1 |
| toml | high | `<=5.0.0` | `>=5.0.1` | [GHSA-9j65-hcg3-gc64](https://github.com/advisories/GHSA-9j65-hcg3-gc64) | Upgraded: 5.0.1 |
| toml | high | `<=5.0.0` | `>=5.0.1` | [GHSA-9qmh-m7c6-f92p](https://github.com/advisories/GHSA-9qmh-m7c6-f92p) | Upgraded: 5.0.1 |
| storybook | high | `>=10.0.0 <10.6.2` | `>=10.6.2` | [GHSA-px85-f4q5-jh77](https://github.com/advisories/GHSA-px85-f4q5-jh77) | Upgraded: 10.6.2 |
| dompurify | critical | `>=3.4.4 <=3.4.16` | `>=3.4.17` | [GHSA-cg2g-xh64-v4q7](https://github.com/advisories/GHSA-cg2g-xh64-v4q7) | Upgraded: 3.4.17 |
| dompurify | high | `<=3.4.16` | `>=3.4.17` | [GHSA-mrv2-3pqr-j5jr](https://github.com/advisories/GHSA-mrv2-3pqr-j5jr) | Upgraded: 3.4.17 |
| dompurify | low | `<=3.4.16` | `>=3.4.17` | [GHSA-c6gr-qvv4-5v3f](https://github.com/advisories/GHSA-c6gr-qvv4-5v3f) | Upgraded: 3.4.17 |
| markdown-it | moderate | `<14.3.1` | `>=14.3.1` | [GHSA-253c-mchw-3w2r](https://github.com/advisories/GHSA-253c-mchw-3w2r) | Upgraded: 14.3.3 |
| zod | high | `<4.6.6` | `>=4.6.6` | [GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5) | Upgraded: 4.6.7 |
| body-parser | high | `<1.20.9` | `>=1.20.9` | [GHSA-27r3-jxmh-p828](https://github.com/advisories/GHSA-27r3-jxmh-p828) | Upgraded: 1.20.9 |
| undici | moderate | `>=7.0.0 <7.30.1` | `>=7.30.1` | [GHSA-4qv8-2p9j-2685](https://github.com/advisories/GHSA-4qv8-2p9j-2685) | Upgraded: 7.30.1 |
| undici | moderate | `>=7.15.0 <7.30.1` | `>=7.30.1` | [GHSA-f72v-6w45-q472](https://github.com/advisories/GHSA-f72v-6w45-q472) | Upgraded: 7.30.1 |
| serialize-javascript | low | `<7.1.3` | `>=7.1.3` | [GHSA-m5mm-p8h7-m29v](https://github.com/advisories/GHSA-m5mm-p8h7-m29v) | Removed from graph |
| fast-uri | moderate | `>=3.0.0 <3.1.9` | `>=3.1.9` | [GHSA-w74x-v6f9-j3xg](https://github.com/advisories/GHSA-w74x-v6f9-j3xg) | Upgraded: 3.1.9 |
| fast-uri | low | `>=3.0.0 <3.1.9` | `>=3.1.9` | [GHSA-xhq7-xmqj-769h](https://github.com/advisories/GHSA-xhq7-xmqj-769h) | Upgraded: 3.1.9 |
| fast-uri | moderate | `>=3.1.3 <3.1.9` | `>=3.1.9` | [GHSA-wjr9-j795-jxcr](https://github.com/advisories/GHSA-wjr9-j795-jxcr) | Upgraded: 3.1.9 |
| fast-uri | low | `>=3.0.0 <3.1.9` | `>=3.1.9` | [GHSA-333x-w4r6-6crp](https://github.com/advisories/GHSA-333x-w4r6-6crp) | Upgraded: 3.1.9 |
| undici | high | `>=7.18.0 <7.30.1` | `>=7.30.1` | [GHSA-xjj6-f33q-929c](https://github.com/advisories/GHSA-xjj6-f33q-929c) | Upgraded: 7.30.1 |
| postcss-selector-parser | moderate | `<6.1.5` | `>=6.1.5` | [GHSA-rj75-hqrm-r3gf](https://github.com/advisories/GHSA-rj75-hqrm-r3gf) | Upgraded: 6.1.5, 7.1.7 |
| postcss-selector-parser | moderate | `>=7.0.0 <7.1.6` | `>=7.1.6` | [GHSA-rj75-hqrm-r3gf](https://github.com/advisories/GHSA-rj75-hqrm-r3gf) | Upgraded: 6.1.5, 7.1.7 |
| qs | moderate | `>=6.7.0 <=6.16.0` | `>=6.16.1` | [GHSA-6pq8-jhm2-2vcw](https://github.com/advisories/GHSA-6pq8-jhm2-2vcw) | Upgraded: 6.16.1 |
| undici | high | `>=7.0.0 <7.30.1` | `>=7.30.1` | [GHSA-4w7w-5xrc-9h26](https://github.com/advisories/GHSA-4w7w-5xrc-9h26) | Upgraded: 7.30.1 |
| undici | high | `>=7.0.0 <7.30.1` | `>=7.30.1` | [GHSA-2m5v-pch6-74hx](https://github.com/advisories/GHSA-2m5v-pch6-74hx) | Upgraded: 7.30.1 |
| undici | high | `>=7.0.0 <7.30.1` | `>=7.30.1` | [GHSA-qrxm-fqhp-jwvm](https://github.com/advisories/GHSA-qrxm-fqhp-jwvm) | Upgraded: 7.30.1 |
| undici | moderate | `>=7.0.0 <7.30.1` | `>=7.30.1` | [GHSA-9fm3-97px-rjf6](https://github.com/advisories/GHSA-9fm3-97px-rjf6) | Upgraded: 7.30.1 |
| undici | moderate | `>=7.15.0 <7.30.1` | `>=7.30.1` | [GHSA-55wc-3pg8-jfw6](https://github.com/advisories/GHSA-55wc-3pg8-jfw6) | Upgraded: 7.30.1 |
| deepmerge | low | `>=4.2.0 <=4.3.1` | `>=4.3.2` | [GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4) | 4.3.1 + bound patch; full-audit low remains |

## Complete graph version changes

This inventory is not transitive license approval. Integrity and peer-qualified snapshots remain in `pnpm-lock.yaml`. The matching machine-readable inventory is `graph-version-changes.json` in the evidence root.

| Package / official metadata | Base versions | Refreshed versions |
| --- | --- | --- |
| [@babel/helper-globals](https://registry.npmjs.org/@babel/helper-globals) | 7.28.0, 7.29.7 | 7.29.7 |
| [@babel/traverse](https://registry.npmjs.org/@babel/traverse) | 7.28.4, 7.29.7 | 7.29.7 |
| [@emnapi/core](https://registry.npmjs.org/@emnapi/core) | 1.10.0, 1.7.1, 1.9.2 | 1.10.0, 1.11.0, 1.11.1, 1.9.2 |
| [@emnapi/runtime](https://registry.npmjs.org/@emnapi/runtime) | 1.10.0, 1.11.3, 1.7.1, 1.9.2 | 1.10.0, 1.11.0, 1.11.1, 1.11.3, 1.9.2 |
| [@emnapi/wasi-threads](https://registry.npmjs.org/@emnapi/wasi-threads) | 1.1.0, 1.2.1 | 1.2.1, 1.2.2 |
| [@img/sharp-darwin-arm64](https://registry.npmjs.org/@img/sharp-darwin-arm64) | 0.35.3 | 0.35.5 |
| [@img/sharp-darwin-x64](https://registry.npmjs.org/@img/sharp-darwin-x64) | 0.35.3 | 0.35.5 |
| [@img/sharp-freebsd-wasm32](https://registry.npmjs.org/@img/sharp-freebsd-wasm32) | 0.35.3 | 0.35.5 |
| [@img/sharp-libvips-darwin-arm64](https://registry.npmjs.org/@img/sharp-libvips-darwin-arm64) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-darwin-x64](https://registry.npmjs.org/@img/sharp-libvips-darwin-x64) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linux-arm](https://registry.npmjs.org/@img/sharp-libvips-linux-arm) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linux-arm64](https://registry.npmjs.org/@img/sharp-libvips-linux-arm64) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linux-ppc64](https://registry.npmjs.org/@img/sharp-libvips-linux-ppc64) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linux-riscv64](https://registry.npmjs.org/@img/sharp-libvips-linux-riscv64) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linux-s390x](https://registry.npmjs.org/@img/sharp-libvips-linux-s390x) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linux-x64](https://registry.npmjs.org/@img/sharp-libvips-linux-x64) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linuxmusl-arm64](https://registry.npmjs.org/@img/sharp-libvips-linuxmusl-arm64) | 1.3.2 | 1.3.4 |
| [@img/sharp-libvips-linuxmusl-x64](https://registry.npmjs.org/@img/sharp-libvips-linuxmusl-x64) | 1.3.2 | 1.3.4 |
| [@img/sharp-linux-arm](https://registry.npmjs.org/@img/sharp-linux-arm) | 0.35.3 | 0.35.5 |
| [@img/sharp-linux-arm64](https://registry.npmjs.org/@img/sharp-linux-arm64) | 0.35.3 | 0.35.5 |
| [@img/sharp-linux-ppc64](https://registry.npmjs.org/@img/sharp-linux-ppc64) | 0.35.3 | 0.35.5 |
| [@img/sharp-linux-riscv64](https://registry.npmjs.org/@img/sharp-linux-riscv64) | 0.35.3 | 0.35.5 |
| [@img/sharp-linux-s390x](https://registry.npmjs.org/@img/sharp-linux-s390x) | 0.35.3 | 0.35.5 |
| [@img/sharp-linux-x64](https://registry.npmjs.org/@img/sharp-linux-x64) | 0.35.3 | 0.35.5 |
| [@img/sharp-linuxmusl-arm64](https://registry.npmjs.org/@img/sharp-linuxmusl-arm64) | 0.35.3 | 0.35.5 |
| [@img/sharp-linuxmusl-x64](https://registry.npmjs.org/@img/sharp-linuxmusl-x64) | 0.35.3 | 0.35.5 |
| [@img/sharp-wasm32](https://registry.npmjs.org/@img/sharp-wasm32) | 0.35.3 | 0.35.5 |
| [@img/sharp-webcontainers-wasm32](https://registry.npmjs.org/@img/sharp-webcontainers-wasm32) | 0.35.3 | 0.35.5 |
| [@img/sharp-win32-arm64](https://registry.npmjs.org/@img/sharp-win32-arm64) | 0.35.3 | 0.35.5 |
| [@img/sharp-win32-ia32](https://registry.npmjs.org/@img/sharp-win32-ia32) | 0.35.3 | 0.35.5 |
| [@img/sharp-win32-x64](https://registry.npmjs.org/@img/sharp-win32-x64) | 0.35.3 | 0.35.5 |
| [@jridgewell/sourcemap-codec](https://registry.npmjs.org/@jridgewell/sourcemap-codec) | 1.5.5 | 1.5.5, 1.6.0 |
| [@jsonjoy.com/base64](https://registry.npmjs.org/@jsonjoy.com/base64) | absent | 1.1.2, 17.67.0 |
| [@jsonjoy.com/buffers](https://registry.npmjs.org/@jsonjoy.com/buffers) | absent | 1.2.1, 17.67.0 |
| [@jsonjoy.com/codegen](https://registry.npmjs.org/@jsonjoy.com/codegen) | absent | 1.0.0, 17.67.0 |
| [@jsonjoy.com/fs-core](https://registry.npmjs.org/@jsonjoy.com/fs-core) | absent | 4.80.0 |
| [@jsonjoy.com/fs-fsa](https://registry.npmjs.org/@jsonjoy.com/fs-fsa) | absent | 4.80.0 |
| [@jsonjoy.com/fs-node](https://registry.npmjs.org/@jsonjoy.com/fs-node) | absent | 4.80.0 |
| [@jsonjoy.com/fs-node-builtins](https://registry.npmjs.org/@jsonjoy.com/fs-node-builtins) | absent | 4.80.0 |
| [@jsonjoy.com/fs-node-to-fsa](https://registry.npmjs.org/@jsonjoy.com/fs-node-to-fsa) | absent | 4.80.0 |
| [@jsonjoy.com/fs-node-utils](https://registry.npmjs.org/@jsonjoy.com/fs-node-utils) | absent | 4.80.0 |
| [@jsonjoy.com/fs-print](https://registry.npmjs.org/@jsonjoy.com/fs-print) | absent | 4.80.0 |
| [@jsonjoy.com/fs-snapshot](https://registry.npmjs.org/@jsonjoy.com/fs-snapshot) | absent | 4.80.0 |
| [@jsonjoy.com/json-pack](https://registry.npmjs.org/@jsonjoy.com/json-pack) | absent | 1.21.0, 17.67.0 |
| [@jsonjoy.com/json-pointer](https://registry.npmjs.org/@jsonjoy.com/json-pointer) | absent | 1.0.2, 17.67.0 |
| [@jsonjoy.com/util](https://registry.npmjs.org/@jsonjoy.com/util) | absent | 1.9.0, 17.67.0 |
| [@napi-rs/wasm-runtime](https://registry.npmjs.org/@napi-rs/wasm-runtime) | 1.1.1, 1.1.4 | 1.1.1, 1.1.4, 1.2.6 |
| [@oxc-project/types](https://registry.npmjs.org/@oxc-project/types) | 0.127.0, 0.132.0, 0.133.0, 0.96.0 | 0.127.0, 0.132.0, 0.139.0, 0.96.0 |
| [@oxc-resolver/binding-android-arm-eabi](https://registry.npmjs.org/@oxc-resolver/binding-android-arm-eabi) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-android-arm64](https://registry.npmjs.org/@oxc-resolver/binding-android-arm64) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-darwin-arm64](https://registry.npmjs.org/@oxc-resolver/binding-darwin-arm64) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-darwin-x64](https://registry.npmjs.org/@oxc-resolver/binding-darwin-x64) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-freebsd-x64](https://registry.npmjs.org/@oxc-resolver/binding-freebsd-x64) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-arm-gnueabihf](https://registry.npmjs.org/@oxc-resolver/binding-linux-arm-gnueabihf) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-arm-musleabihf](https://registry.npmjs.org/@oxc-resolver/binding-linux-arm-musleabihf) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-arm64-gnu](https://registry.npmjs.org/@oxc-resolver/binding-linux-arm64-gnu) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-arm64-musl](https://registry.npmjs.org/@oxc-resolver/binding-linux-arm64-musl) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-ppc64-gnu](https://registry.npmjs.org/@oxc-resolver/binding-linux-ppc64-gnu) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-riscv64-gnu](https://registry.npmjs.org/@oxc-resolver/binding-linux-riscv64-gnu) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-riscv64-musl](https://registry.npmjs.org/@oxc-resolver/binding-linux-riscv64-musl) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-s390x-gnu](https://registry.npmjs.org/@oxc-resolver/binding-linux-s390x-gnu) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-x64-gnu](https://registry.npmjs.org/@oxc-resolver/binding-linux-x64-gnu) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-linux-x64-musl](https://registry.npmjs.org/@oxc-resolver/binding-linux-x64-musl) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-openharmony-arm64](https://registry.npmjs.org/@oxc-resolver/binding-openharmony-arm64) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-wasm32-wasi](https://registry.npmjs.org/@oxc-resolver/binding-wasm32-wasi) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-win32-arm64-msvc](https://registry.npmjs.org/@oxc-resolver/binding-win32-arm64-msvc) | 11.20.0 | 11.20.0, 11.21.2 |
| [@oxc-resolver/binding-win32-x64-msvc](https://registry.npmjs.org/@oxc-resolver/binding-win32-x64-msvc) | 11.20.0 | 11.20.0, 11.21.2 |
| [@react-router/dev](https://registry.npmjs.org/@react-router/dev) | 7.17.0 | 7.18.4 |
| [@react-router/express](https://registry.npmjs.org/@react-router/express) | 7.18.1 | 7.18.4 |
| [@react-router/node](https://registry.npmjs.org/@react-router/node) | 7.18.1 | 7.18.4 |
| [@react-router/serve](https://registry.npmjs.org/@react-router/serve) | 7.18.1 | 7.18.4 |
| [@rolldown/binding-android-arm64](https://registry.npmjs.org/@rolldown/binding-android-arm64) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-darwin-arm64](https://registry.npmjs.org/@rolldown/binding-darwin-arm64) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-darwin-x64](https://registry.npmjs.org/@rolldown/binding-darwin-x64) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-freebsd-x64](https://registry.npmjs.org/@rolldown/binding-freebsd-x64) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-linux-arm-gnueabihf](https://registry.npmjs.org/@rolldown/binding-linux-arm-gnueabihf) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-linux-arm64-gnu](https://registry.npmjs.org/@rolldown/binding-linux-arm64-gnu) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-linux-arm64-musl](https://registry.npmjs.org/@rolldown/binding-linux-arm64-musl) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-linux-ppc64-gnu](https://registry.npmjs.org/@rolldown/binding-linux-ppc64-gnu) | 1.0.3 | 1.1.5 |
| [@rolldown/binding-linux-s390x-gnu](https://registry.npmjs.org/@rolldown/binding-linux-s390x-gnu) | 1.0.3 | 1.1.5 |
| [@rolldown/binding-linux-x64-gnu](https://registry.npmjs.org/@rolldown/binding-linux-x64-gnu) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-linux-x64-musl](https://registry.npmjs.org/@rolldown/binding-linux-x64-musl) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-openharmony-arm64](https://registry.npmjs.org/@rolldown/binding-openharmony-arm64) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-wasm32-wasi](https://registry.npmjs.org/@rolldown/binding-wasm32-wasi) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-win32-arm64-msvc](https://registry.npmjs.org/@rolldown/binding-win32-arm64-msvc) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@rolldown/binding-win32-x64-msvc](https://registry.npmjs.org/@rolldown/binding-win32-x64-msvc) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [@storybook/addon-docs](https://registry.npmjs.org/@storybook/addon-docs) | 10.4.6 | 10.6.2 |
| [@storybook/addon-links](https://registry.npmjs.org/@storybook/addon-links) | 10.4.6 | 10.6.2 |
| [@storybook/addon-onboarding](https://registry.npmjs.org/@storybook/addon-onboarding) | 10.4.6 | 10.6.2 |
| [@storybook/builder-vite](https://registry.npmjs.org/@storybook/builder-vite) | 10.4.6 | 10.6.2 |
| [@storybook/builder-webpack5](https://registry.npmjs.org/@storybook/builder-webpack5) | 10.4.6 | 10.6.2 |
| [@storybook/core-webpack](https://registry.npmjs.org/@storybook/core-webpack) | 10.4.6 | 10.6.2 |
| [@storybook/csf-plugin](https://registry.npmjs.org/@storybook/csf-plugin) | 10.4.6 | absent |
| [@storybook/preset-react-webpack](https://registry.npmjs.org/@storybook/preset-react-webpack) | 10.4.6 | 10.6.2 |
| [@storybook/react](https://registry.npmjs.org/@storybook/react) | 10.4.6 | 10.6.2 |
| [@storybook/react-dom-shim](https://registry.npmjs.org/@storybook/react-dom-shim) | 10.4.6 | 10.6.2 |
| [@storybook/react-vite](https://registry.npmjs.org/@storybook/react-vite) | 10.4.6 | 10.6.2 |
| [@storybook/react-webpack5](https://registry.npmjs.org/@storybook/react-webpack5) | 10.4.6 | 10.6.2 |
| [@testing-library/dom](https://registry.npmjs.org/@testing-library/dom) | 10.4.0 | 10.4.2 |
| [@testing-library/user-event](https://registry.npmjs.org/@testing-library/user-event) | 14.6.1 | 14.6.8 |
| [@tiptap/core](https://registry.npmjs.org/@tiptap/core) | 2.26.3 | 2.27.3 |
| [@tiptap/extension-blockquote](https://registry.npmjs.org/@tiptap/extension-blockquote) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/extension-bold](https://registry.npmjs.org/@tiptap/extension-bold) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-bubble-menu](https://registry.npmjs.org/@tiptap/extension-bubble-menu) | 2.26.3 | 2.27.3 |
| [@tiptap/extension-bullet-list](https://registry.npmjs.org/@tiptap/extension-bullet-list) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-character-count](https://registry.npmjs.org/@tiptap/extension-character-count) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-code](https://registry.npmjs.org/@tiptap/extension-code) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-code-block](https://registry.npmjs.org/@tiptap/extension-code-block) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-collaboration](https://registry.npmjs.org/@tiptap/extension-collaboration) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-document](https://registry.npmjs.org/@tiptap/extension-document) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/extension-dropcursor](https://registry.npmjs.org/@tiptap/extension-dropcursor) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-emoji](https://registry.npmjs.org/@tiptap/extension-emoji) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-floating-menu](https://registry.npmjs.org/@tiptap/extension-floating-menu) | 2.26.3 | 2.27.3 |
| [@tiptap/extension-gapcursor](https://registry.npmjs.org/@tiptap/extension-gapcursor) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-hard-break](https://registry.npmjs.org/@tiptap/extension-hard-break) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-heading](https://registry.npmjs.org/@tiptap/extension-heading) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/extension-history](https://registry.npmjs.org/@tiptap/extension-history) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/extension-horizontal-rule](https://registry.npmjs.org/@tiptap/extension-horizontal-rule) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-image](https://registry.npmjs.org/@tiptap/extension-image) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-italic](https://registry.npmjs.org/@tiptap/extension-italic) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-list-item](https://registry.npmjs.org/@tiptap/extension-list-item) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/extension-mention](https://registry.npmjs.org/@tiptap/extension-mention) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-ordered-list](https://registry.npmjs.org/@tiptap/extension-ordered-list) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-paragraph](https://registry.npmjs.org/@tiptap/extension-paragraph) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-placeholder](https://registry.npmjs.org/@tiptap/extension-placeholder) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-strike](https://registry.npmjs.org/@tiptap/extension-strike) | 2.26.2 | 2.26.2, 2.27.3 |
| [@tiptap/extension-task-item](https://registry.npmjs.org/@tiptap/extension-task-item) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-task-list](https://registry.npmjs.org/@tiptap/extension-task-list) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-text](https://registry.npmjs.org/@tiptap/extension-text) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/extension-text-align](https://registry.npmjs.org/@tiptap/extension-text-align) | 2.26.1 | 2.27.3 |
| [@tiptap/extension-text-style](https://registry.npmjs.org/@tiptap/extension-text-style) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/extension-underline](https://registry.npmjs.org/@tiptap/extension-underline) | 2.26.1 | 2.27.3 |
| [@tiptap/html](https://registry.npmjs.org/@tiptap/html) | 2.26.2 | 2.27.3 |
| [@tiptap/pm](https://registry.npmjs.org/@tiptap/pm) | 2.26.1, 3.6.6 | 2.27.3 |
| [@tiptap/react](https://registry.npmjs.org/@tiptap/react) | 2.26.1 | 2.27.3 |
| [@tiptap/starter-kit](https://registry.npmjs.org/@tiptap/starter-kit) | 2.26.1 | 2.26.1, 2.27.3 |
| [@tiptap/suggestion](https://registry.npmjs.org/@tiptap/suggestion) | 2.26.1 | 2.27.3 |
| [@tybys/wasm-util](https://registry.npmjs.org/@tybys/wasm-util) | 0.10.1 | 0.10.1, 0.10.4 |
| [@vitest/coverage-v8](https://registry.npmjs.org/@vitest/coverage-v8) | 4.1.8 | 4.1.12 |
| [@vitest/expect](https://registry.npmjs.org/@vitest/expect) | 3.2.4, 4.1.8 | 3.2.4, 4.1.12 |
| [@vitest/mocker](https://registry.npmjs.org/@vitest/mocker) | 4.1.8 | 4.1.12 |
| [@vitest/pretty-format](https://registry.npmjs.org/@vitest/pretty-format) | 3.2.4, 4.1.8 | 3.2.4, 4.1.12 |
| [@vitest/runner](https://registry.npmjs.org/@vitest/runner) | 4.1.8 | 4.1.12 |
| [@vitest/snapshot](https://registry.npmjs.org/@vitest/snapshot) | 4.1.8 | 4.1.12 |
| [@vitest/spy](https://registry.npmjs.org/@vitest/spy) | 3.2.4, 4.1.8 | 3.2.4, 4.1.12 |
| [@vitest/utils](https://registry.npmjs.org/@vitest/utils) | 3.2.4, 4.1.8 | 3.2.4, 4.1.12 |
| [axios](https://registry.npmjs.org/axios) | 1.18.1 | 1.21.0 |
| [baseline-browser-mapping](https://registry.npmjs.org/baseline-browser-mapping) | 2.9.19 | 2.11.0 |
| [body-parser](https://registry.npmjs.org/body-parser) | 1.20.6 | 1.20.9 |
| [brace-expansion](https://registry.npmjs.org/brace-expansion) | 5.0.7 | 5.0.12 |
| [browserslist](https://registry.npmjs.org/browserslist) | 4.28.1 | 4.29.5 |
| [caniuse-lite](https://registry.npmjs.org/caniuse-lite) | 1.0.30001759 | 1.0.30001759, 1.0.30001819 |
| [compression](https://registry.npmjs.org/compression) | 1.8.1 | 1.8.2 |
| [dayjs](https://registry.npmjs.org/dayjs) | 1.11.21 | 1.11.21, 1.11.23 |
| [detect-libc](https://registry.npmjs.org/detect-libc) | 2.0.4, 2.1.2 | 2.1.2 |
| [dom-serializer](https://registry.npmjs.org/dom-serializer) | 1.4.1, 2.0.0 | 1.4.1, 2.0.0, 3.1.1 |
| [domelementtype](https://registry.npmjs.org/domelementtype) | 2.3.0 | 2.3.0, 3.0.0 |
| [domhandler](https://registry.npmjs.org/domhandler) | 4.3.1, 5.0.3 | 4.3.1, 5.0.3, 6.0.1 |
| [dompurify](https://registry.npmjs.org/dompurify) | 3.4.15 | 3.4.17 |
| [domutils](https://registry.npmjs.org/domutils) | 2.8.0, 3.2.2 | 2.8.0, 3.2.2, 4.0.2 |
| [electron-to-chromium](https://registry.npmjs.org/electron-to-chromium) | 1.5.286 | 1.5.465 |
| [entities](https://registry.npmjs.org/entities) | 2.2.0, 4.5.0, 5.0.0, 6.0.1, 7.0.1 | 2.2.0, 4.5.0, 5.0.0, 6.0.1, 8.1.0 |
| [fast-uri](https://registry.npmjs.org/fast-uri) | 3.1.5 | 3.1.9 |
| [follow-redirects](https://registry.npmjs.org/follow-redirects) | 1.16.0 | 1.16.2 |
| [glob-to-regex.js](https://registry.npmjs.org/glob-to-regex.js) | absent | 1.3.1 |
| [htmlparser2](https://registry.npmjs.org/htmlparser2) | 10.1.0, 6.1.0, 8.0.2 | 12.0.0, 6.1.0, 8.0.2 |
| [hyperdyperid](https://registry.npmjs.org/hyperdyperid) | absent | 1.2.0 |
| [is-plain-object](https://registry.npmjs.org/is-plain-object) | 2.0.4, 5.0.0 | 2.0.4, 5.1.0 |
| [js-yaml](https://registry.npmjs.org/js-yaml) | 4.3.0 | 4.3.2 |
| [katex](https://registry.npmjs.org/katex) | 0.16.47 | 0.18.2 |
| [launder](https://registry.npmjs.org/launder) | 1.7.1 | 1.7.2 |
| [magic-string](https://registry.npmjs.org/magic-string) | 0.30.21 | 0.30.21, 1.4.5 |
| [markdown-it](https://registry.npmjs.org/markdown-it) | 14.2.0 | 14.3.3 |
| [memfs](https://registry.npmjs.org/memfs) | 3.5.3 | 3.5.3, 4.80.0 |
| [mime-types](https://registry.npmjs.org/mime-types) | 2.1.18, 2.1.35 | 2.1.18, 2.1.35, 3.0.2 |
| [morgan](https://registry.npmjs.org/morgan) | 1.11.0 | 1.12.1 |
| [nanoid](https://registry.npmjs.org/nanoid) | 3.3.8 | 3.3.20 |
| [node-releases](https://registry.npmjs.org/node-releases) | 2.0.27 | 2.0.60 |
| [oxc-resolver](https://registry.npmjs.org/oxc-resolver) | 11.20.0 | 11.20.0, 11.21.2 |
| [postcss-selector-parser](https://registry.npmjs.org/postcss-selector-parser) | 6.0.10, 6.1.2, 7.1.0 | 6.1.5, 7.1.7 |
| [prosemirror-model](https://registry.npmjs.org/prosemirror-model) | 1.25.3 | 1.25.12 |
| [prosemirror-view](https://registry.npmjs.org/prosemirror-view) | 1.40.0 | 1.42.8 |
| [proxy-addr](https://registry.npmjs.org/proxy-addr) | 2.0.7 | 2.0.8 |
| [qs](https://registry.npmjs.org/qs) | 6.15.2 | 6.16.1 |
| [react-docgen](https://registry.npmjs.org/react-docgen) | 7.1.1, 8.0.1, 8.0.3 | 8.0.3 |
| [react-router](https://registry.npmjs.org/react-router) | 7.18.1 | 7.18.4 |
| [rolldown](https://registry.npmjs.org/rolldown) | 1.0.0-beta.46, 1.0.3 | 1.0.0-beta.46, 1.1.5 |
| [sanitize-html](https://registry.npmjs.org/sanitize-html) | 2.17.5 | 2.18.1 |
| [serialize-javascript](https://registry.npmjs.org/serialize-javascript) | 7.0.5 | absent |
| [sharp](https://registry.npmjs.org/sharp) | 0.35.3 | 0.35.5 |
| [side-channel](https://registry.npmjs.org/side-channel) | 1.1.0 | 1.1.1 |
| [side-channel-list](https://registry.npmjs.org/side-channel-list) | 1.0.0 | 1.0.1 |
| [source-map-js](https://registry.npmjs.org/source-map-js) | 1.2.1 | 1.2.2 |
| [storybook](https://registry.npmjs.org/storybook) | 10.4.6 | 10.6.2 |
| [terser-webpack-plugin](https://registry.npmjs.org/terser-webpack-plugin) | 5.3.16, 5.6.1 | 5.6.1 |
| [thingies](https://registry.npmjs.org/thingies) | absent | 2.6.1 |
| [tinypool](https://registry.npmjs.org/tinypool) | 2.1.0 | 2.2.0 |
| [toml](https://registry.npmjs.org/toml) | 4.1.1 | 5.0.1 |
| [tree-dump](https://registry.npmjs.org/tree-dump) | absent | 1.1.0 |
| [undici](https://registry.npmjs.org/undici) | 7.29.0 | 7.30.1 |
| [update-browserslist-db](https://registry.npmjs.org/update-browserslist-db) | 1.2.3 | 1.3.4 |
| [vite](https://registry.npmjs.org/vite) | 8.0.16 | 8.1.6 |
| [vitest](https://registry.npmjs.org/vitest) | 4.1.8 | 4.1.12 |
| [webpack-dev-middleware](https://registry.npmjs.org/webpack-dev-middleware) | 6.1.3 | 7.4.6 |
| [zod](https://registry.npmjs.org/zod) | 3.25.76 | 4.6.7 |

## Frozen file identities

- `pnpm-workspace.yaml` SHA-256: `5426deaaaa9021cb0ef9fe08de29ba6ff402f132e8182836d909cb1ece2ab785`.
- `pnpm-lock.yaml` SHA-256: `9eb528bf01ce0c250d9812eec7bbd4d070c12e12f3ac4eab6f791a6ab407aa37`.
- `patches/deepmerge@4.3.1.patch` SHA-256: `d3836103c6c78f7829b85509a7f71ee8b0ff4fa769b163d15ab740d9c3c0b1b0`.
- `apps/live/src/controllers/document-repair.controller.ts` SHA-256: `22219dcc37d45ef1f91aecdebbeeef73185b05c16b0d1d018cc7b65c28203757`.
- `apps/live/src/controllers/document.controller.ts` SHA-256: `20cd6f071e17153c405861d4a1acc1f1f1d7283f88d4e984bac7e41fab0fd194`.
- `apps/live/tests/editor/dependency-security.test.ts` SHA-256: `7a9d42456aaa332e5888b924cea9b32c84be5ed0290906c77e1acb51f845c239`.
- `apps/live/tests/controllers/document.test.ts` SHA-256: `f32b6daf4cc7b375a78089b88e7b30d39500b3667e92d4b54b0216e569dfaf88`.
