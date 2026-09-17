---
status: accepted
---

# Repository layout: where things live

Human- and agent-facing layout rules for this repo. Goal: each top-level area has one clear job so newcomers and agents can find the right place without moving runtime module paths.

## Decision

| Area | Role |
|------|------|
| Repo root (`main.py`, `config.py`, `database.py`, `models.py`, …) | Process entry and thin facades only — not a dumping ground for one-off artifacts |
| `api/`, `services/`, `db_core/`, `scraper/` | Backend runtime (do not relocate in layout cleanups) |
| `admin-web/`, `kds/` | Frontends (do not relocate); build outputs stay gitignored |
| `admin-web/public/` | Vite `publicDir` → authoritative copies of the two scoped stylesheets (`recipe.css`, `hygiene-admin.css`) plus `pwa/` icons + manifests; the build copies them into `admin-web/dist/` |
| `public/` | Static serve tree only (`kds/` build incl. `kds/static/pwa/`, `vendor/`, `recipe.css`, `hygiene-admin.css`) — no new HTML apps. The two stylesheets here are **fallbacks**: `main.py` serves `admin-web/dist/*` first and only falls back to this copy |
| `deploy/` | Shop install/runtime ops (systemd, reverse proxy, backup, env templates) |
| `docs/` | Design/ops docs; index at `docs/README.md`; ADRs in `docs/adr/`; agent workflow in `docs/agents/`; release/deploy contracts stay at this top level |
| `docs/pos/`, `docs/prep/`, `docs/hygiene/` | Domain notes grouped by subject — mostly gitignored, so this grouping is local navigation only |
| `docs/superpowers/` | Historical plans (`plans/`) and design specs (`specs/`), date-named, read-only |
| `docs/archive/` | One-off reports and dated snapshots — not living design docs; **no** shop operational data or DB dumps |
| `scripts/` | Day-to-day entry scripts only; see `scripts/README.md` |
| `scripts/archive/` | One-off migration, debug, and smoke utilities kept for reference |
| `tests/` | Automated tests; flat `test_*.py` at this level (no `conftest.py`) — grouping into subpackages breaks `from tests.test_* import`, so it needs its own ADR |
| `data/` | Runtime state/DBs/credentials — **not** source of truth in git (`data/*` gitignored) |
| `data/archive/` | Local one-off artifacts (dated comparison reports, superseded DBs such as pre-merge `recipes.db`) — deliberately **not** under `docs/archive/`, which must stay free of operational data |
| `.scratch/` | Local markdown issue tracker (open features only); see `docs/agents/issue-tracker.md` |
| `AGENTS.md` | Canonical agent guide for this repo |
| `CLAUDE.md` | Short pointer to `AGENTS.md` (tool entry file only) |
| `CONTEXT.md` | Ubiquitous language / domain terms |
| `README.md` | Human overview and quick start |
| Subdirectory `README.md` | Ops manual for that directory — not a second architecture essay |

## Non-goals

- Relocating backend or frontend runtime packages for “neatness”
- Changing public URL paths, Release Bundle layout, or systemd unit names
- Judging which `.scratch/<feature>/` efforts are “done” during a layout pass
- Forcing deletion of already-gitignored local cruft (`dist/` zips, `dump.rdb`, local `data/`)

## Considered options

- **Big-bang runtime re-tree** (`src/` mono-layout, moving `api/`/`services/`): rejected — high break risk for imports, deploy, and agent docs; fails the “layout cleanup ≠ refactor” boundary.
- **Dual full copies of agent docs** (`AGENTS.md` + full `CLAUDE.md`): rejected — drift; keep one canonical body.
- **Delete all legacy scripts**: rejected — shop migrations and POS research still need the files; archive instead.
- **Untrack `.scratch/` entirely**: rejected — issue-tracker workflow depends on it; prune junk only.

## Consequences

- Layout cleanups update path references (README, AGENTS, research docs, tests) in the same change as moves.
- `data/*.db` and ad-hoc reports must not be committed; stray tracked files are removed from the index and reports belong under `docs/archive/`.
- Day-to-day script discovery goes through `scripts/README.md`; archived tools are never the default entry.
- New ADRs that change install/runtime contracts stay out of this file — this ADR only governs document/script/top-level placement.
- `docs/README.md` indexes every living doc under `docs/`; adding or moving a doc means updating that table in the same change.
- `admin-web/public/` and `public/` hold copies of the same two scoped stylesheets, and the copies drift silently because `admin-web/dist/` always wins at runtime — treat `admin-web/public/` as the source of truth when editing them.
