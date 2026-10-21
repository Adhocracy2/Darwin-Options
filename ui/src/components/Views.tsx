import { Fragment, useState } from "react";
import type { Candidate, Event, Performance, Position, Snapshot, Stats, Status } from "../api";
import { api, pct, signed, usd } from "../api";
import { EquityChart } from "./EquityChart";
import { PayoffPanel } from "./PayoffChart";
import { ReviewCard } from "./ReviewCard";
import { RiskCard } from "./RiskCard";

/** Toggle button for an expandable payoff row. */
const PayoffToggle = ({ open, onClick }: { open: boolean; onClick: () => void }) =>
  <button type="button" className="icon" aria-expanded={open} onClick={onClick}>{open ? "Hide" : "Payoff"}</button>;

/** Full-width row under a table row; the chart stays inside the visible width when the table scrolls. */
const ExpandRow = ({ cols, children }: { cols: number; children: React.ReactNode }) => (
  <tr className="expand"><td colSpan={cols}>
    <div style={{ position: "sticky", left: 0, maxWidth: "calc(100vw - 64px)" }}>{children}</div>
  </td></tr>
);

const Pnl = ({ v }: { v: number | null | undefined }) =>
  <span className={v == null ? "muted" : v >= 0 ? "pos" : "neg"}>{signed(v)}</span>;

function legText(p: Position) {
  return p.legs.filter((l) => l.exit_price == null).map((l) => `${l.qty > 0 ? "+" : ""}${l.qty} ${l.symbol} @ ${l.entry_price.toFixed(2)}`);
}

export function Overview({ status, positions, equity, perf }: { status: Status | null; positions: Position[]; equity: Snapshot[]; perf: Performance | null }) {
  const openRisk = positions.reduce((s, p) => s + p.max_loss, 0);
  const unreal = positions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  return (
    <>
      <div className="tiles">
        <div className="tile"><div className="label">Equity</div><div className="value">{usd(status?.account.equity)}</div></div>
        <div className="tile"><div className="label">Buying power</div><div className="value">{usd(status?.account.buying_power)}</div></div>
        <div className="tile"><div className="label">Open max loss</div><div className="value">{usd(openRisk)}</div></div>
        <div className="tile"><div className="label">Unrealized</div><div className="value"><Pnl v={unreal} /></div></div>
        <div className="tile"><div className="label">Realized (all time)</div><div className="value"><Pnl v={perf?.overall.total_pnl} /></div></div>
        <div className="tile"><div className="label">Win rate</div><div className="value">{pct(perf?.overall.win_rate)}</div></div>
      </div>
      <RiskCard refreshKey={status} live={status?.mode === "live"} />
      <div className="card"><h2>Equity</h2><EquityChart data={equity} /></div>
    </>
  );
}

export function Positions({ open, closed, onChange }: { open: Position[]; closed: Position[]; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const close = async (p: Position) => {
    if (!confirm(`Close ${p.strategy} ${p.underlying} now?`)) return;
    setBusy(p.id);
    try {
      const r = await api.closePosition(p.id);
      if (!r.closed) alert("Close order did not fill. It will be retried by the manager.");
    } catch (e) { alert(String(e)); }
    setBusy(null);
    onChange();
  };
  return (
    <>
      <div className="card">
        <h2>Open positions ({open.length})</h2>
        {open.length === 0 ? <p className="muted">No open positions.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Strategy</th><th>Legs</th><th className="num">Max loss</th><th className="num">Unrealized</th><th>Opened</th><th></th></tr></thead>
            <tbody>{open.map((p) => (
              <Fragment key={p.id}>
              <tr>
                <td><b>{p.underlying}</b><div className="muted small">{p.strategy}</div></td>
                <td className="legs">{legText(p).map((t) => <div key={t}>{t}</div>)}</td>
                <td className="num">{usd(p.max_loss)}</td>
                <td className="num"><Pnl v={p.unrealized_pnl} /></td>
                <td className="small muted">{p.opened_at.slice(0, 16).replace("T", " ")}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <PayoffToggle open={shown === p.id} onClick={() => setShown(shown === p.id ? null : p.id)} />{" "}
                  <button className="btn secondary" disabled={busy === p.id} onClick={() => close(p)}>Close</button>
                </td>
              </tr>
              {shown === p.id && (
                <ExpandRow cols={6}>
                  <PayoffPanel load={() => api.positionPayoff(p.id)} />
                  <p className="muted small payoff-source">Whole position, including anything already realised. Commissions to close not included.</p>
                </ExpandRow>
              )}
              </Fragment>))}
            </tbody>
          </table></div>)}
      </div>
      <div className="card">
        <h2>Closed ({closed.length})</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Strategy</th><th>Exit reason</th><th className="num">P&amp;L</th><th>Closed</th></tr></thead>
          <tbody>{closed.slice().reverse().slice(0, 100).map((p) => (
            <tr key={p.id}>
              <td><b>{p.underlying}</b><div className="muted small">{p.strategy}</div></td>
              <td className="small">{p.exit_reason}</td>
              <td className="num"><Pnl v={p.realized_pnl} /></td>
              <td className="small muted">{p.closed_at?.slice(0, 16).replace("T", " ")}</td>
            </tr>))}
          </tbody>
        </table></div>
      </div>
    </>
  );
}

