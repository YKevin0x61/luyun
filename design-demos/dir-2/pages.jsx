/* dir-2 · 作业流 —— 四张页面 */

(function () {

const { useState } = React;
const D = window.DATA;
const {
  Icon, Sep, Pill, Btn, Card, Underline, PageHead, LiveDot,
  DeskShell, PhoneShell, LineChart, CounterShot, ReasonSheet
} = window;

function comma(n) { return Number(n).toLocaleString('en-US'); }
function money(n) { return '¥' + comma(n); }

/* 今天剩下的时间表：时间取自班次定义（早 09–15 / 中 12–20 / 晚 16–22:30），
   09:00 早班上岗已经过去，所以「接下来」从 12:00 起。 */
const AGENDA = [
  { t: '12:00', text: '中班 6 人上岗' },
  { t: '15:00', text: '早班 8 人下班' },
  { t: '16:00', text: '晚班 11 人上岗' },
  { t: '20:00', text: '中班 6 人下班' },
  { t: '22:30', text: '晚班 11 人下班' }
];

/* ============================================================ 今天 */

const todayS = {
  wrap: { maxWidth: 720, margin: '0 auto', padding: '26px 32px 44px' },
  band: { display: 'flex', gap: 6, marginTop: 20 },
  chip: {
    flex: 1, height: 64, borderRadius: 10, display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center', gap: 3
  },
  hero: {
    height: 220, marginTop: 18, borderRadius: 'var(--r-card)', border: '1px solid var(--line-strong)',
    background: 'var(--card)', padding: '16px 18px 16px', display: 'flex', flexDirection: 'column'
  },
  heroMeta: { marginTop: 9, fontSize: 14, color: 'var(--ink-2)', letterSpacing: '-0.01em' },
  heroNote: { marginTop: 7, fontSize: 14, color: 'var(--ink-2)', display: 'flex', alignItems: 'center', gap: 6 },
  restLabel: { marginTop: 26, marginBottom: 10, fontSize: 13, color: 'var(--ink-2)', display: 'flex', alignItems: 'center', gap: 8 },
  rail: { padding: '22px 22px 30px' },
  railRow: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }
};

function RailTitle(props) {
  return <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: '-0.02em' }}>{props.children}</div>;
}

function TodayHero(props) {
  const [state, setState] = useState('pending');
  const readOnly = props.role === 'boss' || props.role === 'staff';
  return (
    <div style={todayS.hero}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber)', letterSpacing: '0.01em' }}>现在最该处理</span>
        <Pill tone={state === 'approved' ? 'brand' : state === 'rejected' ? 'danger' : 'amber'}>
          {state === 'approved' ? '已批准' : state === 'rejected' ? '已驳回' : '待批'}
        </Pill>
        <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--ink-2)' }}>今天唯一要你签的</span>
      </div>
      <div style={{ marginTop: 11, fontSize: 22, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: '28px' }}>张伟请假待批</div>
      <div style={todayS.heroMeta}>
        事假 <Sep /> 10/26–10/27 <Sep /> 2 天
      </div>
      <div style={todayS.heroNote}>
        <Icon name="check" size={15} color="var(--ink-2)" />
        批准后自动写入排班，前厅 A 区少一人
      </div>

      <div style={{ marginTop: 'auto' }}>
        {readOnly ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 52, fontSize: 14, color: 'var(--ink-2)' }}>
            <Icon name="clock" size={16} color="var(--ink-2)" />
            只读身份，等店长批
          </div>
        ) : state === 'pending' ? (
          <div style={{ display: 'flex', gap: 10 }}>
            <Btn grow h={52} onClick={() => setState('approved')}>批准</Btn>
            <Btn kind="outline" h={52} w={132} onClick={() => setState('rejected')}>驳回</Btn>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 52 }}>
            <Icon name={state === 'approved' ? 'check' : 'x'} size={17} color={state === 'approved' ? 'var(--brand)' : 'var(--danger-ink)'} />
            <span style={{ fontSize: 15, fontWeight: 500 }}>
              {state === 'approved' ? '已批准，已写入 10/26–10/27 排班' : '已驳回，张伟会收到通知'}
            </span>
            <button onClick={() => setState('pending')} style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--ink-2)', cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: 3 }}>撤销</button>
          </div>
        )}
      </div>
    </div>
  );
}

