# Internal Portfall Plane builds

This is the separately operated internal Plane fork, excluded from Portfall
customer images and bundles. Retain Plane's AGPL license and publish the exact
deployed corresponding source, build inputs and notices. A successful build is
not artifact clearance or deployment acceptance.

Build from the complete repository root:

```sh
docker build --platform linux/amd64 -f deployments/portfall/Dockerfile.api -t plane-backend:review .
```

The backend includes the real offline editor codec at
`/code/project-bundle-codec.mjs`, invoked only by `/usr/local/bin/node`. The codec
bundles the existing locked editor/schema dependencies; it installs nothing and
uses no environment, network or credential service at runtime. Its Python
caller retains the timeout, output and memory bounds. This image runs as
UID/GID 10001. Only `plane/logs` and `plane/static-assets` are application-writable;
configure writable temporary storage for `/tmp` when enforcing a read-only root.

Both base images are digest-pinned. The refreshed 109-package production Python
graph is version/hash-frozen in `apps/api/requirements/portfall.lock`; selections,
resolver command, advisory limits and fixture coverage are recorded in
[dependency-refresh.md](dependency-refresh.md). Regenerate the lock only as a
reviewed dependency change with uv 0.12.5 and Python 3.12 as the target.
Source distributions still need build tools: retained wheel checksums describe the actual build, not
reproducibility across different compiler/APK inputs. The image records those
wheel hashes and installed Python/APK versions in `/usr/share/plane-build`.
APK repositories and isolated PEP-517 build dependencies are not made hermetic
by this recipe. Each published image requires its own complete SBOM, source and
notice inventory, security/license review and build-input receipt.

The original generic API Dockerfile remains available. The internal recipe uses
the same startup script and application configuration, and does not supply
Secrets, bootstrap an administrator, enable public registration, grant cloud
identity or run a project import. Place stable keys and application credentials
through the registered operator procedure. Never include `.env`, real data or
credential files in the build context; the repository's Docker ignore rules apply.

## Live and frontend images

`Dockerfile.node` builds the same refreshed workspace for four internal targets:

```sh
docker build -f deployments/portfall/Dockerfile.node --target live -t plane-live:review .
docker build -f deployments/portfall/Dockerfile.node --target space -t plane-space:review .
docker build -f deployments/portfall/Dockerfile.node --target web -t plane-web:review .
docker build -f deployments/portfall/Dockerfile.node --target admin -t plane-admin:review .
```

The browser applications use the same origin, with `/api`, `/live`, `/spaces`
and `/god-mode` routing. These paths are build inputs, not runtime URL discovery.
Space retains its existing server-rendered metadata fallback: the historical
metadata loader cannot fetch a relative API URL from Node. Published-page client
requests still use the same-origin API; server-side metadata needs separate
internal-URL design and verification before claiming that feature works.

Space’s ignored `build` directory is copied explicitly from the build stage;
legacy pnpm deployment does not include that runtime entrypoint.

Live and Space run the actual compiled entrypoints as UID/GID 1000, without npm,
npx or Corepack in their runtime stage. Their production dependency directories
come from offline pnpm 11 legacy deployment. That operation can re-resolve peer
contexts, so its success alone does not establish use of the reviewed graph.
`verify-deploy.mjs` compares every external package's file bytes against the
frozen build installation, compares workspace `dist` outputs and rejects links
outside the final directory. The one pnpm self-link back to the source app is
explicitly rebased to the deployed app before verification. No other escaping
link or changed package is accepted. The resulting package inventory is retained
at `/usr/share/plane-build/packages.json`; this verifies deployed bytes against
the install, not independent npm provenance or artifact vulnerability clearance.
The same legacy deployment omitted Sharp's platform-specific optional packages.
The Live preparation step copies only the installed Linux/musl native Sharp and
libvips packages for the build architecture from the frozen installation,
preserves their relative links, and subjects them to the same byte/link checks.
A real PNG conversion runs before the Live image is accepted. The generated
legacy deploy lock alone does not describe that completed runtime; the retained
root lock and actual package inventory remain necessary build evidence.

Web and Admin serve their static builds on port 3000 with pinned NGINX 1.31.4,
UID/GID 101, a direct NGINX entrypoint and writable temporary paths under `/tmp`.
Supply a bounded `/tmp` volume when enforcing a read-only root. The image uses
Google's public mirror of the exact official NGINX index; this is a build input,
not Portfall's reviewed/signed production mirror. The private TLS edge supplies
its own mounted NGINX configuration and source notice. All four images retain
the root dependency lock and workspace configuration. Final image SBOMs, notices,
source correspondence, review, registry publication and runtime checks remain
separate release requirements.


## Private migration target settings

The target import Job selects `DJANGO_SETTINGS_MODULE=plane.settings.project_bundle`.
This separate production-derived module disables the historical public ACL/custom
domain settings, enables signed URLs and keeps the native S3 backend and its exact
configured bucket/endpoint. Ordinary API and worker settings remain unchanged.
The module does not prove bucket privacy or authorize a migration: the importer
must verify the actual bucket policy, anonymous read denial and staged bytes.

Only the separately authorized migration Job receives
`PROJECT_BUNDLE_S3_AUDIT_ACCESS_KEY_ID` and
`PROJECT_BUNDLE_S3_AUDIT_SECRET_ACCESS_KEY` from its named audit Secret. Missing
values remain unset; app credentials are never reused as audit credentials.
The audit identity retains its real bucket-scoped administrative authority even
though the importer uses it only to read policy. Do not mount that Secret in API,
worker or codec containers. The codec child continues to receive an empty
environment. No real Secret, admin bootstrap or migration Job is created by this
build recipe.


## Frontend source retained in the combined build

Historical backend/Live source `ed63f2659994df6ccdd7e52a10a844dbfedc3a10`
and web source `a85d78db295d3a5aa135a7d7755cc6e9bf902f5d` diverge at
`4d97bf720ef649315ca0d557c9b808463676fd31`. The combined source retains the latter's
two Mermaid viewer/centering commits (`5d156ed68a` and `a85d78db29`) alongside the
backend's Page-content changes. It does not replace the refreshed dependency lock
with the historical web lock. The editor's restored DOM tests use Happy DOM
21.0.4 rather than historical 20.14.3; official npm metadata identifies MIT terms,
Node >=20 and publication on 2026-10-20. This is a test-only dependency change and
still belongs in the final dependency/artifact inventory.

The Python and JavaScript dependency records describe their bounded source bases.
The combined source, offline codec, native importer, editor/browser and final
images need their own integration receipts. Publish the final committed source
corresponding to each deployed image; neither historical commit alone describes
this combined build.


## Native CI artifact inventory

The internal image workflow retains actual AMD64 CycloneDX/native Syft reports,
image-config metadata and a source-bound checksum receipt for operator review.
It uses the separately reviewed, checksum-pinned Syft 1.55.0 operator binary;
only archive data is scanned and no image or registry credential is published.
The retained metadata is not an image release, license decision, signature,
complete static-JavaScript/codec attribution or corresponding-source clearance.
