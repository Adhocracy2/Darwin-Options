import { useEffect, useState } from "react";
import type { RiskState } from "../api";
import { api, pct, usd } from "../api";

/** Risk: current limits, brakes, measured edge, and optional equity milestones. */
export function RiskCard({ refreshKey, live }: { refreshKey: unknown; live: boolean }) {
  const [r, setR] = useState<RiskState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.risk().then(setR).catch((e) => setErr(String(e.message ?? e))); }, [refreshKey]);
  if (err) return <div className="card"><div className="error">{err}</div></div>;
  if (!r) return null;

  const reset = async () => {
    if (!confirm(`Reset the drawdown peak to today's equity (${usd(r.equity)})?${live ? " This is the LIVE account." : ""} New trades will resume if nothing else blocks them.`)) return;
    setR(await api.resetRisk());
  };
  const perTrade = r.per_trade_cap;
  const m = r.measured;

  return (
    <div className="card">
      <h2>Risk {r.pilot && <span className="badge live">Pilot</span>}</h2>
      {r.capital_mode && (
        <p className="small muted" style={{ marginTop: 0 }}>
          Trading capital <b>{usd(r.equity)}</b> ({usd(r.capital)} allocated + system P&amp;L) of a {usd(r.account_equity)} account.
          Sizing and drawdown use the trading capital only.
        </p>)}
      {r.goals.length > 0 && <div className="goals">
        {r.goals.map((g) => (
          <div key={g.target} className="goal">
            <div className="small"><b>{usd(g.target)}</b> {g.reached ? <span className="pos status-ok">reached</span> : <span className="muted">{pct(g.progress)}</span>}</div>
            <div className="bar" role="progressbar" aria-valuenow={Math.round(g.progress * 100)} aria-valuemin={0} aria-valuemax={100}
                 aria-label={`Progress to ${usd(g.target)}`}><span style={{ width: `${g.progress * 100}%` }} /></div>
          </div>))}
      </div>}

      {r.smallest_trade_max_loss != null && r.smallest_trade_max_loss > perTrade && (
        <div className="note small halted">
          <b>Account too small for the current limit. </b>
          The cheapest trade in the last scan risks {usd(r.smallest_trade_max_loss)} ({pct(r.smallest_trade_max_loss / r.equity)} of
          equity), above the {usd(perTrade)} allowed per trade, so nothing can open. Add funds,
          or raise the per-trade limit knowing one loss would cost that share of the account.
        </div>)}
      <div className={`note small ${r.halted ? "halted" : ""}`} style={{ display: r.reasons.length ? "block" : "none" }}>
        {r.halted ? <b>New trades paused. </b> : null}{r.reasons.join(" · ")}
        {r.drawdown >= r.limits.drawdown_halt_at && <> <button className="link" onClick={reset}>Reset drawdown peak</button></>}
      </div>

      <div className="tiles compact">
        <div className="tile"><div className="label">Risk per trade now</div><div className="value">{pct(r.risk_per_trade_pct)}</div>
          <div className="muted small">{usd(perTrade)} max loss{r.pilot
            ? ` · pilot · ${r.entries_left_today ?? 0} new trade(s) left today`
            : ` · stage ceiling ${pct(r.stage.risk_per_trade_pct)}`}</div></div>
        <div className="tile"><div className="label">Open risk</div><div className="value">{usd(r.open_risk)}</div>
          <div className="muted small">limit {pct(r.max_portfolio_risk_pct)} = {usd(r.max_portfolio_risk_pct * r.equity)} · {r.open_positions}/{r.max_positions} positions</div></div>
        <div className="tile"><div className="label">Drawdown</div><div className="value">{pct(r.drawdown)}</div>
          <div className="muted small">peak {usd(r.peak)} · halve at {pct(r.limits.drawdown_reduce_at)}, stop at {pct(r.limits.drawdown_halt_at)}</div></div>
        <div className="tile"><div className="label">Today</div><div className="value">{r.day_change_pct >= 0 ? "+" : ""}{pct(r.day_change_pct)}</div>
          <div className="muted small">stop new trades at −{pct(r.limits.daily_loss_halt_pct)}</div></div>
        <div className="tile"><div className="label">Measured edge</div>
          <div className="value">{m.mean_return_on_risk == null ? "–" : `${m.mean_return_on_risk >= 0 ? "+" : ""}${pct(m.mean_return_on_risk)}`}</div>
          <div className="muted small">{m.trades} trades{m.kelly != null ? ` · Kelly ${pct(m.kelly)}, using half` : " · sizing from results after 30"}</div></div>
      </div>
      {r.pilot ? (
        <p className="muted small" style={{ margin: 0 }}>
          Pilot: one trade may use up to the whole trading capital, but only one position and one new trade a day.
          A full loss trips the drawdown brake and pauses trading until you reset it here.
        </p>
      ) : <p className="muted small" style={{ margin: 0 }}>
        Stage: accounts from {usd(r.stage.min_equity)}. Limits tighten as the account grows. Trade size is the smaller of the stage ceiling
        and half the Kelly bet from your own results; {r.size_multiplier < 1 ? "currently halved by the drawdown brake." : "underlyings that move together share one budget."}
      </p>}
    </div>
  );
}
