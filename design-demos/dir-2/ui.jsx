/* dir-2 · 作业流 —— 共用件：壳 / 图标 / 卡片 / pill / 按钮 / SVG 图
   每个 text/babel 文件跑在全局作用域里，所以整份套一个 IIFE，末尾挂 window。 */

(function () {

const { useState: uiUseState } = React;

const UI_ROLES = {
  manager: { name: '陈静', title: '店长', full: '店长 陈静' },
  boss: { name: '周志远', title: '老板', full: '老板 周志远' },
  chef: { name: '王国强', title: '厨师长', full: '厨师长 王国强' },
  staff: { name: '李小雨', title: '员工', full: '员工 李小雨' }
};

const UI_NAV = [
  { key: 'today', label: '今天', icon: 'sun', roles: ['manager', 'boss', 'chef', 'staff'] },
  { key: 'business', label: '生意', icon: 'trend', roles: ['manager', 'boss', 'chef'] },
  { key: 'people', label: '人', icon: 'users', roles: ['manager'], badge: '2' },
  { key: 'floor', label: '现场', icon: 'shield', roles: ['manager', 'boss', 'chef'], badge: '9' },
  { key: 'kitchen', label: '厨房', icon: 'pan', roles: ['manager', 'chef'] },
  { key: 'system', label: '系统', icon: 'sliders', roles: ['manager'] }
];

const UI_ICONS = {
  sun: { c: [[12, 12, 4]], d: ['M12 2.6v2.4', 'M12 19v2.4', 'M2.6 12h2.4', 'M19 12h2.4', 'M5.3 5.3l1.7 1.7', 'M17 17l1.7 1.7', 'M18.7 5.3L17 7', 'M7 17l-1.7 1.7'] },
  trend: { d: ['M4 16.4l5-5 3.2 3.2L20 7', 'M15 7h5v5'] },
  users: { c: [[9, 8, 3.2], [17.2, 9.4, 2.4]], d: ['M3 20.2c0-3.3 2.7-5.8 6-5.8s6 2.5 6 5.8', 'M16.6 14.6c2.4.3 4.2 2.2 4.2 4.6'] },
  shield: { d: ['M12 3.2l7 2.6v5.5c0 4.3-3 8.1-7 9.5-4-1.4-7-5.2-7-9.5V5.8l7-2.6z', 'M9 12.1l2.2 2.2 4.3-4.4'] },
  pan: { d: ['M4.4 12h15.2', 'M5.8 12c0 3.7 2.8 6.5 6.2 6.5s6.2-2.8 6.2-6.5', 'M2.2 12h2.2', 'M19.6 12h2.2'] },
  sliders: { c: [[15.4, 7, 2], [10.6, 17, 2]], d: ['M4 7h9.4', 'M17.4 7h2.6', 'M4 17h4.6', 'M12.6 17h7.4'] },
  check: { d: ['M4.6 12.6l4.8 4.8L19.4 6.6'] },
  x: { d: ['M6 6l12 12', 'M18 6L6 18'] },
  clock: { c: [[12, 12, 8.6]], d: ['M12 7.4V12l3.2 2'] },
  chevron: { d: ['M9.6 6l6 6-6 6'] },
  down: { d: ['M6 9.6l6 6 6-6'] },
  plus: { d: ['M12 5.2v13.6', 'M5.2 12h13.6'] },
  undo: { d: ['M4.4 9.2h9.4a5.4 5.4 0 010 10.8H8.6', 'M8.2 5L4 9.2l4.2 4.2'] },
  download: { d: ['M12 3.6v11', 'M7.4 10.6L12 15.2l4.6-4.6', 'M4.2 20h15.6'] },
  camera: { c: [[12, 13.4, 3]], d: ['M4.4 8.4h3.2L9.1 5.8h5.8l1.5 2.6h3.2a1.6 1.6 0 011.6 1.6v8.4a1.6 1.6 0 01-1.6 1.6H4.4a1.6 1.6 0 01-1.6-1.6V10a1.6 1.6 0 011.6-1.6z'] },
  box: { d: ['M12 3.2l8 4.2v9.2l-8 4.2-8-4.2V7.4l8-4.2z', 'M4.2 7.6l7.8 4.2 7.8-4.2', 'M12 11.8v9'] },
  alert: { c: [[12, 17.2, 0.9]], d: ['M12 4.4l8.6 15.2H3.4L12 4.4z', 'M12 10v4'] },
  user: { c: [[12, 8, 3.6]], d: ['M4.6 20.4c0-4.1 3.3-7 7.4-7s7.4 2.9 7.4 7'] },
  calendar: { d: ['M4.6 6.6h14.8a1.2 1.2 0 011.2 1.2v11.6a1.2 1.2 0 01-1.2 1.2H4.6a1.2 1.2 0 01-1.2-1.2V7.8a1.2 1.2 0 011.2-1.2z', 'M8 4v4', 'M16 4v4', 'M3.6 11.2h16.8'] },
  grid: { d: ['M4 4h6.4v6.4H4z', 'M13.6 4H20v6.4h-6.4z', 'M4 13.6h6.4V20H4z', 'M13.6 13.6H20V20h-6.4z'] },
  edit: { d: ['M4.4 19.6h4l10-10-4-4-10 10v4z', 'M13.6 4.4l4 4'] },
  dots: { c: [[6, 12, 1.3], [12, 12, 1.3], [18, 12, 1.3]] }
};

function Icon(props) {
  const def = UI_ICONS[props.name] || { d: [], c: [] };
  const size = props.size || 18;
  const color = props.color || 'currentColor';
  const solid = props.solid;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke={color} strokeWidth={props.sw || 1.7} strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true" style={{ display: 'block', flex: '0 0 auto' }}>
      {(def.d || []).map((p, i) => <path key={'p' + i} d={p} />)}
      {(def.c || []).map((c, i) => (
        <circle key={'c' + i} cx={c[0]} cy={c[1]} r={c[2]}
                fill={solid ? color : 'none'} stroke={solid ? 'none' : color} />
      ))}
    </svg>
  );
}

/* 「A · B」里的那个点做成分隔件：视觉一样，读屏不念「点」，也避免整段文本里堆点 */
function Sep() {
  return <span aria-hidden="true" style={{ display: 'inline-block', width: '0.74em', textAlign: 'center', color: 'var(--ink-3)', fontWeight: 500 }}>·</span>;
}

function Mark() {
  return (
    <span style={{ width: 24, height: 24, borderRadius: 7, background: 'var(--brand)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}>
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="var(--brand-ink)" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true">
        <path d="M2.5 9.5h9" /><path d="M4.6 9.5V5.6" /><path d="M7 9.5V3.4" /><path d="M9.4 9.5V6.8" />
      </svg>
    </span>
  );
}

const UI_PILL = {
  brand: { bg: 'var(--brand-soft)', fg: 'var(--brand)' },
  amber: { bg: 'var(--amber-soft)', fg: 'var(--amber)' },
  danger: { bg: 'var(--danger-soft)', fg: 'var(--danger-ink)' },
  neutral: { bg: 'var(--card-2)', fg: 'var(--ink-2)' }
};

function Pill(props) {
  const tone = UI_PILL[props.tone] || UI_PILL.brand;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5, height: 24, padding: '0 9px',
      borderRadius: 999, background: tone.bg, color: tone.fg,
      fontSize: 13, fontWeight: 600, lineHeight: 1, letterSpacing: '-0.01em', whiteSpace: 'nowrap'
    }}>{props.children}</span>
  );
}

