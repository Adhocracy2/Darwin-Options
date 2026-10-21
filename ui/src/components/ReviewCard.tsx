import { useEffect, useState } from "react";
import type { Review } from "../api";
import { api, pct } from "../api";

const ror = (v: number) => <span className={v >= 0 ? "pos" : "neg"}>{v >= 0 ? "+" : "−"}{Math.abs(v * 100).toFixed(1)}%</span>;

/** Trade review: groups closed trades by entry conditions and lists evidence-backed changes. */
export function ReviewCard() {
  const [r, setR] = useState<Review | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.review().then(setR).catch((e) => setErr(String(e.message ?? e))); }, []);
  if (err) return <div className="card"><div className="error">{err}</div></div>;
  if (!r) return <div className="card"><p className="muted">Reviewing trades…</p></div>;
  return (
    <div className="card">
      <h2>Trade review</h2>
      <p className="muted small">
        Closed trades grouped by their conditions at entry. Return on risk = P&amp;L ÷ max loss. Changes are only
        suggested when a group has {r.min_trades_per_group}+ trades and the difference is bigger than chance.
      </p>
      {r.note && <p className="small note">{r.note}</p>}
      {r.execution && (
        <p className="small">
          <b>Fill quality:</b> {r.execution.orders} orders filled on average{" "}
          <b>${Math.abs(r.execution.avg_slippage_per_share).toFixed(3)}/share {r.execution.avg_slippage_per_share >= 0 ? "worse" : "better"}</b>{" "}
          than the mid price{r.execution.avg_slippage_pct_of_mid != null && ` (${pct(Math.abs(r.execution.avg_slippage_pct_of_mid))} of the mid credit)`}.
        </p>)}
      <h3 className="small">Suggested changes</h3>
      {r.suggestions.length === 0 ? <p className="muted small">None yet.</p> : (
        <ul className="suggestions">{r.suggestions.map((s, i) => (
          <li key={i}>
            <b>{s.title}</b> <span className="badge">{s.strategy}</span>
            <div className="small">{s.detail}</div>
            {s.setting && <div className="small muted">Change <code>{s.setting}</code> to <code>{JSON.stringify(s.value)}</code></div>}
          </li>))}
        </ul>)}
      {r.strategies.map((s) => (
        <details key={s.strategy} className="review-strategy">
          <summary>
            <b>{s.strategy}</b> · {s.overall.trades} trades · win {pct(s.overall.win_rate)} · return on risk {ror(s.overall.avg_return_on_risk)}
            {s.model && <span className="muted small"> · model predicted {pct(s.model.predicted_win_rate)} winners</span>}
          </summary>
          {Object.entries(s.groups).map(([name, groups]) => (
            <div key={name} className="table-wrap">
              <table>
                <thead><tr><th>By {name}</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">Return on risk</th></tr></thead>
                <tbody>{groups.map((g) => (
                  <tr key={g.label} className={g.trades < r.min_trades_per_group ? "muted" : ""}>
                    <td>{g.label}</td><td className="num">{g.trades}</td><td className="num">{pct(g.win_rate)}</td>
                    <td className="num">{ror(g.avg_return_on_risk)}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>))}
        </details>))}
    </div>
  );
}
