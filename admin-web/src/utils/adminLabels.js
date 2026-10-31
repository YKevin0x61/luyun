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
  prep_plan_days: '备货计划（按天）',
  prep_plan_entries: '备货计划明细',
  prep_staff_station_picks: '员工备货档口',
  wecom_push_webhooks: '企微 Webhook',
  wecom_push_jobs: '企微推送任务',
  wecom_push_logs: '企微推送日志',
  app_settings: '应用设置',
  auth: '登录认证',
  admin_user: '管理员账号',
  sessions: '登录会话',
  api_tokens: 'API 令牌',
  sop_stations: '配方岗位',
  sop_recipes: '配方内容',
  sop_recipes_history: '配方历史',
  hygiene_employees: '卫生员工',
  hygiene_staff_sessions: '卫生员工会话',
  hygiene_shift_picks: '卫生班次选择',
  hygiene_zones: '卫生区域',
  hygiene_daily_items: '日常卫生项目',
  hygiene_standards: '卫生标准',
  hygiene_daily_instances: '日常卫生实例',
  hygiene_daily_submissions: '日常卫生提交',
  hygiene_settings: '卫生设置',
  hygiene_overdue_notices: '日常逾期提醒',
  hygiene_board_events: '卫生看板事件',
  hygiene_deep_clean_items: '深度清洁项目',
  hygiene_deep_clean_instances: '深度清洁实例',
  hygiene_deep_clean_submissions: '深度清洁提交',
  hygiene_deep_clean_overdue_notices: '深度清洁逾期提醒',
  hygiene_fix_tickets: '整改工单',
  hygiene_fix_reshoots: '整改补拍',
  hygiene_fix_overdue_notices: '整改逾期提醒',
  hygiene_teaching_examples: '卫生教学示例',
  hygiene_capture_variants: '卫生图片缩略图',
  hygiene_wecom_shares: '验收照片投递',
  hygiene_attire_standard: '仪容仪表标准图',
  hygiene_attire_shots: '仪容仪表自拍',
  prep_plan_snapshots: '备货预测快照',
  prep_plan_actuals: '备货实际销量',
  staff_shifts: '排班班次',
  staff_assignments: '排班结果',
  scheduling_rules: '轮转规则',
  scheduling_overrides: '单日覆盖',
  scheduling_requests: '请假与换班',
  scheduling_zone_defaults: '默认工作区',
  wecom_push_subscriptions: '企微推送订阅',
  wecom_channel_groups: '企微渠道分组',
  wecom_channel_group_members: '企微分组成员',
  wecom_push_outbox: '企微出站队列',
  wecom_push_audit: '企微配置变更',
  overtime_entries: '加班与补钟',
  overtime_salary_snapshots: '计薪底薪快照',
  overtime_leave_deductions: '补钟抵扣存假',
  seniority_bonus_changes: '工龄奖变更',
  statutory_holidays: '法定节假日',
  attendance_ledger: '月度考勤台账',
  wastage_records: '损耗登记',
  wastage_images: '损耗照片',
  wastage_templates: '损耗常用模板',
  logs: '运行日志',
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
  prep_plan_days: 'calendar',
  prep_plan_entries: 'calculator',
  prep_staff_station_picks: 'user',
  wecom_push_webhooks: 'file-text',
  wecom_push_jobs: 'timer',
  wecom_push_logs: 'file-text',
  app_settings: 'file-text',
  auth: 'file-text',
  admin_user: 'user',
  sessions: 'timer',
  api_tokens: 'key',
  sop_stations: 'store',
  sop_recipes: 'file-text',
  sop_recipes_history: 'clipboard',
  hygiene_employees: 'user',
  hygiene_staff_sessions: 'timer',
  hygiene_shift_picks: 'calendar',
  hygiene_zones: 'store',
  hygiene_daily_items: 'clipboard',
  hygiene_standards: 'camera',
  hygiene_daily_instances: 'calendar',
  hygiene_daily_submissions: 'package',
  hygiene_settings: 'file-text',
  hygiene_overdue_notices: 'timer',
  hygiene_board_events: 'file-text',
  hygiene_deep_clean_items: 'sparkles',
  hygiene_deep_clean_instances: 'calendar',
  hygiene_deep_clean_submissions: 'package',
  hygiene_deep_clean_overdue_notices: 'timer',
  hygiene_fix_tickets: 'clipboard',
  hygiene_fix_reshoots: 'camera',
  hygiene_fix_overdue_notices: 'timer',
  hygiene_teaching_examples: 'file-text',
  hygiene_capture_variants: 'image',
  hygiene_wecom_shares: 'image',
  hygiene_attire_standard: 'image',
  hygiene_attire_shots: 'camera',
  prep_plan_snapshots: 'calculator',
  prep_plan_actuals: 'clipboard',
  staff_shifts: 'calendar',
  staff_assignments: 'calendar',
  scheduling_rules: 'receipt',
  scheduling_overrides: 'calendar',
  scheduling_requests: 'clipboard',
  scheduling_zone_defaults: 'store',
  wecom_push_subscriptions: 'file-text',
  wecom_channel_groups: 'store',
  wecom_channel_group_members: 'user',
  wecom_push_outbox: 'package',
  wecom_push_audit: 'receipt',
  overtime_entries: 'timer',
  overtime_salary_snapshots: 'calculator',
  overtime_leave_deductions: 'calendar',
  seniority_bonus_changes: 'receipt',
  statutory_holidays: 'calendar',
  attendance_ledger: 'clipboard',
  wastage_records: 'trending-down',
  wastage_images: 'image',
  wastage_templates: 'file-text',
  logs: 'file-text',
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
  'prep_plan_days.plan_day': '营业日', 'prep_plan_days.window_start': '窗口开始', 'prep_plan_days.window_end': '窗口结束',
  'prep_plan_days.created_at': '创建时间', 'prep_plan_days.updated_at': '更新时间', 'prep_plan_days.rowid': '序号',
  'prep_plan_entries.plan_day_id': '计划ID', 'prep_plan_entries.prep_item_id': '备货项ID',
  'prep_plan_entries.item_name': '备货项名称', 'prep_plan_entries.unit': '单位', 'prep_plan_entries.station': '档口',
  'prep_plan_entries.kind': '做的/买的', 'prep_plan_entries.suggested_qty': '建议量',
  'prep_plan_entries.final_qty': '实际采用量', 'prep_plan_entries.qty_source': '数量来源',
  'prep_plan_entries.done_at': '勾选时间', 'prep_plan_entries.done_by': '勾选人',
  'prep_plan_entries.created_at': '创建时间', 'prep_plan_entries.updated_at': '更新时间', 'prep_plan_entries.rowid': '序号',
  'prep_staff_station_picks.employee_id': '员工ID', 'prep_staff_station_picks.pick_day': '日期',
  'prep_staff_station_picks.station': '档口', 'prep_staff_station_picks.created_at': '创建时间',
  'prep_staff_station_picks.rowid': '序号',
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
  // 下面这些是新表/新增列的表级覆盖：同名列在别处含义不同，或要写得更具体。
  'prep_items.shelf_life_hours': '保质期(小时)', 'prep_items.lead_time_hours': '提前量(小时)',
  'prep_items.min_batch_qty': '最小起批量', 'prep_items.safety_stock_ratio': '安全库存系数',
  'prep_plan_entries.produced_by': '产量登记人', 'prep_plan_entries.produced_at': '产量登记时间',
  'staff_shifts.name': '班次名称', 'staff_shifts.duty_slot': '卫生档位',
  'staff_assignments.source': '来源', 'staff_assignments.half_rest': '休半天',
  'scheduling_overrides.kind': '覆盖来源', 'scheduling_overrides.voided': '已作废',
  'scheduling_requests.kind': '申请类型',
  'scheduling_requests.note': '备注',
  'wecom_push_webhooks.hygiene_feed': '卫生播报', 'wecom_channel_groups.name': '分组名称',
  'hygiene_wecom_shares.kind': '业务类型',
  'hygiene_employees.health_cert_date': '健康证到期',
  'hygiene_employees.resigned_at': '离职日期',
  'overtime_leave_deductions.days': '抵扣天数', 'overtime_leave_deductions.half_hours': '抵扣小时数',
  'attendance_ledger.month': '月份', 'attendance_ledger.shift_name': '本月班次',
  'wastage_records.item_name': '物品名称', 'wastage_records.reason': '损耗原因',
  'wastage_templates.item_name': '物品名称', 'wastage_templates.reason': '损耗原因',
  'orders.source': '堂食/外卖', 'orders.dish_status': '出餐状态', 'orders.ready_time': '出餐时间',
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
  slug: '标识', title: '标题', section: '分类', recipe_name: '配方名称',
  body_markdown: '正文', sort_order: '排序', is_new: '是否新品', is_active: '是否启用',
  ingredients_json: '食材', steps_json: '步骤', tips_json: '提示',
  base_servings_qty: '基准份数', base_servings_unit: '基准单位', legacy_markdown: '旧版正文',
  needs_review: '待复核', station_slug: '岗位标识', changed_at: '变更时间',
  phone: '手机号', job_title: '职务', permission: '权限', approved: '是否审批', disabled: '是否停用',
  employee_id: '员工ID', business_date: '营业日', shift: '班次', zone_id: '区域ID',
  day_shift: '白班', night_shift: '夜班', current_standard_id: '当前标准ID',
  item_id: '项目ID', capture_id: '图片ID', content_type: '内容类型', byte_size: '文件大小(字节)',
  content_sha256: '内容SHA256', markup_json: '标记数据', pending_submission_id: '待提交ID',
  instance_id: '实例ID', frozen_standard_id: '冻结标准ID', submitter_id: '提交人ID',
  submitter_phone: '提交人手机号', zone_name: '区域名称', captured_at: '拍摄时间',
  notified_at: '通知时间', board: '看板', event_type: '事件类型', occurred_at: '发生时间',
  weekday: '星期', before_capture_id: '整改前图片ID', after_capture_id: '整改后图片ID',
  before_content_type: '整改前内容类型', after_content_type: '整改后内容类型',
  before_captured_at: '整改前拍摄时间', after_captured_at: '整改后拍摄时间',
  ticket_type: '工单类型', body_text: '正文', duration_seconds: '时长(秒)', deadline: '截止时间',
  opener_kind: '发起人类型', opener_id: '发起人ID', opener_phone: '发起人手机号',
  pending_reshoot_id: '待补拍ID', ticket_id: '工单ID', photographer_id: '拍摄人ID',
  photographer_phone: '拍摄人手机号', kind: '类型', left_label: '左图标签', right_label: '右图标签',
  left_capture_id: '左图ID', right_capture_id: '右图ID', left_content_type: '左图类型',
  right_content_type: '右图类型', left_markup_json: '左图标记', source_capture_id: '原图ID',
  variant: '规格', width: '宽度', height: '高度',
  // ── 以下是通用列名。新增表（排班 / 企微订阅 / 人事 / 考勤 / 损耗 / 备货快照）里
  //    同名列都走这里，避免每张表各写一遍、写漏一处就露出裸英文。
  tenant_id: '租户ID', unit: '单位', position: '岗位', active: '是否启用',
  plan_day: '营业日', window_start: '窗口开始', window_end: '窗口结束',
  shift_id: '班次ID', shift_name: '班次', cycle: '轮转周期', anchor_date: '起算日',
  start_date: '开始日期', end_date: '结束日期', note: '备注', peer_employee_id: '换班对象',
  snapshot_at: '快照时间', suggested_qty: '建议量', final_qty: '实际采用量',
  qty_source: '数量来源', demand_qty: '需求量', store_factor: '门店系数', quantile: '分位点',
  basis_json: '计算依据', rules_fingerprint: '规则指纹', sold_qty: '实际销量',
  computed_at: '计算时间', produced_qty: '实际产量', produced_by: '产量登记人',
  produced_at: '产量登记时间',
  topic_id: '主题ID', params_json: '参数', schedule_id: '计划ID',
  target_channel_id: '目标渠道ID', target_group_id: '目标分组ID',
  group_id: '分组ID', channel_id: '渠道ID', content_summary: '内容摘要',
  attempts: '尝试次数', last_error: '最后错误', idempotency_key: '幂等键',
  scheduled_at: '计划发送时间', finished_at: '完成时间', sending_at: '开始发送时间',
  actor: '操作人', action: '动作', object_type: '对象类型', object_id: '对象ID',
  object_name: '对象名称', before_json: '变更前', after_json: '变更后',
  entry_date: '加班日期', half_hours: '时长(小时)', reject_reason: '驳回原因',
  created_by: '登记人', decided_by: '审批人', decided_at: '审批时间',
  month: '月份', base_salary: '底薪', old_value: '调整前', new_value: '调整后',
  changed_by: '修改人',
  day: '日期', opening_balance: '期初结余', closing_balance: '期末结余',
  lodging: '是否住宿', borrowed_store: '借调门店', resigned: '是否离职', sealed_at: '封账时间',
  quantity: '数量', reason: '原因', remark: '备注', wastage_date: '损耗日期',
  operator_kind: '登记人类型', operator_name: '登记人', record_id: '记录ID',
  owner_key: '归属键', owner_kind: '归属类型', ref_key: '关联标识', dedupe_key: '去重键',
  caption: '说明文字', extra_capture_id: '附加图片ID',
  recipe_id: '配方ID', admin_caps: '管理权限', id_card_no: '身份证号',
  hire_date: '入职日期', seniority_bonus: '工龄奖',
  steamer_id: '蒸笼ID', port_index: '笼位', stack_order: '叠放顺序',
  loaded_at: '上笼时间', is_hold: '叫起', is_rushed: '催菜', fired_at: '起菜时间',
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