function TodayTodo(props) {
  const t = props.tone;
  const tint = t === 'amber' ? { bg: 'var(--amber-soft)', fg: 'var(--amber)' }
    : t === 'neutral' ? { bg: 'var(--card-2)', fg: 'var(--ink-2)' }
      : { bg: 'var(--brand-soft)', fg: 'var(--brand)' };
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 14, padding: '14px 16px',
      background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--r-card)'
    }}>
      <span style={{ width: 38, height: 38, borderRadius: 10, background: tint.bg, color: tint.fg, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}>
        <Icon name={props.icon} size={19} />
      </span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.02em' }}>{props.title}</span>
          {props.pill ? <Pill tone={props.pillTone || 'amber'}>{props.pill}</Pill> : null}
        </div>
        <div style={{ marginTop: 4, fontSize: 14, color: 'var(--ink-2)', display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>{props.meta}</div>
      </div>
      {props.action ? <Btn kind="outline" h={40} fs={14} onClick={props.onAction}>{props.action}</Btn> : null}
    </div>
  );
}

function TodayRail() {
  const kpi = [
    { label: '营业额', value: money(D.today.revenue), sub: '较昨日 ' + D.today.deltaYest + '，较上周六 ' + D.today.deltaLastSat },
    { label: '订单', value: D.today.orders + ' 单', sub: '客单价 ¥' + D.today.avg },
    { label: '待验收', value: D.todo.hygiene + ' 项', sub: '其中逾期 ' + D.todo.hygieneOverdue }
  ];
  const now = [
    { label: '出餐速率', value: D.today.rate30 + ' 单/时' },
    { label: '在店桌台', value: D.today.tablesUsed + '/' + D.today.tablesTotal },
    { label: '最慢档口', value: D.today.slowest + ' ' + D.today.slowestMin + ' 分钟' }
  ];
  const hr = '1px solid var(--line)';
  return (
    <div style={todayS.rail}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <RailTitle>接下来</RailTitle>
        <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{D.store.dateLabel} {D.store.weekday}</span>
      </div>

      <div>
        <div style={{ marginTop: 8 }}>
          {AGENDA.map((a, i) => (
            <div key={a.t + a.text} style={{ display: 'flex', alignItems: 'center', gap: 12, height: 32, borderTop: i ? hr : 'none' }}>
              <span className="num" style={{ width: 42, fontSize: 13, color: 'var(--ink-2)' }}>{a.t}</span>
              <span style={{ fontSize: 14, letterSpacing: '-0.01em' }}>{a.text}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 16, paddingTop: 16, borderTop: hr }}>
        <RailTitle>经营</RailTitle>
        <div style={{ marginTop: 8 }}>
          {kpi.map((k, i) => (
            <div key={k.label} style={{ paddingTop: i ? 8 : 0, marginTop: i ? 8 : 0, borderTop: i ? hr : 'none' }}>
              <div style={todayS.railRow}>
                <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{k.label}</span>
                <span className="num" style={{ fontSize: 21, fontWeight: 600, letterSpacing: '-0.02em' }}>{k.value}</span>
              </div>
              <div style={{ marginTop: 2, fontSize: 13, color: 'var(--ink-2)', textAlign: 'right' }}>{k.sub}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 16, paddingTop: 16, borderTop: hr }}>
        <RailTitle>现在</RailTitle>
        <div style={{ marginTop: 6 }}>
          {now.map((n, i) => (
            <div key={n.label} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 28, borderTop: i ? hr : 'none' }}>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{n.label}</span>
              <span className="num" style={{ fontSize: 14, fontWeight: 500 }}>{n.value}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 16, paddingTop: 16, borderTop: hr }}>
        <RailTitle>今天谁上班</RailTitle>
        <div style={{ marginTop: 6 }}>
          {D.shifts.map((s, i) => (
            <div key={s.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 28, borderTop: i ? hr : 'none' }}>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{s.label} <span className="num">{s.time}</span></span>
              <span className="num" style={{ fontSize: 14, fontWeight: 600 }}>{s.count} 人</span>
            </div>
          ))}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 28, borderTop: hr }}>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>休 <span className="num">{D.offCount} 人</span></span>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>请假 <span className="num">1 人</span>（张伟）</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 3, marginTop: 8 }}>
          {[8, 6, 11].map((n, i) => (
            <span key={i} style={{ width: n * 8 + 'px', height: 6, borderRadius: 3, background: i === 0 ? 'var(--shift-am)' : i === 1 ? 'var(--shift-mid)' : 'var(--shift-pm)' }} />
          ))}
          <span style={{ width: '24px', height: 6, borderRadius: 3, background: 'var(--shift-off)' }} />
          <span style={{ width: '8px', height: 6, borderRadius: 3, background: 'var(--shift-leave)' }} />
        </div>
      </div>
    </div>
  );
}

function TodayPage(props) {
  return (
    <DeskShell role={props.role} active="today" rail={<TodayRail />}>
      <div style={todayS.wrap}>
        <PageHead
          title="今天"
          sub={<span>{D.store.dateLabel} {D.store.weekday} <Sep /> 营业日 {D.store.openFrom} 起</span>}
          right={<LiveDot />}
        />

        <div style={todayS.band}>
          {D.week.map((d, i) => {
            const on = i === D.week.length - 1;
            return (
              <div key={d.d} style={Object.assign({}, todayS.chip, {
                background: on ? 'var(--brand)' : 'var(--card)',
                border: '1px solid ' + (on ? 'var(--brand)' : 'var(--line)')
              })}>
                <span style={{ fontSize: 12, color: on ? 'var(--brand-ink)' : 'var(--ink-2)' }}>{d.w}</span>
                <span className="num" style={{ fontSize: 18, fontWeight: 600, lineHeight: '20px', letterSpacing: '-0.02em', color: on ? 'var(--brand-ink)' : 'var(--ink)' }}>{d.d}</span>
              </div>
            );
          })}
        </div>

        <TodayHero role={props.role} />

        <div style={todayS.restLabel}>
          <span>其余待办 3 件</span>
          <span style={{ flex: 1, height: 1, background: 'var(--line)' }} />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <TodayTodo
            icon="shield" tone="brand" title="卫生验收"
            pill={'逾期 ' + D.todo.hygieneOverdue} pillTone="danger"
            meta={<span><span className="num">{D.todo.hygiene}</span> 项待验收 <Sep /> 后厨 灶台 等</span>}
            action={props.role === 'boss' ? '查看' : '去验收'} />
          <TodayTodo
            icon="alert" tone="amber" title="整改单 1 条"
            pill="待整改" pillTone="amber"
            meta={<span>{D.rectify.zone} <Sep /> {D.rectify.item} <Sep /> 距截止 <span className="num">{D.rectify.left}</span></span>}
            action="查看" />
          <TodayTodo
            icon="box" tone="neutral" title="备货还差 7 项"
            meta={<span>热菜 <span className="num">3</span> <Sep /> 蒸笼 <span className="num">2</span> <Sep /> 凉菜 <span className="num">2</span></span>}
            action={props.role === 'boss' ? '查看' : '去备货'} />
        </div>
      </div>
    </DeskShell>
  );
}

