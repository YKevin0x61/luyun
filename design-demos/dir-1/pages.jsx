/* 方向一 · 仪表台 —— 四张页面
   today  / sales / daily / calendar */

const P = React;
const D = window.D1;

/* =====================================================================
   今天 · 桌面 1440（店长）
   ===================================================================== */

const pageCol = {
  strip: {
    height: 34,
    flex: '0 0 34px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottom: '1px solid var(--line)'
  },
  ruler: { position: 'relative', paddingTop: 6, borderBottom: '1px solid var(--line)' }
};

function PageRateRuler() {
  const hostRef = P.useRef(null);
  const [W, setW] = P.useState(1328);

  P.useEffect(function () {
    const el = hostRef.current;
    if (!el) return;
    const read = function () { setW(el.clientWidth || 1328); };
    read();
    let ro = null;
    if (window.ResizeObserver) {
      ro = new ResizeObserver(read);
      ro.observe(el);
    } else {
      window.addEventListener('resize', read);
    }
    return function () {
      if (ro) ro.disconnect();
      else window.removeEventListener('resize', read);
    };
  }, []);

  const START = 6;
  const END = 24;
  const N = (END - START) * 4;
  const H = 104;
  const AX = 78;
  const TOP = 18;
  const step = W / N;
  const xOf = function (h) { return ((h - START) / (END - START)) * W; };

  const nowH = 18 + 40 / 60;
  const profile = P.useMemo(function () {
    const arr = [];
    for (let i = 0; i < N; i++) {
      const t = START + i * 0.25 + 0.125;
      const lunch = 30 * Math.exp(-Math.pow((t - 12.4) / 1.15, 2));
      const dinner = 58 * Math.exp(-Math.pow((t - 18.55) / 1.55, 2));
      arr.push(12 + lunch + dinner);
    }
    const idx = Math.floor(((nowH - START) / (END - START)) * N);
    const scale = 76 / arr[Math.min(idx, arr.length - 1)];
    const scaled = arr.map(function (v) { return v * scale; });
    return { values: scaled, max: Math.max.apply(null, scaled) };
  }, []);

  const peakFrom = 17.75;
  const peakTo = 19.5;
  const nowX = xOf(nowH);

  const hourLabels = [];
  for (let h = 6; h <= 24; h += 2) hourLabels.push(h);

  return (
    <div style={pageCol.ruler}>
      <div style={pageCol.strip}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 16, color: 'var(--ink)', letterSpacing: '.08em' }}>今天</span>
          <span style={{ width: 1, height: 12, background: 'var(--line-strong)' }} />
          <UiLabel>出餐速率</UiLabel>
          <span style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: 'var(--ink-2)' }}>
            <span>每 15 分钟一格</span>
            <span style={{ width: 1, height: 10, background: 'var(--line-strong)' }} />
            <span>全店出品</span>
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>3 件事等你处理</span>
          <span style={{ width: 1, height: 12, background: 'var(--line-strong)' }} />
          <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)' }}>更新 18:40</span>
        </div>
      </div>

      <div ref={hostRef} style={{ paddingTop: 4 }}>
        <svg width="100%" height={H} viewBox={'0 0 ' + W + ' ' + H} preserveAspectRatio="none" style={{ display: 'block' }}>
          {hourLabels.map(function (h) {
            return <line key={'g' + h} x1={xOf(h)} y1={TOP} x2={xOf(h)} y2={AX} stroke="var(--line)" strokeWidth="1" />;
          })}
          <rect x={xOf(peakFrom)} y={TOP - 6} width={xOf(peakTo) - xOf(peakFrom)} height={AX - TOP + 6} fill="var(--panel-2)" />
          <line x1={xOf(peakFrom)} y1={TOP - 6} x2={xOf(peakFrom)} y2={AX} stroke="var(--line-strong)" strokeWidth="1" strokeDasharray="2 3" />
          <line x1={xOf(peakTo)} y1={TOP - 6} x2={xOf(peakTo)} y2={AX} stroke="var(--line-strong)" strokeWidth="1" strokeDasharray="2 3" />
          <text x={xOf(peakFrom) + 8} y={11} fill="var(--ink)" fontSize="12" letterSpacing="1.4">峰值段</text>

          {profile.values.map(function (v, i) {
            const t = START + i * 0.25 + 0.125;
            const inPeak = t >= peakFrom && t <= peakTo;
            const h = Math.max(2, (v / profile.max) * (AX - TOP - 2));
            return (
              <rect
                key={i}
                x={i * step + 2}
                y={AX - h}
                width={Math.max(2, step - 3)}
                height={h}
                fill={inPeak ? 'var(--ink-3)' : 'var(--line-strong)'}
              />
            );
          })}

          <line x1="0" y1={AX} x2={W} y2={AX} stroke="var(--line-strong)" strokeWidth="1" />

          {profile.values.map(function (v, i) {
            const hourTick = i % 4 === 0;
            return (
              <line
                key={'t' + i}
                x1={i * step}
                y1={AX}
                x2={i * step}
                y2={AX + (hourTick ? 8 : 4)}
                stroke={hourTick ? 'var(--ink-3)' : 'var(--line-strong)'}
                strokeWidth="1"
              />
            );
          })}
          <line x1={W - 0.5} y1={AX} x2={W - 0.5} y2={AX + 8} stroke="var(--ink-3)" strokeWidth="1" />

          {hourLabels.map(function (h, i) {
            const x = xOf(h);
            const anchor = i === 0 ? 'start' : h === 24 ? 'end' : 'middle';
            return (
              <text key={h} x={x} y={AX + 22} fill="var(--ink-2)" fontSize="12" textAnchor={anchor} className="tnum">
                {String(h).padStart(2, '0')}
              </text>
            );
          })}

          <line x1={nowX} y1={TOP - 10} x2={nowX} y2={AX + 8} stroke="var(--signal)" strokeWidth="1.5" />
          <polygon points={(nowX - 4) + ',' + (TOP - 14) + ' ' + (nowX + 4) + ',' + (TOP - 14) + ' ' + nowX + ',' + (TOP - 6)} fill="var(--signal)" />
          <rect x={nowX + 12} y={0} width={5} height={5} fill="var(--signal)" />
          <text x={nowX + 23} y={8} fill="var(--ink)" fontSize="13" letterSpacing=".4" className="tnum">18:40</text>
          <text x={nowX + 70} y={8} fill="var(--ink-2)" fontSize="12" className="tnum">76 单/时</text>
        </svg>
      </div>

      <div style={{ position: 'relative', height: 30 }}>
        <div style={{ position: 'absolute', left: 0, top: 7, fontSize: 12, color: 'var(--ink-2)' }}>每格 15 分钟</div>
        <div style={{ position: 'absolute', right: 0, top: 7, fontSize: 12, color: 'var(--ink-2)' }}>营业日 06:00 切</div>
        <div style={{ position: 'absolute', left: nowX, top: 0, transform: 'translateX(-50%)', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <span style={{ width: 1, height: 6, background: 'var(--signal)' }} />
          <span style={{ display: 'flex', alignItems: 'center', gap: 7, whiteSpace: 'nowrap', paddingTop: 3 }}>
            <span style={{ width: 4, height: 4, background: 'var(--signal)' }} />
            <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>最慢档口</span>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{D.today.slowest.station}</span>
            <span className="tnum" style={{ fontSize: 13, color: 'var(--signal-text)' }}>{D.today.slowest.minutes} 分</span>
          </span>
        </div>
      </div>
    </div>
  );
}

