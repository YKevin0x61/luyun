-- 0057_refund_records_kind.sql
--
-- 补 0056 漏掉的一列。
--
-- 背景：门店库里 refund_records 是**先建表、后应用迁移**的（21:49 应用 0056 之前，
-- 这张表已经按不带 kind 的旧版结构建出来了），而 0056 用的是
-- CREATE TABLE IF NOT EXISTS —— 表在就整段跳过，于是 kind 列、以及依赖它的
-- 两个索引都没落上。表现是 /api/analytics/refunds/detail 一查就报
-- column "kind" does not exist，运营日报的「退单与赠单」也取不到数。
--
-- 幂等：ADD COLUMN / DROP INDEX / CREATE INDEX 全带 IF EXISTS / IF NOT EXISTS，
-- 重复应用无副作用。唯一索引必须先删再建 —— 旧索引的列组里没有 kind，
-- CREATE INDEX IF NOT EXISTS 见到同名索引会直接跳过，不会改它的列。

ALTER TABLE "refund_records"
    ADD COLUMN IF NOT EXISTS "kind" TEXT NOT NULL DEFAULT 'co';

DROP INDEX IF EXISTS "idx_refund_records_bill_line";
CREATE UNIQUE INDEX IF NOT EXISTS "idx_refund_records_bill_line"
    ON "refund_records" ("tenant_id", "shop_id", "kind", "bs_id", "source_line");

DROP INDEX IF EXISTS "idx_refund_records_date";
CREATE INDEX IF NOT EXISTS "idx_refund_records_date"
    ON "refund_records" ("tenant_id", "kind", "refund_date", "refund_time" DESC);
