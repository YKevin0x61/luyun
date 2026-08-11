#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin 表行列表必须暴露 rowid，供编辑/删除 URL 使用。"""

import sqlite3
import tempfile
import unittest

from db_core.schema import _TABLE_SCHEMAS


class AdminRowsRowidTest(unittest.TestCase):
    def test_rowid_as_rowid_exposes_rowid_and_id_for_integer_pk_tables(self):
        """INTEGER PRIMARY KEY 表上 SELECT rowid,* 会把首列别名成 id，必须用 AS rowid。"""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            conn = sqlite3.connect(tmp.name)
            conn.execute(_TABLE_SCHEMAS["dish_stations"])
            conn.execute(
                "INSERT INTO dish_stations (dish_name, station_id, notes, created_at, updated_at) "
                "VALUES ('测试菜', 'shulong', '', '2026-01-01', '2026-01-01')"
            )
            conn.commit()

            bad_cur = conn.execute("SELECT rowid, * FROM dish_stations LIMIT 1")
            bad_cols = [d[0] for d in bad_cur.description]
            self.assertNotIn("rowid", bad_cols)

            good_cur = conn.execute("SELECT rowid AS rowid, * FROM dish_stations LIMIT 1")
            good_cols = [d[0] for d in good_cur.description]
            row = good_cur.fetchone()
            row_dict = dict(zip(good_cols, row))

            self.assertEqual(good_cols[0], "rowid")
            self.assertIn("id", good_cols)
            self.assertEqual(row_dict["rowid"], row_dict["id"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