// 值的中文写法。**只用于列表展示**（`DataTable` 的单元格文本与 title），
// 行编辑 / 批量编辑提交的仍是库里那一份原值 —— 所以在这里翻成中文不会写坏数据。
// 键可以是 `表.列`（同一列在表间含义不同）或 `列`（全库同一个含义，如布尔位）。
const YES_NO = { 0: '否', 1: '是' }

const CELL_VALUE_LABELS = {
  'orders.source': {
    dine_in: '堂食',
    delivery: '外卖',
  },
  // 排班
  'staff_shifts.duty_slot': { day: '白班', night: '夜班' },
  'staff_assignments.source': { rule: '规则轮转', override: '单日覆盖' },
  'scheduling_overrides.kind': { manual: '店长手改', leave: '请假', swap: '换班' },
  'scheduling_requests.kind': { leave: '请假', swap: '换班' },
  'scheduling_requests.status': {
    pending_peer: '待对方确认',
    pending_manager: '待店长审批',
    approved: '已批准',
    rejected: '已驳回',
    cancelled: '已撤回',
  },
  // 加班与补钟
  'overtime_entries.status': {
    pending: '待审批',
    approved: '已批准',
    rejected: '已驳回',
    cancelled: '已撤回',
    voided: '已作废',
  },
  // 加班与补钟：补钟用存假抵扣（ADR 0124）
  'overtime_leave_deductions.status': {
    pending: '待审批',
    approved: '已批准',
    rejected: '已驳回',
    cancelled: '已撤回',
    voided: '已作废',
  },
  // 企微出站
  'wecom_push_outbox.status': {
    pending: '待发送',
    sending: '发送中',
    sent: '已发送',
    failed: '发送失败',
    skipped: '已跳过',
  },
  'hygiene_wecom_shares.kind': {
    attire: '工装',
    daily: '日常卫生',
    deep_clean: '深度清洁',
    fix: '整改',
  },
  // 损耗
  'wastage_records.reason': {
    spoiled: '变质腐败',
    expired: '过期丢弃',
    damaged: '破损/污染',
    production_fail: '制作失败',
    excess: '备货过量',
    other: '其他',
  },
  'wastage_records.operator_kind': { staff: '员工', admin: '管理端' },
  'wastage_templates.owner_kind': { staff: '员工', admin: '管理端' },
  // 布尔位（库里是 0 / 1）
  enabled: YES_NO,
  is_active: YES_NO,
  active: YES_NO,
  approved: YES_NO,
  disabled: YES_NO,
  is_hold: YES_NO,
  is_rushed: YES_NO,
  half_rest: YES_NO,
}

export function getCellLabel(table, column, value) {
  const columnName = String(column || '')
  const mapped = CELL_VALUE_LABELS[`${String(table || '')}.${columnName}`]
    || CELL_VALUE_LABELS[columnName]
    || CELL_VALUE_LABELS[columnName.toLowerCase()]
  if (!mapped) return value
  const label = mapped[value]
  return label === undefined ? value : label
}
