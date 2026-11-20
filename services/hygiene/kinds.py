#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫生照片的七种类型名。

名字只有一份：归档页的筛选与导出、照片台账的归一、清扫日志都往这里取。
把常量放在独立模块里是为了断开循环 —— 台账要知道类型，归档要知道台账，
而归档本身又定义了类型的展示文案。
"""

KIND_DAILY = "daily"
KIND_DEEP = "deep"
KIND_FIX = "fix"
KIND_FIX_RESHOOT = "fix_reshoot"
KIND_TEACHING = "teaching"
KIND_STANDARD = "standard"
KIND_ATTIRE = "attire"

KIND_LABELS = {
    KIND_DAILY: "日常实拍",
    KIND_DEEP: "专项前后",
    KIND_FIX: "整改原图",
    KIND_FIX_RESHOOT: "整改回拍",
    KIND_TEACHING: "卫生教材",
    KIND_STANDARD: "标准图版本",
    KIND_ATTIRE: "仪容仪表",
}