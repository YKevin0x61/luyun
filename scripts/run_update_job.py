#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update Job oneshot entry: run Apply Update outside the web process.

Invoked by deploy/luyun-update.service (Type=oneshot) or Docker detached starter.
Reads queued intent from data/update_job.json, runs backup → fetch bundle →
install (atomic switch) → conditional deps → restart, and writes progress back
for Admin polling.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path when launched via systemd WorkingDirectory.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.github_release_config import get_effective_config  # noqa: E402
from services.release_update.factory import default_deploy_dir  # noqa: E402
from services.release_update.job_adapters import (  # noqa: E402
    PipDepsSyncAdapter,
    ReleaseBundleInstallAdapter,
    SnapshotBackupAdapter,
    build_main_service_adapter,
)
from services.release_update.job_runner import UpdateJobRunner  # noqa: E402
from services.release_update.job_state import (  # noqa: E402
    FileJobStateStore,
    job_log_path,
    job_state_path,
)


def _configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def build_runner(deploy_dir: Path) -> UpdateJobRunner:
    gh = get_effective_config()
    return UpdateJobRunner(
        job_store=FileJobStateStore(job_state_path()),
        backup=SnapshotBackupAdapter(),
        bundle=ReleaseBundleInstallAdapter(
            deploy_dir,
            github_repo=gh.repo or "",
            token=gh.token,
        ),
        deps=PipDepsSyncAdapter(deploy_dir),
        service=build_main_service_adapter(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the luyun Update Job once.")
    parser.add_argument(
        "--dry-run-contract",
        action="store_true",
        help="Print stage contract / paths and exit (no destructive work).",
    )
    args = parser.parse_args(argv)

    log_file = job_log_path()
    state_file = job_state_path()
    deploy_dir = default_deploy_dir()

    if args.dry_run_contract:
        stages = [
            "queued",
            "backing_up",
            "fetching_bundle",
            "installing",
            "syncing_deps",
            "restarting",
            "succeeded|failed",
        ]
        print("UPDATE_JOB_CONTRACT")
        print(f"state_file={state_file}")
        print(f"log_file={log_file}")
        print(f"deploy_dir={deploy_dir}")
        print("stages=" + ",".join(stages))
        print("backup_before_swap=1")
        print("preserve_data=1")
        print("out_of_process=1")
        print("bundle=luyun-release-bundle.tar.gz")
        print("checksums=SHA256SUMS")
        return 0

    _configure_logging(log_file)
    logger = logging.getLogger("update_job")
    logger.info("Update Job starting deploy_dir=%s state=%s", deploy_dir, state_file)

    runner = build_runner(deploy_dir)
    final = runner.run()
    logger.info(
        "Update Job finished stage=%s error=%s rollback_attempted=%s log=%s",
        final.stage,
        final.error,
        final.rollback_attempted,
        final.log_path or log_file,
    )
    return 0 if final.stage == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