/* ============================================================ 销售报表 */

const salesS = {
  store: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13, color: 'var(--ink-2)' },
  chips: { display: 'flex', gap: 8, marginTop: 12, paddingBottom: 2 },
  chip: { height: 36, padding: '0 14px', borderRadius: 999, fontSize: 14, fontWeight: 500, whiteSpace: 'nowrap', cursor: 'pointer' },
  stats: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 },
  statCard: { background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: '12px 14px' },
  statLabel: { fontSize: 13, color: 'var(--ink-2)' },
  statValue: { marginTop: 4, fontSize: 25, fontWeight: 600, letterSpacing: '-0.03em', lineHeight: '30px' },
  secHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  secTitle: { fontSize: 16, fontWeight: 600, letterSpacing: '-0.02em' }
};

function SalesStat(props) {
  return (
    <div style={salesS.statCard}>
      <div style={salesS.statLabel}>{props.label}</div>
      <div className="num" style={salesS.statValue}>{props.value}</div>
    </div>
  );
}

function SalesPage(props) {
  const [period, setPeriod] = useState('7d');
  const [tip, setTip] = useState(false);
  const periods = [{ k: 'today', t: '今天' }, { k: 'yest', t: '昨天' }, { k: '7d', t: '近 7 天' }, { k: 'month', t: '本月' }];
  const maxPct = D.sales.stations[0].pct;
  return (
    <PhoneShell role={props.role} title="销售报表" tab="sales" onTab={props.onTab}>
      <div style={salesS.store}>
        <span>{D.store.name}<Sep />{D.store.branch}</span>
        <span>只读报表</span>
      </div>

      <div className="row-x" style={salesS.chips}>
        {periods.map(p => {
          const on = p.k === period;
          return (
            <button key={p.k} onClick={() => setPeriod(p.k)} style={Object.assign({}, salesS.chip, {
              background: on ? 'var(--brand)' : 'var(--card)',
              border: '1px solid ' + (on ? 'var(--brand)' : 'var(--line)'),
              color: on ? 'var(--brand-ink)' : 'var(--ink-2)',
              fontWeight: on ? 600 : 500
            })}>{p.t}</button>
          );
        })}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 16 }}>
        <span style={salesS.secTitle}>近 7 天</span>
        <button onClick={() => setTip(!tip)} aria-label="口径说明"
                style={{ width: 26, height: 26, borderRadius: 13, border: '1px solid var(--line-strong)', color: 'var(--ink-2)', fontSize: 13, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>?</button>
      </div>

      <div style={salesS.stats}>
        <SalesStat label="销售金额" value={money(D.sales.amount)} />
        <SalesStat label="订单" value={comma(D.sales.orders)} />
        <SalesStat label="客单价" value={'¥' + D.sales.avg} />
        <SalesStat label="菜品" value={String(D.sales.dishes)} />
      </div>
      <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13, color: 'var(--ink-2)' }}>
        <span>退菜 <span className="num">{D.sales.refund}</span> 行</span>
        <span>近 7 天合计</span>
      </div>

      {tip ? (
        <div style={{ marginTop: 10, padding: '10px 12px', background: 'var(--card-2)', border: '1px solid var(--line)', borderRadius: 10, fontSize: 13, lineHeight: '20px', color: 'var(--ink-2)' }}>
          销售金额 = 已结账订单合计；客单价 = 销售金额 ÷ 订单数（¥186,420 ÷ 6,028 = ¥30.9）。
        </div>
      ) : null}

      <Card pad={14} style={{ marginTop: 12 }}>
        <div style={salesS.secHead}>
          <span style={salesS.secTitle}>营业额走势</span>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>周六最高</span>
        </div>
        <LineChart days={D.sales.days} />
      </Card>

      <Card pad={14} style={{ marginTop: 12 }}>
        <div style={salesS.secHead}>
          <span style={salesS.secTitle}>档口占比</span>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>按金额</span>
        </div>
        {D.sales.stations.map(s => (
          <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 10, height: 28 }}>
            <span style={{ width: 42, fontSize: 14, color: 'var(--ink)' }}>{s.name}</span>
            <span style={{ flex: 1, height: 8, borderRadius: 4, background: 'var(--line)', overflow: 'hidden' }}>
              <span style={{ display: 'block', height: 8, width: (s.pct / maxPct * 100) + '%', background: 'var(--brand)', borderRadius: 4 }} />
            </span>
            <span className="num" style={{ width: 46, textAlign: 'right', fontSize: 13, color: 'var(--ink-2)' }}>{s.pct}%</span>
          </div>
        ))}
      </Card>

      <Card pad={14} style={{ marginTop: 12 }}>
        <div style={salesS.secHead}>
          <span style={salesS.secTitle}>菜品榜</span>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>前 5</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', fontSize: 13, color: 'var(--ink-2)', paddingBottom: 6 }}>
          <span style={{ width: 20 }}></span>
          <span style={{ flex: 1 }}>菜品</span>
          <span style={{ width: 36 }}>档口</span>
          <span style={{ width: 48, textAlign: 'right' }}>数量</span>
          <span style={{ width: 50, textAlign: 'right' }}>占比</span>
        </div>
        {D.sales.top.map(t => (
          <div key={t.name} style={{ display: 'flex', alignItems: 'center', height: 46, borderTop: '1px solid var(--line)' }}>
            <span className="num" style={{ width: 20, fontSize: 13, color: 'var(--ink-3)' }}>{t.rank}</span>
            <span style={{ flex: 1, minWidth: 0, fontSize: 16, fontWeight: 500, letterSpacing: '-0.02em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}</span>
            <span style={{ width: 36, fontSize: 13, color: 'var(--ink-2)' }}>{t.station}</span>
            <span className="num" style={{ width: 48, textAlign: 'right', fontSize: 16 }}>{t.qty}</span>
            <span className="num" style={{ width: 50, textAlign: 'right', fontSize: 13, color: 'var(--ink-2)' }}>{t.pct}</span>
          </div>
        ))}
      </Card>

      <div style={{ marginTop: 16 }}>
        <Btn kind="outline" h={48} grow icon="download">导出 CSV</Btn>
      </div>
      <div style={{ marginTop: 20 }} />
    </PhoneShell>
  );
}

