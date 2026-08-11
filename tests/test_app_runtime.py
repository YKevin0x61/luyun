#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AppRuntime container wiring for get_db()."""

import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException

from database import get_db
from services.app_runtime import AppRuntime, get_runtime, set_runtime


class AppRuntimeTests(unittest.TestCase):
    def tearDown(self):
        set_runtime(None)

    def test_get_db_reads_runtime(self):
        db = MagicMock(name="db")
        set_runtime(AppRuntime(db=db, dish_catalog=None, scraper=None))
        self.assertIs(get_db(), db)
        self.assertIs(get_runtime().db, db)

    def test_get_db_raises_when_unset(self):
        set_runtime(None)
        # Without a live main.db_manager either, get_db must fail closed.
        import main as main_module
        old = main_module.db_manager
        main_module.db_manager = None
        try:
            with self.assertRaises(HTTPException) as ctx:
                get_db()
            self.assertEqual(ctx.exception.status_code, 500)
        finally:
            main_module.db_manager = old
