"""mitmproxy addon: capture POS/龙管家 login-related HTTPS traffic.

Usage:
  mitmdump -s scripts/archive/mitm_pos_login_filter.py -p 8080 \\
    --set block_global=false \\
    --flow-detail 0

Writes redacted summaries to /tmp/luyun-mitm/captures.jsonl
and full bodies (password redacted) under /tmp/luyun-mitm/flows/
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from mitmproxy import ctx, http

OUT = Path("/tmp/luyun-mitm")
FLOWS = OUT / "flows"
LOG = OUT / "captures.jsonl"

INTERESTING_HOST_SUBSTR = (
    "tcsl.com.cn",
    "wuuxiang.com",
    "fxscm.net",
)

INTERESTING_PATH_SUBSTR = (
    "login",
    "auth",
    "token",
    "flag",
    "getGroupList",
    "housekeeper",
    "auth-center",
    "publick",
    "rsa",
    "password",
    "sms",
    # cy7mm POS / 实时桌态
    "cy7MobileReports",
    "cy7MicroserviceUser",
    "realtimetable",
    "getrealtime",
    "getbusypoint",
    "getbsdetail",
    "settledbill",
    "mobilemanager",
    "reportm",
    "ztc",
    "getALlManageableShopList",
)
JSONL_BODY_LIMIT = 4000
FULL_BODY_LIMIT = 2_000_000  # save up to 2MB in flows/

PASSWORD_KEYS = re.compile(
    r'("(?:password|pwd|passwd|encryption)"\s*:\s*")([^"]*)(")',
    re.I,
)
TOKEN_KEYS = re.compile(
    r'("(?:token|accessToken|access_token|TCSL-BP-TOKEN)"\s*:\s*")([^"]*)(")',
    re.I,
)
MOBILE_KEYS = re.compile(
    r'("(?:mobile|phone|username)"\s*:\s*")(\d{3})\d+(\d{4})(")',
    re.I,
)


def _redact(text: str) -> str:
    if not text:
        return text
    text = PASSWORD_KEYS.sub(r'\1***\3', text)
    text = TOKEN_KEYS.sub(
        lambda m: f"{m.group(1)}{m.group(2)[:8]}…(len={len(m.group(2))}){m.group(3)}",
        text,
    )
    text = MOBILE_KEYS.sub(r'\1\2****\3\4', text)
    return text


def _interesting(flow: http.HTTPFlow) -> bool:
    host = flow.request.pretty_host or ""
    path = flow.request.path or ""
    if not any(h in host for h in INTERESTING_HOST_SUBSTR):
        return False
    # skip static assets
    if any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".css", ".js", ".svg")):
        return False
    if "video-cdn" in host or "oss-cn-beijing" in host:
        return False
    if any(h in host for h in ("tcsl.com.cn",)):
        return True
    # wuuxiang: keep POS/report API paths (cy7mm WebView, cyuser microservices)
    if "wuuxiang.com" in host:
        return any(s in path for s in INTERESTING_PATH_SUBSTR)
    return any(s in path for s in INTERESTING_PATH_SUBSTR)


class PosLoginCapture:
    def load(self, loader):  # noqa: ANN001
        OUT.mkdir(parents=True, exist_ok=True)
        FLOWS.mkdir(parents=True, exist_ok=True)
        ctx.log.info(f"[pos-capture] writing to {OUT}")

    def response(self, flow: http.HTTPFlow) -> None:
        if not _interesting(flow):
            return
        host = flow.request.pretty_host
        path = flow.request.path.split("?", 1)[0]
        method = flow.request.method
        status = flow.response.status_code if flow.response else None
        req_body = ""
        try:
            req_body = flow.request.get_text(strict=False) or ""
        except Exception:
            req_body = ""
        resp_body = ""
        try:
            resp_body = flow.response.get_text(strict=False) or "" if flow.response else ""
        except Exception:
            resp_body = ""

        req_full = _redact(req_body)[:FULL_BODY_LIMIT]
        resp_full = _redact(resp_body)[:FULL_BODY_LIMIT]
        req_body_r = req_full[:JSONL_BODY_LIMIT]
        resp_body_r = resp_full[:JSONL_BODY_LIMIT]
        ts = time.strftime("%Y%m%d-%H%M%S")
        safe_path = re.sub(r"[^a-zA-Z0-9._-]+", "_", path)[:80]
        name = f"{ts}_{method}_{status}_{host}_{safe_path}"
        req_headers = {
            k: ("***" if "token" in k.lower() or k.lower() == "cookie" else v)
            for k, v in flow.request.headers.items()
            if k.lower()
            in (
                "content-type",
                "tcsl-bp-token",
                "token",
                "authorization",
                "cookie",
                "user-agent",
                "origin",
                "referer",
            )
        }
        record = {
            "ts": ts,
            "method": method,
            "status": status,
            "url": _redact(flow.request.pretty_url),
            "req_headers": req_headers,
            "req_body": req_body_r,
            "resp_body": resp_body_r,
            "req_body_len": len(req_body),
            "resp_body_len": len(resp_body),
        }
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        full_record = {**record, "req_body": req_full, "resp_body": resp_full}
        (FLOWS / f"{name}.json").write_text(
            json.dumps(full_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        table_markers = ("realtimetable", "getbusy", "getbsdetail", "settledbill", "getrealtime")
        if any(m in path.lower() for m in table_markers):
            mark = "TABLE"
        elif any(s in path.lower() for s in ("login", "auth", "token", "sms")):
            mark = "LOGIN?"
        else:
            mark = "pos"
        ctx.log.warn(
            f"[pos-capture][{mark}] {method} {status} {host}{path} "
            f"req={len(req_body)}B resp={len(resp_body)}B"
        )


addons = [PosLoginCapture()]
