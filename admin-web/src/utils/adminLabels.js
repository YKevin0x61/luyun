// 数据表管理中文标签映射，移植自旧版 public/index.html（TABLE_LABELS / COLUMN_LABELS）。
// 新增表/字段时，同步在此补充映射，避免管理后台出现裸英文列名。

export const TABLE_LABELS = {
  dish_stations: '菜品档口映射',
  orders: '订单记录',
  stations: '档口配置',
  tables: '餐桌状态',
  semi_finished_rules: '半成品换算规则',
  report_dishes: '固定报表菜品',
  prep_items: '备货品主数据',
  prep_batches: '备货批次',
  prep_stock_movements: '库存流水',
  prep_plan_runs: '备货计划记录',
  prep_plan_items: '计划明细',
  prep_plan_item_slots: '计划时段',
  wecom_push_webhooks: '企微 Webhook',
  wecom_push_jobs: '企微推送任务',
  wecom_push_logs: '企微推送日志',
  app_settings: '应用设置',
  auth: '登录认证',
}

// 数据表侧边栏图标名（对应 TableIcon.vue 内置 SVG path 集合），移植自旧版 public/index.html（TABLE_ICONS）。
export const TABLE_ICONS = {
  orders: 'clipboard',
  tables: 'armchair',
  stations: 'store',
  dish_stations: 'utensils',
  semi_finished_rules: 'receipt',
  report_dishes: 'file-text',
  prep_items: 'cooking-pot',
  prep_batches: 'package',
  prep_stock_movements: 'trending-down',
  prep_plan_runs: 'calendar',
  prep_plan_items: 'calculator',
  prep_plan_item_slots: 'timer',
  wecom_push_webhooks: 'file-text',
  wecom_push_jobs: 'timer',
  wecom_push_logs: 'file-text',
  app_settings: 'file-text',
  auth: 'file-text',
}