function Btn(props) {
  const kind = props.kind || 'solid';
  const h = props.h || 52;
  const base = {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7,
    height: h, borderRadius: 'var(--r-btn)', fontSize: props.fs || 16, fontWeight: 600,
    letterSpacing: '-0.01em', cursor: 'pointer', whiteSpace: 'nowrap',
    width: props.w || (props.grow ? '100%' : 'auto'),
    flex: props.grow ? '1 1 auto' : '0 0 auto',
    padding: props.w || props.grow ? '0 16px' : '0 18px'
  };
  let skin;
  if (props.disabled) {
    skin = { background: 'var(--card-2)', color: 'var(--ink-2)', border: '1px solid var(--line)', cursor: 'default' };
  } else if (kind === 'solid') {
    skin = { background: 'var(--brand)', color: 'var(--brand-ink)', border: '1px solid var(--brand)' };
  } else if (kind === 'danger') {
    skin = { background: 'var(--bg)', color: 'var(--danger-ink)', border: '1px solid var(--danger)' };
  } else if (kind === 'outline') {
    skin = { background: 'transparent', color: 'var(--ink)', border: '1px solid var(--line-strong)' };
  } else {
    skin = { background: 'transparent', color: 'var(--ink-2)', border: '1px solid transparent' };
  }
  return (
    <button className={'press btn-' + (props.disabled ? 'off' : 'on')} onClick={props.disabled ? undefined : props.onClick}
            disabled={props.disabled ? true : undefined} style={Object.assign({}, base, skin, props.style)}>
      {props.icon ? <Icon name={props.icon} size={17} /> : null}
      {props.children}
    </button>
  );
}

