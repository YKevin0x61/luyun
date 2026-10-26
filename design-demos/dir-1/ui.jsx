/* 方向一 · 仪表台 —— 壳与基础件
   图标全部手画内联 SVG（24 网格、1.5 描边），图表也是 SVG，不引图表库。 */

const U = React;

const uiTone = {
  ink: 'var(--ink)',
  ink2: 'var(--ink-2)',
  ink3: 'var(--ink-3)',
  signal: 'var(--signal)',
  signalText: 'var(--signal-text)',
  ok: 'var(--ok)',
  danger: 'var(--danger)',
  dangerText: 'var(--danger-text)',
  line: 'var(--line)',
  lineStrong: 'var(--line-strong)'
};

/* ---------------- 图标 ---------------- */

const uiIconPaths = {
  today: (
    <g>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 7.6V12l3.1 2.1" />
      <path d="M12 3.75v1.4M20.25 12h-1.4M12 20.25v-1.4M3.75 12h1.4" />
    </g>
  ),
  business: (
    <g>
      <path d="M3.5 20h17" />
      <path d="M6 16.4l4.2-4.9 3.3 2.7 4.9-6.6" />
      <path d="M18.4 7.6h-2.2M18.4 7.6v2.2" />
    </g>
  ),
  people: (
    <g>
      <circle cx="9.2" cy="8.6" r="3.1" />
      <path d="M3.4 19.2c0-3 2.6-5.1 5.8-5.1s5.8 2.1 5.8 5.1" />
      <path d="M15.9 6.1a3 3 0 0 1 0 4.7" />
      <path d="M17.4 14.6c2 .7 3.2 2.3 3.2 4.3" />
    </g>
  ),
  floor: (
    <g>
      <rect x="4.25" y="4.25" width="15.5" height="15.5" rx="1" />
      <path d="M8 12.2l2.7 2.7 5.4-5.6" />
    </g>
  ),
  kitchen: (
    <g>
      <path d="M12 3.4c2.7 3.5 4.9 5.7 4.9 8.8a4.9 4.9 0 0 1-9.8 0c0-1.7.7-3 1.8-4.4.2 1.9 1 3 1.9 3.5.8-2.7 1-4.7 1.2-7.9z" />
    </g>
  ),
  system: (
    <g>
      <path d="M3.5 7.5h17M3.5 12h17M3.5 16.5h17" />
      <circle cx="8.5" cy="7.5" r="2" fill="var(--bg)" />
      <circle cx="15.5" cy="12" r="2" fill="var(--bg)" />
      <circle cx="10" cy="16.5" r="2" fill="var(--bg)" />
    </g>
  ),
  me: (
    <g>
      <circle cx="12" cy="8.4" r="3.4" />
      <path d="M4.9 19.6c0-3.6 3.2-6 7.1-6s7.1 2.4 7.1 6" />
    </g>
  ),
  check: <path d="M4.5 12.6l4.8 4.8L19.6 6.9" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  chevron: <path d="M9.5 5.5l6.5 6.5-6.5 6.5" />,
  chevronLeft: <path d="M14.5 5.5L8 12l6.5 6.5" />,
  undo: (
    <g>
      <path d="M8.6 7.2L4.2 11.6l4.4 4.4" />
      <path d="M4.2 11.6h9.3a5.1 5.1 0 0 1 5.1 5.1v1.7" />
    </g>
  ),
  download: (
    <g>
      <path d="M12 3.6v10.4" />
      <path d="M7.8 10.2L12 14.4l4.2-4.2" />
      <path d="M4.4 16.8v3.2h15.2v-3.2" />
    </g>
  ),
  help: (
    <g>
      <circle cx="12" cy="12" r="8.4" />
      <path d="M9.7 9.5a2.4 2.4 0 1 1 3.4 2.2c-.8.4-1.1 1-1.1 1.8" />
      <path d="M12 16.6v.6" />
    </g>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  clock: (
    <g>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 7.9V12l2.9 1.9" />
    </g>
  ),
  pin: (
    <g>
      <path d="M12 20.5s6-5.3 6-10a6 6 0 1 0-12 0c0 4.7 6 10 6 10z" />
      <circle cx="12" cy="10.4" r="2.1" />
    </g>
  )
};

function UiIcon(props) {
  const name = props.name;
  const size = props.size || 22;
  const color = props.color || 'currentColor';
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={props.strokeWidth || 1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={props.style}
    >
      {uiIconPaths[name]}
    </svg>
  );
}

/* ---------------- 文字件 ---------------- */

function UiLabel(props) {
  const style = Object.assign(
    { fontSize: 12, letterSpacing: '.12em', color: 'var(--ink-2)', lineHeight: 1.4 },
    props.style
  );
  return <span style={style}>{props.children}</span>;
}

function UiHair(props) {
  return <div style={Object.assign({ height: 1, background: 'var(--line)', width: '100%' }, props.style)} />;
}

function UiSectionHead(props) {
  return (
    <div
      style={Object.assign(
        {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: props.height || 42,
          padding: props.padding || '0 20px',
          borderBottom: props.border === false ? 'none' : '1px solid var(--line)'
        },
        props.style
      )}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {props.mark ? <span style={{ width: 4, height: 4, background: 'var(--signal)' }} /> : null}
        <span style={{ fontSize: 13, color: 'var(--ink)', letterSpacing: '.04em' }}>{props.title}</span>
        {props.count != null ? (
          <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)' }}>
            {props.count}
          </span>
        ) : null}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>{props.right}</div>
    </div>
  );
}

