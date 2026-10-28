# Plane backend dependency refresh

Checked 2026-10-28. This records the bounded backend dependency change from
`ed63f2659994df6ccdd7e52a10a844dbfedc3a10` on `feat/portfall-dependency-refresh`.
Only requirements, focused dependency tests, and this record change. The parent
owns the final combined image, codec/Live integration, migration checks, review,
commits, notices/SBOM, source publication, signing and deployment.
The [internal Plane exception](/portfall/handbook/src/branch/main/adr/0090-private-internal-plane-fork.md)
and [license policy](/portfall/handbook/src/branch/main/policy/license-policy.md)
were read at handbook commit `7aca0d3`; this is internal tooling only.

## Selection and advisory decisions

Stable maintenance releases are preferred. Django remains on 5.2 LTS. Celery is
5.6.3, not the 5.7 prerelease. OpenTelemetry's Django instrumentation uses the
project's normal `0.66b1` release numbering, aligned with SDK/API 1.45.1; this is
an explicit exception to the general prerelease exclusion.

| Dependency | Previous direct pin | Selected | Reason and primary evidence (checked 2026-10-28) |
|---|---|---|---|
| Django | 5.2.15 | 5.2.18 | Required LTS security fixes, including July cache disclosure, August fixes and [October security release](https://www.djangoproject.com/weblog/2026/oct/06/security-releases/): CVE-2026-77050, -84429, -87890, -87975 and the -15830 follow-up. [July](https://www.djangoproject.com/weblog/2026/jul/07/security-releases/), [August](https://www.djangoproject.com/weblog/2026/aug/04/security-releases/). |
| DRF | 3.17.1 | 3.18.3 | [Maintainer advisories](https://github.com/encode/django-rest-framework/security/advisories): body-size bypass and AdminRenderer fixes in 3.17.2; Accept parsing, attacker-named codec and token-revocation fixes in 3.18.2. Same major, latest stable patch. |
| PyJWT | 2.13.0 | 2.15.1 | [Maintainer changelog](https://github.com/jpadilla/pyjwt/blob/2.15.1/CHANGELOG.rst) and [advisories](https://github.com/jpadilla/pyjwt/security/advisories). Old per-version metadata has claim-validation/key-selection advisories. GHSA-gvp8-978c-rx2q lists affected <=2.13.0 with no patched-version field; do not reinterpret that empty field as all future versions affected or as proof fixed. Selected-version metadata reports no advisories; application auth tests pass. A read-only run of the advisory's options-reuse scenario on the actual 2.15.1 image preserved the caller dictionary and rejected the expired token after validation was re-enabled. |
| urllib3 | >=2.7.0 | 2.8.0 | Exact pin includes [September advisories](https://github.com/urllib3/urllib3/security/advisories) GHSA-8988-9cw3-xx77, GHSA-gh4c-6fx4-qh6g, GHSA-vxq7-64xx-v4gw, in addition to the earlier transport fixes. |
| OpenTelemetry | 1.28.1 / 0.49b1 | 1.45.1 / 0.66b1 | [GHSA-xcq7-p3pf-p88h](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-xcq7-p3pf-p88h) affects API <=1.45.0. Resolve the whole SDK/exporter/instrumentation family together. |
| Gunicorn | 23.0.0 | 25.3.0 | [Security policy](https://github.com/benoitc/gunicorn/security) explicitly supports 25.3.0 and excludes 23. [25.3 release](https://github.com/benoitc/gunicorn/releases/tag/25.3.0). This avoids unrelated 26-series HTTP/2 changes. PyPI JSON reports latest 26.2.0 while GitHub's release list shows 26.2.2; no nonexistent PyPI pin was invented. |
| Uvicorn | 0.29.0 | 0.54.0 | [Releases](https://uvicorn.dev/release-notes/), [security page](https://github.com/Kludex/uvicorn/security), [deployment contract](https://uvicorn.dev/deployment/). `uvicorn.workers.UvicornWorker` is deprecated but still present and works with Gunicorn 25.3.0. A test starts the literal entrypoint command and serves a real ASGI request; no entrypoint migration is necessary in this unit. Future removal requires an explicit uvicorn-worker migration. |
| Celery / Kombu | 5.5.3 / historically resolved 5.5.4 | 5.6.3 / 5.6.2 | [Stable Celery docs](https://docs.celeryq.dev/en/stable/), [Kombu metadata](https://pypi.org/pypi/kombu/5.6.2/json). New options exist for exclusive event/control queues, but default queue declarations still require the RabbitMQ compatibility flag below. |
| Redis client | 5.0.4 | 6.4.0 | Latest stable within Kombu 5.6.2's declared Redis-extra constraint `<6.5` (same primary metadata). [Redis 6.4 release](https://github.com/redis/redis-py/releases/tag/v6.4.0) and [compatibility table](https://github.com/redis/redis-py#redis-version-compatibility) cover the target server generation; avoid 8.x default protocol changes. |
| boto3 / botocore | 1.34.96 / historical 1.34.162 | 1.43.120 / 1.43.120 | [boto3 metadata](https://pypi.org/pypi/boto3/1.43.120/json), [botocore metadata](https://pypi.org/pypi/botocore/1.43.120/json), [boto3 security](https://github.com/boto/boto3/security), [botocore security](https://github.com/boto/botocore/security). Refreshes the service-model and transfer stack alongside current HTTP transport. No maintainer advisory published on those security pages is evidence of absence of a published advisory, not proof of safety. Actual PlaneStorage/S3 conformance is parent-owned and must rerun this client. |
| nh3 | 0.2.18 | 0.3.7 | [Release](https://github.com/messense/nh3/releases/tag/v0.3.7) embeds ammonia 4.1.4, including the July MathML/SVG fixes. The upstream [nh3 security page](https://github.com/messense/nh3/security) has no published advisories, but embedded Rust advisories still need separate review below. |
| OpenAI SDK | 1.63.2 | 3.40.0 | [Version metadata](https://pypi.org/pypi/openai/3.40.0/json), [upstream policy](https://github.com/openai/openai-python/blob/main/PYTHON_VERSION_POLICY.md): old SDK releases have no guaranteed backports. Existing Plane chat-completions path is verified against a local real HTTP service without SDK mocks or external credentials. |
| PostHog / Scout | 3.5.0 / 3.5.3 | 7.78.0 / 4.0.0 | Current stable clients, unchanged MIT licenses. [PostHog metadata](https://pypi.org/pypi/posthog/7.78.0/json), [Scout changelog](https://github.com/scoutapp/scout_apm_python/blob/master/CHANGELOG.md). Plane's analytics capture reaches a local HTTP receiver; production ASGI startup includes Scout's Django app with monitoring disabled in the fixture. External analytics/APM service acceptance is not claimed. |

The latest nh3 wheel is **not free of embedded Rust advisories**. Ammonia 4.1.4
falls in the affected ranges of [GHSA-f2pc-rwv3-69mv](https://github.com/rust-ammonia/ammonia/security/advisories/GHSA-f2pc-rwv3-69mv)
(SVG animation, fixed in 4.1.7), [GHSA-69v2-w9vg-4g83](https://github.com/rust-ammonia/ammonia/security/advisories/GHSA-69v2-w9vg-4g83)
(CSS URL escaping, fixed in 4.1.7), and [GHSA-x43w-36pp-96g3](https://github.com/rust-ammonia/ammonia/security/advisories/GHSA-x43w-36pp-96g3)
(CSS URL case matching, fixed in 4.1.8). This is a conditional applicability
assessment: Plane's actual `content_validator.py` allows no SVG/animation tags,
and its sole `nh3.clean()` call does not enable `filter_style_properties`, which
the CSS advisories require. Reassess before allowing these tags or enabling that
CSS feature; a newer upstream wheel embedding the fixes is preferred when
available. No patched nh3 release beyond 0.3.7 was available from official PyPI
at this check, and no unreviewed Rust fork or wheel substitution is introduced.
The official sdist Cargo.lock was checked against its PyPI archive hash and
confirms ammonia 4.1.4. A read-only probe through Plane's actual sanitizer also
stripped the upstream SVG animation payload and confirmed the CSS-filter
default is None.

Faker remains on the last 25.x patch; django-filter 25.2 supports Django 5.2.
Neither has an upstream EOL declaration established by this check. This is a
compatibility retention, not a claim of a vendor LTS promise. Cron-descriptor
remains <2 per django-celery-beat; python-dateutil remains <3 per botocore; wrapt
remains <2 per Scout 4. Their precise restrictions are in the linked package
metadata. Other changed pins below take compatible stable patch/minor updates.

## Complete production graph

Every direct production dependency and every resolved transitive was checked
against official PyPI release/version JSON on 2026-10-28. The previous resolved
graph was the parent's read-only historical 110-package lock, SHA-256
`c3a44ab56f48d1a914911a4e6b32c69d878e0bc61e961d53bd1a17a49fdcdd9b`.
The old direct urllib3 requirement was a floor; that historical resolver already
selected 2.8.0. Each package link below is its selected-version primary metadata;
`/pypi/<name>/json` supplied the latest-release comparison. “Direct” includes
production.txt; all other entries are transitive.

Per-version PyPI advisory data reported no non-withdrawn records for the selected
109 Python packages. This is **not a zero-vulnerability finding**: the feed is
OSV-derived, misses embedded Rust records, and the old Django metadata did not
include every October advisory. The primary maintainer reviews above supersede
any inference from an empty feed. No unsupported release was silently accepted
on the strength of an empty result.

License cells are package metadata/classifier evidence, not a complete artifact
license review. Existing LGPL psycopg variants and python-crontab remain REVIEW
items; they require the parent's recorded decision, notices and replacement/source
obligations. Certifi remains MPL. Typing-extensions' existing PSF-2.0 metadata
should retain its Python license text. New packages report BSD/Apache/MIT terms;
no new license outside the policy's ALLOW/REVIEW categories was identified in
this metadata audit. Compiled Rust, native library and OS license inventories
remain part of the parent's final artifact scan.

| Package | Role | Historical resolved | Selected | PyPI latest | License metadata |
|---|---|---|---|---|---|
| [amqp](https://pypi.org/pypi/amqp/5.4.1/json) | Transitive | 5.4.1 | 5.4.1 | 5.4.1 | BSD (classifiers) |
| [annotated-types](https://pypi.org/pypi/annotated-types/0.8.0/json) | Transitive | 0.8.0 | 0.8.0 | 0.8.0 | MIT |
| [anyio](https://pypi.org/pypi/anyio/4.15.1/json) | Transitive | 4.15.1 | 4.15.1 | 4.15.1 | MIT |
| [asgiref](https://pypi.org/pypi/asgiref/3.13.0/json) | Transitive | 3.13.0 | 3.13.0 | 3.13.0 | BSD-3-Clause |
| [attrs](https://pypi.org/pypi/attrs/26.1.0/json) | Transitive | 26.1.0 | 26.1.0 | 26.1.0 | MIT |
| [backoff](https://pypi.org/pypi/backoff/2.2.1/json) | Transitive | 2.2.1 | 2.2.1 | 2.2.1 | MIT |
| [beautifulsoup4](https://pypi.org/pypi/beautifulsoup4/4.15.0/json) | Direct | 4.12.3 | 4.15.0 | 4.15.0 | MIT |
| [billiard](https://pypi.org/pypi/billiard/4.3.1/json) | Transitive | 4.3.1 | 4.3.1 | 4.3.1 | BSD (classifiers) |
| [boto3](https://pypi.org/pypi/boto3/1.43.120/json) | Direct | 1.34.96 | 1.43.120 | 1.43.120 | Apache-2.0 |
| [botocore](https://pypi.org/pypi/botocore/1.43.120/json) | Transitive | 1.34.162 | 1.43.120 | 1.43.120 | Apache-2.0 |
| [celery](https://pypi.org/pypi/celery/5.6.3/json) | Direct | 5.5.3 | 5.6.3 | 5.6.3 | BSD-3-Clause |
| [certifi](https://pypi.org/pypi/certifi/2026.10.23/json) | Transitive | 2026.10.23 | 2026.10.23 | 2026.10.23 | MPL-2.0 |
| [cffi](https://pypi.org/pypi/cffi/2.1.1/json) | Transitive | 2.1.1 | 2.1.1 | 2.1.1 | MIT-0 |
| [channels](https://pypi.org/pypi/channels/4.3.2/json) | Direct | 4.3.2 | 4.3.2 | 4.3.2 | BSD (classifiers) |
| [charset-normalizer](https://pypi.org/pypi/charset-normalizer/3.5.2/json) | Transitive | 3.5.2 | 3.5.2 | 3.5.2 | MIT |
| [click](https://pypi.org/pypi/click/8.5.0/json) | Transitive | 8.5.0 | 8.5.0 | 8.5.0 | BSD-3-Clause |
| [click-didyoumean](https://pypi.org/pypi/click-didyoumean/0.3.1/json) | Transitive | 0.3.1 | 0.3.1 | 0.3.1 | MIT |
| [click-plugins](https://pypi.org/pypi/click-plugins/1.1.1.2/json) | Transitive | 1.1.1.2 | 1.1.1.2 | 1.1.1.2 | BSD-3-Clause |
| [click-repl](https://pypi.org/pypi/click-repl/0.4.1/json) | Transitive | 0.4.1 | 0.4.1 | 0.4.1 | MIT |
| [cron-descriptor](https://pypi.org/pypi/cron-descriptor/1.4.5/json) | Transitive | 1.4.5 | 1.4.5 | 2.2.1 | MIT (classifier) |
| [cryptography](https://pypi.org/pypi/cryptography/50.0.2/json) | Direct | 50.0.0 | 50.0.2 | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| [distro](https://pypi.org/pypi/distro/1.9.0/json) | Transitive | 1.9.0 | 1.9.0 | 1.9.0 | Apache-2.0 |
| [dj-database-url](https://pypi.org/pypi/dj-database-url/3.1.2/json) | Direct | 3.0.1 | 3.1.2 | 3.1.2 | BSD-3-Clause |
| [django](https://pypi.org/pypi/django/5.2.18/json) | Direct | 5.2.15 | 5.2.18 | 6.1.2 | BSD-3-Clause |
| [django-celery-beat](https://pypi.org/pypi/django-celery-beat/2.9.0/json) | Direct | 2.9.0 | 2.9.0 | 2.9.0 | BSD (classifiers) |
| [django-celery-results](https://pypi.org/pypi/django-celery-results/2.6.0/json) | Direct | 2.6.0 | 2.6.0 | 2.6.0 | BSD (classifiers) |
| [django-cors-headers](https://pypi.org/pypi/django-cors-headers/4.9.0/json) | Direct | 4.9.0 | 4.9.0 | 4.9.0 | MIT |
| [django-crum](https://pypi.org/pypi/django-crum/0.7.9/json) | Direct | 0.7.9 | 0.7.9 | 0.7.9 | BSD (classifiers) |
| [django-filter](https://pypi.org/pypi/django-filter/25.2/json) | Direct | 25.2 | 25.2 | 26.2 | BSD (classifier) |
| [django-redis](https://pypi.org/pypi/django-redis/7.0.0/json) | Direct | 7.0.0 | 7.0.0 | 7.0.0 | BSD-3-Clause |
| [django-storages](https://pypi.org/pypi/django-storages/1.14.6/json) | Direct | 1.14.6 | 1.14.6 | 1.14.6 | BSD-3-Clause |
| [django-timezone-field](https://pypi.org/pypi/django-timezone-field/7.2.2/json) | Transitive | 7.2.2 | 7.2.2 | 7.2.2 | BSD-2-Clause |
| [djangorestframework](https://pypi.org/pypi/djangorestframework/3.18.3/json) | Direct | 3.17.1 | 3.18.3 | 3.18.3 | BSD-3-Clause |
| [drf-spectacular](https://pypi.org/pypi/drf-spectacular/0.30.0/json) | Direct | 0.29.0 | 0.30.0 | 0.30.0 | BSD-3-Clause |
| [et-xmlfile](https://pypi.org/pypi/et-xmlfile/2.0.0/json) | Transitive | 2.0.0 | 2.0.0 | 2.0.0 | MIT |
| [faker](https://pypi.org/pypi/faker/25.9.2/json) | Direct | 25.0.0 | 25.9.2 | 40.44.0 | MIT |
| [googleapis-common-protos](https://pypi.org/pypi/googleapis-common-protos/1.76.0/json) | Transitive | 1.75.0 | 1.76.0 | 1.76.0 | Apache-2.0 |
| [grpcio](https://pypi.org/pypi/grpcio/1.85.0/json) | Transitive | 1.85.0 | 1.85.0 | 1.85.0 | Apache-2.0 |
| [gunicorn](https://pypi.org/pypi/gunicorn/25.3.0/json) | Direct | 23.0.0 | 25.3.0 | 26.2.0 | MIT |
| [h11](https://pypi.org/pypi/h11/0.16.0/json) | Transitive | 0.16.0 | 0.16.0 | 0.16.0 | MIT |
| [httpcore2](https://pypi.org/pypi/httpcore2/2.16.0/json) | Transitive | new | 2.16.0 | 2.16.0 | BSD-3-Clause |
| [httpx2](https://pypi.org/pypi/httpx2/2.16.0/json) | Transitive | new | 2.16.0 | 2.16.0 | BSD-3-Clause |
| [idna](https://pypi.org/pypi/idna/3.20/json) | Transitive | 3.20 | 3.20 | 3.20 | BSD-3-Clause |
| [inflection](https://pypi.org/pypi/inflection/0.5.1/json) | Transitive | 0.5.1 | 0.5.1 | 0.5.1 | MIT |
| [jiter](https://pypi.org/pypi/jiter/0.18.0/json) | Transitive | 0.18.0 | 0.18.0 | 0.18.0 | MIT |
| [jmespath](https://pypi.org/pypi/jmespath/1.1.0/json) | Transitive | 1.1.0 | 1.1.0 | 1.1.0 | MIT |
| [jsonmodels](https://pypi.org/pypi/jsonmodels/2.9.0/json) | Direct | 2.7.0 | 2.9.0 | 2.9.0 | BSD-3-Clause (license text) |
| [jsonschema](https://pypi.org/pypi/jsonschema/4.26.0/json) | Transitive | 4.26.0 | 4.26.0 | 4.26.0 | MIT |
| [jsonschema-specifications](https://pypi.org/pypi/jsonschema-specifications/2025.9.1/json) | Transitive | 2025.9.1 | 2025.9.1 | 2025.9.1 | MIT |
| [kombu](https://pypi.org/pypi/kombu/5.6.2/json) | Transitive | 5.5.4 | 5.6.2 | 5.6.2 | BSD-3-Clause |
| [lxml](https://pypi.org/pypi/lxml/6.1.3/json) | Direct | 6.1.0 | 6.1.3 | 6.1.3 | BSD-3-Clause |
| [nh3](https://pypi.org/pypi/nh3/0.3.7/json) | Direct | 0.2.18 | 0.3.7 | 0.3.7 | MIT |
| [openai](https://pypi.org/pypi/openai/3.40.0/json) | Direct | 1.63.2 | 3.40.0 | 3.40.0 | Apache-2.0 |
| [openpyxl](https://pypi.org/pypi/openpyxl/3.1.5/json) | Direct | 3.1.2 | 3.1.5 | 3.1.5 | MIT |
| [opentelemetry-api](https://pypi.org/pypi/opentelemetry-api/1.45.1/json) | Direct | 1.28.1 | 1.45.1 | 1.45.1 | Apache-2.0 |
| [opentelemetry-exporter-http-transport](https://pypi.org/pypi/opentelemetry-exporter-http-transport/0.66b1/json) | Transitive | new | 0.66b1 | 0.66b1 | Apache-2.0 |
| [opentelemetry-exporter-otlp](https://pypi.org/pypi/opentelemetry-exporter-otlp/1.45.1/json) | Direct | 1.28.1 | 1.45.1 | 1.45.1 | Apache-2.0 |
| [opentelemetry-exporter-otlp-common](https://pypi.org/pypi/opentelemetry-exporter-otlp-common/0.66b1/json) | Transitive | new | 0.66b1 | 0.66b1 | Apache-2.0 |
| [opentelemetry-exporter-otlp-proto-common](https://pypi.org/pypi/opentelemetry-exporter-otlp-proto-common/1.45.1/json) | Transitive | 1.28.1 | 1.45.1 | 1.45.1 | Apache-2.0 |
| [opentelemetry-exporter-otlp-proto-grpc](https://pypi.org/pypi/opentelemetry-exporter-otlp-proto-grpc/1.45.1/json) | Direct | 1.28.1 | 1.45.1 | 1.45.1 | Apache-2.0 |
| [opentelemetry-exporter-otlp-proto-http](https://pypi.org/pypi/opentelemetry-exporter-otlp-proto-http/1.45.1/json) | Transitive | 1.28.1 | 1.45.1 | 1.45.1 | Apache-2.0 |
| [opentelemetry-instrumentation](https://pypi.org/pypi/opentelemetry-instrumentation/0.66b1/json) | Transitive | 0.49b1 | 0.66b1 | 0.66b1 | Apache-2.0 |
| [opentelemetry-instrumentation-django](https://pypi.org/pypi/opentelemetry-instrumentation-django/0.66b1/json) | Direct | 0.49b1 | 0.66b1 | 0.66b1 | Apache-2.0 |
| [opentelemetry-instrumentation-wsgi](https://pypi.org/pypi/opentelemetry-instrumentation-wsgi/0.66b1/json) | Transitive | 0.49b1 | 0.66b1 | 0.66b1 | Apache-2.0 |
| [opentelemetry-proto](https://pypi.org/pypi/opentelemetry-proto/1.45.1/json) | Transitive | 1.28.1 | 1.45.1 | 1.45.1 | Apache-2.0 |
| [opentelemetry-sdk](https://pypi.org/pypi/opentelemetry-sdk/1.45.1/json) | Direct | 1.28.1 | 1.45.1 | 1.45.1 | Apache-2.0 |
| [opentelemetry-semantic-conventions](https://pypi.org/pypi/opentelemetry-semantic-conventions/0.66b1/json) | Transitive | 0.49b1 | 0.66b1 | 0.66b1 | Apache-2.0 |
| [opentelemetry-util-http](https://pypi.org/pypi/opentelemetry-util-http/0.66b1/json) | Transitive | 0.49b1 | 0.66b1 | 0.66b1 | Apache-2.0 |
| [packaging](https://pypi.org/pypi/packaging/26.3/json) | Transitive | 26.3 | 26.3 | 26.3 | Apache-2.0 OR BSD-2-Clause |
| [posthog](https://pypi.org/pypi/posthog/7.78.0/json) | Direct | 3.5.0 | 7.78.0 | 7.78.0 | MIT |
| [prompt-toolkit](https://pypi.org/pypi/prompt-toolkit/3.0.53/json) | Transitive | 3.0.53 | 3.0.53 | 3.0.53 | BSD (classifier) |
| [protobuf](https://pypi.org/pypi/protobuf/7.36.2/json) | Transitive | 5.29.6 | 7.36.2 | 7.36.2 | BSD-3-Clause |
| [psutil](https://pypi.org/pypi/psutil/7.2.2/json) | Transitive | 7.2.2 | 7.2.2 | 7.2.2 | BSD-3-Clause |
| [psycopg](https://pypi.org/pypi/psycopg/3.3.6/json) | Direct | 3.3.4 | 3.3.6 | 3.3.6 | LGPL-3.0-only |
| [psycopg-binary](https://pypi.org/pypi/psycopg-binary/3.3.6/json) | Direct | 3.3.4 | 3.3.6 | 3.3.6 | LGPL-3.0-only |
| [psycopg-c](https://pypi.org/pypi/psycopg-c/3.3.6/json) | Direct | 3.3.4 | 3.3.6 | 3.3.6 | LGPL-3.0-only |
| [pycparser](https://pypi.org/pypi/pycparser/3.12/json) | Transitive | 3.12 | 3.12 | 3.12 | BSD-3-Clause |
| [pydantic](https://pypi.org/pypi/pydantic/2.14.1/json) | Transitive | 2.14.1 | 2.14.1 | 2.14.1 | MIT |
| [pydantic-core](https://pypi.org/pypi/pydantic-core/2.50.1/json) | Transitive | 2.50.1 | 2.50.1 | 2.50.1 | MIT |
| [pyjwt](https://pypi.org/pypi/pyjwt/2.15.1/json) | Direct | 2.13.0 | 2.15.1 | 2.15.1 | MIT |
| [python-crontab](https://pypi.org/pypi/python-crontab/3.4.0/json) | Transitive | 3.4.0 | 3.4.0 | 3.4.0 | LGPL-3.0 |
| [python-dateutil](https://pypi.org/pypi/python-dateutil/2.9.0.post0/json) | Transitive | 2.9.0.post0 | 2.9.0.post0 | 3.0.0 | Apache-2.0 OR BSD |
| [python-json-logger](https://pypi.org/pypi/python-json-logger/4.2.0/json) | Direct | 4.0.0 | 4.2.0 | 4.2.0 | BSD-2-Clause |
| [pytz](https://pypi.org/pypi/pytz/2026.5/json) | Direct | 2024.1 | 2026.5 | 2026.5 | MIT |
| [pyyaml](https://pypi.org/pypi/pyyaml/6.0.3/json) | Transitive | 6.0.3 | 6.0.3 | 6.0.3 | MIT |
| [redis](https://pypi.org/pypi/redis/6.4.0/json) | Direct | 5.0.4 | 6.4.0 | 8.1.0 | MIT |
| [referencing](https://pypi.org/pypi/referencing/0.37.0/json) | Transitive | 0.37.0 | 0.37.0 | 0.37.0 | MIT |
| [requests](https://pypi.org/pypi/requests/2.34.2/json) | Direct | 2.33.0 | 2.34.2 | 2.34.2 | Apache-2.0 |
| [rpds-py](https://pypi.org/pypi/rpds-py/2026.9.1/json) | Transitive | 2026.9.1 | 2026.9.1 | 2026.9.1 | MIT |
| [s3transfer](https://pypi.org/pypi/s3transfer/0.19.3/json) | Transitive | 0.10.4 | 0.19.3 | 0.19.3 | Apache-2.0 |
| [scout-apm](https://pypi.org/pypi/scout-apm/4.0.0/json) | Direct | 3.5.3 | 4.0.0 | 4.0.0 | MIT |
| [six](https://pypi.org/pypi/six/1.17.0/json) | Transitive | 1.17.0 | 1.17.0 | 1.17.0 | MIT |
| [slack-sdk](https://pypi.org/pypi/slack-sdk/3.45.0/json) | Direct | 3.27.1 | 3.45.0 | 3.45.0 | MIT |
| [sniffio](https://pypi.org/pypi/sniffio/1.3.1/json) | Transitive | 1.3.1 | 1.3.1 | 1.3.1 | MIT OR Apache-2.0 |
| [soupsieve](https://pypi.org/pypi/soupsieve/3.0.4/json) | Transitive | 3.0.4 | 3.0.4 | 3.0.4 | MIT |
| [sqlparse](https://pypi.org/pypi/sqlparse/0.6.0/json) | Transitive | 0.6.0 | 0.6.0 | 0.6.0 | BSD (classifier) |
| [truststore](https://pypi.org/pypi/truststore/0.10.5/json) | Transitive | new | 0.10.5 | 0.10.5 | MIT |
| [typing-extensions](https://pypi.org/pypi/typing-extensions/4.16.0/json) | Transitive | 4.16.0 | 4.16.0 | 4.16.0 | PSF-2.0 |
| [typing-inspection](https://pypi.org/pypi/typing-inspection/0.4.4/json) | Transitive | 0.4.4 | 0.4.4 | 0.4.4 | MIT |
| [tzdata](https://pypi.org/pypi/tzdata/2026.6/json) | Transitive | 2026.6 | 2026.6 | 2026.6 | Apache-2.0 |
| [tzlocal](https://pypi.org/pypi/tzlocal/5.4.5/json) | Transitive | new | 5.4.5 | 5.4.5 | MIT |
| [uritemplate](https://pypi.org/pypi/uritemplate/4.2.0/json) | Transitive | 4.2.0 | 4.2.0 | 4.2.0 | BSD 3-Clause OR Apache-2.0 |
| [urllib3](https://pypi.org/pypi/urllib3/2.8.0/json) | Direct | 2.8.0 | 2.8.0 | 2.8.0 | MIT |
| [uvicorn](https://pypi.org/pypi/uvicorn/0.54.0/json) | Direct | 0.29.0 | 0.54.0 | 0.54.0 | BSD-3-Clause |
| [vine](https://pypi.org/pypi/vine/5.1.0/json) | Transitive | 5.1.0 | 5.1.0 | 5.1.0 | BSD (classifiers) |
| [wcwidth](https://pypi.org/pypi/wcwidth/0.9.4/json) | Transitive | 0.9.4 | 0.9.4 | 0.9.4 | MIT (classifier) |
| [whitenoise](https://pypi.org/pypi/whitenoise/6.12.0/json) | Direct | 6.12.0 | 6.12.0 | 6.12.0 | MIT |
| [wrapt](https://pypi.org/pypi/wrapt/1.17.3/json) | Transitive | 1.17.3 | 1.17.3 | 2.5.1 | BSD (classifiers) |
| [zxcvbn](https://pypi.org/pypi/zxcvbn/4.5.0/json) | Direct | 4.4.28 | 4.5.0 | 4.5.0 | MIT |

Removed from production resolution: deprecated 3.0.0, httpcore 1.0.9,
httpx 0.28.1, importlib-metadata 8.5.0, monotonic 1.6, tqdm 4.70.1 and zipp 4.1.2.
Their old per-version metadata was also queried; it reported no current
non-withdrawn advisories. HTTPX 0.28.1 remains a **test-only** requirement.
OpenAI/OTel now resolve httpx2/httpcore2 instead; their selected-version metadata
and licenses are included above.

## Resolver and artifact boundary

`apps/api/requirements/portfall.lock` contains 109 exact pins and 1,508 SHA-256
archive hashes, all checked against official selected-version PyPI JSON, with no
yanked selected archive. Lock SHA-256:
`6bdf4e6a74f379a2457bb54b8ff68cd516113609e552a2db34315489e6a42a12`.
Generation used uv **0.12.5**, targeting **Python 3.12**, with official PyPI only:

```sh
uv pip compile --no-config --python-version 3.12 \
  --default-index https://pypi.org/simple --generate-hashes \
  --no-header --emit-index-url apps/api/requirements/production.txt \
  -o apps/api/requirements/portfall.lock
```

The host resolver executable is aarch64-linux-gnu, running on host Python 3.12.3;
no dependencies were installed into the host. Actual tests use **Python 3.12.16**,
**Alpine 3.24**, linux/arm64, from:
`python:3.12.16-alpine3.24@sha256:44bf3bcc455e0c9ccd912b78f39019b2f7e9d2f4fb47c6d6973c7638a99aff14`.
All 109 production pins were compared with installed distribution metadata;
Django is exactly **5.2.18**, not the old fixture's 5.2.17 or source's 5.2.15.
The final test image is immutable local image ID:
`sha256:08d44bc61567ef604aac7ad5ab69e935c5c901540e257de920256ebab43f0cef`
(tag `pf-plane-dependency-refresh:da611408-final-tests`). It contains a git archive
of the exact source base, the owned requirements and focused test additions;
no source tree is mounted writable. It is a dependency test fixture, not the
parent's final deployable codec/runtime image. Test-only additions were resolved
with all production pins constrained and their own temporary hash lock, then
installed with `--require-hashes`. `pip check` passed after both installs.

The production lock freezes Python distribution archives, **not every build
input**. psycopg-c builds from its hashed sdist with PEP 517 build isolation;
build-system dependencies are not recursively frozen by this requirements lock.
APK indexes/packages and native toolchains were not frozen. The fixture records
installed APK versions and image metadata, but that does not make rebuilding
sdists bit-reproducible. The parent image must retain built-wheel hashes and
its APK/build-input inventory; final multiarch images require their own checks.

## Compatibility and verification

Fixtures used fresh tmpfs PostgreSQL 15.19, Valkey 7.2.15 and RabbitMQ 4.3.7,
a uniquely named internal Docker network, no published ports, synthetic-only
configuration, an empty task Docker config and public image pulls. Existing
images, daemons, networks, volumes and source trees were not modified. Service
image indexes were:

- PostgreSQL: `postgres:15.19-alpine3.24@sha256:54e0dd8e8cddcdbb9156c2486eed5a43eccb7214d7c6828097d41ce049fb3984`.
- Valkey: `valkey/valkey:7.2.15-alpine3.24@sha256:f8c1a646f4b31ae4dc3980ff06723a20b533ea1123b9796d450d78ed53ad6d3e`.
- RabbitMQ: `rabbitmq:4.3.7-management-alpine@sha256:ddbbe6b99ce941f74f566947463e3f47f99c3bf0fe80d4ee250cf30b942b1800`.

`tests/test_dependency_contracts.py` exercises the actual Plane boundaries:
local HTTP completion through `get_llm_response`, local HTTP analytics through
`track_event`, literal entrypoint Gunicorn/UvicornWorker startup and a real ASGI
health request, plus opt-in AMQP control replies, task events and repeated ORM
DatabaseScheduler delivery with Valkey-observed effects. The scheduler check
uses its own task queue so it cannot consume unrelated tests' pending jobs.
Enable it only against explicitly supplied synthetic services with
`PLANE_DEPENDENCY_BROKER_TESTS=1`.

Proof-first RabbitMQ result: the default 4.3.7 broker rejects the new test's
real event queue with AMQP 541 (`transient_nonexcl_queues` not permitted).
The same client and test pass with:

```ini
deprecated_features.permit.transient_nonexcl_queues = true
```

The dependency upgrade alone does **not** remove this setting. Control, events,
heartbeat and the database scheduler remain exercised. A future migration to
exclusive/durable queues must cover all producers, receivers and control reply
queues, not just suppress one worker warning. See [RabbitMQ's queue policy](https://www.rabbitmq.com/docs/queues)
and [Celery's event/control settings](https://docs.celeryq.dev/en/stable/userguide/configuration.html).

Existing assertions were inspected and retained in authentication, API/app
project authorization, SSRF advisory and URL pinning, generic/file assets,
copy-S3 tasks, CSV/XLSX export sanitization, HTML/Page bridge and middleware
contracts. This unit does not add native project import/export coverage, which
belongs to the source worker and parent integration.

The initial full run was 528 passed / 63 failed / 15 skipped: absent WEB_URL and
APP_BASE_URL, shared fixed-token rate state, and missing Live accounted for the
failures. Supplying synthetic URLs and resetting **only this fixture's** cache
between modules gave 589 passed / 5 failed / 15 skipped with the three initial
focused tests. A temporary pytest plugin performs the module cache reset; it
changes no repository assertions, limiter configuration or rates. The five
remaining failures are genuine missing-service 503s, not claimed as green.

Final broad run: **590 passed, 15 skipped, 5 deselected**, in 59.58 seconds. The following five tests are explicitly
excluded pending the parent's real Live/Yjs integration, after their 503 failures
were observed unchanged:

- `TestProjectPageAPI::test_body_patch_keeps_a_canonical_collaborative_document`
- `TestProjectPageAPI::test_create_get_update_preserves_exact_content`
- `TestProjectPageAPI::test_member_can_write_but_guest_is_read_only`
- `TestProjectPageAPI::test_html_is_sanitized_and_external_links_remain_usable`
- `TestProjectPageAPI::test_html_update_retains_canonical_live_editor_history`

All are in `plane/tests/contract/api/test_pages.py`; other tests in that file
run normally. The 15 upstream skips are the opt-in PAT/Live integration module.
The parent owns full combined image/API/gateway/browser checks, these Page cases,
new 0123/native import/export/codec paths, S3 acceptance with this SDK, migration,
source/license publication and deployed acceptance. No cloud or live acceptance
is asserted here. No entrypoint, models, migrations, editor/Node locks or infra
files were changed.