/* ============================================================ 日常验收 */

const dailyS = {
  date: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13, color: 'var(--ink-2)' },
  sum: { display: 'flex', marginTop: 10, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--r-card)', overflow: 'hidden' },
  sumCell: { flex: 1, padding: '12px 0', textAlign: 'center' },
  sumNum: { fontSize: 19, fontWeight: 600, letterSpacing: '-0.02em', lineHeight: '22px' },
  sumLabel: { marginTop: 3, fontSize: 13, color: 'var(--ink-2)' },
  card: { marginTop: 12, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: 14 },
  shots: { display: 'flex', gap: 8, marginTop: 12 },
  fig: { flex: 1, position: 'relative', margin: 0, height: 240, borderRadius: 8, overflow: 'hidden', border: '1px solid var(--line)' },
  tag: { position: 'absolute', left: 6, top: 6, display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 7px', borderRadius: 6, background: 'var(--bg)', fontSize: 13, fontWeight: 600, letterSpacing: '-0.01em' },
  queue: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 14, padding: '0 2px' }
};

function DailyPage(props) {
  const [sheet, setSheet] = useState(false);
  const [res, setRes] = useState(null);
  const [reason, setReason] = useState('');
  const h = D.hygiene;
  const sum = [
    { n: h.pending, label: '待验收', color: 'var(--amber)' },
    { n: h.toShoot, label: '待拍', color: 'var(--ink)' },
    { n: h.passed, label: '已通过', color: 'var(--ink-2)' },
    { n: h.missed, label: '漏验', color: 'var(--danger-ink)' }
  ];
  return (
    <PhoneShell role={props.role} title="日常验收" tab="floor" onTab={props.onTab}>
      <div style={dailyS.date}>
        <span>{D.store.dateLabel} {D.store.weekday}</span>
        <span className="num">营业日 {D.store.openFrom} 起</span>
      </div>

      <div style={dailyS.sum}>
        {sum.map((s, i) => (
          <div key={s.label} style={Object.assign({}, dailyS.sumCell, { borderLeft: i ? '1px solid var(--line)' : 'none' })}>
            <div className="num" style={Object.assign({}, dailyS.sumNum, { color: s.color })}>{s.n}</div>
            <div style={dailyS.sumLabel}>{s.label}</div>
          </div>
        ))}
      </div>

      <div style={dailyS.card}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.03em' }}>灶台 <Sep /> 油污与残渣</div>
            <div style={{ marginTop: 4, fontSize: 13, color: 'var(--ink-2)' }}>后厨 <Sep /> 日常验收</div>
          </div>
          <Pill tone={res === 'pass' ? 'brand' : res === 'reject' ? 'danger' : 'amber'}>
            {res === 'pass' ? '已通过' : res === 'reject' ? '已驳回' : '待验收'}
          </Pill>
        </div>

        <div style={dailyS.shots}>
          <figure style={dailyS.fig}>
            <CounterShot variant="std" />
            <figcaption style={Object.assign({}, dailyS.tag, { color: 'var(--ink-2)' })}>标准图</figcaption>
          </figure>
          <figure style={dailyS.fig}>
            <CounterShot variant="act" />
            <figcaption style={Object.assign({}, dailyS.tag, { color: 'var(--ink)' })}>
              实拍 <Sep /> {h.current.by} <span className="num">{h.current.at}</span>
            </figcaption>
          </figure>
        </div>

        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, color: 'var(--ink-2)' }}>
          <Icon name="camera" size={15} color="var(--ink-2)" />
          <span>{h.current.shift} <Sep /> 提交人不能自验 <Sep /> 对照标准图判</span>
        </div>

        {res ? (
          <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 10, height: 52 }}>
            <Icon name={res === 'pass' ? 'check' : 'x'} size={17} color={res === 'pass' ? 'var(--brand)' : 'var(--danger-ink)'} />
            <span style={{ fontSize: 15, fontWeight: 500 }}>
              {res === 'pass' ? '已通过，王芳 10:24 的实拍' : '已驳回，理由已发给王芳'}
            </span>
            <button onClick={() => { setRes(null); setReason(''); }} style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--ink-2)', textDecoration: 'underline', textUnderlineOffset: 3, cursor: 'pointer' }}>撤销</button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <Btn h={52} grow onClick={() => setRes('pass')}>通过</Btn>
            <Btn kind="danger" h={52} w={128} onClick={() => setSheet(true)}>驳回</Btn>
          </div>
        )}
      </div>

      <div style={dailyS.queue}>
        <span style={{ fontSize: 14, color: 'var(--ink-2)' }}>下一项 <Sep /> 蒸笼区（3/9）</span>
        <span style={{ display: 'flex', gap: 4 }}>
          {[1, 2, 3, 4, 5, 6, 7, 8, 9].map(i => (
            <span key={i} style={{ width: 5, height: 5, borderRadius: 3, background: i <= h.next.index ? 'var(--brand)' : 'var(--line-strong)' }} />
          ))}
        </span>
      </div>

      {reason ? (
        <div style={{ marginTop: 10, padding: '10px 12px', background: 'var(--card-2)', border: '1px solid var(--line)', borderRadius: 10, fontSize: 13, lineHeight: '20px', color: 'var(--ink-2)' }}>
          驳回理由：{reason}
        </div>
      ) : null}

      <div style={{ marginTop: 12, display: 'flex', gap: 10 }}>
        <div style={{ flex: 1, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 10, padding: '10px 12px' }}>
          <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>一次通过率</div>
          <div className="num" style={{ marginTop: 2, fontSize: 18, fontWeight: 600, letterSpacing: '-0.02em' }}>{h.passRate}%</div>
        </div>
        <div style={{ flex: 1, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 10, padding: '10px 12px' }}>
          <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>近 30 天判定</div>
          <div className="num" style={{ marginTop: 2, fontSize: 18, fontWeight: 600, letterSpacing: '-0.02em' }}>{comma(h.judged)} 项</div>
        </div>
      </div>

      <ReasonSheet key={sheet ? 'open' : 'closed'} open={sheet} onClose={() => setSheet(false)}
                   onSubmit={txt => { setReason(txt); setRes('reject'); setSheet(false); }} />
    </PhoneShell>
  );
}