function UiBtn(props) {
  const variant = props.variant || 'default';
  const size = props.size || 'md';
  return (
    <button
      type="button"
      className="btn"
      data-variant={variant}
      data-size={size === 'md' ? undefined : size}
      onClick={props.onClick}
      disabled={props.disabled}
      style={props.style}
    >
      {props.icon ? <UiIcon name={props.icon} size={size === 'lg' ? 20 : 16} /> : null}
      {props.children}
    </button>
  );
}

function UiChip(props) {
  const onStyle = props.on
    ? { background: 'var(--ink)', borderColor: 'var(--ink)', color: 'var(--panel-3)' }
    : null;
  return (
    <button
      type="button"
      className="chip"
      data-on={props.on ? 'true' : 'false'}
      onClick={props.onClick}
      style={Object.assign({}, props.style, onStyle)}
    >
      {props.children}
    </button>
  );
}

/* ---------------- 数字件 ---------------- */

function UiSpark(props) {
  const values = props.values;
  const w = props.w || 116;
  const h = props.h || 44;
  const pad = 3;
  const max = Math.max.apply(null, values);
  const min = Math.min.apply(null, values);
  const span = max - min || 1;
  const step = (w - pad * 2) / (values.length - 1);
  const pts = values.map(function (v, i) {
    return [pad + i * step, h - pad - ((v - min) / span) * (h - pad * 2)];
  });
  const line = pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
  const area =
    'M' + pts[0][0].toFixed(1) + ',' + (h - pad) + ' L' +
    pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' L') +
    ' L' + pts[pts.length - 1][0].toFixed(1) + ',' + (h - pad) + ' Z';
  const last = pts[pts.length - 1];
  return (
    <svg width={w} height={h} viewBox={'0 0 ' + w + ' ' + h} aria-hidden="true">
      <line x1="0" y1={h - pad} x2={w} y2={h - pad} stroke="var(--line)" strokeWidth="1" />
      <path d={area} fill="var(--line)" opacity="0.75" />
      <polyline points={line} fill="none" stroke="var(--ink-2)" strokeWidth="1.25" />
      {pts.map(function (p, i) {
        return <line key={i} x1={p[0]} y1={h - pad} x2={p[0]} y2={h - pad + 3} stroke="var(--line-strong)" strokeWidth="1" />;
      })}
      <rect x={last[0] - 1.5} y={last[1] - 1.5} width="3" height="3" fill="var(--ink)" />
    </svg>
  );
}

function UiSegMeter(props) {
  const total = props.total;
  const on = props.on;
  const items = [];
  for (let i = 0; i < total; i++) {
    items.push(
      <span
        key={i}
        style={{
          flex: 1,
          height: props.height || 8,
          background: i < on ? props.color || 'var(--ink)' : 'transparent',
          border: i < on ? 'none' : '1px solid var(--line-strong)'
        }}
      />
    );
  }
  return <div style={{ display: 'flex', gap: 2, width: '100%' }}>{items}</div>;
}

