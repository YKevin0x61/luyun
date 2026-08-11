"""配方（SOP）模块 REST 路由。返回 JSON 与 HTML 片段；写操作需 admin 鉴权，读路由保持公开。"""

from __future__ import annotations

import csv
import io
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from services.recipes.store import (
    RecipeStore, SLUG_MAX_LEN, TEXT_FIELD_MAX_LEN, BODY_MAX_LEN,
)
from services.recipes.rendering import render_markdown_to_html, render_markdown_to_docx
from services.recipes.sop_parse import infer_recipe_is_new
from api.security import verify_admin_token

router = APIRouter(prefix="/api/recipes", tags=["recipes"])

CSV_IMPORT_MAX_BYTES = 2 * 1024 * 1024
CSV_IMPORT_MAX_ROWS = 2_000
CSV_REQUIRED_FIELDS = {"section", "recipe_name", "body_markdown", "sort_order", "is_new"}
DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@")
FORBIDDEN_SLUG_CHARS = set('/\\:*?"<>|\n\r\t\x00')


def _get_recipe_store() -> RecipeStore:
    from main import recipe_store
    if recipe_store is None:
        raise HTTPException(status_code=500, detail="配方库未初始化")
    return recipe_store


# ---- 校验 ----
def _validate_slug(slug: str) -> str:
    s = (slug or "").strip()
    if not s or s in (".", ".."):
        raise HTTPException(status_code=400, detail="标识不合法")
    if slug != s:
        raise HTTPException(status_code=400, detail="标识首尾不能有空格")
    if len(s) > SLUG_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"标识长度不能超过 {SLUG_MAX_LEN} 个字符")
    if any(ch in FORBIDDEN_SLUG_CHARS for ch in s):
        raise HTTPException(status_code=400, detail="标识不能包含 / \\ : * ? \" < > | 或换行等字符")
    return s


def _validate_text(value: str, label: str, *, required: bool = True, max_len: int = TEXT_FIELD_MAX_LEN) -> str:
    text = (value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if len(text) > max_len:
        raise HTTPException(status_code=400, detail=f"{label}长度不能超过 {max_len} 个字符")
    return text


def _validate_body(value: str) -> str:
    body = value or ""
    if len(body) > BODY_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"正文长度不能超过 {BODY_MAX_LEN} 个字符")
    return body


def _csv_safe_cell(value) -> str:
    text = "" if value is None else str(value)
    if text.startswith(DANGEROUS_CSV_PREFIXES):
        return "'" + text
    return text


def _parse_bool_flag(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "是"}


# ---- 请求体 ----
class StationCreate(BaseModel):
    slug: str
    title: Optional[str] = None


class StationRename(BaseModel):
    title: str


class RecipeCreate(BaseModel):
    section: str = "配方"
    recipe_name: str
    body: str = ""
    sort_order: Optional[int] = None
    is_new: bool = False


class RecipeUpdate(BaseModel):
    section: str = "配方"
    recipe_name: str
    body: str = ""
    sort_order: Optional[int] = None
    is_new: bool = False


# ---- 浏览 ----
@router.get("/stations")
async def list_stations(store: RecipeStore = Depends(_get_recipe_store)):
    return {"stations": await store.list_stations()}


@router.get("/stations/{slug}")
async def station_detail(slug: str, include_inactive: bool = False,
                         store: RecipeStore = Depends(_get_recipe_store)):
    station = await store.get_station(slug)
    if station is None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    md = await store.station_display_markdown(slug, include_inactive=include_inactive)
    if md is None:
        raise HTTPException(status_code=404, detail="该岗位暂无可显示条目")
    return {"slug": station["slug"], "title": station["title"],
            "content_html": render_markdown_to_html(md)}


@router.get("/stations/{slug}/recipes")
async def list_recipes(slug: str, store: RecipeStore = Depends(_get_recipe_store)):
    if not await store.station_exists(slug):
        raise HTTPException(status_code=404, detail="岗位不存在")
    return {"recipes": await store.list_recipes(slug)}


# ---- 岗位管理 ----
@router.post("/stations", dependencies=[Depends(verify_admin_token)])
async def create_station(payload: StationCreate, store: RecipeStore = Depends(_get_recipe_store)):
    slug = _validate_slug(payload.slug)
    title = _validate_text((payload.title or "").strip() or slug, "岗位名称")
    if await store.station_exists(slug):
        raise HTTPException(status_code=400, detail="该标识已存在")
    await store.create_station(slug, title)
    return {"slug": slug, "title": title}


@router.post("/stations/{slug}/rename", dependencies=[Depends(verify_admin_token)])
async def rename_station(slug: str, payload: StationRename, store: RecipeStore = Depends(_get_recipe_store)):
    if not await store.station_exists(slug):
        raise HTTPException(status_code=404, detail="岗位不存在")
    title = _validate_text(payload.title, "岗位名称")
    await store.rename_station(slug, title)
    return {"slug": slug, "title": title}


@router.delete("/stations/{slug}", dependencies=[Depends(verify_admin_token)])
async def delete_station(slug: str, store: RecipeStore = Depends(_get_recipe_store)):
    if not await store.station_exists(slug):
        raise HTTPException(status_code=404, detail="岗位不存在")
    await store.delete_station(slug)
    return {"ok": True}