/* ============================================================ 排班月历 */

const calS = {
  wrap: { maxWidth: 720, margin: '0 auto', padding: '22px 32px 16px', display: 'flex', flexDirection: 'column', minHeight: '100%' },
  head: { display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 },
  h1: { margin: 0, fontSize: 28, lineHeight: '34px', fontWeight: 700, letterSpacing: '-0.03em' },
  legend: { display: 'flex', alignItems: 'center', gap: 14, marginTop: 14, fontSize: 13, color: 'var(--ink-2)' },
  lgItem: { display: 'inline-flex', alignItems: 'center', gap: 6 },
  wkHead: { display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6, marginTop: 12, marginBottom: 6 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6 },
  cell: { height: 118, borderRadius: 8, background: 'var(--card)', border: '1px solid var(--line)', padding: '7px 7px 6px', display: 'flex', flexDirection: 'column' },
  bars: { display: 'flex', flexDirection: 'column', gap: 2, marginTop: 6, flex: 1, overflow: 'hidden' },
  bar: { height: 3, borderRadius: 1.5 },
  pen: { marginTop: 12, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: '9px 12px 9px', display: 'flex', flexDirection: 'column' },
  seg: { display: 'flex', gap: 4 },
  segBtn: { height: 36, minWidth: 46, padding: '0 10px', borderRadius: 8, fontSize: 14, fontWeight: 500, cursor: 'pointer', border: '1px solid transparent' },
  rail: { padding: '20px 20px 14px' },
  row: { display: 'flex', alignItems: 'center', gap: 9, padding: '4px 0', borderTop: '1px solid var(--line)' }
};

const CAL_BAR = {
  am: 'var(--shift-am)', mid: 'var(--shift-mid)', pm: 'var(--shift-pm)',
  off: 'var(--shift-off)', leave: 'var(--shift-leave)'
};
const CAL_LEGEND = [
  { k: 'am', t: '班' }, { k: 'off', t: '休' }, { k: 'leave', t: '假' }
];

function CalLegendSwatch(props) {
  if (props.k === '班') {
    return (
      <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 1.5, width: 16 }}>
        <span style={{ height: 3, borderRadius: 1.5, background: 'var(--shift-am)' }} />
        <span style={{ height: 3, borderRadius: 1.5, background: 'var(--shift-mid)' }} />
        <span style={{ height: 3, borderRadius: 1.5, background: 'var(--shift-pm)' }} />
      </span>
    );
  }
  return <span style={{ width: 16, height: 4, borderRadius: 2, background: props.color, border: props.dash ? '1px dashed var(--line-strong)' : 'none' }} />;
}

