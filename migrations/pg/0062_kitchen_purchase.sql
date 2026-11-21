-- 0062：厨房采购（采购下单 / 商品与供应商 / 数据分析）
--
-- 从 worktool2 小程序搬来的三块功能（spec: .scratch/worktool2-import/spec.md）。
-- 十二张表，全部是加成性新表；不改任何既有表。写入口只有 services/purchase/store.py，
-- Admin 数据浏览器只读列出（db_core/schema.py 的 PURCHASE_TABLES）。
--
-- 口径（ADR 0156 / 0157）：
--   * purchase_stock_counts 是「盘点快照」，不是库存台账：商品 + 北京自然日唯一，
--     同一天重提覆盖，不结转、不扣减；没填按 0 是照搬小程序，页面必须警告。
--   * purchase_date_tags / purchase_weekday_defaults 只给采购参考值用，与备货参考值
--     是两套独立模型。
--
-- 幂等：IF NOT EXISTS / ON CONFLICT DO NOTHING，可重复执行；纯加成，不 DROP。

CREATE TABLE IF NOT EXISTS purchase_suppliers (
    "id" BIGSERIAL PRIMARY KEY,
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "name" TEXT NOT NULL,
    "code" TEXT NOT NULL,
    "next_category_code" INTEGER NOT NULL DEFAULT 1,
    "active" BOOLEAN NOT NULL DEFAULT TRUE,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE ("tenant_id", "name"),
    UNIQUE ("tenant_id", "code"),
    CONSTRAINT purchase_suppliers_code_check CHECK ("code" ~ '^[0-9]{2}$')
);

CREATE TABLE IF NOT EXISTS purchase_categories (
    "id" BIGSERIAL PRIMARY KEY,
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "supplier_id" BIGINT NOT NULL REFERENCES purchase_suppliers(id),
    "name" TEXT NOT NULL,
    "code" TEXT NOT NULL,
    "next_product_seq" INTEGER NOT NULL DEFAULT 1,
    "active" BOOLEAN NOT NULL DEFAULT TRUE,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE ("supplier_id", "name"),
    UNIQUE ("supplier_id", "code"),
    CONSTRAINT purchase_categories_code_check CHECK ("code" ~ '^[0-9]{2}$')
);

CREATE TABLE IF NOT EXISTS purchase_products (
    "id" BIGSERIAL PRIMARY KEY,
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "product_code" TEXT NOT NULL,
    "supplier_id" BIGINT NOT NULL REFERENCES purchase_suppliers(id),
    "category_id" BIGINT NOT NULL REFERENCES purchase_categories(id),
    "name" TEXT NOT NULL,
    "specification" TEXT NOT NULL DEFAULT '',
    "unit" TEXT NOT NULL DEFAULT '',
    "remark" TEXT NOT NULL DEFAULT '',
    "order_step" NUMERIC(10,2) NOT NULL DEFAULT 1,
    "safety_factor" NUMERIC(5,2) NOT NULL DEFAULT 1.2,
    "need_stock_check" BOOLEAN NOT NULL DEFAULT FALSE,
    "is_long_cycle" BOOLEAN NOT NULL DEFAULT FALSE,
    "active" BOOLEAN NOT NULL DEFAULT TRUE,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE ("tenant_id", "product_code"),
    CONSTRAINT purchase_products_step_check CHECK ("order_step" > 0)
);
CREATE INDEX IF NOT EXISTS idx_purchase_products_category
    ON purchase_products ("tenant_id", "category_id");
CREATE INDEX IF NOT EXISTS idx_purchase_products_active
    ON purchase_products ("tenant_id", "active");

CREATE TABLE IF NOT EXISTS purchase_positions (
    "id" BIGSERIAL PRIMARY KEY,
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "name" TEXT NOT NULL,
    "sort_order" INTEGER NOT NULL DEFAULT 0,
    "show_in_order" BOOLEAN NOT NULL DEFAULT TRUE,
    "active" BOOLEAN NOT NULL DEFAULT TRUE,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE ("tenant_id", "name")
);

CREATE TABLE IF NOT EXISTS purchase_product_positions (
    "product_id" BIGINT NOT NULL REFERENCES purchase_products(id) ON DELETE CASCADE,
    "position_id" BIGINT NOT NULL REFERENCES purchase_positions(id),
    "sort_order" INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY ("product_id", "position_id")
);
CREATE INDEX IF NOT EXISTS idx_purchase_product_positions_position
    ON purchase_product_positions ("position_id");