export function Scanner({ scan, onScan, scanning }: { scan: { ts: string | null; candidates: Candidate[] }; onScan: () => void; scanning: boolean }) {
  const [onlyOk, setOnlyOk] = useState(false);
  const [shown, setShown] = useState<number | null>(null);
  const rows = scan.candidates.filter((c) => !onlyOk || c.approved);
  return (
    <div className="card">
      <div className="toolbar">
        <button className="btn" onClick={onScan} disabled={scanning}>{scanning ? "Scanning…" : "Scan now"}</button>
        <label className="small"><input type="checkbox" checked={onlyOk} onChange={(e) => setOnlyOk(e.target.checked)} /> Approved only</label>
        <span className="muted small">{scan.ts ? `Last scan ${scan.ts.replace("T", " ").slice(0, 16)} UTC · ${scan.candidates.length} candidates` : "No scan yet"}</span>
      </div>
      <p className="muted small">Scanning doesn't trade. Trades are placed by the scheduled cycle, or by “Run cycle”.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>Risk gate</th><th>Trade</th><th className="num">Credit</th><th className="num">Max loss</th><th className="num">POP</th><th className="num">EV</th><th className="num">EV / risk</th><th className="num">Qty</th><th></th></tr></thead>
        <tbody>{rows.slice(0, 150).map((c, i) => (
          <Fragment key={`${scan.ts}-${i}`}>
          <tr>
            <td style={{ whiteSpace: "nowrap" }} className={c.approved ? "pos status-ok" : "muted status-no"}>{c.approved ? "Pass" : "Blocked"}</td>
            <td>{c.description}{c.reasons.length > 0 && <div className="muted small">{c.reasons.join("; ")}</div>}</td>
            <td className="num">{c.net_credit.toFixed(2)}</td>
            <td className="num">{usd(c.max_loss)}</td>
            <td className="num">{pct(c.pop)}</td>
            <td className="num">{usd(c.expected_value, 2)}</td>
            <td className="num">{pct(c.score)}</td>
            <td className="num">{c.approved ? c.qty : "–"}</td>
            <td><PayoffToggle open={shown === i} onClick={() => setShown(shown === i ? null : i)} /></td>
          </tr>
          {shown === i && (
            <ExpandRow cols={9}>
              <PayoffPanel load={() => api.payoff({
                strategy: c.strategy, net_credit: c.net_credit, qty: 1,
                legs: c.legs.map((l) => ({ symbol: l.symbol, ratio: l.ratio })) })} />
              <p className="muted small payoff-source">Per 1 unit at the expected fill. {c.attach_to ? "Short call only; the LEAPS it's written against isn't shown." : ""}</p>
            </ExpandRow>
          )}
          </Fragment>))}
        </tbody>
      </table></div>
    </div>
  );
}

const statRow = (name: string, s: Stats) => (
  <tr key={name}>
    <td>{name}</td><td className="num">{s.trades}</td><td className="num">{pct(s.win_rate)}</td>
    <td className="num">{usd(s.avg_win)}</td><td className="num">{usd(s.avg_loss)}</td>
    <td className="num">{usd(s.expectancy)}</td><td className="num">{s.profit_factor?.toFixed(2) ?? "–"}</td>
    <td className="num"><Pnl v={s.total_pnl} /></td>
  </tr>
);

export function PerformanceView({ perf }: { perf: Performance | null }) {
  if (!perf) return null;
  return (
    <>
      <ReviewCard />
      <div className="tiles">
        <div className="tile"><div className="label">Closed trades</div><div className="value">{perf.overall.trades}</div></div>
        <div className="tile"><div className="label">Expectancy / trade</div><div className="value">{usd(perf.overall.expectancy)}</div></div>
        <div className="tile"><div className="label">Profit factor</div><div className="value">{perf.overall.profit_factor?.toFixed(2) ?? "–"}</div></div>
        <div className="tile"><div className="label">Max drawdown</div><div className="value">{pct(perf.max_drawdown)}</div></div>
        <div className="tile"><div className="label">Largest loss</div><div className="value">{usd(perf.overall.largest_loss)}</div></div>
      </div>
      <div className="card"><h2>By strategy</h2><div className="table-wrap"><table>
        <thead><tr><th>Strategy</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">Avg win</th><th className="num">Avg loss</th><th className="num">Expectancy</th><th className="num">Profit factor</th><th className="num">Total</th></tr></thead>
        <tbody>{statRow("All", perf.overall)}{Object.entries(perf.by_strategy).map(([k, s]) => statRow(k, s))}</tbody>
      </table></div></div>
      <div className="card"><h2>By exit reason</h2><div className="table-wrap"><table>
        <thead><tr><th>Reason</th><th className="num">Count</th><th className="num">P&amp;L</th></tr></thead>
        <tbody>{Object.entries(perf.by_exit_reason).map(([k, r]) => (
          <tr key={k}><td>{k}</td><td className="num">{r.count}</td><td className="num"><Pnl v={r.pnl} /></td></tr>))}</tbody>
      </table></div></div>
    </>
  );
}

export function Journal({ events }: { events: Event[] }) {
  return (
    <div className="card"><h2>Journal</h2><div className="table-wrap"><table>
      <thead><tr><th>Time (UTC)</th><th>Kind</th><th>Message</th></tr></thead>
      <tbody>{events.map((e) => (
        <tr key={e.id}>
          <td className="small muted" style={{ whiteSpace: "nowrap" }}>{e.ts.replace("T", " ").slice(0, 19)}</td>
          <td className={e.kind === "error" || e.kind === "reconcile" ? "neg" : ""}>{e.kind}</td>
          <td className="small">{e.message}</td>
        </tr>))}</tbody>
    </table></div></div>
  );
}