function CalendarPage(props) {
  const [view, setView] = useState('month');
  const [shift, setShift] = useState(null);
  const [zone, setZone] = useState('后厨');
  const [batches, setBatches] = useState([]);

  const cal = D.cal;
  /* 10/25 是周日（第 7 列，索引 6），据此倒推 10/1 落在第 4 列 → 前导 3 格 */
  const leads = ((6 - (cal.today - 1)) % 7 + 7) % 7;
  const cells = [];
  for (let i = 0; i < leads; i++) cells.push(null);
  for (let d = 1; d <= 31; d++) cells.push(d);
  while (cells.length < 35) cells.push(null);

  const writeList = batches.reduce((acc, b) => acc.concat(b.items), []);
  function writesOn(day) { return writeList.filter(w => w.day === day); }

  function pickShift(k) { setShift(k); }
  function pickZone(z) { setZone(z); }

  function clickCell(day) {
    if (!shift) return;
    const key = shift + '|' + zone;
    setBatches(prev => {
      const next = prev.slice();
      const last = next[next.length - 1];
      if (last && last.key === key) {
        next[next.length - 1] = { key: key, items: last.items.concat([{ day: day, shift: shift, zone: zone }]) };
      } else {
        next.push({ key: key, items: [{ day: day, shift: shift, zone: zone }] });
      }
      return next;
    });
  }

  function undo() {
    if (!batches.length) return;
    setBatches(batches.slice(0, batches.length - 1));
  }

  const hint = shift
    ? '已选 ' + (shift === 'off' ? '休' : { am: '早', mid: '中', pm: '晚' }[shift]) + '班（' + zone + '），点日期写入'
    : '先选班次，再点日期写入';

  return (
    <DeskShell role={props.role} active="people" rail={<CalendarRail />}>
      <div style={calS.wrap}>
        <div style={calS.head}>
          <div>
            <h1 style={calS.h1}>{cal.year} 年 {cal.month} 月</h1>
            <Underline />
            <div style={{ marginTop: 8, fontSize: 13, color: 'var(--ink-2)' }}>
              {view === 'month' ? '整月排班，28 人花名册' : '第 4 周，10/19 – 10/25'}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ display: 'flex', border: '1px solid var(--line-strong)', borderRadius: 8, overflow: 'hidden' }}>
              {[{ k: 'month', t: '月' }, { k: 'week', t: '周' }].map(v => {
                const on = v.k === view;
                return (
                  <button key={v.k} onClick={() => setView(v.k)} style={{
                    height: 36, width: 46, fontSize: 14, fontWeight: 500, cursor: 'pointer',
                    background: on ? 'var(--card-2)' : 'transparent', color: on ? 'var(--ink)' : 'var(--ink-2)'
                  }}>{v.t}</button>
                );
              })}
            </div>
          </div>
        </div>

        <div style={calS.legend}>
          {CAL_LEGEND.map(l => (
            <span key={l.t} style={calS.lgItem}>
              {l.k === 'am' ? <CalLegendSwatch k="班" /> : <CalLegendSwatch color={CAL_BAR[l.k]} />}
              {l.t}
            </span>
          ))}
          <span style={calS.lgItem}><CalLegendSwatch color="transparent" dash />未排</span>
          <span style={calS.lgItem}><span style={{ width: 16, height: 4, borderRadius: 2, background: 'var(--amber-soft)', border: '1px solid var(--amber)' }} />待批</span>
        </div>

        <div style={calS.wkHead}>
          {cal.weekdays.map(w => (
            <div key={w} style={{ fontSize: 13, color: 'var(--ink-2)', textAlign: 'center' }}>{w}</div>
          ))}
        </div>

        <div style={calS.grid}>
          {cells.map((day, idx) => {
            if (!day) return <div key={'e' + idx} />;
            const isToday = day === cal.today;
            const isLeave = cal.leaveDays.indexOf(day) >= 0;
            const isPending = day === cal.pendingDay;
            const unsched = cal.unscheduled.indexOf(day) >= 0;
            const ws = writesOn(day);
            const delta = ws.reduce((s, w) => s + (w.shift === 'off' ? -1 : 1), 0);
            const count = cal.counts[day - 1] + delta;
            const base = Math.max(6, Math.min(11, cal.counts[day - 1] - 16));
            const am = Math.ceil(base * 0.45), mid = Math.ceil(base * 0.75);
            const extraShift = isLeave ? ['leave'] : (isPending ? ['leave'] : []);
            const barKinds = [];
            if (!unsched) {
              for (let i = 0; i < base; i++) barKinds.push(i < am ? 'am' : (i < mid ? 'mid' : 'pm'));
              extraShift.forEach(k => barKinds.push(k));
            }
            ws.forEach(w => barKinds.push(w.shift));
            const dim = view === 'week' && (day < 19 || day > 25);
            return (
              <div key={day} className={shift ? 'cell press' : 'cell'}
                   onClick={() => clickCell(day)}
                   style={Object.assign({}, calS.cell, {
                     border: '1px ' + (unsched ? 'dashed' : 'solid') + ' ' + (isToday ? 'var(--brand)' : (ws.length ? 'var(--line-strong)' : 'var(--line)')),
                     background: dim ? 'var(--bg)' : 'var(--card)',
                     cursor: shift ? 'pointer' : 'default'
                   })}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 20 }}>
                  <span className="num" style={{ fontSize: 13, fontWeight: 600, color: isToday ? 'var(--brand)' : 'var(--ink)' }}>{day}</span>
                  {isLeave || isPending ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', height: 18, padding: '0 6px', borderRadius: 999, background: 'var(--amber-soft)', color: 'var(--amber)', fontSize: 13, fontWeight: 600, lineHeight: 1 }}>待批</span>
                  ) : null}
                  {unsched ? <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>未排</span> : null}
                </div>
                <div style={calS.bars}>
                  {barKinds.slice(0, 12).map((k, i) => (
                    <span key={i} style={Object.assign({}, calS.bar, { background: CAL_BAR[k] })} />
                  ))}
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <span className="num" style={{ fontSize: 13, color: 'var(--ink-2)' }}>{unsched ? '—' : count}</span>
                </div>
              </div>
            );
          })}
        </div>

        <div style={calS.pen}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 13, color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>写排班</span>
            <div style={calS.seg}>
              {[{ k: 'am', t: '早' }, { k: 'mid', t: '中' }, { k: 'pm', t: '晚' }, { k: 'off', t: '休' }].map(s => {
                const on = shift === s.k;
                return (
                  <button key={s.k} onClick={() => pickShift(s.k)} style={Object.assign({}, calS.segBtn, {
                    background: on ? 'var(--brand)' : 'var(--card-2)',
                    color: on ? 'var(--brand-ink)' : 'var(--ink-2)',
                    fontWeight: on ? 600 : 500
                  })}>{s.t}</button>
                );
              })}
            </div>
            <span style={{ width: 1, height: 22, background: 'var(--line)', flex: '0 0 auto' }} />
            <div style={calS.seg}>
              {['后厨', '前厅', '吧台'].map(z => {
                const on = zone === z;
                return (
                  <button key={z} onClick={() => pickZone(z)} style={Object.assign({}, calS.segBtn, {
                    minWidth: 52,
                    background: on ? 'var(--card-2)' : 'transparent',
                    border: '1px solid ' + (on ? 'var(--line-strong)' : 'transparent'),
                    color: on ? 'var(--ink)' : 'var(--ink-2)'
                  })}>{z}</button>
                );
              })}
            </div>
            <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>
              <Icon name="edit" size={15} color="var(--ink-3)" />
              笔在手上，点格子就写入
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
            <span style={{ fontSize: 13, color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{hint}</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{batches.length ? '已写 ' + batches.length + ' 批' : '还没写入'}</span>
              <Btn kind="ghost" h={30} fs={13} icon="undo" disabled={!batches.length} onClick={undo}>撤销最近一批</Btn>
            </div>
          </div>
        </div>
      </div>
    </DeskShell>
  );
}