function PageKpi() {
  const t = D.today;
  const cell = {
    padding: '15px 18px',
    borderRight: '1px solid var(--line)',
    display: 'flex',
    justifyContent: 'space-between',
    gap: 12,
    minWidth: 0
  };
  const value = { fontSize: 30, lineHeight: 1.12, color: 'var(--ink)', letterSpacing: '.01em' };
  const meta = { fontSize: 12, color: 'var(--ink-2)', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: '1px solid var(--line)', height: 126, flex: '0 0 126px' }}>
      <div style={cell}>
        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minWidth: 0 }}>
          <UiLabel>今日营业额</UiLabel>
          <div className="tnum" style={value}>{t.revenue}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 1, fontSize: 12, color: 'var(--ink-2)' }}>
            <span style={{ display: 'flex', gap: 6 }}>
              <span>较昨日</span>
              <span className="tnum" style={{ color: 'var(--ok)' }}>{t.deltaDay}</span>
            </span>
            <span style={{ display: 'flex', gap: 6 }}>
              <span>较上周六</span>
              <span className="tnum" style={{ color: 'var(--ok)' }}>{t.deltaWeek}</span>
            </span>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', justifyContent: 'flex-end', gap: 3 }}>
          <UiSpark values={D.days7.map(function (d) { return d.value; })} w={120} h={46} />
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>近 7 天</span>
        </div>
      </div>

      <div style={cell}>
        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minWidth: 0 }}>
          <UiLabel>今日订单</UiLabel>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span className="tnum" style={value}>{t.orders}</span>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>单</span>
          </div>
          <div style={meta}>
            <span>客单价</span>
            <span className="tnum" style={{ color: 'var(--ink)' }}>{t.avgTicket}</span>
            <span style={{ width: 1, height: 10, background: 'var(--line-strong)' }} />
            <span>出品</span>
            <span className="tnum" style={{ color: 'var(--ink)' }}>{t.output} 件</span>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', justifyContent: 'flex-end', gap: 3 }}>
          <UiSpark values={D.days7.map(function (d) { return d.orders; })} w={120} h={46} />
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>近 7 天</span>
        </div>
      </div>

      <div style={cell}>
        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minWidth: 0 }}>
          <UiLabel>待验收</UiLabel>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span className="tnum" style={value}>{D.hygiene.verify}</span>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>项</span>
          </div>
          <div style={meta}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, background: 'var(--signal-soft)', border: '1px solid var(--signal)', padding: '1px 6px', color: 'var(--signal-text)', fontSize: 12 }}>
              逾期 {D.counts.verifyOverdue}
            </span>
            <span>近 30 天通过率 {D.hygiene.passRate}</span>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', justifyContent: 'flex-end', gap: 3 }}>
          <UiSpark values={[12, 9, 11, 7, 10, 6, 9]} w={120} h={46} />
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>近 7 天</span>
        </div>
      </div>

      <div style={Object.assign({}, cell, { borderRight: 'none', display: 'block' })}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <UiLabel>
            <span>现在</span>
            <span className="tnum" style={{ marginLeft: 7, color: 'var(--ink)' }}>18:40</span>
          </UiLabel>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--ink-2)' }}>
            <span style={{ width: 5, height: 5, background: 'var(--ok)' }} />
            营业中
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 6 }}>
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>在店桌台</span>
          <span className="tnum" style={{ fontSize: 24, color: 'var(--ink)', lineHeight: 1 }}>{t.tablesOn}</span>
          <span className="tnum" style={{ fontSize: 14, color: 'var(--ink-2)' }}>/ {t.tablesTotal}</span>
        </div>
        <div style={{ marginTop: 7 }}>
          <UiSegMeter total={t.tablesTotal} on={t.tablesOn} height={7} color="var(--ink)" />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 9, fontSize: 12, color: 'var(--ink-2)' }}>
          <span>在店 {Math.round((t.tablesOn / t.tablesTotal) * 100)}%</span>
          <span style={{ color: 'var(--signal-text)' }}>最慢 烧烤 {t.slowest.minutes} 分</span>
        </div>
      </div>
    </div>
  );
}