CREATE TABLE IF NOT EXISTS purchase_orders (
    "id" BIGSERIAL PRIMARY KEY,
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "order_no" TEXT NOT NULL,
    "position_id" BIGINT REFERENCES purchase_positions(id),
    "position_name" TEXT NOT NULL,
    "submitter_employee_id" BIGINT,
    "submitter_name" TEXT NOT NULL,
    "submitter_kind" TEXT NOT NULL DEFAULT 'staff',
    "status" TEXT NOT NULL DEFAULT 'pending',
    "submitted_at" TIMESTAMPTZ NOT NULL,
    "completed_at" TIMESTAMPTZ,
    "completed_by" TEXT,
    "cancelled_at" TIMESTAMPTZ,
    "cancelled_by" TEXT,
    "deleted_at" TIMESTAMPTZ,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updated_by" TEXT,
    UNIQUE ("tenant_id", "order_no"),
    CONSTRAINT purchase_orders_status_check
        CHECK ("status" IN ('pending', 'completed', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_date
    ON purchase_orders ("tenant_id", "submitted_at" DESC);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_status_date
    ON purchase_orders ("tenant_id", "status", "submitted_at" DESC);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_position_date
    ON purchase_orders ("tenant_id", "position_id", "submitted_at" DESC);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    "id" BIGSERIAL PRIMARY KEY,
    "order_id" BIGINT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    "product_id" BIGINT REFERENCES purchase_products(id),
    "product_code" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "specification" TEXT NOT NULL DEFAULT '',
    "unit" TEXT NOT NULL DEFAULT '',
    "supplier_id" BIGINT,
    "supplier_name" TEXT NOT NULL DEFAULT '',
    "category_name" TEXT NOT NULL DEFAULT '',
    "position_name" TEXT NOT NULL DEFAULT '',
    "quantity" NUMERIC(10,2) NOT NULL,
    "reference_qty" NUMERIC(10,2),
    "stock_qty" NUMERIC(10,2),
    "suggested_qty" NUMERIC(10,2),
    "order_step" NUMERIC(10,2) NOT NULL DEFAULT 1,
    "sort_order" INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT purchase_order_items_quantity_check CHECK ("quantity" > 0)
);
CREATE INDEX IF NOT EXISTS idx_purchase_order_items_order
    ON purchase_order_items ("order_id");
CREATE INDEX IF NOT EXISTS idx_purchase_order_items_product
    ON purchase_order_items ("product_id");

CREATE TABLE IF NOT EXISTS purchase_stock_counts (
    "id" BIGSERIAL PRIMARY KEY,
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "product_id" BIGINT NOT NULL REFERENCES purchase_products(id),
    "count_date" DATE NOT NULL,
    "current_stock" NUMERIC(10,2) NOT NULL,
    "counted_by_employee_id" BIGINT,
    "counted_by_name" TEXT,
    "counted_kind" TEXT,
    "counted_at" TIMESTAMPTZ NOT NULL,
    "source_order_id" BIGINT,
    UNIQUE ("tenant_id", "product_id", "count_date")
);
CREATE INDEX IF NOT EXISTS idx_purchase_stock_counts_date
    ON purchase_stock_counts ("tenant_id", "count_date");

CREATE TABLE IF NOT EXISTS purchase_date_tags (
    "id" BIGSERIAL PRIMARY KEY,
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "tag_date" DATE NOT NULL,
    "tag" TEXT NOT NULL,
    "updated_by" TEXT,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE ("tenant_id", "tag_date"),
    CONSTRAINT purchase_date_tags_tag_check
        CHECK ("tag" IN ('2万', '3万', '4万', '5万', '6万', '7万', '8万'))
);

CREATE TABLE IF NOT EXISTS purchase_weekday_defaults (
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "weekday" SMALLINT NOT NULL,
    "tag" TEXT NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("tenant_id", "weekday"),
    CONSTRAINT purchase_weekday_defaults_weekday_check CHECK ("weekday" BETWEEN 0 AND 6),
    CONSTRAINT purchase_weekday_defaults_tag_check
        CHECK ("tag" IN ('2万', '3万', '4万', '5万', '6万', '7万', '8万'))
);

CREATE TABLE IF NOT EXISTS purchase_order_events (
    "id" BIGSERIAL PRIMARY KEY,
    "order_id" BIGINT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    "kind" TEXT NOT NULL,
    "actor_employee_id" BIGINT,
    "actor_name" TEXT,
    "actor_kind" TEXT,
    "detail_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_purchase_order_events_order
    ON purchase_order_events ("order_id", "created_at");

CREATE TABLE IF NOT EXISTS purchase_product_code_changes (
    "id" BIGSERIAL PRIMARY KEY,
    "product_id" BIGINT NOT NULL,
    "old_code" TEXT NOT NULL,
    "new_code" TEXT NOT NULL,
    "old_supplier_id" BIGINT,
    "new_supplier_id" BIGINT,
    "old_supplier_name" TEXT,
    "new_supplier_name" TEXT,
    "old_category_id" BIGINT,
    "new_category_id" BIGINT,
    "old_category_name" TEXT,
    "new_category_name" TEXT,
    "actor_employee_id" BIGINT,
    "actor_name" TEXT,
    "actor_kind" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_purchase_code_changes_product
    ON purchase_product_code_changes ("product_id", "created_at");

-- 种子岗位：照搬小程序 positions 里的 8 个（spec 4.4）。
INSERT INTO purchase_positions ("tenant_id", "name", "sort_order")
VALUES
    (1, '西饼', 10),
    (1, '肠粉', 20),
    (1, '熟笼', 30),
    (1, '明档', 40),
    (1, '明档2', 50),
    (1, '煎炸', 60),
    (1, '馅档', 70),
    (1, '干货', 80)
ON CONFLICT ("tenant_id", "name") DO NOTHING;
