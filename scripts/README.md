# scripts/

Day-to-day entry scripts live here. One-off migration, debug, and smoke utilities are under [`archive/`](./archive/).

## Daily

| Script | Purpose |
|--------|---------|
| `start.py` | Start the FastAPI app (dev-friendly wrapper) |
| `quick_start.sh` | Quick local bootstrap helper |
| `build_kds.sh` | Build KDS H5 and deploy into `public/kds/` |
| `publish_release.sh` | Publish a GitHub Release Bundle |
| `bootstrap_install.sh` | Shop Bootstrap Install from a Release Bundle |
| `curl_install.sh` | Curl\|bash install entry used with Releases |
| `docker_up.sh` | Docker / Compose process-shell bring-up |
| `run_update_job.py` | Out-of-process Update Job runner |
| `reconcile_settled_bills.py` | Settled-bill reconciliation utility |

## Archive (`archive/`)

| Script | Purpose |
|--------|---------|
| `consolidate_dbs.py` (+ `consolidate_dbs.rollback.md`) | One-time multi-db → `app.db` migration |
| `mitm_pos_login_filter.py` | mitmproxy addon for POS login capture |
| `test_pos_auth.py` | POS auth smoke (A/B/C) |
| `smoke_public_pages.py` | Playwright public-page smoke |
| `verify_plan_features.py` | Prep-plan feature verification helper |

Layout rules: [ADR 0012](../docs/adr/0012-repo-layout.md).