function UiLineChart(props) {
  const data = props.data;
  const w = props.w || 358;
  const h = props.h || 168;
  const padL = 8;
  const padR = 8;
  const padT = 26;
  const padB = 26;
  const max = Math.max.apply(null, data.map(function (d) { return d.value; }));
  const min = Math.min.apply(null, data.map(function (d) { return d.value; }));
  const span = max - min || 1;
  const step = (w - padL - padR) / (data.length - 1);
  const yFor = function (v) { return padT + (1 - (v - min) / span) * (h - padT - padB); };
  const pts = data.map(function (d, i) {
    return { x: padL + i * step, y: yFor(d.value), d: d };
  });
  const line = pts.map(function (p) { return p.x.toFixed(1) + ',' + p.y.toFixed(1); }).join(' ');
  const area =
    'M' + pts[0].x.toFixed(1) + ',' + (h - padB) + ' L' +
    pts.map(function (p) { return p.x.toFixed(1) + ',' + p.y.toFixed(1); }).join(' L') +
    ' L' + pts[pts.length - 1].x.toFixed(1) + ',' + (h - padB) + ' Z';
  const maxIdx = data.findIndex(function (d) { return d.value === max; });
  const maxPt = pts[maxIdx];
  return (
    <svg width="100%" height={h} viewBox={'0 0 ' + w + ' ' + h} preserveAspectRatio="none" style={{ display: 'block' }}>
      {[0, 0.5, 1].map(function (t, i) {
        const y = padT + t * (h - padT - padB);
        return <line key={i} x1={0} y1={y} x2={w} y2={y} stroke="var(--line)" strokeWidth="1" />;
      })}
      <path d={area} fill="var(--line)" opacity="0.6" />
      <polyline points={line} fill="none" stroke="var(--ink)" strokeWidth="1.5" />
      {pts.map(function (p, i) {
        return (
          <circle
            key={i}
            cx={p.x}
            cy={p.y}
            r={i === maxIdx ? 3.2 : 2}
            fill={i === maxIdx ? 'var(--ink)' : 'var(--bg)'}
            stroke={i === maxIdx ? 'var(--ink)' : 'var(--ink-2)'}
            strokeWidth="1.2"
          />
        );
      })}
      <line x1={maxPt.x} y1={maxPt.y - 4} x2={maxPt.x} y2={padT - 4} stroke="var(--line-strong)" strokeWidth="1" strokeDasharray="2 3" />
      {props.labels
        ? props.labels.map(function (lb, i) {
            return (
              <text key={lb} x={padL + i * step} y={h - 6} fill="var(--ink-2)" fontSize="13" textAnchor={i === 0 ? 'start' : i === props.labels.length - 1 ? 'end' : 'middle'}>
                {lb}
              </text>
            );
          })
        : null}
      <text x={maxPt.x} y={12} fill="var(--ink)" fontSize="13" textAnchor={maxIdx > 4 ? 'end' : 'middle'} className="tnum">
        {props.maxLabel}
      </text>
    </svg>
  );
}

/* ---------------- 弹层 ---------------- */

function UiSheet(props) {
  if (!props.open) return null;
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 20,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        background: 'var(--scrim)'
      }}
      onClick={props.onClose}
    >
      <div
        onClick={function (e) { e.stopPropagation(); }}
        style={{
          background: 'var(--panel)',
          borderTop: '1px solid var(--line-strong)',
          padding: '18px 16px 16px'
        }}
      >
        {props.children}
      </div>
    </div>
  );
}

/* ---------------- 桌面壳 ---------------- */

function UiStatusBar(props) {
  const [clock, setClock] = U.useState('18:40:00');
  U.useEffect(function () {
    let sec = 0;
    const id = setInterval(function () {
      sec += 1;
      const total = 18 * 3600 + 40 * 60 + sec;
      const hh = String(Math.floor(total / 3600) % 24).padStart(2, '0');
      const mm = String(Math.floor(total / 60) % 60).padStart(2, '0');
      const ss = String(total % 60).padStart(2, '0');
      setClock(hh + ':' + mm + ':' + ss);
    }, 1000);
    return function () { clearInterval(id); };
  }, []);

  const sep = <span style={{ width: 1, height: 12, background: 'var(--line-strong)' }} />;
  return (
    <div
      style={{
        height: 40,
        flex: '0 0 40px',
        borderBottom: '1px solid var(--line)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 20px'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <span style={{ fontSize: 13, color: 'var(--ink)', letterSpacing: '.02em' }}>{props.shop.name}</span>
        {sep}
        <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-2)' }}>营业日 {props.shop.dayLabel}</span>
        {sep}
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 6, height: 6, background: 'var(--ok)' }} />
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>在线</span>
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{props.identity.label}</span>
        {sep}
        <span className="tnum" style={{ fontSize: 13, color: 'var(--ink)', letterSpacing: '.04em' }}>{clock}</span>
      </div>
    </div>
  );
}

