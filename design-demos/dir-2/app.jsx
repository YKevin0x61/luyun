/* dir-2 · 作业流 —— 入口：?p= today|sales|daily|calendar，?role= boss|manager|chef|staff */

(function () {

function readParams() {
  const q = new URLSearchParams(window.location.search);
  /* window.__PAGE__ / __ROLE__ 是静态体检（file:// 下没有 query）时的兜底 */
  return {
    page: window.__PAGE__ || q.get('p') || 'today',
    role: window.__ROLE__ || q.get('role') || 'manager'
  };
}

function App() {
  const init = readParams();
  const [page, setPage] = React.useState(init.page);
  const [tab, setTab] = React.useState(init.page === 'daily' ? 'floor' : 'today');

  /* tab 只接本次真有的两屏手机页：现场 = 日常验收，今天 = 手机版今天。
     厨房 / 我的 两键没有对应页面，不接跳转。 */
  function onTab(k) {
    if (k === 'floor') { setPage('daily'); setTab('floor'); }
    else if (k === 'today') { setPage('mtoday'); setTab('today'); }
  }

  const map = {
    today: window.TodayPage,
    sales: window.SalesPage,
    daily: window.DailyPage,
    calendar: window.CalendarPage,
    mtoday: window.MobileTodayPage
  };
  const Page = map[page] || window.TodayPage;
  return <Page role={init.role} tab={tab} onTab={onTab} />;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);

})();
