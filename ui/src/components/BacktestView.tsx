/**
 * Backtest tab: run the trader over historical data (not IB Gateway) with the simulator, in the
 * background, using your current strategy settings; then compare runs.
 */
import { useCallback, useEffect, useState } from "react";
import type { BacktestDataset, BacktestRun, BacktestStats, Snapshot } from "../api";
import { api, pct, signed, usd } from "../api";
import { EquityChart } from "./EquityChart";

const wr = (s: BacktestStats) =>
  s.win_rate == null ? "–" : `${pct(s.win_rate)}${s.predicted_win_rate != null ? ` (model ${pct(s.predicted_win_rate)})` : ""}`;

function StatsTable({ title, rows }: { title: string; rows: [string, BacktestStats][] }) {
  if (!rows.length) return null;
  return (
    <div className="card">
      <h2>{title}</h2>
      <div className="table-wrap"><table>
        <thead><tr><th></th><th className="num">Trades</th><th className="num">Win rate (model predicted)</th>
          <th className="num">Avg win</th><th className="num">Avg loss</th><th className="num">P&amp;L</th></tr></thead>
        <tbody>{rows.map(([k, s]) => (
          <tr key={k}><td>{k}</td><td className="num">{s.trades}</td><td className="num">{wr(s)}</td>
            <td className="num">{usd(s.avg_win)}</td><td className="num">{usd(s.avg_loss)}</td>
            <td className={`num ${s.pnl >= 0 ? "pos" : "neg"}`}>{signed(s.pnl)}</td></tr>
        ))}</tbody>
      </table></div>
    </div>
  );
}

function Results({ run }: { run: BacktestRun }) {
  const r = run.results;
  if (!r) return <p className="muted">{run.status === "failed" ? `Failed: ${run.error}` : "No results yet."}</p>;
  const o = r.overall;
  const curve: Snapshot[] = r.equity.map((e) => ({ ts: e.ts, equity: e.equity, open_risk: 0, realized: 0, unrealized: 0 }));
  const exits = Object.entries(r.by_exit_reason).sort((a, b) => b[1].count - a[1].count);
  return (
    <>
      <div className="tiles">
        <div className="tile"><div className="label">Net P&amp;L</div><div className={`value ${o.pnl >= 0 ? "pos" : "neg"}`}>{signed(o.pnl)}</div></div>
        <div className="tile"><div className="label">Return on {usd(run.capital)}</div><div className="value">{pct(o.return_pct)}</div></div>
        <div className="tile"><div className="label">Max drawdown</div><div className="value">{pct(o.max_drawdown)}</div></div>
        <div className="tile"><div className="label">Trades</div><div className="value">{o.trades}</div></div>
        <div className="tile"><div className="label">Win rate</div><div className="value">{pct(o.win_rate)}</div>
          {o.predicted_win_rate != null && <div className="muted small">model predicted {pct(o.predicted_win_rate)}</div>}</div>
        <div className="tile"><div className="label">Fees</div><div className="value">{usd(o.fees)}</div></div>
      </div>
      <div className="card"><h2>Equity</h2><EquityChart data={curve} /></div>
      <StatsTable title="By year" rows={Object.entries(r.by_year)} />
      <StatsTable title="By strategy" rows={Object.entries(r.by_strategy)} />
      {exits.length > 0 && (
        <div className="card"><h2>How trades closed</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>Exit reason</th><th className="num">Trades</th><th className="num">P&amp;L</th></tr></thead>
            <tbody>{exits.map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{v.count}</td>
              <td className={`num ${v.pnl >= 0 ? "pos" : "neg"}`}>{signed(v.pnl)}</td></tr>)}</tbody>
          </table></div>
        </div>
      )}
      <div className="card"><h2>Trades ({r.trades.length}{r.trades.length >= 300 ? ", latest" : ""})</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Opened</th><th>Closed</th><th>Trade</th><th>Exit</th><th className="num">Model win chance</th><th className="num">P&amp;L</th></tr></thead>
          <tbody>{[...r.trades].reverse().map((t) => (
            <tr key={t.id}><td className="small">{t.opened}</td><td className="small">{t.closed}</td>
              <td className="small">{t.description ?? t.strategy}</td><td className="small">{t.exit}</td>
              <td className="num">{pct(t.pop)}</td><td className={`num ${t.pnl >= 0 ? "pos" : "neg"}`}>{signed(t.pnl)}</td></tr>
          ))}</tbody>
        </table></div>
      </div>
      <p className="muted small">
        Simulated on recorded prices, filled from the recorded bid/ask with your slippage and fees. With end-of-day data,
        exits are checked once a day, so stops hit during the day are missed: real results would be somewhat worse.
        {run.failed_cycles ? ` ${run.failed_cycles} day(s) couldn't be simulated.` : ""}
      </p>
    </>
  );
}

