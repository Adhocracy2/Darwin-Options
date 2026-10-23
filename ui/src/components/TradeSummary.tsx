import type { PositionSummary, SummaryLeg } from "../api";
import { signed, usd } from "../api";

const money = (v: number) => `$${v.toFixed(2)}`;
const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const kind = (l: SummaryLeg) => `${l.strike} ${l.right === "C" ? "call" : "put"}`;
const count = (l: SummaryLeg) => (Math.abs(l.qty) > 1 ? `${Math.abs(l.qty)}× ` : "");
const pct0 = (v: number) => `${Math.round(v * 100)}%`;

/** Plain-language breakdown of an open position under its payoff chart. */
export function TradeSummary({ s, underlying }: { s: PositionSummary; underlying: string }) {
  const credit = s.open_value > 0;
  const r = s.rules;
  const o = s.outlook;
  const near = s.nearest_short;
  // spot where the "threatened" exit fires: within buffer_pct of the strike, on the losing side
  const threatAt = r.threatened && near
    ? (near.right === "C" ? r.threatened.strike / (1 + r.threatened.buffer_pct) : r.threatened.strike / (1 - r.threatened.buffer_pct))
    : null;

  return (
    <div className="trade-summary">
      <section>
        <h3>The trade</h3>
        <ul>
          {s.legs.map((l) => (
            <li key={l.symbol}>{l.qty < 0 ? "Sold" : "Bought"} {count(l)}{kind(l)} at {money(l.entry_price)}
              <span className="muted"> · {day(l.expiry)}</span></li>
          ))}
        </ul>
        <p>
          {credit ? <>You received <b>{usd(s.open_value)}</b></> : <>You paid <b>{usd(-s.open_value)}</b></>}
          <span className="muted"> (options are 100 shares each; fees so far {usd(s.commissions, 2)}).</span>
        </p>
      </section>

      <section>
        <h3>Closing it now</h3>
        {s.close_value == null ? <p className="muted">No current quotes for every leg.</p> : (
          <>
            <ul>
              {s.legs.map((l) => (
                <li key={l.symbol}>{l.qty < 0 ? "Buy back" : "Sell"} {count(l)}{kind(l)} at about {money(l.mark ?? 0)}</li>
              ))}
            </ul>
            <p>
              {s.close_value < 0 ? <>Costs <b>{usd(-s.close_value)}</b></> : <>Brings in <b>{usd(s.close_value)}</b></>}
              {" "}→ P&amp;L now <b className={(s.pnl_now ?? 0) >= 0 ? "pos" : "neg"}>{signed(s.pnl_now)}</b>
              <span className="muted"> (mid prices, before closing fees).</span>
            </p>
            {credit && near && (
              <p className="muted small">
                {underlying} at {money(s.spot)} is {pct0(Math.abs(near.distance_pct))} {near.distance_pct > 0 ? "below" : "above"} the {near.strike} strike
                with {s.days_left} days left, so the options still hold time value. {near.distance_pct > 0 === (near.right === "C")
                  ? <>If {underlying} stays {near.right === "C" ? "below" : "above"} {near.strike}, that value shrinks toward $0 as expiry nears, fastest in the last few weeks.</>
                  : <>The short strike is already in the money.</>}
              </p>
            )}
          </>
        )}
      </section>

      {o && (
        <section>
          <h3>Projected outcome</h3>
          <div className="outlook-tiles">
            <div><div className="label">Chance it closes at a profit</div><div className="value">{pct0(o.chance_profit)}</div></div>
            <div><div className="label">Average result</div><div className={`value ${o.expected_pnl >= 0 ? "pos" : "neg"}`}>{signed(o.expected_pnl)}</div></div>
          </div>
          <div className="outlook-bar" role="img" aria-label={`Take profit ${pct0(o.chances.take_profit)}, time exit ${pct0(o.chances.time_exit)}, strike threatened ${pct0(o.chances.threatened)}, stop loss ${pct0(o.chances.stop_loss)}`}>
            <span className="tp" style={{ flexGrow: o.chances.take_profit }} />
            <span className="te" style={{ flexGrow: o.chances.time_exit }} />
            <span className="th" style={{ flexGrow: o.chances.threatened }} />
            <span className="sl" style={{ flexGrow: o.chances.stop_loss }} />
          </div>
          <div className="outlook-legend small">
            <span><i className="tp" />Take profit {pct0(o.chances.take_profit)}</span>
            <span><i className="te" />Time exit {pct0(o.chances.time_exit)}</span>
            <span><i className="th" />Strike threatened {pct0(o.chances.threatened)}</span>
            <span><i className="sl" />Stop loss {pct0(o.chances.stop_loss)}</span>
          </div>
          <p className="muted small">
            Estimate from {o.paths.toLocaleString()} simulated {underlying} paths to {day(o.exit_date)} at {pct0(o.sigma)} yearly volatility,
            closing each by the rules below. Recalculated from current prices every minute. A model, not a promise:
            it checks once a day and assumes volatility stays put.
          </p>
        </section>
      )}

      <section>
        <h3>Exit plan</h3>
        <ul>
          {r.take_profit && (r.take_profit.close_cost != null
            ? <li>Take profit when closing costs {usd(r.take_profit.close_cost)} or less: <b className="pos">{signed(r.take_profit.pnl)}</b></li>
            : <li>Take profit when it's worth {usd(r.take_profit.close_value)}: <b className="pos">{signed(r.take_profit.pnl)}</b></li>)}
          {r.stop_loss && (r.stop_loss.close_cost != null
            ? <li>Stop loss if closing costs {usd(r.stop_loss.close_cost)} or more: <b className="neg">{signed(r.stop_loss.pnl)}</b></li>
            : <li>Stop loss if it falls to {usd(r.stop_loss.close_value)}: <b className="neg">{signed(r.stop_loss.pnl)}</b></li>)}
          {r.threatened && threatAt != null && (
            <li>Close early if {underlying} {near?.right === "C" ? "rises above" : "falls below"} about {money(threatAt)} (near the {r.threatened.strike} strike)</li>
          )}
          {r.time_exit && (r.time_exit.time
            ? <li>Close by {r.time_exit.time} New York on {day(r.time_exit.date)}</li>
            : <li>Close on {day(r.time_exit.date)} ({r.time_exit.dte} days before expiry) if nothing else fires first</li>)}
        </ul>
        <p className="muted small">If held to expiry: best {signed(s.max_profit)}, worst {signed(-s.max_loss)}.</p>
      </section>
    </div>
  );
}
