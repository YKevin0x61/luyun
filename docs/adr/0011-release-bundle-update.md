---
status: accepted
---

# Release Bundle as the sole shop install/upgrade artifact

Shop Runtime Instances install and upgrade from a single GitHub Release **Release Bundle** (app tree + prebuilt Admin/KDS + manifest/checksums), not from `git checkout` plus split frontend tarballs. Admin Version Check reads the local **Release Manifest**, compares it to formal Releases, and runs **Update Preflight** before Apply is offered; Apply still only queues an out-of-process **Update Job** (systemd oneshot or Docker detached process) that mandatory-backups, downloads and hard-verifies the bundle, extracts beside the live tree, atomically switches (keeping the previous tree for failure recovery), runs `pip` only when the requirements fingerprint changed, then restarts via systemd or `docker.sock`. Rollback is re-applying an older formal Release’s bundle. The GitHub repo is **public**; Bootstrap and Update Job download the same bundle anonymously — no Deploy Key, clone, or required Releases PAT (optional PAT only for higher API rate limits). Docker/`1Panel` may host the process with bind mounts, but **image pull is not** the product delivery model; Playwright browsers stay out of the bundle (installed once at bootstrap).

## Considered options

- **Keep git + split assets (ADR 0010):** short-term familiar, but long brittle pipelines, Deploy Key burden, and Docker shops forced into `.git` + sock mental load.
- **Docker image as truth (compose pull):** matches 1Panel habits, rejected for this product — shop data/credentials model and current ops stay bind-mount oriented; we optimize the mounted app dir, not re-platform onto images.
- **In-process binary swap (sub2api-style):** unfit for Python + prebuilt SPA/KDS + Playwright layout.
- **Soft checksums / late preflight:** rejected — failed apply on the shop floor is worse than a red light on Version Check.

## Consequences

- Publish must emit one bundle + checksums; Update Job and Bootstrap share that contract.
- Installed identity moves from git tag to Release Manifest; legacy git-based shops flip on their next successful Apply.
- Deploy Key and a required Releases PAT are no longer needed for shop install/upgrade on the public repo; an optional PAT may still be configured for rate limits.
- `docs/release-asset-layout.md` documents the Release Bundle contract; ADR 0010 remains only as the superseded historical decision (split frontend tarballs + git checkout).
