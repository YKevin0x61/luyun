# Release Bundle asset layout

Contract for GitHub Release attachments produced by `scripts/publish_release.sh`.
**Update Job** and **Bootstrap Install** install from this layout; Runtime Instances never build Admin SPA or KDS.

> **Current shop contract (ADR 0011).** The former split `admin-web-dist.tar.gz` /
> `kds-dist.tar.gz` + git checkout path (ADR 0010) is **retired** — do not document
> or implement shop install/upgrade against those asset names.

## Asset names

| Release asset                   | Role |
| ------------------------------- | ---- |
| `luyun-release-bundle.tar.gz`   | Single **Release Bundle**: application tree + prebuilt Admin SPA + KDS + `RELEASE_MANIFEST.json` |
| `SHA256SUMS`                    | Hard integrity sidecar for the bundle (verify-before-activate) |
| `install.sh`                    | curl\|bash Bootstrap entry (same as `scripts/curl_install.sh`, with repo/tag baked in) |

The bundle archive is a **tar.gz of directory contents** (not a wrapper folder). After unpack into a deploy tree:

- `RELEASE_MANIFEST.json` must exist (installed Release identity)
- `admin-web/dist/index.html` must exist
- `public/kds/index.html` and `public/kds/assets/` must exist
- `requirements.txt` must exist (fingerprint source for conditional pip)

## Release Manifest (`RELEASE_MANIFEST.json`)

Embedded in the bundle. Stable fields for Version Check / Update Job:

| Field | Meaning |
| ----- | ------- |
| `schema_version` | Integer; currently `1` |
| `tag` | GitHub Release tag (e.g. `v0.1.0`) |
| `app_version` | Bare semver aligned to `config.APP_VERSION` |
| `commit` | Full commit SHA the Release was built from |
| `requirements_fingerprint` | `sha256:<hex>` of `requirements.txt` at publish time |
| `artifacts.bundle` | Bundle asset name (`luyun-release-bundle.tar.gz`) |
| `artifacts.checksums` | Checksums asset name (`SHA256SUMS`) |

## Bundle exclusions

Never included in the Release Bundle:

- Shop writable data (`data/`)
- Credentials / secrets (e.g. `secrets/`, `.env`, `deploy/env.production`, credential key material)
- Playwright browser binaries (`ms-playwright/`, etc.)
- Frontend `node_modules/` and other build caches

## Checksums

`SHA256SUMS` lists a SHA-256 digest for `luyun-release-bundle.tar.gz` in the common `sha256sum` two-space form:

```text
<64-hex>  luyun-release-bundle.tar.gz
```

Consumers must hard-fail on missing or mismatched digests before activating a new tree.

## Producer

```bash
# Validates clean worktree + APP_VERSION ↔ tag, then builds and publishes.
./scripts/publish_release.sh v0.1.0

# Contract check without builds / GitHub calls:
./scripts/publish_release.sh --dry-run v0.1.0
```

Tag may include a leading `v`; `APP_VERSION` in `config.py` is the bare semver (`0.1.0`). They must match after stripping that prefix.

## Consumers

Update Job and Bootstrap Install share this contract:

1. Download `luyun-release-bundle.tar.gz` and `SHA256SUMS` from the GitHub Release for the target tag.
2. Verify the bundle digest against `SHA256SUMS` (fail closed on mismatch/missing) before activation.
3. After unpack, require `RELEASE_MANIFEST.json` and the prebuilt Admin/KDS paths listed above.
4. Do **not** run `npm` / `uni` / `build_kds.sh` on the Runtime Instance.
5. Do **not** replace shop `data/` or credential stores from the bundle.

If the bundle or checksum asset is missing, the update/install must fail clearly rather than leaving a half-applied tree.
