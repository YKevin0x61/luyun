#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
POS session: browser/login/recovery, business-hours gate, and table POS HTTP/parse.
"""

import json
import logging

from datetime import datetime
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from config import settings
from scraper._common import CHINA_TZ, ScraperSessionError, pos_response_indicates_auth_failure
from scraper.pos_http_client import PosHttpClient
from scraper.sly20_auth import build_cy7mm_biz_data, login_sly20
from services import credentials_store
from typing import Any, Dict, List, Optional
from services.dish_normalize import strip_trailing_dash_suffix
from services.credentials_store import CredentialBundle
import asyncio

logger = logging.getLogger(__name__)

CY7MM_TEMP_PAGE_URL = "https://cy7mm.wuuxiang.com/tempPage"
CY7MM_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 16; sdk_gphone64_arm64 Build/BE2A.250530.026.D1; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 Mobile "
    "Safari/537.36 TCSLMessenger"
)
CY7MM_SSO_TIMEOUT_MS = 45000
# 一次完整 2.0 登录 + cy7mm SSO 实测约 25~48s，冷却必须明显长于单次登录耗时，
# 否则密码失效时会持续不断地重试登录。
LOGIN_RETRY_COOLDOWN_SECONDS = 60


class PosSession:
    """Browser/login/recovery + table list/detail POS HTTP and parse."""

    def __init__(self, dish_catalog, *, logger_: Optional[logging.Logger] = None):
        from scraper.order_line_builder import OrderLineBuilder

        self.dish_catalog = dish_catalog
        self.order_lines = OrderLineBuilder(dish_catalog)
        self.logger = logger_ or logging.getLogger(__name__)
        self.browser = None
        self.page = None
        self.context = None
        self.playwright = None
        self._initialized = False
        self._paused = False
        self._last_business_check = None
        self._init_lock = asyncio.Lock()
        self._last_login_failed_at = None
        self._login_retry_cooldown_seconds = LOGIN_RETRY_COOLDOWN_SECONDS
        self._consecutive_login_failures = 0
        self._creds: Optional[CredentialBundle] = None
        self._no_credentials = True
        self.http = PosHttpClient(
            get_page=lambda: self.page,
            recover=self._recover_session,
            timeout_ms=settings.SETTLED_BILL_API_TIMEOUT_MS,
            max_retries=settings.SETTLED_BILL_API_MAX_RETRIES,
            retry_backoff_s=settings.SETTLED_BILL_API_RETRY_BACKOFF_S,
            logger_=self.logger,
        )
        self.config = {
            "settings": {
                "headless": True,
                "timeout": 30000,
                "retry_count": 3,
                "interval_min": 5,
                "interval_max": 20,
                "delivery_cancel_miss_threshold": 3,
            },
            "business_hours": {
                "work_start": "07:30",
                "work_end": "21:30",
            },
        }
        self.table_mapping = {
            "1": 698, "2": 699, "3": 700, "5": 701, "6": 702,
            "8": 703, "9": 704, "10": 705, "11": 706, "12": 707,
            "13": 708, "15": 709, "16": 710, "18": 711, "19": 712,
            "20": 713, "21": 714, "22": 715, "23": 716, "25": 717,
            "26": 718, "28": 719, "29": 720, "30": 721, "31": 722,
            "32": 723, "33": 724, "35": 725, "36": 726, "38": 727,
            "39": 728, "50": 729, "51": 730, "52": 731, "53": 732,
            "55": 733, "56": 734, "58": 735, "59": 736, "60": 737,
            "61": 738, "62": 739, "63": 740, "福运": 741, "禄运": 742,
            "加1": 743, "加2": 744, "加3": 745, "加4": 746,
            "加5": 747, "加6": 748, "加7": 749, "加8": 749, "加9": 750, "加10": 751,
        }
        self._load_credentials_sync()

    async def ensure_ready(self, *, ignore_pause: bool = False) -> bool:
        """Public gate: browser ready and logged in (optional pause bypass for reconcile)."""
        return await self._ensure_initialized(ignore_pause=ignore_pause)

    def classify_dish(self, dish_name: str) -> str:
        from scraper.order_line_builder import classify_dish

        return classify_dish(dish_name)

    def load_credentials_sync(self) -> None:
        """Refresh credential cache from disk (sync; safe in idle loop)."""
        self._load_credentials_sync()

    def refresh_business_hours(self) -> bool:
        """Recompute pause from business hours; returns whether currently open."""
        return self._is_business_hours()

    def inject_credentials(self, bundle) -> None:
        """Install credentials for login probes (does not persist to disk)."""
        self._creds = bundle
        self._no_credentials = bundle is None

    def force_unpaused(self) -> None:
        """Bypass business-hours pause (CLI / auth probes)."""
        self._paused = False

    async def init_browser(self, headless: bool = True):
        return await self._init_browser(headless=headless)

    async def login(self, phone: str, password: str) -> bool:
        return await self._login(phone, password)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def no_credentials(self) -> bool:
        return self._no_credentials

    @property
    def consecutive_login_failures(self) -> int:
        return self._consecutive_login_failures

    @property
    def initialized(self) -> bool:
        return self._initialized

    def session_status(self) -> dict:
        """DTO for /api/scraper/status (no private-field poking)."""
        return {
            "paused": self._paused,
            "has_credentials": bool(self._creds),
            "initialized": self._initialized,
            "no_credentials": self._no_credentials,
            "last_login_failed_at": self._last_login_failed_at,
            "login_retry_cooldown_seconds": int(self._login_retry_cooldown_seconds or 0),
            "consecutive_login_failures": self._consecutive_login_failures,
            "table_list_url": self._creds.table_list_url if self._creds else "",
        }

    @property
    def _settled_api_failures(self) -> int:
        """Backward-compatible alias; counter lives on PosHttpClient."""
        return self.http.api_failures

    async def _recover_session(self) -> bool:
        """会话失效后强制重新登录一次。

        ``ignore_pause=True``：调用方已经在执行一个获准的请求（可能是打烊后的对账，
        也可能恰好跨过营业时段边界），此时不该再被营业时段门禁拦下。
        """
        self.logger.warning("🔁 检测到 cy7mm 会话失效，尝试重新登录…")
        self._initialized = False
        return await self._ensure_initialized(ignore_pause=True)

    async def _post_pos_form(self, url: str, headers: dict, encoded_data: str, context_label: str):
        return await self.http.post_form(
            url,
            headers=headers,
            encoded_data=encoded_data,
            context_label=context_label,
        )

    async def _pos_request_with_recovery(self, request_fn, *, context_label: str):
        """Thin alias → PosHttpClient.request_with_recovery (login probes must not use this)."""
        return await self.http.request_with_recovery(request_fn, context_label=context_label)

    def _load_credentials_sync(self) -> None:
        """同步刷新凭据缓存（在 ``__init__`` 中调用，不会触发任何 IO 异步等待）。"""
        bundle = credentials_store.get_credentials()
        self._creds = bundle
        self._no_credentials = bundle is None
        if self._no_credentials:
            self.logger.warning(
                "⚠️  尚未配置登录凭据，请访问 /setup 页面填写账号 / 密码 / 门店 ID 后再启动爬取"
            )
        else:
            self.logger.info(
                "🔐 已加载登录凭据（shopId=%s, companyId=%s, shopName=%s）",
                bundle.shop_id,
                bundle.company_id,
                bundle.shop_name,
            )

    async def reload_credentials(self) -> bool:
        """凭据被外部更新后调用：刷新缓存并强制下次循环重新登录。"""
        previous = self._creds
        bundle = credentials_store.reload()
        self._creds = bundle
        self._no_credentials = bundle is None

        if bundle is None:
            self.logger.warning("🧹 凭据已清空，爬虫进入待机状态")
            self._initialized = False
            try:
                if self.page:
                    await self.page.close()
                if self.browser:
                    await self.browser.close()
            except Exception:
                pass
            self.page = None
            self.browser = None
            return False

        cred_changed = (
            previous is None
            or previous.phone != bundle.phone
            or previous.password != bundle.password
            or previous.shop_id != bundle.shop_id
            or previous.company_id != bundle.company_id
            or previous.delivery_shop_id != bundle.delivery_shop_id
            or previous.shop_name != bundle.shop_name
        )
        if cred_changed:
            self.logger.info("♻️ 凭据已更新，下一轮爬取将重新登录")
            self._initialized = False
        return True

    def apply_runtime_settings(self, data: dict) -> None:
        """把运行配置字典应用到内存 ``self.config``（同步，供启动/热重载调用）。

        仅覆盖存在的字段，缺省沿用当前内存值，避免部分字段丢失。
        """
        business_hours = self.config.setdefault('business_hours', {})
        if data.get('work_start'):
            business_hours['work_start'] = data['work_start']
        if data.get('work_end'):
            business_hours['work_end'] = data['work_end']

        scraper_settings = self.config.setdefault('settings', {})
        if 'headless' in data:
            scraper_settings['headless'] = bool(data['headless'])
        if 'timeout_ms' in data:
            scraper_settings['timeout'] = int(data['timeout_ms'])
        if 'retry_count' in data:
            scraper_settings['retry_count'] = int(data['retry_count'])
        if 'interval_min' in data:
            scraper_settings['interval_min'] = int(data['interval_min'])
        if 'interval_max' in data:
            scraper_settings['interval_max'] = int(data['interval_max'])
        if 'delivery_cancel_miss_threshold' in data:
            scraper_settings['delivery_cancel_miss_threshold'] = int(
                data['delivery_cancel_miss_threshold']
            )

    async def reload_runtime_settings(self, db) -> dict:
        """从 DB 重新加载运行配置并应用到内存，返回生效后的配置。"""
        from services.runtime_settings import load_runtime_settings

        data = await load_runtime_settings(db)
        self.apply_runtime_settings(data)
        self.logger.info(
            "♻️ 运行配置已生效：营业 %s-%s，轮询 %s~%ss，headless=%s，retry=%s",
            self.config['business_hours']['work_start'],
            self.config['business_hours']['work_end'],
            self.config['settings']['interval_min'],
            self.config['settings']['interval_max'],
            self.config['settings']['headless'],
            self.config['settings']['retry_count'],
        )
        return data

    def _is_business_hours(self) -> bool:
        """检查当前是否在营业时间内 (非休息时间)"""
        try:
            now = datetime.now(CHINA_TZ)
            current_time = now.strftime("%H:%M")

            work_start = self.config['business_hours']['work_start']  # 07:30
            work_end = self.config['business_hours']['work_end']      # 21:30
            # 休息时段由营业时段派生（仅支持同日营业）：营业结束即休息开始，次日营业开始即休息结束
            rest_start = work_end   # 21:30
            rest_end = work_start   # 07:30 (次日)

            # 检查是否在休息时间内 (21:30-次日07:30)
            # 如果当前时间 >= 21:30 或者 <= 07:30，则为休息时间
            is_rest_time = (current_time >= rest_start) or (current_time <= rest_end)
            is_business = not is_rest_time

            # 检查状态变化
            if self._last_business_check is not None:
                was_business = self._last_business_check
                if was_business and not is_business:
                    self.logger.info(f"⏰ 休息时间开始 ({rest_start}-次日{rest_end})，爬虫自动暂停")
                    self._paused = True
                elif not was_business and is_business:
                    self.logger.info(f"🕐 营业时间开始 ({work_start}-{work_end})，爬虫自动启动")
                    self._paused = False
            else:
                # 首次检查，设置初始状态
                if not is_business:
                    self.logger.info(f"⏸️  当前处于休息时间 ({rest_start}-次日{rest_end})，爬虫暂停")
                    self._paused = True
                else:
                    self.logger.info(f"✅ 当前处于营业时间 ({work_start}-{work_end})，爬虫运行")
                    self._paused = False

            self._last_business_check = is_business
            return is_business

        except Exception as e:
            self.logger.error(f"检查营业时间失败: {e}")
            return True  # 如果检查失败，默认认为在营业时间内

    async def _ensure_initialized(self, *, ignore_pause: bool = False):
        """确保爬虫已初始化。

        ``ignore_pause``：跳过营业时段门禁。供打烊后运行的对账任务使用——
        自动对账默认 22:05 触发，此时 ``_paused`` 已为 True。
        """
        # 先检查营业时间
        self._is_business_hours()

        # 检查是否暂停
        if self._paused and not ignore_pause:
            self.logger.info("⏸️  爬虫已暂停（非营业时间）")
            return False

        # 没有凭据时直接待机，避免反复尝试空登录
        if self._creds is None:
            self._load_credentials_sync()
        if self._creds is None:
            return False

        if not self._initialized:
            async with self._init_lock:
                if self._initialized:
                    return True

                if self._last_login_failed_at is not None:
                    elapsed_seconds = (datetime.now(CHINA_TZ) - self._last_login_failed_at).total_seconds()
                    if elapsed_seconds < self._login_retry_cooldown_seconds:
                        wait_seconds = int(self._login_retry_cooldown_seconds - elapsed_seconds)
                        self.logger.warning(f"⏳ 登录失败冷却中，{wait_seconds}s 后重试")
                        return False

                try:
                    headless = self.config.get('settings', {}).get('headless', True)
                    await self._init_browser(headless=headless)

                    phone = self._creds.phone
                    password = self._creds.password

                    if phone and password:
                        if await self._login(phone, password):
                            self.logger.info("✅ 爬虫登录成功")
                            self._initialized = True
                            self._last_login_failed_at = None
                            self._consecutive_login_failures = 0
                        else:
                            self._last_login_failed_at = datetime.now(CHINA_TZ)
                            self._consecutive_login_failures += 1
                            self.logger.error(
                                "❌ 爬虫登录失败（连续 %s 次）", self._consecutive_login_failures
                            )
                            return False
                    else:
                        self.logger.error("❌ 凭据信息不完整，请到 /setup 页面重新配置")
                        return False

                except Exception as e:
                    self._last_login_failed_at = datetime.now(CHINA_TZ)
                    self._consecutive_login_failures += 1
                    self.logger.error(f"❌ 爬虫初始化失败: {e}")
                    return False

        return True

    async def _init_browser(self, headless=True):
        """初始化浏览器"""
        try:
            # 如果已经有浏览器实例，先关闭
            if self.page:
                try:
                    await self.page.close()
                except:
                    pass
            if getattr(self, "context", None):
                try:
                    await self.context.close()
                except:
                    pass
            if self.browser:
                try:
                    await self.browser.close()
                except:
                    pass
            if self.playwright:
                try:
                    await self.playwright.stop()
                except:
                    pass

            # 重新初始化
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=headless,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--no-zygote',
                    '--disable-gpu'
                ]
            )
            self.context = await self.browser.new_context(
                user_agent=CY7MM_ANDROID_UA,
                viewport={"width": 1920, "height": 1080},
            )
            self.page = await self.context.new_page()

            self.page.set_default_timeout(30000)

            self.logger.info("✅ 浏览器初始化成功")

        except Exception as e:
            self.logger.error(f"❌ 浏览器初始化失败: {e}")
            raise

    async def _login(self, phone: str, password: str) -> bool:
        """2.0 auth-center 登录 + Playwright cy7mm tempPage SSO 建立 Cookie 会话。"""
        try:
            self.logger.info("🔐 正在通过龙管家 2.0 登录…")

            if not self.page or self.page.is_closed():
                self.logger.error("❌ 浏览器页面已关闭，重新初始化")
                await self._init_browser(headless=self.config.get('settings', {}).get('headless', True))

            if self._creds is None:
                self.logger.error("❌ 无登录凭据")
                return False

            login_result = await login_sly20(phone, password)
            if not login_result.ok:
                self.logger.error(
                    "❌ 2.0 登录失败: %s",
                    login_result.message or "unknown error",
                )
                return False

            self.logger.info(
                "✅ 2.0 auth-center 登录成功 (accountId=%s)",
                (login_result.principal or {}).get("accountId"),
            )

            return await self._establish_cy7mm_session(login_result)

        except Exception as e:
            self.logger.error(f"❌ 登录失败: {e}")
            error_message = str(e).lower()
            if any(token in error_message for token in ("target page", "context", "browser has been closed")):
                try:
                    if self.page and not self.page.is_closed():
                        await self.page.close()
                except Exception:
                    pass
                try:
                    if self.browser:
                        await self.browser.close()
                except Exception:
                    pass
                try:
                    if self.playwright:
                        await self.playwright.stop()
                except Exception:
                    pass
                self.page = None
                self.browser = None
                self.playwright = None
            return False

    async def _establish_cy7mm_session(self, login_result) -> bool:
        """Playwright 打开 cy7mm tempPage，注入 App SSO bizData，换取 cy7mm Cookie。"""
        assert self._creds is not None
        assert self.page is not None

        target_path = (
            f"/home/tableList/1/{self._creds.shop_id}/{self._creds.company_id}"
        )
        biz_data = build_cy7mm_biz_data(
            token=login_result.token,
            principal=login_result.principal or {},
            target_path=target_path,
        )
        biz_json = json.dumps(biz_data, ensure_ascii=False)

        # Must be an IIFE — a bare `() => { ... }` is never invoked by add_init_script.
        await self.context.add_init_script(
            f"""
            (function () {{
              const bizData = {biz_json};
              window.JsMobile = {{
                getUserDataByMobile: function () {{
                  const deliver = () => {{
                    if (typeof window.sendData === 'function') {{
                      window.sendData(bizData);
                      return;
                    }}
                    setTimeout(deliver, 100);
                  }};
                  deliver();
                }},
              }};
            }})();
            """
        )

        self.logger.info("🌐 建立 cy7mm WebView SSO 会话 (tempPage → tableList)…")
        await self.page.goto(CY7MM_TEMP_PAGE_URL, wait_until="domcontentloaded")

        try:
            await self.page.wait_for_function(
                """() => {
                  const path = location.pathname || '';
                  return path.includes('/home/tableList') || path.includes('/home/realTimeTable');
                }""",
                timeout=CY7MM_SSO_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            current_url = self.page.url
            if "login" in current_url:
                self.logger.error("❌ cy7mm SSO 失败，被重定向到登录页: %s", current_url)
            else:
                self.logger.error("❌ cy7mm SSO 超时，当前 URL: %s", current_url)
            return False

        table_list_url = self._creds.table_list_url
        if "tableList" not in self.page.url:
            await self.page.goto(table_list_url, wait_until="networkidle")
        else:
            await self.page.wait_for_load_state("networkidle")

        if "login" in self.page.url:
            self.logger.error("❌ cy7mm 会话无效，仍在登录页")
            return False

        probe = await self.probe_busy_point_api_login_ok()
        if not probe.get("ok"):
            self.logger.error(
                "❌ getbusypointdata 校验失败: %s",
                probe.get("message") or "unknown",
            )
            return False

        rows = probe.get("rows_count")
        self.logger.info(
            "✅ cy7mm 会话就绪，getbusypointdata 校验通过 (tables=%s)",
            rows if rows is not None else "?",
        )
        return True

    async def release_browser(self) -> bool:
        """释放浏览器资源（非营业时段调用），幂等。

        打烊后主循环每 5 分钟空转一次，若不释放，headless Chromium 会空挂约 10 小时。
        返回是否实际执行了释放，便于调用方避免重复打印日志。
        """
        if self.browser is None and self.page is None and self.playwright is None:
            return False

        await self.close()
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
        return True

    async def close(self):
        """关闭爬虫"""
        try:
            self._initialized = False

            if self.page and not self.page.is_closed():
                try:
                    await self.page.close()
                except Exception as e:
                    self.logger.warning(f"关闭页面时出错: {e}")

            if getattr(self, "context", None):
                try:
                    await self.context.close()
                except Exception as e:
                    self.logger.warning(f"关闭浏览器上下文时出错: {e}")

            if self.browser:
                try:
                    await self.browser.close()
                except Exception as e:
                    self.logger.warning(f"关闭浏览器时出错: {e}")

            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception as e:
                    self.logger.warning(f"停止Playwright时出错: {e}")

            self.logger.info("✅ 爬虫已关闭")
        except Exception as e:
            self.logger.error(f"关闭爬虫失败: {e}")

    async def scrape_table_data(self) -> List[Dict]:
        """获取餐桌数据。

        会话 Cookie 存活于 browser context，``page.request`` 与 ``Referer`` 头均不依赖
        当前页面 URL，因此无需每轮重新导航到 tableList 页——实测每轮可省约 3.5s。
        建立会话时（``_establish_cy7mm_session``）已导航过一次。
        """
        try:
            # 确保已初始化
            if not await self._ensure_initialized():
                return []

            # 检查营业时间
            if not self._is_business_hours():
                return []

            # 通过API获取餐桌数据
            table_data = await self._get_table_data_via_api()

            if table_data:
                self.logger.info(f"✅ 获取到 {len(table_data)} 个餐桌数据")
                return table_data
            else:
                self.logger.info("ℹ️  当前没有餐桌数据，可能是非营业时间或餐厅暂时无订单")
                return []

        except ScraperSessionError:
            raise
        except Exception as e:
            self.logger.error(f"获取餐桌数据失败: {e}")
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}")
            return []

    BUSY_POINT_API_URL = (
        "https://cy7mm.wuuxiang.com/cy7MobileReports/canyin/mobile/realtimetablestate/getbusypointdata"
    )
    BS_DETAIL_API_URL = (
        "https://cy7mm.wuuxiang.com/cy7MobileReports/canyin/mobile/realtimetablestate/getbsdetail"
    )

    @staticmethod
    def _busy_point_response_indicates_auth_failure(payload: Dict) -> bool:
        """根据餐桌列表 API 返回体判断是否未登录 / 无权。"""
        return pos_response_indicates_auth_failure(payload)

    async def _busy_point_api_request_raw(self) -> tuple[int, Any]:
        """调用餐桌占用列表 getbusypointdata，返回 (HTTP 状态码, JSON 或原始文本)。"""
        if self.page is None or self.page.is_closed():
            raise RuntimeError("浏览器页面未就绪")
        if self._creds is None:
            raise RuntimeError("无登录凭据")

        headers = await self.http.build_headers(
            referer=self._creds.table_list_referer,
            content_type="application/json;charset=UTF-8",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            ),
        )
        payload = {
            "shopId": self._creds.shop_id,
            "companyId": self._creds.company_id,
            "shopName": self._creds.shop_name,
        }
        response = await self.page.request.post(
            self.BUSY_POINT_API_URL,
            headers=headers,
            data=json.dumps(payload),
        )
        status = response.status
        try:
            return status, await response.json()
        except Exception:
            try:
                return status, await response.text()
            except Exception:
                return status, None

    async def probe_busy_point_api_login_ok(self) -> Dict[str, Any]:
        """用餐桌列表 API 校验当前会话是否已登录（供 /setup 验证）。"""
        try:
            status, body = await self._busy_point_api_request_raw()
            if status != 200:
                return {
                    "ok": False,
                    "http_status": status,
                    "message": f"餐桌列表 API 请求失败（HTTP {status}）",
                }
            if isinstance(body, str):
                lowered = body.lower()
                if "login" in lowered or "<html" in lowered:
                    return {
                        "ok": False,
                        "http_status": status,
                        "message": "餐桌列表 API 返回非 JSON，可能未登录或会话已失效",
                    }
                return {
                    "ok": False,
                    "http_status": status,
                    "message": "餐桌列表 API 响应无法解析为 JSON",
                }
            if isinstance(body, dict):
                if self._busy_point_response_indicates_auth_failure(body):
                    return {
                        "ok": False,
                        "http_status": status,
                        "message": "餐桌列表 API 判定未登录或无权",
                    }
                if body.get("success") is False:
                    detail = str(body.get("message") or body.get("msg") or "").strip()
                    return {
                        "ok": False,
                        "http_status": status,
                        "message": detail or "餐桌列表 API 返回 success=false",
                    }
            rows = self._parse_api_response(body) if isinstance(body, (dict, list)) else []
            return {
                "ok": True,
                "http_status": status,
                "message": "餐桌列表 API 校验通过",
                "rows_count": len(rows),
            }
        except Exception as exc:
            self.logger.error("餐桌列表 API 探测失败: %s", exc)
            return {"ok": False, "http_status": 0, "message": f"餐桌列表 API 调用异常: {exc}"}

    def _resolve_point_id_from_busy_row(self, item: Dict) -> str:
        """从 getbusypointdata 行解析 pointId；优先 API 字段，回退 table_mapping。"""
        point_id = item.get("pointId") or item.get("pointID")
        if point_id:
            return str(point_id)
        table_number = str(item.get("pointName") or "")
        if not table_number:
            return ""
        return self._get_point_id(table_number)

    @staticmethod
    def _busy_point_rows_from_body(body: Any) -> List[Dict]:
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return [row for row in body["data"] if isinstance(row, dict)]
        if isinstance(body, list):
            return [row for row in body if isinstance(row, dict)]
        return []

    async def _bs_detail_api_request_raw(self, point_id: str) -> tuple[int, Any]:
        """调用单桌点菜明细 getbsdetail，返回 (HTTP 状态码, JSON 或原始文本)。"""
        import urllib.parse

        if self.page is None or self.page.is_closed():
            raise RuntimeError("浏览器页面未就绪")
        if self._creds is None:
            raise RuntimeError("无登录凭据")
        if not point_id:
            raise ValueError("point_id is required")

        referer = self._creds.occupy_table_referer_template.format(point_id=point_id)
        headers = await self.http.build_headers(referer=referer)
        request_json = {
            "shops": [int(self._creds.delivery_shop_id)],
            "pointId": str(point_id),
            "selectDeadline": "sameTime",
        }
        encoded_data = urllib.parse.urlencode({"data": json.dumps(request_json)})
        response = await self.http.post_form(
            self.BS_DETAIL_API_URL,
            headers=headers,
            encoded_data=encoded_data,
            context_label=f"getbsdetail pointId={point_id}",
        )
        status = response.status
        try:
            return status, await response.json()
        except Exception:
            try:
                return status, await response.text()
            except Exception:
                return status, None

    async def probe_bs_detail_api(self) -> Dict[str, Any]:
        """用 getbsdetail 校验 cy7mm 会话（优先选占用桌，否则任取一桌）。"""
        try:
            status, body = await self._busy_point_api_request_raw()
            if status != 200:
                return {
                    "ok": False,
                    "http_status": status,
                    "message": f"getbusypointdata 前置请求失败（HTTP {status}）",
                }
            if not isinstance(body, dict):
                return {
                    "ok": False,
                    "http_status": status,
                    "message": "getbusypointdata 响应无法解析为 JSON",
                }
            if self._busy_point_response_indicates_auth_failure(body):
                return {
                    "ok": False,
                    "http_status": status,
                    "message": "getbusypointdata 判定未登录或无权",
                }
            if body.get("success") is False:
                detail = str(body.get("message") or body.get("msg") or body.get("errorMsg") or "").strip()
                return {
                    "ok": False,
                    "http_status": status,
                    "message": detail or "getbusypointdata 返回 success=false",
                }

            rows = self._busy_point_rows_from_body(body)
            candidate_point_id = ""
            candidate_table = ""
            fallback_point_id = ""
            fallback_table = ""
            for row in rows:
                point_id = self._resolve_point_id_from_busy_row(row)
                if not point_id:
                    continue
                table_name = str(row.get("pointName") or "")
                if float(row.get("bizMoney") or 0) > 0:
                    candidate_point_id = point_id
                    candidate_table = table_name
                    break
                if not fallback_point_id:
                    fallback_point_id = point_id
                    fallback_table = table_name

            point_id = candidate_point_id or fallback_point_id
            table_name = candidate_table or fallback_table
            if not point_id:
                return {
                    "ok": True,
                    "skipped": True,
                    "http_status": status,
                    "message": "getbusypointdata 通过，但无法解析 pointId",
                    "table_rows": len(rows),
                }

            detail_status, detail_body = await self._bs_detail_api_request_raw(point_id)
            if detail_status != 200:
                return {
                    "ok": False,
                    "http_status": detail_status,
                    "message": f"getbsdetail 请求失败（HTTP {detail_status}）",
                    "point_id": point_id,
                    "table_name": table_name,
                }
            if not isinstance(detail_body, dict):
                return {
                    "ok": False,
                    "http_status": detail_status,
                    "message": "getbsdetail 响应无法解析为 JSON",
                    "point_id": point_id,
                    "table_name": table_name,
                }
            if pos_response_indicates_auth_failure(detail_body):
                return {
                    "ok": False,
                    "http_status": detail_status,
                    "message": "getbsdetail 判定未登录或无权",
                    "point_id": point_id,
                    "table_name": table_name,
                }
            if detail_body.get("success") is False:
                detail = str(
                    detail_body.get("errorMsg")
                    or detail_body.get("message")
                    or detail_body.get("msg")
                    or ""
                ).strip()
                return {
                    "ok": False,
                    "http_status": detail_status,
                    "message": detail or "getbsdetail 返回 success=false",
                    "point_id": point_id,
                    "table_name": table_name,
                }

            sc_detail = (detail_body.get("data") or {}).get("scDetail") or []
            dish_count = len(sc_detail) if isinstance(sc_detail, list) else 0
            return {
                "ok": True,
                "http_status": detail_status,
                "message": "getbsdetail 校验通过",
                "point_id": point_id,
                "table_name": table_name,
                "dish_count": dish_count,
                "occupied_probe": bool(candidate_point_id),
            }
        except Exception as exc:
            self.logger.error("getbsdetail API 探测失败: %s", exc)
            return {"ok": False, "http_status": 0, "message": f"getbsdetail API 调用异常: {exc}"}

    async def _get_table_data_via_api(self) -> List[Dict]:
        """通过 API 获取餐桌数据；会话失效时自动重新登录并重试一次。"""
        try:
            if self._creds is None:
                self.logger.error("❌ 无登录凭据，跳过 API 调用")
                return []

            status, data = await self.http.request_with_recovery(
                self._busy_point_api_request_raw,
                context_label="getbusypointdata",
            )

            if status != 200:
                self.logger.error(f"❌ API请求失败，状态码: {status}")
                return []

            self.logger.debug("✅ API请求成功，获取到餐桌数据")
            return self._parse_api_response(data) if isinstance(data, (dict, list)) else []

        except ScraperSessionError:
            raise
        except Exception as e:
            self.logger.error(f"❌ API请求出错: {e}")
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}")
            return []

    def _parse_api_response(self, data: Dict) -> List[Dict]:
        """解析API响应数据"""
        try:
            table_data = []

            # 尝试不同的数据结构解析
            if isinstance(data, dict) and 'data' in data:
                tables = data['data']

                if isinstance(tables, list):
                    for i, table in enumerate(tables):
                        table_info = self._parse_table_item(table, i)
                        if table_info:
                            table_data.append(table_info)

            elif isinstance(data, list):
                for i, table in enumerate(data):
                    table_info = self._parse_table_item(table, i)
                    if table_info:
                        table_data.append(table_info)

            elif isinstance(data, dict):
                for key in ['result', 'items', 'tables', 'list', 'rows']:
                    if key in data and isinstance(data[key], list):
                        for i, table in enumerate(data[key]):
                            table_info = self._parse_table_item(table, i)
                            if table_info:
                                table_data.append(table_info)
                        break

            return table_data

        except Exception as e:
            self.logger.error(f"❌ 解析API响应失败: {e}")
            return []

    def _parse_table_item(self, item: Dict, index: int) -> Optional[Dict]:
        """解析单个餐桌数据 - 基于原始爬虫的逻辑"""
        try:
            if not isinstance(item, dict):
                self.logger.warning(f"⚠️  项目{index+1}不是字典格式: {item}")
                return None

            # 基于原始爬虫的字段映射
            table_number = str(item.get('pointName', f"桌{index+1}"))
            amount = float(item.get('bizMoney', 0.0))
            people = int(item.get('peopleQty', 1))
            duration = int(item.get('dinnerTime', 0))

            # 根据金额判断状态
            status = "占用" if amount > 0 else "空闲"

            return {
                "id": f"table_{index}",
                "table_number": table_number,  # 数据库期望的字段名
                "number": table_number,
                "status": status,
                "amount": amount,
                "people": people,
                "duration": duration,
                "capacity": 4,  # 默认容量
                "area": "大厅",
                "last_update": datetime.now().isoformat(),
                "point_id": self._resolve_point_id_from_busy_row(item),
            }

        except Exception as e:
            self.logger.error(f"❌ 解析餐桌数据失败: {e}")
            return None

    def resolve_point_id(self, table_number: str) -> str:
        """根据餐桌编号推导 point_id（table_mapping + shop_id 前缀）。"""
        table_key = str(table_number)
        if table_key not in self.table_mapping:
            return ""
        mapping_code = self.table_mapping[table_key]
        if self._creds is None:
            return ""
        prefix = f"{self._creds.shop_id}00000000"
        return f"{prefix}{mapping_code}"

    def _get_point_id(self, table_number: str) -> str:
        """Backward-compatible alias for resolve_point_id."""
        return self.resolve_point_id(table_number)

    async def fetch_table_orders(self, table_number: str, point_id: str) -> List[Dict]:
        """Public: pull one table's dish detail and expand to intake order rows."""
        try:
            if not point_id:
                self.logger.error("❌ 餐桌 %s 缺少 pointId，跳过点菜明细", table_number)
                return []

            status, data = await self._bs_detail_api_request_raw(point_id)
            if status != 200:
                self.logger.error(f"❌ 获取餐桌{table_number}订单失败，状态码: {status}")
                return []
            if not isinstance(data, dict):
                self.logger.error(f"❌ 获取餐桌{table_number}订单失败，响应非 JSON")
                return []

            if not data.get("success", False):
                self.logger.error(f"❌ API业务逻辑错误: {data.get('errorMsg', '未知错误')}")
                return []

            return await self._parse_api_order_response(data, table_number)

        except Exception as e:
            self.logger.error(f"❌ 获取餐桌{table_number}订单出错: {e}")
            return []

    async def _get_table_orders(self, table_number: str, point_id: str) -> List[Dict]:
        """Backward-compatible alias for fetch_table_orders."""
        return await self.fetch_table_orders(table_number, point_id)

    async def _parse_api_order_response(self, data: Dict, table_number: str) -> List[Dict]:
        """解析桌情 API → RawOrderLine → OrderLineBuilder 入库行。"""
        from scraper.order_line_builder import FLOW_MODE_COMBO, FLOW_MODE_UNIT, RawOrderLine
        from scraper.order_source import classify_order_source

        try:
            if not data.get('success', False):
                self.logger.error(f"❌ API响应失败: {data.get('errorMsg', '未知错误')}")
                return []

            response_data = data.get('data', {})
            if not response_data:
                self.logger.error("❌ API响应中没有数据")
                return []

            business_flow_id = response_data.get('bsCode', '')
            sc_detail = response_data.get('scDetail', [])
            if not sc_detail:
                self.logger.error("❌ 没有找到菜品详情")
                return []

            current_year = datetime.now(CHINA_TZ).year
            current_date = datetime.now(CHINA_TZ).strftime("%m-%d")
            table_overlays = {
                "status": "未结",
                "source": classify_order_source(
                    table_number=table_number or response_data.get("pointName") or "",
                    people_qty=response_data.get("peopleQty"),
                ),
            }
            raws: List[RawOrderLine] = []

            for item in sc_detail:
                try:
                    dish_name = strip_trailing_dash_suffix(item.get('itemName', ''))
                    quantity = int(float(item.get('lastQty', 0) or 0))
                    price = float(item.get('lastPrice', 0) or 0)
                    order_time_str = item.get('orderTime', '')
                    if order_time_str:
                        formatted_time = f"{current_year}-{current_date} {order_time_str}"
                        try:
                            order_time_dt = datetime.strptime(
                                formatted_time, "%Y-%m-%d %H:%M"
                            ).replace(tzinfo=CHINA_TZ)
                        except Exception:
                            order_time_dt = datetime.now(CHINA_TZ)
                    else:
                        order_time_dt = datetime.now(CHINA_TZ)

                    if quantity > 0 and dish_name:
                        raws.append(
                            RawOrderLine(
                                bs_code=business_flow_id,
                                dish_name=dish_name,
                                quantity=quantity,
                                unit_price=price,
                                table_number=table_number,
                                order_time=order_time_dt,
                                flow_mode=FLOW_MODE_UNIT,
                                overlays=dict(table_overlays),
                            )
                        )
                        if quantity > 1:
                            self.logger.info(
                                f"🔄 拆分菜品: {dish_name} x{quantity} → {quantity}条独立记录"
                            )

                    for child in item.get('children', []) or []:
                        try:
                            child_name = strip_trailing_dash_suffix(child.get('itemName', ''))
                            child_qty = int(float(child.get('lastQty', 0) or 0))
                            if child_qty <= 0 or not child_name:
                                continue
                            raws.append(
                                RawOrderLine(
                                    bs_code=business_flow_id,
                                    dish_name=child_name,
                                    quantity=child_qty,
                                    unit_price=0.0,
                                    table_number=table_number,
                                    order_time=order_time_dt,
                                    flow_mode=FLOW_MODE_COMBO,
                                    overlays=dict(table_overlays),
                                )
                            )
                            if child_qty > 1:
                                self.logger.info(
                                    f"🔄 拆分套餐子项: {child_name} x{child_qty} → {child_qty}条独立记录"
                                )
                        except Exception as e:
                            self.logger.error(f"❌ 解析套餐明细失败: {e}")
                except Exception as e:
                    self.logger.error(f"❌ 解析菜品数据失败: {e}")

            return await self.order_lines.expand_many(raws)
        except Exception as e:
            self.logger.error(f"❌ 解析API响应失败: {e}")
            return []