export function BacktestView() {
  const [datasets, setDatasets] = useState<BacktestDataset[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selected, setSelected] = useState<BacktestRun | null>(null);
  const [form, setForm] = useState({ dataset: "", start: "", end: "", capital: 10000 });
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.backtests();
      setRuns(list);
      if (selected) setSelected(await api.backtest(selected.id));
    } catch (e) { setError(String(e)); }
  }, [selected]);

  useEffect(() => {
    api.backtestDatasets().then((ds) => {
      setDatasets(ds);
      if (ds.length) setForm((f) => (f.dataset ? f : { ...f, dataset: ds[0].id, start: ds[0].first, end: ds[0].last }));
    }).catch((e) => setError(String(e)));
    api.backtests().then(setRuns).catch(() => {});
  }, []);
  const running = runs.find((r) => r.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [running, refresh]);

  const ds = datasets.find((d) => d.id === form.dataset);
  const start = async () => {
    setError(null);
    try {
      const run = await api.startBacktest({ dataset: form.dataset, start: form.start, end: form.end, capital: form.capital });
      setRuns((r) => [run, ...r]);
      setSelected(run);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <>
      <div className="card">
        <h2>New backtest</h2>
        <p className="muted small">Runs the trader on historical prices instead of IB Gateway, with the simulator filling orders and its own journal.
          Your live trading and journal aren't touched. It uses your current strategy settings.</p>
        {datasets.length === 0 ? <p className="muted">No historical data yet. Import some with <code>darwin import-history</code>.</p> : (
          <div className="bt-form">
            <label>Data<select value={form.dataset} onChange={(e) => {
              const d = datasets.find((x) => x.id === e.target.value);
              setForm({ ...form, dataset: e.target.value, start: d?.first ?? "", end: d?.last ?? "" });
            }}>{datasets.map((d) => <option key={d.id} value={d.id}>{d.label} ({d.symbols.join(", ")}, {d.first} to {d.last}, {d.days} days)</option>)}</select></label>
            <label>From<input type="date" value={form.start} min={ds?.first} max={ds?.last} onChange={(e) => setForm({ ...form, start: e.target.value })} /></label>
            <label>To<input type="date" value={form.end} min={ds?.first} max={ds?.last} onChange={(e) => setForm({ ...form, end: e.target.value })} /></label>
            <label>Capital<input type="number" min={1000} step={1000} value={form.capital} onChange={(e) => setForm({ ...form, capital: Number(e.target.value) })} /></label>
            <button className="btn" onClick={start} disabled={!!running || !form.dataset}>Run backtest</button>
          </div>
        )}
        {running && (
          <div className="bt-progress">
            <div className="bt-bar"><span style={{ width: `${running.total ? (100 * running.done) / running.total : 0}%` }} /></div>
            <span className="small">Testing {running.at ? running.at.slice(0, 10) : "…"} · {running.done}/{running.total || "?"} days</span>
            <button className="btn secondary" onClick={() => api.stopBacktest(running.id).then(refresh)}>Stop</button>
          </div>
        )}
        {error && <p className="neg small">{error}</p>}
      </div>

      {runs.length > 0 && (
        <div className="card">
          <h2>Runs</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>Run</th><th>Status</th><th className="num">Trades</th><th className="num">Win rate</th>
              <th className="num">P&amp;L</th><th className="num">Return</th><th className="num">Max DD</th><th></th></tr></thead>
            <tbody>{runs.map((r) => {
              const o = r.overall ?? (selected?.id === r.id ? selected.results?.overall : undefined);
              return (
                <tr key={r.id} className={selected?.id === r.id ? "selected" : ""}>
                  <td><b>{r.label}</b><div className="muted small">{r.strategies.join(", ")} · {usd(r.capital)}</div></td>
                  <td className="small">{r.status === "running" ? `running ${r.done}/${r.total}` : r.status}</td>
                  <td className="num">{o?.trades ?? "–"}</td><td className="num">{pct(o?.win_rate)}</td>
                  <td className="num">{o ? signed(o.pnl) : "–"}</td><td className="num">{pct(o?.return_pct)}</td>
                  <td className="num">{pct(o?.max_drawdown)}</td>
                  <td><button className="icon" onClick={() => api.backtest(r.id).then(setSelected)}>View</button></td>
                </tr>
              );
            })}</tbody>
          </table></div>
        </div>
      )}
      {selected && <><h2 className="bt-title">{selected.label}</h2><Results run={selected} /></>}
    </>
  );
}