function CalendarRail() {
  const leavePill = { 早班: 'brand', 中班: 'neutral', 晚班: 'neutral', 请假: 'danger' };
  const [done, setDone] = useState({});
  return (
    <div style={calS.rail}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <div>
          <span className="num" style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.02em' }}>10.25</span>
          <span style={{ marginLeft: 8, fontSize: 15, color: 'var(--ink-2)' }}>周日</span>
        </div>
        <Pill tone="amber">待批 2</Pill>
      </div>
      <div style={{ marginTop: 4, fontSize: 13, color: 'var(--ink-2)' }}>当天名单 <Sep /> 25 人在岗</div>

      <div style={{ marginTop: 10 }}>
        {D.roster.map(p => (
          <div key={p.name} style={calS.row}>
            <span style={{ width: 24, height: 24, borderRadius: 12, background: 'var(--card-2)', color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 600, flex: '0 0 auto' }}>{p.name.charAt(0)}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-0.01em' }}>{p.name}</span>
                <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{p.role}</span>
              </div>
              <div style={{ marginTop: 1, fontSize: 13, color: 'var(--ink-2)', display: 'flex', alignItems: 'center' }}>
                {p.zone}
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Pill tone={leavePill[p.shift] || 'neutral'}>{p.shift}</Pill>
              <span className="num" style={{ fontSize: 13, color: 'var(--ink-2)' }}>{p.time}</span>
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line-strong)' }}>
        <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: '-0.02em' }}>待批请假</div>
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {D.pendingLeave.map(l => {
            const key = l.name + l.range;
            const state = done[key];
            return (
              <div key={key} style={{ background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 10, padding: '8px 11px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>{l.name} <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink-2)' }}>{l.type}</span></span>
                  <span className="num" style={{ fontSize: 13, color: 'var(--ink-2)' }}>{l.range}</span>
                </div>
                <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>共 <span className="num">{l.days}</span> 天</span>
                  {state ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      <Pill tone={state === 'ok' ? 'brand' : 'danger'}>{state === 'ok' ? '已批准' : '已驳回'}</Pill>
                      <button onClick={() => setDone(Object.assign({}, done, { [key]: null }))}
                              style={{ fontSize: 13, color: 'var(--ink-2)', textDecoration: 'underline', textUnderlineOffset: 3, cursor: 'pointer' }}>撤销</button>
                    </span>
                  ) : (
                    <span style={{ display: 'flex', gap: 6 }}>
                      <Btn h={30} fs={13} onClick={() => setDone(Object.assign({}, done, { [key]: 'ok' }))}>批准</Btn>
                      <Btn kind="outline" h={30} fs={13} onClick={() => setDone(Object.assign({}, done, { [key]: 'no' }))}>驳回</Btn>
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 38, cursor: 'pointer' }}>
          <Icon name="calendar" size={17} color="var(--ink-2)" />
          <span style={{ fontSize: 14, fontWeight: 600 }}>固定班规则</span>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}><span className="num">{D.cal.fixed}</span> 条生效中</span>
          <span style={{ marginLeft: 'auto' }}><Icon name="chevron" size={16} color="var(--ink-3)" /></span>
        </div>
      </div>
    </div>
  );
}

/* ============================================================ 手机版今天（tab 的「今天」落到这里） */

const mtodayS = {
  band: { display: 'flex', gap: 4, marginTop: 12 },
  chip: { flex: 1, height: 54, borderRadius: 9, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2 },
  hero: { marginTop: 12, background: 'var(--card)', border: '1px solid var(--line-strong)', borderRadius: 'var(--r-card)', padding: 14 },
  row: { background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 10, padding: '11px 13px', display: 'flex', alignItems: 'center', gap: 11 }
};

function MobileTodayPage(props) {
  const [state, setState] = useState('pending');
  return (
    <PhoneShell role={props.role} title="今天" tab="today" onTab={props.onTab}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13, color: 'var(--ink-2)' }}>
        <span>{D.store.dateLabel} {D.store.weekday}</span>
        <span className="num">营业日 {D.store.openFrom} 起</span>
      </div>

      <div style={mtodayS.band}>
        {D.week.map((d, i) => {
          const on = i === D.week.length - 1;
          return (
            <div key={d.d} style={Object.assign({}, mtodayS.chip, {
              background: on ? 'var(--brand)' : 'var(--card)',
              border: '1px solid ' + (on ? 'var(--brand)' : 'var(--line)')
            })}>
              <span style={{ fontSize: 12, color: on ? 'var(--brand-ink)' : 'var(--ink-2)' }}>{d.w.slice(1)}</span>
              <span className="num" style={{ fontSize: 16, fontWeight: 600, lineHeight: '18px', color: on ? 'var(--brand-ink)' : 'var(--ink)' }}>{d.d}</span>
            </div>
          );
        })}
      </div>

      <div style={mtodayS.hero}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber)' }}>现在最该处理</span>
          <Pill tone={state === 'approved' ? 'brand' : state === 'rejected' ? 'danger' : 'amber'}>
            {state === 'approved' ? '已批准' : state === 'rejected' ? '已驳回' : '待批'}
          </Pill>
        </div>
        <div style={{ marginTop: 8, fontSize: 20, fontWeight: 700, letterSpacing: '-0.03em' }}>张伟请假待批</div>
        <div style={{ marginTop: 5, fontSize: 16, color: 'var(--ink-2)' }}>事假 <Sep /> 10/26–10/27 <Sep /> 2 天</div>
        <div style={{ marginTop: 4, fontSize: 13, color: 'var(--ink-2)' }}>批准后自动写入排班</div>
        <div style={{ marginTop: 12 }}>
          {state === 'pending' ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <Btn grow h={52} onClick={() => setState('approved')}>批准</Btn>
              <Btn kind="outline" h={52} w={104} onClick={() => setState('rejected')}>驳回</Btn>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 52 }}>
              <Icon name={state === 'approved' ? 'check' : 'x'} size={17} color={state === 'approved' ? 'var(--brand)' : 'var(--danger-ink)'} />
              <span style={{ fontSize: 15 }}>{state === 'approved' ? '已批准，已写入排班' : '已驳回，张伟会收到通知'}</span>
              <button onClick={() => setState('pending')} style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--ink-2)', textDecoration: 'underline', textUnderlineOffset: 3, cursor: 'pointer' }}>撤销</button>
            </div>
          )}
        </div>
      </div>

      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={mtodayS.row}>
          <span style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--brand-soft)', color: 'var(--brand)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}><Icon name="shield" size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>卫生验收 <span className="num">{D.todo.hygiene}</span> 项</div>
            <div style={{ marginTop: 2, fontSize: 13, color: 'var(--ink-2)' }}>其中逾期 1 项</div>
          </div>
          <Icon name="chevron" size={16} color="var(--ink-3)" />
        </div>
        <div style={mtodayS.row}>
          <span style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--amber-soft)', color: 'var(--amber)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}><Icon name="alert" size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>整改单 1 条</div>
            <div style={{ marginTop: 2, fontSize: 13, color: 'var(--ink-2)' }}>{D.rectify.zone} <Sep /> {D.rectify.item}</div>
          </div>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{D.rectify.left}</span>
        </div>
        <div style={mtodayS.row}>
          <span style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--card-2)', color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}><Icon name="box" size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>备货还差 7 项</div>
            <div style={{ marginTop: 2, fontSize: 13, color: 'var(--ink-2)' }}>热菜 3 <Sep /> 蒸笼 2 <Sep /> 凉菜 2</div>
          </div>
          <Icon name="chevron" size={16} color="var(--ink-3)" />
        </div>
      </div>

      <div style={{ marginTop: 14, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: '12px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>今天谁上班</span>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>请假 1 人（张伟）</span>
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          {D.shifts.map(s => (
            <div key={s.key} style={{ flex: 1 }}>
              <div className="num" style={{ fontSize: 19, fontWeight: 600, letterSpacing: '-0.02em' }}>{s.count}</div>
              <div style={{ marginTop: 1, fontSize: 13, color: 'var(--ink-2)' }}>{s.label}</div>
            </div>
          ))}
          <div style={{ flex: 1 }}>
            <div className="num" style={{ fontSize: 19, fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--ink-2)' }}>{D.offCount}</div>
            <div style={{ marginTop: 1, fontSize: 13, color: 'var(--ink-2)' }}>休</div>
          </div>
        </div>
      </div>
    </PhoneShell>
  );
}

Object.assign(window, { TodayPage, SalesPage, DailyPage, CalendarPage, MobileTodayPage });

})();