function PageTodo() {
  const [state, setState] = P.useState({});
  const [open, setOpen] = P.useState(false);
  const remaining = D.todos.filter(function (t) { return !state[t.id]; }).length;

  function act(id, action) {
    setState(function (prev) {
      const next = Object.assign({}, prev);
      next[id] = action;
      return next;
    });
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--line)', minHeight: 0 }}>
      <UiSectionHead
        title="今天要处理"
        mark
        count={remaining + ' 件'}
        right={<span style={{ display: 'flex', gap: 8, fontSize: 12, color: 'var(--ink-2)' }}><span>逾期 1</span><span>待批 2</span></span>}
      />
      <div style={{ flex: '1 1 auto', minHeight: 0 }}>
        {D.todos.map(function (t) {
          const done = state[t.id];
          const overdue = t.tone === 'overdue';
          return (
            <div
              key={t.id}
              style={{
                position: 'relative',
                height: 102,
                flex: '0 0 102px',
                padding: '0 20px',
                borderBottom: '1px solid var(--line)',
                display: 'flex',
                alignItems: 'center',
                gap: 16
              }}
            >
              {overdue ? <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: 'var(--signal)' }} /> : null}
              <div style={{ minWidth: 0, flex: '1 1 auto' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 12, letterSpacing: '.12em', color: 'var(--ink-2)' }}>{t.tag}</span>
                  <span
                    style={{
                      fontSize: 12,
                      lineHeight: 1.5,
                      padding: '0 6px',
                      border: '1px solid ' + (overdue ? 'var(--signal)' : 'var(--line-strong)'),
                      background: overdue ? 'var(--signal-soft)' : 'transparent',
                      color: overdue ? 'var(--signal-text)' : 'var(--ink-2)'
                    }}
                  >
                    {t.badge}
                  </span>
                  {done ? (
                    <span style={{ fontSize: 12, color: done === '驳回' ? 'var(--danger-text)' : 'var(--ok)' }}>
                      已{done} · 已通知本人
                    </span>
                  ) : null}
                </div>
                <div style={{ fontSize: 15, color: 'var(--ink)', marginTop: 3, letterSpacing: '.01em' }}>{t.title}</div>
                <div style={{ display: 'flex', gap: 10, fontSize: 12, color: 'var(--ink-2)', marginTop: 2 }}>
                  <span>{t.meta}</span>
                  {t.sub ? <span>{t.sub}</span> : null}
                </div>
                {t.id === 'rectify' && open ? (
                  <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 4, borderLeft: '1px solid var(--line-strong)', paddingLeft: 8 }}>
                    检查项「垃圾桶 · 加盖」· 责任人 吴倩 · 复核人 陈静
                  </div>
                ) : null}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '0 0 auto' }}>
                {done ? null : t.actions.map(function (a, i) {
                  const primary = i === 0;
                  return (
                    <UiBtn
                      key={a}
                      variant={primary ? (t.id === 'leave' ? 'ink' : t.id === 'hygiene' ? 'signal' : 'default') : 'quiet'}
                      onClick={function () {
                        if (t.id === 'hygiene') {
                          window.location.search = '?p=daily&role=chef';
                          return;
                        }
                        if (t.id === 'rectify') {
                          setOpen(function (v) { return !v; });
                          return;
                        }
                        act(t.id, a);
                      }}
                    >
                      {a}
                    </UiBtn>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ borderTop: '1px solid var(--line)', flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <UiSectionHead height={36} title="现场" right={<span style={{ fontSize: 12, color: 'var(--ink-2)' }}>近 30 天 {D.hygiene.judged} 项判定</span>} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', flex: '1 1 auto', minHeight: 0 }}>
          {[
            { label: '待拍', value: D.hygiene.toShoot, unit: '项' },
            { label: '已通过', value: D.hygiene.passedToday, unit: '项' },
            { label: '漏验', value: D.hygiene.missed, unit: '项' },
            { label: '一次通过率', value: D.hygiene.passRate, unit: '' }
          ].map(function (m, i) {
            return (
              <div
                key={m.label}
                style={{
                  padding: '10px 20px',
                  borderRight: i % 2 === 0 ? '1px solid var(--line)' : 'none',
                  borderBottom: i < 2 ? '1px solid var(--line)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                  gap: 2
                }}
              >
                <span style={{ fontSize: 12, color: 'var(--ink-2)', letterSpacing: '.08em' }}>{m.label}</span>
                <span style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
                  <span className="tnum" style={{ fontSize: 22, color: 'var(--ink)' }}>{m.value}</span>
                  {m.unit ? <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{m.unit}</span> : null}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function PageShifts() {
  const maxCount = 11;
  const off = D.offDuty;
  const t0 = 9;
  const t1 = 22.5;
  const span = t1 - t0;
  const hourMarks = [9, 12, 15, 18, 21, 22.5];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--line)', minHeight: 0 }}>
      <UiSectionHead
        title="今天谁上班"
        count="29 人"
        right={<span style={{ display: 'flex', gap: 8, fontSize: 12, color: 'var(--ink-2)' }}><span>休 3</span><span>请假 1</span></span>}
      />

      {D.shifts.map(function (s) {
        return (
          <div key={s.key} style={{ padding: '11px 20px 12px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontSize: 13, color: 'var(--ink)' }}>{s.name}</span>
                <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{s.time}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
                <span className="tnum" style={{ fontSize: 22, lineHeight: 1, color: 'var(--ink)' }}>{s.count}</span>
                <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>人</span>
              </div>
            </div>
            <div style={{ marginTop: 8 }}>
              <UiSegMeter total={maxCount} on={s.count} height={9} color={'var(' + s.token + ')'} />
            </div>
          </div>
        );
      })}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 20px', borderBottom: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>休</span>
          <span className="tnum" style={{ fontSize: 16, color: 'var(--ink)' }}>{off.rest}</span>
          <span style={{ width: 1, height: 10, background: 'var(--line-strong)' }} />
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>请假</span>
          <span className="tnum" style={{ fontSize: 16, color: 'var(--signal-text)' }}>{off.leave.count}</span>
        </div>
        <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{off.leave.name} · {off.leave.type} {off.leave.range}</span>
      </div>

      <div style={{ padding: '12px 20px 14px', flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
          <UiLabel>在岗时段</UiLabel>
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>09:00 — 22:30</span>
        </div>
        <div style={{ position: 'relative', flex: '1 1 auto', minHeight: 0 }}>
          {D.shifts.map(function (s) {
            const left = ((s.start - t0) / span) * 100;
            const width = ((s.end - s.start) / span) * 100;
            return (
              <div key={s.key} style={{ display: 'flex', alignItems: 'center', height: 30 }}>
                <span style={{ width: 30, fontSize: 12, color: 'var(--ink-2)' }}>{s.name.slice(0, 1)}</span>
                <div style={{ position: 'relative', flex: 1, height: 30 }}>
                  <div style={{ position: 'absolute', left: 0, right: 0, top: 15, borderTop: '1px dashed var(--line)' }} />
                  <div
                    style={{
                      position: 'absolute',
                      left: left + '%',
                      width: width + '%',
                      top: 6,
                      height: 16,
                      background: 'var(' + s.token + ')',
                      opacity: s.key === 'late' ? 0.92 : 1
                    }}
                  />
                  <span className="tnum" style={{ position: 'absolute', left: 'calc(' + left + '% + 5px)', top: 6, fontSize: 12, lineHeight: '16px', color: 'var(--panel-3)' }}>
                    {s.count}
                  </span>
                </div>
              </div>
            );
          })}
          <div style={{ display: 'flex', height: 20 }}>
            <span style={{ width: 30 }} />
            <div style={{ position: 'relative', flex: 1 }}>
              {[9, 12, 15, 18].map(function (h) {
                const x = ((h - t0) / span) * 100;
                return (
                  <div key={h} style={{ position: 'absolute', left: 'calc(' + x + '% - 0.5px)', top: 0 }}>
                    <div style={{ width: 1, height: 5, background: 'var(--line-strong)' }} />
                    <div className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)', transform: 'translateX(-50%)', marginTop: 1 }}>
                      {String(h).padStart(2, '0')}
                    </div>
                  </div>
                );
              })}
              <span className="tnum" style={{ position: 'absolute', right: 0, top: 6, fontSize: 12, color: 'var(--ink-2)' }}>22:30</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PagePrep() {
  const p = D.prep;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <UiSectionHead
        title="备货"
        right={<span style={{ fontSize: 12, color: 'var(--ink-2)' }}>备货计划</span>}
      />
      <div style={{ padding: '14px 20px 16px', borderBottom: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span className="tnum" style={{ fontSize: 34, lineHeight: 1, color: 'var(--ink)' }}>{p.missing}</span>
          <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>项待补</span>
        </div>
        <div style={{ marginTop: 10 }}>
          <UiSegMeter total={p.missing} on={p.missing} height={7} color="var(--signal)" />
        </div>
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {p.groups.map(function (g) {
            return (
              <div key={g.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ width: 40, fontSize: 12, color: 'var(--ink-2)' }}>{g.name}</span>
                <div style={{ flex: 1 }}>
                  <UiSegMeter total={7} on={g.count} height={6} color="var(--signal)" />
                </div>
                <span className="tnum" style={{ fontSize: 13, color: 'var(--ink)', width: 14, textAlign: 'right' }}>{g.count}</span>
              </div>
            );
          })}
        </div>
        <div style={{ marginTop: 14 }}>
          <UiBtn variant="signal" style={{ width: '100%' }} onClick={function () { window.__d1toast('备货计划：热菜 3 · 蒸笼 2 · 凉菜 2'); }}>
            去备货
          </UiBtn>
        </div>
      </div>

      <div style={{ flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <UiSectionHead height={36} title="人事" count="3 条提醒" />
        <div style={{ flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {[
          { label: '健康证 30 天内到期', value: D.hr.certExpiring, unit: '人' },
          { label: '本月生日', value: D.hr.birthday, unit: '人' },
          { label: '工龄奖该调', value: D.hr.seniority, unit: '人' },
          { label: '加班待批', value: D.hr.overtime.count, unit: '条 · ' + D.hr.overtime.hours }
        ].map(function (r, i) {
          return (
            <div
              key={r.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flex: '1 1 0',
                minHeight: 34,
                padding: '0 20px',
                borderBottom: i < 3 ? '1px solid var(--line)' : 'none'
              }}
            >
              <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{r.label}</span>
              <span style={{ display: 'flex', alignItems: 'baseline', gap: 3 }}>
                <span className="tnum" style={{ fontSize: 14, color: 'var(--ink)' }}>{r.value}</span>
                <span style={{ display: 'flex', gap: 4, fontSize: 12, color: 'var(--ink-2)' }}>
                  {r.unit.split(' · ').map(function (u) { return <span key={u}>{u}</span>; })}
                </span>
              </span>
            </div>
          );
        })}
        </div>
      </div>
    </div>
  );
}

function PageToday() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <PageRateRuler />
      <PageKpi />
      <div style={{ flex: '1 1 auto', minHeight: 0, display: 'grid', gridTemplateColumns: '1.35fr 1fr .95fr' }}>
        <PageTodo />
        <PageShifts />
        <PagePrep />
      </div>
    </div>
  );
}

/* =====================================================================
   销售报表 · 手机 390（老板，只读）
   ===================================================================== */

const phonePad = { paddingLeft: 16, paddingRight: 16 };

function PageSales() {
  const s = D.sales;
  const [period, setPeriod] = P.useState('7d');
  const [explain, setExplain] = P.useState(null);

  const periodLabel = { today: '今天 · 10.25', yesterday: '昨天 · 10.24', '7d': '近 7 天 · 10.19–10.25', month: '本月 · 10.01–10.25' };

  return (
    <React.Fragment>
      <UiPhoneTitle
        title="销售报表"
        sub={D.shop.shortName + ' · 中山路店'}
        right={<span style={{ fontSize: 13, color: 'var(--ink-2)' }}>老板 · 周志远</span>}
      />
      <div className="phone-body scroll">
        <div style={Object.assign({ paddingTop: 14, paddingBottom: 24 }, phonePad)}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6 }}>
            {s.periods.map(function (p) {
              return (
                <UiChip
                  key={p.key}
                  on={period === p.key}
                  onClick={function () { setPeriod(p.key); }}
                  style={{ height: 44, padding: 0, fontSize: 14 }}
                >
                  {p.label}
                </UiChip>
              );
            })}
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--ink-2)', letterSpacing: '.06em' }}>{periodLabel[period]}</span>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>只读</span>
          </div>

          <div style={{ marginTop: 6, paddingBottom: 8, borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>销售金额</div>
                <div className="tnum" style={{ fontSize: 32, lineHeight: 1.15, color: 'var(--ink)' }}>{s.summary[0].value}</div>
              </div>
              <button
                type="button"
                onClick={function () { setExplain(explain === '销售金额' ? null : '销售金额'); }}
                aria-label="销售金额口径"
                style={{ width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--ink-2)' }}
              >
                <UiIcon name="help" size={18} />
              </button>
            </div>
            {explain === '销售金额' ? (
              <div style={{ fontSize: 13, color: 'var(--ink-2)', marginTop: 4 }}>
                按结账时间落在所选期间的有效账单合计，扣退菜，不含抹零。
              </div>
            ) : null}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', borderBottom: '1px solid var(--line)' }}>
            {s.summary.slice(1, 4).map(function (m, i) {
              return (
                <div
                  key={m.label}
                  style={{
                    padding: '12px 0',
                    borderRight: i < 2 ? '1px solid var(--line)' : 'none',
                    paddingLeft: i === 0 ? 0 : 12
                  }}
                >
                  <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>{m.label}</div>
                  <div className="tnum" style={{ fontSize: 20, color: 'var(--ink)', marginTop: 2 }}>{m.value}</div>
                </div>
              );
            })}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 40 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{s.summary[4].label}</span>
              <span className="tnum" style={{ fontSize: 15, color: 'var(--ink)' }}>{s.summary[4].value}</span>
            </div>
            <button
              type="button"
              onClick={function () { setExplain(explain === '退菜' ? null : '退菜'); }}
              aria-label="退菜口径"
              style={{ width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--ink-2)' }}
            >
              <UiIcon name="help" size={18} />
            </button>
          </div>
          {explain === '退菜' ? (
            <div style={{ fontSize: 13, color: 'var(--ink-2)', marginTop: -6, paddingBottom: 8 }}>
              作废、退单、退菜的明细行数，按行计。
            </div>
          ) : null}

          <div style={{ marginTop: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 16, color: 'var(--ink)' }}>近 7 天营业额</span>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>单位 元</span>
            </div>
            <div style={{ marginTop: 8, borderTop: '1px solid var(--line)', paddingTop: 8 }}>
              <UiLineChart
                data={s.days}
                w={358}
                h={156}
                maxLabel={'周六 ' + s.topDay.value}
                labels={s.days.map(function (d) { return d.label; })}
              />
            </div>
          </div>

          <div style={{ marginTop: 18 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
              <span style={{ fontSize: 16, color: 'var(--ink)' }}>档口占比</span>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>按销售额</span>
            </div>
            {s.stations.map(function (st) {
              return (
                <div key={st.name} style={{ display: 'flex', alignItems: 'center', gap: 10, height: 31 }}>
                  <span style={{ width: 40, fontSize: 14, color: 'var(--ink)' }}>{st.name}</span>
                  <div style={{ flex: 1, height: 8, background: 'var(--line)', position: 'relative' }}>
                    <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (st.pct / 50) * 100 + '%', background: 'var(--ink-2)' }} />
                  </div>
                  <span className="tnum" style={{ width: 54, textAlign: 'right', fontSize: 14, color: 'var(--ink)' }}>{st.pct}%</span>
                </div>
              );
            })}
          </div>

          <div style={{ marginTop: 18 }}>
            <div style={{ fontSize: 16, color: 'var(--ink)', marginBottom: 4 }}>菜品销量榜</div>
            <div style={{ fontSize: 13, color: 'var(--ink-2)', marginBottom: 8 }}>前 5 · 按份数</div>
            {s.top.map(function (d) {
              return (
                <div key={d.name} style={{ display: 'flex', alignItems: 'center', gap: 10, height: 46, borderBottom: '1px solid var(--line)' }}>
                  <span className="tnum" style={{ width: 16, fontSize: 13, color: 'var(--ink-2)' }}>{d.rank}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 15, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.name}</div>
                    <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>{d.station}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div className="tnum" style={{ fontSize: 14, color: 'var(--ink)' }}>{d.qty}</div>
                    <div className="tnum" style={{ fontSize: 13, color: 'var(--ink-2)' }}>{d.pct}</div>
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ marginTop: 16 }}>
            <UiBtn variant="quiet" size="lg" icon="download" style={{ width: '100%', letterSpacing: 0 }} onClick={function () { window.__d1toast('导出 CSV · 近 7 天 6,028 单'); }}>
              导出 CSV
            </UiBtn>
            <div style={{ fontSize: 13, color: 'var(--ink-2)', marginTop: 8, textAlign: 'center' }}>老板只读 · 不含员工个人信息</div>
          </div>
        </div>
      </div>
      <UiPhoneTabs
        items={[
          { key: 'today', label: '今天', icon: 'today' },
          { key: 'sales', label: '生意', icon: 'business' },
          { key: 'floor', label: '现场', icon: 'floor' },
          { key: 'me', label: '我的', icon: 'me' }
        ]}
        active="sales"
        onSelect={function () { window.__d1toast('方向一样张只含今天 / 销售 / 验收 / 排班四页'); }}
      />
    </React.Fragment>
  );
}

/* =====================================================================
   日常验收 · 手机 390（厨师长）
   ===================================================================== */

function PageStainlessScene(props) {
  const dirty = props.mode === 'actual';
  const spots = [
    { cx: 60, cy: 190, rx: 15, ry: 5, o: 0.85 },
    { cx: 96, cy: 176, rx: 7, ry: 2.6, o: 0.6 },
    { cx: 126, cy: 196, rx: 16, ry: 6, o: 0.9 },
    { cx: 150, cy: 214, rx: 9, ry: 3.2, o: 0.6 },
    { cx: 86, cy: 226, rx: 10, ry: 3.4, o: 0.5 },
    { cx: 40, cy: 176, rx: 5, ry: 2, o: 0.45 },
    { cx: 112, cy: 240, rx: 7, ry: 2.4, o: 0.55 }
  ];
  return (
    <svg
      viewBox="0 0 180 420"
      width="100%"
      height="100%"
      preserveAspectRatio="xMidYMid slice"
      style={{ display: 'block' }}
      aria-hidden="true"
    >
      <rect x="0" y="0" width="180" height="420" fill="var(--metal-4)" />

      {/* 排烟罩 */}
      <rect x="0" y="0" width="180" height="34" fill="var(--metal-3)" />
      {[0, 1, 2, 3].map(function (i) {
        return <rect key={i} x={10 + i * 42} y={9} width={28} height={16} fill="var(--metal-4)" />;
      })}
      <line x1="0" y1="34.5" x2="180" y2="34.5" stroke="var(--line-strong)" strokeWidth="1" />

      {/* 墙面：不锈钢挡板 */}
      <rect x="0" y="35" width="180" height="115" fill="var(--metal-3)" />
      <rect x="0" y="35" width="180" height="8" fill="var(--panel-3)" opacity="0.45" />
      <line x1="0" y1="36.5" x2="180" y2="36.5" stroke="var(--steel-light)" strokeWidth="1" opacity="0.5" />
      <line x1="0" y1="78" x2="180" y2="78" stroke="var(--line-strong)" strokeWidth="1" />
      <line x1="0" y1="78.5" x2="180" y2="78.5" stroke="var(--steel-light)" strokeWidth="1" opacity="0.35" />
      <line x1="0" y1="118" x2="180" y2="118" stroke="var(--line-strong)" strokeWidth="1" />
      <line x1="60" y1="35" x2="60" y2="150" stroke="var(--line)" strokeWidth="1" />
      <line x1="120" y1="35" x2="120" y2="150" stroke="var(--line)" strokeWidth="1" />

      {/* 挂具横杆与汤勺 */}
      <rect x="16" y="96" width="148" height="3" fill="var(--metal-2)" />
      {[48, 112].map(function (x) {
        return <rect key={x} x={x} y="99" width="3" height="7" fill="var(--metal-2)" />;
      })}
      <line x1="49.5" y1="106" x2="49.5" y2="132" stroke="var(--metal-2)" strokeWidth="2" />
      <circle cx="49.5" cy="139" r="7" fill="none" stroke="var(--metal-2)" strokeWidth="2" />
      <line x1="113.5" y1="106" x2="113.5" y2="126" stroke="var(--metal-2)" strokeWidth="2" />
      <rect x="106" y="126" width="15" height="10" fill="var(--metal-2)" />

      {/* 灶台台面 */}
      <rect x="0" y="150" width="180" height="120" fill="var(--metal)" />
      <line x1="0" y1="150.5" x2="180" y2="150.5" stroke="var(--steel-light)" strokeWidth="1" />
      <line x1="0" y1="204" x2="180" y2="204" stroke="var(--metal-3)" strokeWidth="1" opacity="0.45" />

      {/* 灶眼 */}
      <circle cx="55" cy="196" r="26" fill="var(--metal-4)" />
      <circle cx="55" cy="196" r="26" fill="none" stroke="var(--metal-2)" strokeWidth="2.5" />
      <circle cx="55" cy="196" r="12" fill="none" stroke="var(--metal-2)" strokeWidth="1.5" />
      <line x1="29" y1="196" x2="81" y2="196" stroke="var(--metal-3)" strokeWidth="2.5" />
      <line x1="55" y1="170" x2="55" y2="222" stroke="var(--metal-3)" strokeWidth="2.5" />

      <circle cx="126" cy="196" r="21" fill="var(--metal-4)" />
      <circle cx="126" cy="196" r="21" fill="none" stroke="var(--metal-2)" strokeWidth="2.5" />
      <circle cx="126" cy="196" r="9" fill="none" stroke="var(--metal-2)" strokeWidth="1.5" />

      {/* 台面下沿 */}
      <rect x="0" y="270" width="180" height="12" fill="var(--metal-2)" />
      <line x1="0" y1="270.5" x2="180" y2="270.5" stroke="var(--steel-light)" strokeWidth="1" />

      {/* 立面 */}
      <rect x="0" y="282" width="180" height="138" fill="var(--metal-3)" />
      <line x1="0" y1="296" x2="180" y2="296" stroke="var(--line-strong)" strokeWidth="1" />
      {[24, 50, 76].map(function (x) {
        return (
          <g key={x}>
            <circle cx={x} cy={318} r="10" fill="var(--metal)" stroke="var(--line-strong)" strokeWidth="1" />
            <line x1={x} y1="309" x2={x} y2="318" stroke="var(--ink-2)" strokeWidth="1.5" />
          </g>
        );
      })}
      <rect x="100" y="306" width="72" height="104" fill="none" stroke="var(--line-strong)" strokeWidth="1" />
      <rect x="106" y="312" width="60" height="92" fill="var(--metal-4)" opacity="0.45" />
      <rect x="110" y="318" width="52" height="5" fill="var(--metal-2)" />

      {dirty
        ? spots.map(function (sp, i) {
            return <ellipse key={i} cx={sp.cx} cy={sp.cy} rx={sp.rx} ry={sp.ry} fill="var(--panel-3)" opacity={sp.o} />;
          })
        : null}

      {dirty ? <circle cx="126" cy="196" r="42" fill="none" stroke="var(--signal)" strokeWidth="2.5" strokeDasharray="6 5" /> : null}
      {!dirty ? <line x1="0" y1="168" x2="180" y2="168" stroke="var(--steel-light)" strokeWidth="1" opacity="0.35" /> : null}
      <rect x="0" y="0" width="180" height="420" fill="none" stroke="var(--line)" strokeWidth="1" />
    </svg>
  );
}

function PageDaily() {
  const d = D.daily;
  const [verdict, setVerdict] = P.useState(null);
  const [sheet, setSheet] = P.useState(false);
  const [reason, setReason] = P.useState('');
  const [dayIdx, setDayIdx] = P.useState(0);
  const dayLabels = ['10.25 周日', '10.24 周六', '10.23 周五'];

  function submitReject() {
    if (!reason.trim()) return;
    setVerdict({ type: 'reject', reason: reason.trim() });
    setSheet(false);
    setReason('');
  }

  const labelBox = {
    position: 'absolute',
    top: 8,
    background: 'var(--label-bg)',
    border: '1px solid var(--line-strong)',
    padding: '2px 7px',
    fontSize: 13,
    color: 'var(--ink)',
    whiteSpace: 'nowrap'
  };

  return (
    <React.Fragment>
      <UiPhoneTitle
        title="日常验收"
        sub="厨师长 · 王国强"
        right={
          <button
            type="button"
            onClick={function () { setDayIdx((dayIdx + 1) % 3); }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              height: 32,
              padding: '0 8px',
              border: '1px solid var(--line-strong)',
              color: 'var(--ink)',
              fontSize: 13
            }}
          >
            <span className="tnum">{dayLabels[dayIdx]}</span>
            <UiIcon name="chevron" size={14} color="var(--ink-2)" />
          </button>
        }
      />
      <div className="phone-body scroll">
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '12px 16px 16px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', border: '1px solid var(--line)' }}>
            {d.summary.map(function (m, i) {
              return (
                <div
                  key={m.label}
                  style={{
                    padding: '8px 6px 9px',
                    borderRight: i < 3 ? '1px solid var(--line)' : 'none',
                    textAlign: 'left',
                    paddingLeft: 8
                  }}
                >
                  <div style={{ fontSize: 13, color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{m.label}</div>
                  <div
                    className="tnum"
                    style={{ fontSize: 20, color: m.tone === 'signal' ? 'var(--signal-text)' : 'var(--ink)', lineHeight: 1.3 }}
                  >
                    {m.value}
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginTop: 16 }}>
            <div>
              <div style={{ fontSize: 13, color: 'var(--ink-2)', letterSpacing: '.08em' }}>检查项</div>
              <div style={{ fontSize: 17, color: 'var(--ink)', marginTop: 2 }}>{d.item.check}</div>
            </div>
            <span style={{ fontSize: 13, color: 'var(--ink)', border: '1px solid var(--line-strong)', padding: '2px 8px' }}>
              工作区 {d.item.zone}
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--line)', marginTop: 12, border: '1px solid var(--line)', flex: '1 1 auto', minHeight: 180 }}>
            <div style={{ position: 'relative', background: 'var(--metal-4)', overflow: 'hidden' }}>
              <PageStainlessScene mode="standard" />
              <span style={Object.assign({}, labelBox, { left: 8 })}>标准图</span>
            </div>
            <div style={{ position: 'relative', background: 'var(--metal-4)', overflow: 'hidden' }}>
              <PageStainlessScene mode="actual" />
              <div style={Object.assign({}, labelBox, { right: 8, textAlign: 'right', lineHeight: 1.35 })}>
                <div>实拍</div>
                <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>
                  {d.item.shotBy} {d.item.shiftName} {d.item.shotAt}
                </div>
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>红色标注圈为不合格处</span>
            <span className="tnum" style={{ fontSize: 13, color: 'var(--ink-2)' }}>第 3 项 / 共 9 项</span>
          </div>

          {verdict ? (
            <div
              style={{
                marginTop: 12,
                border: '1px solid ' + (verdict.type === 'pass' ? 'var(--ok)' : 'var(--danger)'),
                background: verdict.type === 'pass' ? 'var(--ok-soft)' : 'var(--danger-soft)',
                padding: '10px 12px'
              }}
            >
              <div style={{ fontSize: 15, color: 'var(--ink)' }}>
                {verdict.type === 'pass' ? '已通过 · 10:26' : '已驳回 · 现已通知王芳重拍'}
              </div>
              {verdict.reason ? (
                <div style={{ fontSize: 13, color: 'var(--ink-2)', marginTop: 4 }}>理由：{verdict.reason}</div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <div style={{ flex: '0 0 auto', borderTop: '1px solid var(--line)', padding: '8px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>下一项 · {d.item.queueNext}（{d.item.position}）</span>
        <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>待验收 9</span>
      </div>

      <div style={{ flex: '0 0 auto', display: 'flex', gap: 10, padding: '10px 16px 14px', borderTop: '1px solid var(--line)' }}>
        <UiBtn
          variant="ok"
          size="lg"
          style={{ flex: 1 }}
          disabled={!!verdict}
          onClick={function () { setVerdict({ type: 'pass' }); }}
        >
          通过
        </UiBtn>
        <UiBtn
          variant="danger"
          size="lg"
          style={{ flex: 1 }}
          disabled={!!verdict}
          onClick={function () { setSheet(true); }}
        >
          驳回
        </UiBtn>
      </div>

      <UiPhoneTabs
        items={[
          { key: 'today', label: '今天', icon: 'today' },
          { key: 'floor', label: '现场', icon: 'floor' },
          { key: 'kitchen', label: '厨房', icon: 'kitchen' },
          { key: 'me', label: '我的', icon: 'me' }
        ]}
        active="floor"
        onSelect={function () { window.__d1toast('方向一样张只含今天 / 销售 / 验收 / 排班四页'); }}
      />

      <UiSheet open={sheet} onClose={function () { setSheet(false); }}>
        <div style={{ fontSize: 16, color: 'var(--ink)' }}>驳回这一项</div>
        <div style={{ fontSize: 13, color: 'var(--ink-2)', marginTop: 4 }}>{d.item.check} · {d.item.zone}</div>
        <label htmlFor="reject-reason" style={{ display: 'block', fontSize: 13, color: 'var(--ink)', marginTop: 14 }}>
          {d.rejectHint}
        </label>
        <textarea
          id="reject-reason"
          value={reason}
          onChange={function (e) { setReason(e.target.value); }}
          placeholder="例如：灶台右侧有油污未擦净"
          style={{
            width: '100%',
            height: 96,
            marginTop: 6,
            resize: 'none',
            background: 'var(--panel-3)',
            color: 'var(--ink)',
            border: '1px solid ' + (reason.trim() ? 'var(--line-strong)' : 'var(--danger)'),
            padding: '10px 12px',
            fontSize: 16,
            lineHeight: 1.5,
            fontFamily: 'var(--font)'
          }}
        />
        <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
          <UiBtn size="lg" variant="quiet" style={{ flex: 1 }} onClick={function () { setSheet(false); }}>
            取消
          </UiBtn>
          <UiBtn size="lg" variant="danger" style={{ flex: 1 }} disabled={!reason.trim()} onClick={submitReject}>
            提交驳回
          </UiBtn>
        </div>
      </UiSheet>
    </React.Fragment>
  );
}

/* =====================================================================
   排班月历 · 桌面 1440（店长）
   ===================================================================== */

function PageCalendar() {
  const cal = D.calendar;
  const [view, setView] = P.useState('month');
  const [days, setDays] = P.useState(function () { return cal.days.map(function (d) { return Object.assign({}, d); }); });
  const [history, setHistory] = P.useState([]);
  const [shift, setShift] = P.useState('early');
  const [zone, setZone] = P.useState(cal.zones[0]);
  const [loop, setLoop] = P.useState(false);
  const [fixedOpen, setFixedOpen] = P.useState(false);
  const [leaves, setLeaves] = P.useState(cal.pendingLeaves);
  const [weekDay, setWeekDay] = P.useState(25);

  const shiftIdx = { early: 0, mid: 1, late: 2, rest: 3 }[shift];
  const shiftName = { early: '早班', mid: '中班', late: '晚班', rest: '休' }[shift];

  const colorOf = { early: 'var(--shift-early)', mid: 'var(--shift-mid)', late: 'var(--shift-late)' };
  const zoneName = function (z) { return z.split(' · ')[0]; };

  function writeDay(index) {
    const day = days[index];
    if (!day || day.out) return;
    const prev = JSON.parse(JSON.stringify(day));
    const next = JSON.parse(JSON.stringify(day));
    next.plan = next.plan.slice();
    next.plan[shiftIdx] += 1;
    next.head += 1;
    const zn = zoneName(zone);
    if (shift === 'rest') {
      next.rest = next.plan[3];
    } else {
      const hit = next.bars.find(function (b) { return b.shift === shift && b.zone === zn; });
      if (hit) {
        hit.n += 1;
        hit.w += 1;
      } else {
        next.bars = next.bars.concat([{ shift: shift, zone: zn, n: 1, w: 1 }]);
      }
    }
    const copy = days.slice();
    copy[index] = next;
    setDays(copy);
    setHistory(history.concat([{ index: index, prev: prev }]));
  }

  function undo() {
    if (!history.length) return;
    const last = history[history.length - 1];
    const copy = days.slice();
    copy[last.index] = last.prev;
    setDays(copy);
    setHistory(history.slice(0, -1));
  }

  const headCell = { display: 'flex', alignItems: 'center', height: 26, paddingLeft: 8, fontSize: 12, color: 'var(--ink-2)', borderBottom: '1px solid var(--line)' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ height: 52, flex: '0 0 52px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ fontSize: 16, color: 'var(--ink)', letterSpacing: '.06em' }}>{cal.month}</span>
          <span style={{ width: 1, height: 12, background: 'var(--line-strong)' }} />
          <div style={{ display: 'flex', gap: 0 }}>
            <UiChip on={view === 'month'} onClick={function () { setView('month'); }} style={{ height: 28, borderRadius: '2px 0 0 2px' }}>
              月
            </UiChip>
            <UiChip on={view === 'week'} onClick={function () { setView('week'); }} style={{ height: 28, borderRadius: '0 2px 2px 0', borderLeft: 'none' }}>
              周
            </UiChip>
          </div>
          <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)' }}>
            {view === 'month' ? '5 周 · 31 天' : '10.19 — 10.25'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {[
            { t: '早', bg: 'var(--shift-early)' },
            { t: '中', bg: 'var(--shift-mid)' },
            { t: '晚', bg: 'var(--shift-late)' }
          ].map(function (l) {
            return (
              <span key={l.t} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--ink-2)' }}>
                <span style={{ width: 8, height: 3, background: l.bg }} />
                {l.t}
              </span>
            );
          })}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--ink-2)' }}>
            <span style={{ width: 12, height: 0, borderTop: '1px dashed var(--ink-3)' }} />
            休
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--ink-2)' }}>
            <span style={{ width: 8, height: 3, background: 'var(--signal-soft)', borderLeft: '2px solid var(--signal)' }} />
            假
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--ink-2)' }}>
            <span style={{ width: 8, height: 3, background: 'var(--signal)' }} />
            待批
          </span>
        </div>
      </div>

      <div style={{ flex: '1 1 auto', minHeight: 0, display: 'flex' }}>
        <div style={{ flex: '1 1 auto', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          {view === 'month' ? (
            <React.Fragment>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', height: 26, flex: '0 0 26px', borderBottom: '1px solid var(--line)' }}>
                {cal.weekLabels.map(function (w) {
                  return (
                    <div key={w} style={{ paddingLeft: 8, fontSize: 12, color: 'var(--ink-2)', letterSpacing: '.12em', lineHeight: '26px', borderRight: '1px solid var(--line)' }}>
                      {w}
                    </div>
                  );
                })}
              </div>
              <div style={{ flex: '1 1 auto', minHeight: 0, display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gridTemplateRows: 'repeat(5, 1fr)', gap: 1, background: 'var(--line)' }}>
                {days.map(function (day, i) {
                  return (
                    <button
                      key={i}
                      type="button"
                      onClick={function () { writeDay(i); }}
                      style={{
                        position: 'relative',
                        textAlign: 'left',
                        padding: '7px 8px 6px',
                        background: day.out ? 'var(--panel-3)' : day.selected ? 'var(--panel)' : 'var(--bg)',
                        outline: day.selected ? '1px solid var(--ink-3)' : 'none',
                        outlineOffset: -1,
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'stretch',
                        minHeight: 0,
                        overflow: 'hidden'
                      }}
                      className="rowable"
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 18 }}>
                        <span className="tnum" style={{ fontSize: 13, color: day.out ? 'var(--ink-2)' : day.selected ? 'var(--ink)' : 'var(--ink-2)' }}>
                          {day.d}
                        </span>
                        {day.pending ? (
                          <span style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 12, color: 'var(--signal-text)' }}>
                            <span style={{ width: 5, height: 5, background: 'var(--signal)' }} />
                            待批
                          </span>
                        ) : null}
                        {day.selected ? <span style={{ fontSize: 12, color: 'var(--ink)' }}>今天</span> : null}
                      </div>

                      {!day.out ? (
                        <div style={{ flex: '1 1 auto', minHeight: 0, marginTop: 4 }}>
                          {day.bars.map(function (b, bi) {
                            return (
                              <div
                                key={bi}
                                style={{
                                  height: 3,
                                  marginBottom: 4,
                                  width: Math.min(100, (b.w / 6) * 100) + '%',
                                  background: colorOf[b.shift]
                                }}
                              />
                            );
                          })}
                          {day.rest > 0 ? (
                            <div style={{ height: 0, marginBottom: 3, width: Math.min(100, (day.rest / 6) * 100) + '%', borderTop: '1px dashed var(--ink-3)' }} />
                          ) : null}
                          {day.leave > 0 ? (
                            <div style={{ height: 3, marginBottom: 3, width: '26%', background: 'var(--signal-soft)', borderLeft: '2px solid var(--signal)' }} />
                          ) : null}
                        </div>
                      ) : null}

                      {!day.out ? (
                        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 4 }}>
                          <span className="tnum" style={{ fontSize: 13, color: 'var(--ink-2)' }}>{day.head}</span>
                          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>人</span>
                        </div>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </React.Fragment>
          ) : (
            <div style={{ flex: '1 1 auto', minHeight: 0, padding: '10px 0 0' }}>
              <div style={{ display: 'flex', alignItems: 'center', height: 22, fontSize: 12, color: 'var(--ink-2)' }}>
                <span style={{ width: 96 }} />
                <div style={{ position: 'relative', flex: 1, height: 22 }}>
                  {[9, 12, 15, 18, 21].map(function (h) {
                    return (
                      <span
                        key={h}
                        className="tnum"
                        style={{ position: 'absolute', left: ((h - 9) / 13.5) * 100 + '%', top: 4, paddingLeft: 4, borderLeft: '1px solid var(--line)' }}
                      >
                        {String(h).padStart(2, '0')}:00
                      </span>
                    );
                  })}
                  <span className="tnum" style={{ position: 'absolute', right: 0, top: 4 }}>22:30</span>
                </div>
              </div>
              {days.filter(function (d) { return !d.out && d.d >= 19 && d.d <= 25; }).map(function (day) {
                const left0 = 9;
                const spanH = 13.5;
                const kinds = [
                  { key: 'early', name: '早', start: 9, end: 15, color: 'var(--shift-early)' },
                  { key: 'mid', name: '中', start: 12, end: 20, color: 'var(--shift-mid)' },
                  { key: 'late', name: '晚', start: 16, end: 22.5, color: 'var(--shift-late)' }
                ];
                const idx = { early: 0, mid: 1, late: 2 };
                return (
                  <div
                    key={day.d}
                    style={{ display: 'flex', alignItems: 'center', height: 92, borderBottom: '1px solid var(--line)', background: day.d === weekDay ? 'var(--panel)' : 'transparent' }}
                  >
                    <button
                      type="button"
                      onClick={function () { setWeekDay(day.d); }}
                      style={{ width: 96, paddingLeft: 8, textAlign: 'left', display: 'flex', alignItems: 'baseline', gap: 6 }}
                    >
                      <span className="tnum" style={{ fontSize: 15, color: 'var(--ink)' }}>{day.d}</span>
                      <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{cal.weekLabels[day.col]}</span>
                    </button>
                    <div style={{ flex: 1, position: 'relative', height: 60 }}>
                      {[9, 12, 15, 18, 21].map(function (h) {
                        return (
                          <span
                            key={h}
                            style={{ position: 'absolute', left: ((h - left0) / spanH) * 100 + '%', top: 0, bottom: 0, width: 1, background: 'var(--line)' }}
                          />
                        );
                      })}
                      {kinds.map(function (k, ki) {
                        return (
                          <div
                            key={k.key}
                            style={{
                              position: 'absolute',
                              left: ((k.start - left0) / spanH) * 100 + '%',
                              width: ((k.end - k.start) / spanH) * 100 + '%',
                              top: 4 + ki * 19,
                              height: 14,
                              background: k.color
                            }}
                          >
                            <span
                              className="tnum"
                              style={{
                                position: 'absolute',
                                left: 4,
                                top: -1,
                                fontSize: 12,
                                color: k.key === 'late' ? 'var(--panel-3)' : 'var(--panel-3)'
                              }}
                            >
                              {day.plan[idx[k.key]]}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ width: 320, flex: '0 0 320px', borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ height: 44, flex: '0 0 44px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 16px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span className="tnum" style={{ fontSize: 15, color: 'var(--ink)' }}>10.25</span>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>周六</span>
            </div>
            <span style={{ display: 'flex', gap: 6, fontSize: 12, color: 'var(--ink-2)' }}><span>早 8</span><span>中 6</span><span>晚 11</span></span>
          </div>

          <div style={{ flex: '1 1 auto', minHeight: 0, overflowY: 'auto' }} className="scroll">
            <div style={{ display: 'flex', padding: '6px 16px', fontSize: 12, color: 'var(--ink-2)', borderBottom: '1px solid var(--line)' }}>
              <span style={{ width: 72 }}>姓名</span>
              <span style={{ width: 76 }}>班次</span>
              <span style={{ flex: 1 }}>工作区</span>
            </div>
            {cal.roster.map(function (r) {
              const isLeave = r.shift === '请假';
              return (
                <div key={r.name} style={{ display: 'flex', alignItems: 'center', height: 36, padding: '0 16px', borderBottom: '1px solid var(--line)' }}>
                  <span style={{ width: 72, fontSize: 13, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 3, height: 12, background: isLeave ? 'var(--signal)' : 'var(' + r.token + ')' }} />
                    {r.name}
                  </span>
                  <span className="tnum" style={{ width: 88, fontSize: 12, color: 'var(--ink-2)', paddingRight: 4 }}>{r.time}</span>
                  <span style={{ flex: 1, fontSize: 12, color: 'var(--ink-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.zone}</span>
                </div>
              );
            })}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 16px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontSize: 12, color: 'var(--ink-2)', letterSpacing: '.12em' }}>待批请假</span>
              <span className="tnum" style={{ fontSize: 12, color: 'var(--signal-text)' }}>{leaves.length}</span>
            </div>
            {leaves.map(function (lv) {
              return (
                <div key={lv.id} style={{ padding: '8px 16px', borderBottom: '1px solid var(--line)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: 13, color: 'var(--ink)' }}>
                      {lv.name} · {lv.type}
                    </span>
                    <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{lv.range}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 5 }}>
                    <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{lv.zone}</span>
                    <span style={{ display: 'flex', gap: 6 }}>
                      <UiBtn
                        size="sm"
                        onClick={function () { setLeaves(leaves.filter(function (x) { return x.id !== lv.id; })); }}
                      >
                        批准
                      </UiBtn>
                      <UiBtn
                        size="sm"
                        variant="quiet"
                        onClick={function () { setLeaves(leaves.filter(function (x) { return x.id !== lv.id; })); }}
                      >
                        驳回
                      </UiBtn>
                    </span>
                  </div>
                </div>
              );
            })}

            <div style={{ padding: '10px 16px' }}>
              <button
                type="button"
                onClick={function () { setFixedOpen(!fixedOpen); }}
                style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 32, border: '1px solid var(--line-strong)', padding: '0 10px', color: 'var(--ink)' }}
              >
                <span style={{ fontSize: 13 }}>固定班规则 · {cal.fixedShift.count} 条</span>
                <span style={{ transform: fixedOpen ? 'rotate(90deg)' : 'none', display: 'flex' }}>
                  <UiIcon name="chevron" size={14} color="var(--ink-2)" />
                </span>
              </button>
              {fixedOpen ? (
                <div style={{ marginTop: 6, border: '1px solid var(--line)' }}>
                  {cal.fixedShift.rules.map(function (r) {
                    return (
                      <div key={r.name} style={{ padding: '7px 10px', borderBottom: '1px solid var(--line)' }}>
                        <div style={{ fontSize: 12, color: 'var(--ink)' }}>{r.name}</div>
                        <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{r.detail}</div>
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <div style={{ height: 62, flex: '0 0 62px', borderTop: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 14, padding: '0 4px' }}>
        <span style={{ fontSize: 12, color: 'var(--ink-2)', letterSpacing: '.12em', paddingLeft: 4 }}>排班笔</span>
        <div style={{ display: 'flex', gap: 0 }}>
          {[
            { k: 'early', t: '早' },
            { k: 'mid', t: '中' },
            { k: 'late', t: '晚' },
            { k: 'rest', t: '休' }
          ].map(function (o, i) {
            const on = shift === o.k;
            return (
              <button
                key={o.k}
                type="button"
                onClick={function () { setShift(o.k); }}
                style={{
                  width: 46,
                  height: 32,
                  border: '1px solid ' + (on ? 'var(--ink-3)' : 'var(--line-strong)'),
                  borderLeft: i === 0 ? '1px solid var(--line-strong)' : 'none',
                  background: on ? 'var(--panel-2)' : 'transparent',
                  color: on ? 'var(--ink)' : 'var(--ink-2)',
                  fontSize: 13
                }}
              >
                {o.t}
              </button>
            );
          })}
        </div>

        <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
          <select
            value={zone}
            onChange={function (e) { setZone(e.target.value); }}
            style={{
              height: 32,
              minWidth: 160,
              background: 'var(--panel-2)',
              color: 'var(--ink)',
              border: '1px solid var(--line-strong)',
              fontSize: 13,
              padding: '0 26px 0 10px',
              borderRadius: 0,
              appearance: 'none',
              fontFamily: 'var(--font)'
            }}
          >
            {cal.zones.map(function (z) {
              return <option key={z} value={z}>{z}</option>;
            })}
          </select>
          <span style={{ position: 'absolute', right: 8, pointerEvents: 'none', display: 'flex' }}>
            <UiIcon name="chevron" size={14} color="var(--ink-2)" />
          </span>
        </div>

        <button
          type="button"
          onClick={function () { setLoop(!loop); }}
          style={{ display: 'flex', alignItems: 'center', gap: 6, height: 32, padding: '0 8px', color: loop ? 'var(--ink)' : 'var(--ink-2)', fontSize: 12 }}
        >
          <span style={{ width: 12, height: 12, border: '1px solid ' + (loop ? 'var(--ink)' : 'var(--line-strong)'), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {loop ? <span style={{ width: 6, height: 6, background: 'var(--ink)' }} /> : null}
          </span>
          循环
        </button>

        <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>
          {view === 'month' ? '点日历格写入：' : '周视图只读汇总：'}
          <span style={{ color: 'var(--ink)' }}>{shiftName} · {zone}</span>
        </span>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{history.length ? history.length + ' 笔待保存' : '无待保存改动'}</span>
          <UiBtn variant="quiet" icon="undo" disabled={!history.length} onClick={undo}>
            撤销最近一批
          </UiBtn>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, {
  PageToday: PageToday,
  PageSales: PageSales,
  PageDaily: PageDaily,
  PageCalendar: PageCalendar
});
