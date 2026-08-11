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
| `public/` | Static serve tree only (`kds/` build, `vendor/`, `recipe.css`) — no new HTML apps |
| `deploy/` | Shop install/runtime ops (systemd, reverse proxy, backup, env templates) |
| `docs/` | Design/ops docs; ADRs in `docs/adr/`; agent workflow in `docs/agents/` |
| `docs/archive/` | One-off reports and dated snapshots — not living design docs |
| `scripts/` | Day-to-day entry scripts only; see `scripts/README.md` |
| `scripts/archive/` | One-off migration, debug, and smoke utilities kept for reference |
| `tests/` | Automated tests; imports must follow script moves |
| `data/` | Runtime state/DBs/credentials — **not** source of truth in git (`data/*` gitignored) |
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
