#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商鹏云云打印：客户端（spyun）+ 排版（ticket）+ 配置（config）+ 编排（service）。

这里刻意不 re-export 内部符号：四个模块之间直接互相 import 就行，包入口只负责
让人看懂目录。调用方按需 `from services.printer.<module> import ...`。
"""