# ---- 条目管理 ----
@router.post("/stations/{slug}/recipes", dependencies=[Depends(verify_admin_token)])
async def create_recipe(slug: str, payload: RecipeCreate, store: RecipeStore = Depends(_get_recipe_store)):
    if not await store.station_exists(slug):
        raise HTTPException(status_code=404, detail="岗位不存在")
    section = _validate_text(payload.section or "配方", "章节")
    name = _validate_text(payload.recipe_name, "条目名称")
    body = _validate_body(payload.body)
    rid = await store.create_recipe(slug, section, name, body, payload.sort_order, payload.is_new)
    return {"id": rid}


@router.put("/recipes/{recipe_id}", dependencies=[Depends(verify_admin_token)])
async def update_recipe(recipe_id: int, payload: RecipeUpdate, store: RecipeStore = Depends(_get_recipe_store)):
    current = await store.get_recipe(recipe_id)
    if current is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    section = _validate_text(payload.section or "配方", "章节")
    name = _validate_text(payload.recipe_name, "条目名称")
    body = _validate_body(payload.body)
    sort_order = payload.sort_order if payload.sort_order is not None else int(current["sort_order"])
    updated = await store.update_recipe(recipe_id, section, name, body, sort_order, payload.is_new)
    return updated


@router.delete("/recipes/{recipe_id}", dependencies=[Depends(verify_admin_token)])
async def delete_recipe(recipe_id: int, store: RecipeStore = Depends(_get_recipe_store)):
    slug = await store.delete_recipe(recipe_id)
    if slug is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return {"ok": True, "station_slug": slug}


@router.post("/recipes/{recipe_id}/toggle-active", dependencies=[Depends(verify_admin_token)])
async def toggle_active(recipe_id: int, store: RecipeStore = Depends(_get_recipe_store)):
    updated = await store.toggle_active(recipe_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return updated


@router.get("/recipes/{recipe_id}/history")
async def recipe_history(recipe_id: int, store: RecipeStore = Depends(_get_recipe_store)):
    current = await store.get_recipe(recipe_id)
    if current is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return {"current": current, "history": await store.list_history(recipe_id)}


# ---- CSV ----
@router.get("/stations/{slug}/export")
async def export_csv(slug: str, store: RecipeStore = Depends(_get_recipe_store)):
    if not await store.station_exists(slug):
        raise HTTPException(status_code=404, detail="岗位不存在")
    rows = await store.list_recipes(slug)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "recipe_name", "body_markdown", "sort_order", "is_new"])
    for r in rows:
        writer.writerow([
            _csv_safe_cell(r["section"]), _csv_safe_cell(r["recipe_name"]),
            _csv_safe_cell(r["body_markdown"]), r["sort_order"], r["is_new"],
        ])
    filename = quote(f"{slug}.csv")
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/stations/{slug}/import", dependencies=[Depends(verify_admin_token)])
async def import_csv(slug: str, csv_file: UploadFile = File(...),
                     store: RecipeStore = Depends(_get_recipe_store)):
    if not await store.station_exists(slug):
        raise HTTPException(status_code=404, detail="岗位不存在")
    raw = await csv_file.read(CSV_IMPORT_MAX_BYTES + 1)
    if len(raw) > CSV_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="CSV 文件不能超过 2MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="导入失败：CSV 必须使用 UTF-8 编码")
    try:
        reader = csv.DictReader(io.StringIO(content))
        if reader.fieldnames is None:
            raise HTTPException(status_code=400, detail="CSV 文件缺少表头")
        missing = CSV_REQUIRED_FIELDS.difference(reader.fieldnames)
        if missing:
            raise HTTPException(status_code=400, detail="CSV 表头缺少字段：" + "、".join(sorted(missing)))
        pending: list[tuple] = []
        errors: list[str] = []
        for idx, row_data in enumerate(reader, start=2):
            if idx - 1 > CSV_IMPORT_MAX_ROWS:
                errors.append(f"最多一次导入 {CSV_IMPORT_MAX_ROWS} 条")
                break
            try:
                section = _validate_text(row_data.get("section") or "配方", "章节")
                name = _validate_text(row_data.get("recipe_name") or "", "条目名称")
                body = _validate_body(row_data.get("body_markdown") or "")
            except HTTPException as exc:
                errors.append(f"第 {idx} 行：{exc.detail}")
                if len(errors) >= 10:
                    errors.append("错误过多，已停止检查")
                    break
                continue
            try:
                sort_order = int((row_data.get("sort_order") or "0").strip() or "0")
            except ValueError:
                errors.append(f"第 {idx} 行：排序号必须是整数")
                continue
            is_new = 1 if (infer_recipe_is_new(name, body) or _parse_bool_flag(row_data.get("is_new"))) else 0
            pending.append((section, name, body, sort_order, is_new))
    except csv.Error as e:
        raise HTTPException(status_code=400, detail=f"导入失败：CSV 格式错误：{e}")
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    if not pending:
        raise HTTPException(status_code=400, detail="CSV 中没有可导入的有效条目")
    count = await store.bulk_insert_recipes(slug, pending)
    return {"imported": count}


# ---- docx ----
@router.get("/stations/{slug}/docx")
async def export_docx(slug: str, store: RecipeStore = Depends(_get_recipe_store)):
    station = await store.get_station(slug)
    if station is None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    md = await store.station_display_markdown(slug)
    if md is None:
        raise HTTPException(status_code=404, detail="该岗位暂无可显示条目")
    doc = render_markdown_to_docx(md)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    filename = quote(f"{slug}.docx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
