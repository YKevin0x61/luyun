# scripts/

Day-to-day entry scripts live here. One-off migration, debug, and smoke utilities are under [`archive/`](./archive/).

## Daily

| Script | Purpose |
|--------|---------|
| `start.py` | Start the FastAPI app (dev-friendly wrapper) |
| `quick_start.sh` | Quick local bootstrap helper |
| `build_kds.sh` | Build KDS H5 and deploy into `public/kds/` |
| `generate_kds_pwa.py` | Inject the versioned KDS manifest/service worker after the H5 build (called by `build_kds.sh`) |
| `publish_release.sh` | Publish a GitHub Release Bundle |
| `bootstrap_install.sh` | Shop Bootstrap Install from a Release Bundle |
| `curl_install.sh` | Curl\|bash install entry used with Releases |
| `docker_up.sh` | Docker / Compose process-shell bring-up |
| `run_update_job.py` | Out-of-process Update Job runner |
| `cold_backup.py` | 冷备归档入口（库快照 + 凭据 + 密钥 + 卫生照片 + 清单 + 校验和 + 状态文件）；由 `deploy/backup.sh` 调度 |
| `reconcile_settled_bills.py` | Settled-bill reconciliation utility |
| `migrate_recipes_structured.py` (+ `migrate_recipes_structured.rollback.md`) | One-time SOP `body_markdown` → structured JSON (dry-run first; move to `archive/` after a real-shop verify) |

## Archive (`archive/`)

| Script | Purpose |
|--------|---------|
| `consolidate_dbs.py` (+ `consolidate_dbs.rollback.md`) | One-time multi-db → `app.db` migration |
| `sqlite_to_pg_schema.py` | Generate PostgreSQL DDL (with `tenant_id`) from the SQLite schema → `migrations/pg/` |
| `migrate_sqlite_to_postgres.py` | One-time SQLite `app.db` → PostgreSQL data migration (`--dry-run` first; runbook in `migrations/pg/README.md`) |
| `mitm_pos_login_filter.py` | mitmproxy addon for POS login capture |
| `test_pos_auth.py` | POS auth smoke (A/B/C) |
| `smoke_public_pages.py` | Playwright public-page smoke |
| `verify_plan_features.py` | Prep-plan feature verification helper |

Layout rules: [ADR 0012](../docs/adr/0012-repo-layout.md).