export const COLUMN_LABELS = {
  id: '序号',
  'dish_stations.dish_name': '菜品名称', 'dish_stations.station_id': '档口', 'dish_stations.notes': '备注',
  'dish_stations.created_at': '创建时间', 'dish_stations.updated_at': '更新时间', 'dish_stations.rowid': '序号',
  'orders.business_flow_id': '流水号', 'orders.table_number': '桌号', 'orders.dish_name': '菜品名称',
  'orders.quantity': '数量', 'orders.order_time': '下单时间', 'orders.price': '单价', 'orders.total_amount': '总价',
  'orders.status': '状态', 'orders.category': '分类', 'orders.station': '档口', 'orders.priority': '优先级',
  'orders.notes': '备注', 'orders.created_at': '创建时间', 'orders.updated_at': '更新时间', 'orders.rowid': '序号',
  'stations.station_id': '档口ID', 'stations.name': '档口名称', 'stations.color': '颜色', 'stations.config': '配置', 'stations.rowid': '序号',
  'tables.table_number': '桌号', 'tables.amount': '金额', 'tables.people': '人数', 'tables.duration': '时长(分钟)', 'tables.status': '状态', 'tables.updated_at': '更新时间', 'tables.rowid': '序号',
  'semi_finished_rules.dish_name': '菜品名称', 'semi_finished_rules.semi_name': '半成品名称', 'semi_finished_rules.position': '岗位',
  'semi_finished_rules.factor': '换算系数', 'semi_finished_rules.unit': '单位', 'semi_finished_rules.category': '分类',
  'semi_finished_rules.notes': '备注', 'semi_finished_rules.created_at': '创建时间', 'semi_finished_rules.updated_at': '更新时间', 'semi_finished_rules.rowid': '序号',
  'report_dishes.dish_name': '菜品名称', 'report_dishes.display_order': '显示顺序', 'report_dishes.notes': '备注',
  'report_dishes.created_at': '创建时间', 'report_dishes.rowid': '序号',
  'prep_items.item_name': '备货项名称', 'prep_items.unit': '单位', 'prep_items.category': '分类', 'prep_items.enabled': '是否启用',
  'prep_items.safety_stock': '安全库存', 'prep_items.par_level': '目标库存', 'prep_items.lead_minutes': '提前分钟数',
  'prep_items.created_at': '创建时间', 'prep_items.updated_at': '更新时间', 'prep_items.rowid': '序号',
  'prep_batches.batch_name': '批次名称', 'prep_batches.started_at': '开始时间', 'prep_batches.ended_at': '结束时间',
  'prep_batches.status': '状态', 'prep_batches.notes': '备注', 'prep_batches.created_at': '创建时间', 'prep_batches.updated_at': '更新时间', 'prep_batches.rowid': '序号',
  'prep_stock_movements.item_name': '备货项名称', 'prep_stock_movements.movement_type': '变动类型', 'prep_stock_movements.quantity': '数量',
  'prep_stock_movements.unit': '单位', 'prep_stock_movements.reason': '原因', 'prep_stock_movements.related_ref': '关联单号',
  'prep_stock_movements.occured_at': '发生时间', 'prep_stock_movements.created_at': '创建时间', 'prep_stock_movements.rowid': '序号',
  'prep_plan_runs.run_id': '运行ID', 'prep_plan_runs.plan_date': '计划日期', 'prep_plan_runs.start_time': '开始时间',
  'prep_plan_runs.end_time': '结束时间', 'prep_plan_runs.status': '状态', 'prep_plan_runs.notes': '备注',
  'prep_plan_runs.created_at': '创建时间', 'prep_plan_runs.updated_at': '更新时间', 'prep_plan_runs.rowid': '序号',
  'prep_plan_items.run_id': '运行ID', 'prep_plan_items.plan_item_id': '计划项ID', 'prep_plan_items.prep_item_id': '备货项ID',
  'prep_plan_items.item_name': '备货项名称', 'prep_plan_items.unit': '单位', 'prep_plan_items.forecast_qty': '预测量',
  'prep_plan_items.available_qty': '可用量', 'prep_plan_items.recommended_qty': '建议备货量', 'prep_plan_items.created_at': '创建时间', 'prep_plan_items.rowid': '序号',
  'prep_plan_item_slots.run_id': '运行ID', 'prep_plan_item_slots.plan_item_id': '计划项ID',
  'prep_plan_item_slots.prep_item_id': '备货项ID', 'prep_plan_item_slots.item_name': '备货项名称',
  'prep_plan_item_slots.unit': '单位', 'prep_plan_item_slots.slot_start': '时段开始', 'prep_plan_item_slots.slot_end': '时段结束',
  'prep_plan_item_slots.forecast_qty': '预测量', 'prep_plan_item_slots.available_qty': '可用量',
  'prep_plan_item_slots.recommended_qty': '建议备货量', 'prep_plan_item_slots.created_at': '创建时间', 'prep_plan_item_slots.rowid': '序号',
  'wecom_push_webhooks.name': '名称', 'wecom_push_webhooks.webhook_url_encrypted': 'Webhook地址(加密)',
  'wecom_push_webhooks.webhook_url_masked': 'Webhook地址(脱敏)', 'wecom_push_webhooks.enabled': '是否启用',
  'wecom_push_webhooks.notes': '备注', 'wecom_push_webhooks.created_at': '创建时间',
  'wecom_push_webhooks.updated_at': '更新时间', 'wecom_push_webhooks.rowid': '序号',
  'wecom_push_jobs.name': '任务名称', 'wecom_push_jobs.webhook_id': 'Webhook ID',
  'wecom_push_jobs.push_type': '推送类型', 'wecom_push_jobs.schedule_time': '定时时间',
  'wecom_push_jobs.date_range_mode': '日期范围', 'wecom_push_jobs.station': '档口',
  'wecom_push_jobs.enabled': '是否启用', 'wecom_push_jobs.last_sent_date': '上次发送日期',
  'wecom_push_jobs.notes': '备注', 'wecom_push_jobs.created_at': '创建时间',
  'wecom_push_jobs.updated_at': '更新时间', 'wecom_push_jobs.rowid': '序号',
  'wecom_push_logs.job_id': '任务ID', 'wecom_push_logs.webhook_id': 'Webhook ID',
  'wecom_push_logs.webhook_name': 'Webhook名称', 'wecom_push_logs.push_type': '推送类型',
  'wecom_push_logs.status': '状态', 'wecom_push_logs.message_bytes': '消息大小(字节)',
  'wecom_push_logs.error': '错误信息', 'wecom_push_logs.response_text': '响应内容',
  'wecom_push_logs.sent_at': '发送时间', 'wecom_push_logs.rowid': '序号',
  'app_settings.key': '配置键', 'app_settings.value': '配置值', 'app_settings.updated_at': '更新时间',
  'app_settings.rowid': '序号',
  'auth.username': '用户名', 'auth.password_hash': '密码哈希', 'auth.session_id': '会话ID',
  'auth.expires_at': '过期时间', 'auth.last_seen_at': '最后活跃', 'auth.token_hash': 'Token哈希',
  'auth.label': '标签', 'auth.revoked_at': '撤销时间', 'auth.created_at': '创建时间',
  'auth.updated_at': '更新时间', 'auth.rowid': '序号',
  'orders.source': '来源', 'orders.dish_status': '出餐状态', 'orders.ready_time': '出餐时间',
  run_id: '运行ID', plan_item_id: '计划项ID', prep_item_id: '备货项ID', item_name: '备货项名称',
  slot_start: '时段开始', slot_end: '时段结束', forecast_qty: '预测量', available_qty: '可用量',
  recommended_qty: '建议备货量', created_at: '创建时间', updated_at: '更新时间',
  job_id: '任务ID', webhook_id: 'Webhook ID', webhook_name: 'Webhook名称', push_type: '推送类型',
  message_bytes: '消息大小(字节)', error: '错误信息', response_text: '响应内容', sent_at: '发送时间',
  enabled: '是否启用', name: '名称', notes: '备注', key: '配置键', value: '配置值',
  schedule_time: '定时时间', date_range_mode: '日期范围', last_sent_date: '上次发送日期',
  webhook_url_encrypted: 'Webhook地址(加密)', webhook_url_masked: 'Webhook地址(脱敏)',
  username: '用户名', password_hash: '密码哈希', session_id: '会话ID', expires_at: '过期时间',
  last_seen_at: '最后活跃', token_hash: 'Token哈希', label: '标签', revoked_at: '撤销时间',
  status: '状态', station: '档口',
}

