/* 方向一 · 仪表台 —— 入口：?p=today|sales|daily|calendar & ?role=boss|manager|chef|staff */

const A = React;
const dd = window.D1;

function AppPhone(props) {
  return (
    <div className="phone-stage">
      <div className="phone">
        {props.children}
        <UiToastHost bottom={78} />
      </div>
    </div>
  );
}

function AppDesktop(props) {
  return (
    <div className="desktop-root">
      <UiStatusBar shop={dd.shop} identity={props.identity} />
      <div className="desktop-body">
        <UiRail
          active={props.active}
          onSelect={function (key) {
            const role = props.role;
            if (key === 'today') {
              window.location.search = '?p=today&role=' + role;
              return;
            }
            if (key === 'people') {
              window.location.search = '?p=calendar&role=' + role;
              return;
            }
            const names = { business: '生意', floor: '现场', kitchen: '厨房', system: '系统' };
            window.__d1toast(names[key] + ' · 方向一样张只含今天 / 销售 / 验收 / 排班四页');
          }}
        />
        <main className="desktop-main scroll">{props.children}</main>
      </div>
      <UiToastHost bottom={24} />
    </div>
  );
}

function App() {
  const params = new URLSearchParams(window.location.search);
  const page = params.get('p') || 'today';
  const fallbackRole = { today: 'manager', calendar: 'manager', sales: 'boss', daily: 'chef' }[page] || 'manager';
  const role = params.get('role') || fallbackRole;
  const identity = dd.identity[role] || dd.identity.manager;

  if (page === 'sales') {
    return (
      <AppPhone>
        <PageSales />
      </AppPhone>
    );
  }
  if (page === 'daily') {
    return (
      <AppPhone>
        <PageDaily />
      </AppPhone>
    );
  }
  if (page === 'calendar') {
    return (
      <AppDesktop active="people" identity={identity} role={role}>
        <PageCalendar />
      </AppDesktop>
    );
  }
  return (
    <AppDesktop active="today" identity={identity} role={role}>
      <PageToday />
    </AppDesktop>
  );
}

Object.assign(window, { App: App });
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