function Card(props) {
  return (
    <div style={Object.assign({
      background: 'var(--card)', border: '1px solid var(--line)',
      borderRadius: 'var(--r-card)', padding: props.pad || 16
    }, props.style)}>{props.children}</div>
  );
}

function Underline(props) {
  const w = props.w || 24;
  return <span style={{ display: 'block', width: w, height: 3, borderRadius: 1.5, background: 'var(--amber)', marginTop: props.mt === undefined ? 8 : props.mt }} />;
}

function PageHead(props) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16 }}>
        <h1 style={{ margin: 0, fontSize: 34, lineHeight: '40px', fontWeight: 700, letterSpacing: '-0.03em' }}>{props.title}</h1>
        {props.right}
      </div>
      <Underline />
      {props.sub ? <div style={{ marginTop: 12, fontSize: 14, color: 'var(--ink-2)', letterSpacing: '-0.01em' }}>{props.sub}</div> : null}
    </div>
  );
}

function LiveDot(props) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, color: 'var(--ink-2)' }}>
      <span style={{ width: 7, height: 7, borderRadius: 4, background: 'var(--brand)', flex: '0 0 auto' }} />
      {props.children || '实时连接'}
    </span>
  );
}

/* ---------- 桌面壳：224 导航 | 中列 | 360 详情 ---------- */
function DeskShell(props) {
  const role = UI_ROLES[props.role] || UI_ROLES.manager;
  const navs = UI_NAV.filter(n => n.roles.indexOf(props.role || 'manager') >= 0);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '224px minmax(0, 1fr) 360px', height: '100vh', overflow: 'hidden' }}>
      <aside style={{ display: 'flex', flexDirection: 'column', minHeight: 0, padding: '22px 12px 16px' }}>
        <div style={{ padding: '0 8px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <Mark />
            <span style={{ fontSize: 17, fontWeight: 700, letterSpacing: '-0.02em' }}>厨务管家</span>
          </div>
          <div style={{ marginTop: 7, fontSize: 13, color: 'var(--ink-2)' }}>
            云味小馆<Sep />中山路店
          </div>
        </div>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {navs.map(n => {
            const on = n.key === props.active;
            return (
              <div key={n.key} className={'nav-i' + (on ? ' on' : '')} style={{
                position: 'relative', display: 'flex', alignItems: 'center', gap: 10,
                height: 40, padding: '0 10px 0 14px', borderRadius: 8, cursor: 'pointer',
                color: on ? 'var(--ink)' : 'var(--ink-2)', fontSize: 14, fontWeight: on ? 600 : 500
              }}>
                {on ? <span style={{ position: 'absolute', left: 0, top: 9, bottom: 9, width: 4, borderRadius: 2, background: 'var(--brand)' }} /> : null}
                <Icon name={n.icon} size={18} color={on ? 'var(--brand)' : 'var(--ink-3)'} />
                <span>{n.label}</span>
                {n.badge ? <span className="num" style={{ marginLeft: 'auto', fontSize: 13, color: on ? 'var(--ink-2)' : 'var(--ink-3)' }}>{n.badge}</span> : null}
              </div>
            );
          })}
        </nav>
        <div style={{ marginTop: 'auto', borderTop: '1px solid var(--line)', paddingTop: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ width: 28, height: 28, borderRadius: 14, background: 'var(--brand-soft)', color: 'var(--brand)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 600 }}>{role.name.charAt(0)}</span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-0.01em' }}>{role.full}</div>
            <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>{props.role === 'boss' ? '只读权限' : props.role === 'chef' ? '厨房与现场' : '全部权限'}</div>
          </div>
        </div>
      </aside>

      <main className="scrl" style={{ minWidth: 0, overflowY: 'auto', overflowX: 'hidden' }}>{props.children}</main>

      <aside className="scrl" style={{ borderLeft: '1px solid var(--line)', minWidth: 0, overflowY: 'auto', overflowX: 'hidden' }}>
        {props.rail}
      </aside>
    </div>
  );
}

