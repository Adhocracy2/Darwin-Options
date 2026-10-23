import { useCallback, useEffect, useState } from "react";
import type { Candidate, Event, Performance, Position, Snapshot, Status } from "./api";
import { api } from "./api";
import { StrategiesView } from "./components/StrategiesView";
import { Journal, Overview, PerformanceView, Positions, Scanner } from "./components/Views";

const TABS = ["Overview", "Positions", "Scanner", "Strategies", "Performance", "Journal"] as const;
type Tab = (typeof TABS)[number];

function brokerLabel(s: Status) {
  if (s.mode === "sim") return s.data === "synthetic" ? "Offline sim" : "Local sim · IBKR data";
  return s.mode === "live" ? "LIVE · IBKR" : "IBKR paper";
}

export default function App() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [status, setStatus] = useState<Status | null>(null);
  const [open, setOpen] = useState<Position[]>([]);
  const [closed, setClosed] = useState<Position[]>([]);
  const [scan, setScan] = useState<{ ts: string | null; candidates: Candidate[] }>({ ts: null, candidates: [] });
  const [equity, setEquity] = useState<Snapshot[]>([]);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"scan" | "run" | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, o, c, sc, eq, pf, ev] = await Promise.all([
        api.status(), api.positions("open"), api.positions("closed"), api.scan(), api.equity(), api.performance(), api.events(),
      ]);
      setStatus(s); setOpen(o); setClosed(c); setScan(sc); setEquity(eq); setPerf(pf); setEvents(ev);
      setError(null);
    } catch (e) { setError(String(e)); }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const runScan = async () => {
    setBusy("scan");
    try { setScan(await api.runScan()); } catch (e) { setError(String(e)); }
    setBusy(null);
  };

  const runCycle = async () => {
    if (status?.mode === "live" && !confirm("This is LIVE trading with real money. Run a cycle?")) return;
    setBusy("run");
    try {
      const r = await api.runCycle(false);
      if (r.skipped) alert(`Skipped: ${r.skipped}`);
    } catch (e) { setError(String(e)); }
    setBusy(null);
    refresh();
  };

  return (
    <div className="app">
      <header>
        <h1>Darwin Options</h1>
        <div className="badges">
          {status && <>
            <span className={`badge ${status.mode === "live" ? "live" : ""}`}>{brokerLabel(status)}</span>
            {status.gateway && !status.gateway.connected
              ? <span className="badge live" title={status.gateway.error ?? ""}>IB Gateway offline</span>
              : status.account && <span className="badge">{status.account.is_market_open ? "Market open" : "Market closed"}</span>}
            <span className="badge">{status.universe.join(" · ")}</span>
            {!status.allow_new_entries && <span className="badge">New entries off</span>}
          </>}
        </div>
        <a className="btn secondary" href="#/3d" title="The system as teams at desks, with the Supervisor (desktop)">3D office</a>
        <button className="btn" onClick={runCycle} disabled={busy !== null}>{busy === "run" ? "Running…" : "Run cycle"}</button>
      </header>
      {error && <div className="error">{error}</div>}
      <nav>{TABS.map((t) => (
        <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
          {t}{t === "Positions" && open.length ? ` (${open.length})` : ""}
        </button>))}
      </nav>
      {tab === "Overview" && <Overview status={status} positions={open} equity={equity} perf={perf} />}
      {tab === "Positions" && <Positions open={open} closed={closed} onChange={refresh} />}
      {tab === "Scanner" && <Scanner scan={scan} onScan={runScan} scanning={busy === "scan"} />}
      {tab === "Strategies" && <StrategiesView live={status?.mode === "live"} onSaved={refresh} />}
      {tab === "Performance" && <PerformanceView perf={perf} />}
      {tab === "Journal" && <Journal events={events} />}
    </div>
  );
}