/** 自增/自动维护列：新增记录表单中永不展示；编辑记录表单中始终隐藏（不可篡改）。 */
export const AUTO_COLUMNS = ['id', 'rowid', 'created_at', 'updated_at']

/** 时间戳自动列：created_at/updated_at 由后端维护，前端表单不可新增也不可编辑。 */
export const TIMESTAMP_COLUMNS = ['created_at', 'updated_at']

export function isAutoColumn(name) {
  return AUTO_COLUMNS.includes(String(name || '').toLowerCase())
}

export function isTimestampColumn(name) {
  return TIMESTAMP_COLUMNS.includes(String(name || '').toLowerCase())
}

export function getTableLabel(name) {
  return TABLE_LABELS[name] || name
}

export function getTableIcon(name) {
  return TABLE_ICONS[name] || 'file-text'
}

export function getColumnLabel(table, column) {
  const tableName = String(table || '')
  const columnName = String(column || '')
  const tableNameLower = tableName.toLowerCase()
  const columnNameLower = columnName.toLowerCase()
  return COLUMN_LABELS[`${tableName}.${columnName}`]
    || COLUMN_LABELS[`${tableNameLower}.${columnNameLower}`]
    || COLUMN_LABELS[columnName]
    || COLUMN_LABELS[columnNameLower]
    || column
}