/* ---------- 手机壳：390×844 ---------- */
function TabBar(props) {
  const tabs = [
    { k: 'today', label: '今天', icon: 'sun' },
    { k: 'floor', label: '现场', icon: 'shield' },
    { k: 'kitchen', label: '厨房', icon: 'pan' },
    { k: 'me', label: '我的', icon: 'user' }
  ];
  return (
    <nav style={{ height: 64, flex: '0 0 64px', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderTop: '1px solid var(--line)' }}>
      {tabs.map(t => {
        const on = t.k === props.active;
        return (
          <button key={t.k} onClick={props.onTab ? () => props.onTab(t.k) : undefined}
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, cursor: props.onTab ? 'pointer' : 'default' }}>
            <span style={{ height: 6, display: 'flex', alignItems: 'center' }}>
              {on ? <span style={{ width: 6, height: 6, borderRadius: 3, background: 'var(--brand)' }} /> : null}
            </span>
            <Icon name={t.icon} size={19} solid={on} color={on ? 'var(--brand)' : 'var(--ink-3)'} />
            <span style={{ fontSize: 13, fontWeight: on ? 600 : 500, color: on ? 'var(--ink)' : 'var(--ink-2)' }}>{t.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

function PhoneShell(props) {
  const role = UI_ROLES[props.role] || UI_ROLES.manager;
  return (
    <div className="phone-stage" style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
      <div className="phone" style={{ width: 390, height: 844, borderRadius: 24, border: '1px solid var(--line-strong)', background: 'var(--bg)', overflow: 'hidden', display: 'flex', flexDirection: 'column', position: 'relative' }}>
        <header style={{ height: 56, flex: '0 0 56px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 16px', borderBottom: '1px solid var(--line)' }}>
          <div>
            <div style={{ fontSize: 20, lineHeight: '22px', fontWeight: 700, letterSpacing: '-0.03em' }}>{props.title}</div>
            <Underline mt={4} />
          </div>
          <div style={{ fontSize: 13, color: 'var(--ink-2)', letterSpacing: '-0.01em' }}>{role.full}</div>
        </header>
        <div className="scrl" style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', padding: 16 }}>
          {props.children}
        </div>
        <TabBar active={props.tab} onTab={props.onTab} />
      </div>
    </div>
  );
}

/* ---------- SVG 折线 ---------- */
function LineChart(props) {
  const days = props.days;
  const W = 326, H = 166;
  const padL = 46, padR = 16, padT = 24, padB = 30;
  const y0 = 20000, y1 = 32000;
  const iw = W - padL - padR;
  const ih = H - padT - padB;
  const px = i => padL + (iw * i) / (days.length - 1);
  const py = v => padT + ih * (1 - (v - y0) / (y1 - y0));
  const topIdx = days.reduce((best, d, i, arr) => (d.v > arr[best].v ? i : best), 0);
  const pts = days.map((d, i) => [px(i), py(d.v)]);
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = line + ' L' + pts[pts.length - 1][0].toFixed(1) + ' ' + (padT + ih) + ' L' + pts[0][0].toFixed(1) + ' ' + (padT + ih) + ' Z';
  const grid = [20000, 25000, 30000];
  const label = v => (v / 10000).toFixed(v % 10000 === 0 ? 0 : 1) + ' 万';
  return (
    <svg width={W} height={H} viewBox={'0 0 ' + W + ' ' + H} style={{ display: 'block', maxWidth: '100%' }} role="img" aria-label="近 7 天营业额折线">
      {grid.map(g => (
        <g key={g}>
          <line x1={padL} x2={W - padR} y1={py(g)} y2={py(g)} stroke="var(--line)" strokeWidth="1" />
          <text x={padL - 10} y={py(g) + 4} textAnchor="end" fontSize="12" fill="var(--ink-2)">{label(g)}</text>
        </g>
      ))}
      <path d={area} style={{ fill: 'var(--brand-soft)' }} />
      <path d={line} fill="none" stroke="var(--brand)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {pts.map((p, i) => (
        <circle key={i} cx={p[0]} cy={p[1]} r={i === topIdx ? 4 : 2.4}
                fill={i === topIdx ? 'var(--brand)' : 'var(--card)'}
                stroke="var(--brand)" strokeWidth={i === topIdx ? 2 : 1.6} />
      ))}
      <text x={pts[topIdx][0]} y={pts[topIdx][1] - 11} textAnchor={topIdx > 3 ? 'end' : 'start'}
            fontSize="12" fontWeight="600" fill="var(--brand)">¥31,240</text>
      {days.map((d, i) => (
        <text key={d.w} x={px(i)} y={H - 8} textAnchor="middle" fontSize="12" fill="var(--ink-2)">{d.w}</text>
      ))}
    </svg>
  );
}

/* ---------- 验收对照图：内联 SVG 抽象不锈钢台面 ---------- */
function CounterShot(props) {
  const act = props.variant === 'act';
  return (
    <svg viewBox="0 0 160 240" preserveAspectRatio="xMidYMid slice" width="100%" height="100%" role="img"
         aria-label={act ? '实拍：灶台台面油污残留' : '标准图：清洁后的灶台台面'}>
      {/* 台面铺满整帧：像手机贴近拍的一张 */}
      <rect x="0" y="0" width="160" height="240" style={{ fill: 'var(--ink-3)' }} opacity={act ? '.34' : '.5'} />
      {/* 靠墙的挡水沿 */}
      <rect x="0" y="0" width="160" height="26" style={{ fill: 'var(--line)' }} />
      <rect x="0" y="26" width="160" height="3" style={{ fill: 'var(--ink-2)' }} opacity={act ? '.2' : '.4'} />
      {/* 拉丝 */}
      {[34, 42, 50, 58, 66, 74, 82, 90, 98, 106, 114, 122, 130, 138, 146, 154, 162, 170, 178, 186, 194, 202].map(y => (
        <rect key={y} x="0" y={y} width="160" height="1" style={{ fill: 'var(--ink-2)' }} opacity={act ? '.1' : '.16'} />
      ))}
      {/* 灶眼与四臂炉架：臂从圈内沿伸到圈外，中间留黑，读起来是灶不是准星 */}
      <circle cx="80" cy="146" r="44" fill="none" style={{ stroke: 'var(--ink-2)' }} strokeWidth="2" opacity={act ? '.24' : '.36'} />
      <circle cx="80" cy="146" r="31" fill="none" style={{ stroke: 'var(--ink-2)' }} strokeWidth="1.2" opacity={act ? '.2' : '.28'} />
      <circle cx="80" cy="146" r="13" style={{ fill: 'var(--bg)' }} opacity=".55" />
      <rect x="75" y="102" width="10" height="30" rx="4" style={{ fill: 'var(--ink-2)' }} opacity={act ? '.24' : '.34'} />
      <rect x="75" y="160" width="10" height="30" rx="4" style={{ fill: 'var(--ink-2)' }} opacity={act ? '.24' : '.34'} />
      <rect x="36" y="141" width="30" height="10" rx="4" style={{ fill: 'var(--ink-2)' }} opacity={act ? '.24' : '.34'} />
      <rect x="94" y="141" width="30" height="10" rx="4" style={{ fill: 'var(--ink-2)' }} opacity={act ? '.24' : '.34'} />
      {/* 前沿与地面阴影 */}
      <rect x="0" y="226" width="160" height="5" style={{ fill: 'var(--ink-2)' }} opacity={act ? '.18' : '.34'} />
      <rect x="0" y="231" width="160" height="9" style={{ fill: 'var(--bg)' }} opacity=".8" />
      {act ? (
        <g>
          {/* 油污与残渣 */}
          <ellipse cx="107" cy="96" rx="14" ry="6" transform="rotate(-14 107 96)" style={{ fill: 'var(--amber)' }} opacity=".62" />
          <ellipse cx="121" cy="106" rx="7" ry="3.2" transform="rotate(-6 121 106)" style={{ fill: 'var(--amber)' }} opacity=".5" />
          <ellipse cx="96" cy="88" rx="6" ry="2.8" transform="rotate(-20 96 88)" style={{ fill: 'var(--amber)' }} opacity=".48" />
          <circle cx="130" cy="88" r="2.3" style={{ fill: 'var(--amber)' }} opacity=".62" />
          <ellipse cx="62" cy="182" rx="7" ry="2.6" style={{ fill: 'var(--amber)' }} opacity=".4" />
          <ellipse cx="118" cy="196" rx="4" ry="1.8" style={{ fill: 'var(--amber)' }} opacity=".38" />
          <circle cx="44" cy="112" r="1.8" style={{ fill: 'var(--amber)' }} opacity=".5" />
          {/* 红圈标注：圈住最大那片油污 */}
          <circle cx="108" cy="98" r="21" fill="none" style={{ stroke: 'var(--danger)' }} strokeWidth="1.8" />
          <path d="M122 82l12-10" style={{ stroke: 'var(--danger)' }} strokeWidth="1.4" />
        </g>
      ) : (
        <g>
          {/* 标准：干净、擦过的台面 */}
          <rect x="18" y="60" width="46" height="1.5" transform="rotate(-3 18 60)" style={{ fill: 'var(--ink)' }} opacity=".22" />
          <rect x="18" y="68" width="28" height="1.5" transform="rotate(-3 18 68)" style={{ fill: 'var(--ink)' }} opacity=".16" />
          <rect x="20" y="150" width="34" height="1" style={{ fill: 'var(--ink)' }} opacity=".18" />
        </g>
      )}
    </svg>
  );
}

/* ---------- 驳回理由层（手机内） ---------- */
function ReasonSheet(props) {
  const [text, setText] = uiUseState('');
  if (!props.open) return null;
  const ok = text.trim().length > 0;
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, zIndex: 30 }}>
      <div onClick={props.onClose} style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, background: 'var(--bg)', opacity: .66 }} />
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, background: 'var(--card)', borderTop: '1px solid var(--line-strong)', borderRadius: '16px 16px 0 0', padding: '18px 16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: '-0.02em' }}>驳回 <Sep /> 灶台 油污与残渣</div>
          <button onClick={props.onClose} aria-label="关闭" style={{ width: 44, height: 44, marginRight: -8, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: 'var(--ink-2)' }}>
            <Icon name="x" size={18} />
          </button>
        </div>
        <div style={{ marginTop: 14, fontSize: 16, color: 'var(--ink)', fontWeight: 500 }}>哪里不合格（必填，员工能看到）</div>
        <textarea value={text} onChange={e => setText(e.target.value)} rows={3}
                  placeholder="例如：灶台右下角仍有油污，排水口没拆洗"
                  style={{
                    marginTop: 8, width: '100%', minHeight: 92, resize: 'none', padding: '12px 12px',
                    fontSize: 16, lineHeight: '24px', color: 'var(--ink)', letterSpacing: '-0.01em',
                    background: 'var(--card-2)', border: '1px solid var(--line-strong)', borderRadius: 10, outline: 'none'
                  }} />
        <div style={{ marginTop: 14, display: 'flex', gap: 10 }}>
          <Btn kind="outline" h={52} w={112} onClick={props.onClose}>取消</Btn>
          <Btn kind="danger" h={52} grow disabled={!ok} onClick={() => { props.onSubmit(text.trim()); setText(''); }}>提交驳回</Btn>
        </div>
        <div style={{ marginTop: 10, fontSize: 13, color: 'var(--ink-2)' }}>驳回后王芳会收到通知，并能直接回拍。</div>
      </div>
    </div>
  );
}

Object.assign(window, {
  UI_ROLES, UI_NAV, Icon, Sep, Mark, Pill, Btn, Card, Underline, PageHead,
  LiveDot, DeskShell, PhoneShell, TabBar, LineChart, CounterShot, ReasonSheet
});

})();