function UiRail(props) {
  const domains = [
    { key: 'today', label: '今天', icon: 'today' },
    { key: 'business', label: '生意', icon: 'business' },
    { key: 'people', label: '人', icon: 'people' },
    { key: 'floor', label: '现场', icon: 'floor' },
    { key: 'kitchen', label: '厨房', icon: 'kitchen' },
    { key: 'system', label: '系统', icon: 'system' }
  ];
  return (
    <nav style={{ width: 72, flex: '0 0 72px', borderRight: '1px solid var(--line)', paddingTop: 6 }}>
      {domains.map(function (d) {
        const on = d.key === props.active;
        return (
          <button
            key={d.key}
            type="button"
            onClick={function () { props.onSelect(d.key); }}
            aria-current={on ? 'page' : undefined}
            style={{
              position: 'relative',
              width: '100%',
              height: 62,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 5,
              background: on ? 'var(--panel)' : 'transparent',
              color: on ? 'var(--ink)' : 'var(--ink-3)'
            }}
            className="railitem"
          >
            {on ? <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: 'var(--signal)' }} /> : null}
            <UiIcon name={d.icon} size={21} />
            <span style={{ fontSize: 12, letterSpacing: '.04em', color: on ? 'var(--ink)' : 'var(--ink-2)' }}>{d.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

/* ---------------- 手机壳 ---------------- */

function UiPhoneTitle(props) {
  return (
    <div
      style={{
        height: 44,
        flex: '0 0 44px',
        borderBottom: '1px solid var(--line)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 16px',
        gap: 10
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
        <span style={{ fontSize: 16, color: 'var(--ink)', letterSpacing: '.02em', whiteSpace: 'nowrap' }}>{props.title}</span>
        {props.sub ? (
          <span style={{ fontSize: 13, color: 'var(--ink-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {props.sub}
          </span>
        ) : null}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '0 0 auto' }}>{props.right}</div>
    </div>
  );
}

function UiPhoneTabs(props) {
  const items = props.items;
  return (
    <div style={{ height: 64, flex: '0 0 64px', borderTop: '1px solid var(--line)', display: 'flex', background: 'var(--panel)' }}>
      {items.map(function (it) {
        const on = it.key === props.active;
        return (
          <button
            key={it.key}
            type="button"
            onClick={function () { if (props.onSelect) props.onSelect(it.key); }}
            style={{
              position: 'relative',
              flex: 1,
              height: 64,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 3,
              color: on ? 'var(--ink)' : 'var(--ink-2)'
            }}
          >
            {on ? <span style={{ position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)', width: 24, height: 2, background: 'var(--ink)' }} /> : null}
            <UiIcon name={it.icon} size={20} color={on ? 'var(--ink)' : 'var(--ink-2)'} />
            <span style={{ fontSize: 13, letterSpacing: '.02em', lineHeight: 1.2 }}>{it.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/* ---------------- 轻提示（未展开的页 / 导出这类动作的反馈） ---------------- */

function uiToast(message) {
  window.dispatchEvent(new CustomEvent('d1:toast', { detail: message }));
}

function UiToastHost(props) {
  const [msg, setMsg] = U.useState(null);
  U.useEffect(function () {
    function onToast(e) { setMsg(e.detail); }
    window.addEventListener('d1:toast', onToast);
    return function () { window.removeEventListener('d1:toast', onToast); };
  }, []);
  U.useEffect(function () {
    if (!msg) return function () {};
    const id = setTimeout(function () { setMsg(null); }, 2600);
    return function () { clearTimeout(id); };
  }, [msg]);
  if (!msg) return null;
  return (
    <div
      role="status"
      style={{
        position: 'absolute',
        left: '50%',
        bottom: props.bottom || 24,
        transform: 'translateX(-50%)',
        zIndex: 40,
        maxWidth: '88%',
        background: 'var(--panel-2)',
        border: '1px solid var(--line-strong)',
        borderLeft: '2px solid var(--signal)',
        color: 'var(--ink)',
        fontSize: 13,
        lineHeight: 1.4,
        padding: '8px 12px',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis'
      }}
    >
      {msg}
    </div>
  );
}

window.__d1toast = uiToast;

Object.assign(window, {
  UiToastHost: UiToastHost,
  uiToast: uiToast,
  UiIcon: UiIcon,
  UiLabel: UiLabel,
  UiHair: UiHair,
  UiSectionHead: UiSectionHead,
  UiBtn: UiBtn,
  UiChip: UiChip,
  UiSpark: UiSpark,
  UiSegMeter: UiSegMeter,
  UiLineChart: UiLineChart,
  UiSheet: UiSheet,
  UiStatusBar: UiStatusBar,
  UiRail: UiRail,
  UiPhoneTitle: UiPhoneTitle,
  UiPhoneTabs: UiPhoneTabs,
  uiTone: uiTone
});
