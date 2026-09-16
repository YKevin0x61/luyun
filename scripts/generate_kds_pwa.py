#!/usr/bin/env python3
"""Inject the generated KDS PWA manifest and versioned service worker."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SW_TEMPLATE_PATH = ROOT / "kds" / "pwa" / "sw-template.js"
MANIFEST_TEMPLATE_PATH = ROOT / "kds" / "pwa" / "manifest.template.json"
SW_FILENAME = "sw.js"
MANIFEST_FILENAME = "manifest.webmanifest"
MANIFEST_LINK = '<link rel="manifest" href="/kds/manifest.webmanifest">'


def _inject_manifest_link(index_path: Path) -> None:
    html = index_path.read_text(encoding="utf-8")
    if 'rel="manifest"' in html:
        return
    if "</head>" not in html:
        raise ValueError(f"{index_path} 缺少 </head>")
    html = html.replace(
        "</head>",
        f'    <meta name="theme-color" content="#0b6bcb">\n'
        f'    {MANIFEST_LINK}\n'
        f"  </head>",
        1,
    )
    index_path.write_text(html, encoding="utf-8")


def _precache_urls(dist_dir: Path) -> list[str]:
    urls: list[str] = []
    for path in sorted(dist_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(dist_dir)
        if relative.as_posix() == SW_FILENAME:
            continue
        if path.name == ".DS_Store" or path.suffix == ".map":
            continue
        urls.append("/kds/" + relative.as_posix())
    return urls


def _build_fingerprint(dist_dir: Path, urls: list[str]) -> str:
    digest = hashlib.sha256()
    for url in urls:
        relative = url.removeprefix("/kds/")
        path = dist_dir / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _verify_manifest_icons(dist_dir: Path, manifest: dict[str, Any]) -> None:
    for icon in manifest.get("icons", []):
        source = str(icon.get("src") or "")
        if not source.startswith("/kds/"):
            raise ValueError(f"KDS manifest 图标必须位于 /kds/: {source}")
        icon_path = dist_dir / source.removeprefix("/kds/")
        if not icon_path.is_file():
            raise ValueError(f"KDS PWA 图标不存在: {icon_path}")


def generate_kds_pwa(
    dist_dir: Path,
    app_version: str,
    *,
    sw_template_path: Path = SW_TEMPLATE_PATH,
    manifest_template_path: Path = MANIFEST_TEMPLATE_PATH,
) -> dict[str, Any]:
    dist_dir = Path(dist_dir)
    index_path = dist_dir / "index.html"
    if not index_path.is_file():
        raise ValueError(f"KDS 构建产物缺少 index.html: {dist_dir}")

    manifest = json.loads(manifest_template_path.read_text(encoding="utf-8"))
    _verify_manifest_icons(dist_dir, manifest)
    _inject_manifest_link(index_path)
    (dist_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    precache_urls = _precache_urls(dist_dir)
    fingerprint = _build_fingerprint(dist_dir, precache_urls)
    cache_name = f"luyun-kds-shell-{app_version}-{fingerprint[:12]}"
    template = sw_template_path.read_text(encoding="utf-8")
    service_worker = (
        template.replace("__LUYUN_KDS_CACHE_NAME__", json.dumps(cache_name))
        .replace("__LUYUN_KDS_VERSION__", json.dumps(app_version))
        .replace(
            "__LUYUN_KDS_PRECACHE_URLS__",
            json.dumps(precache_urls, ensure_ascii=False, indent=2),
        )
    )
    (dist_dir / SW_FILENAME).write_text(service_worker, encoding="utf-8")

    return {
        "cache_name": cache_name,
        "manifest": str(dist_dir / MANIFEST_FILENAME),
        "precache_count": len(precache_urls),
        "service_worker": str(dist_dir / SW_FILENAME),
        "version": app_version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    result = generate_kds_pwa(args.dist, args.version)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
