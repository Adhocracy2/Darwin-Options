/** Everything the last trading cycle received from IBKR: prices, option chains, quotes, notices. */
import type { Status } from "../api";

const dteLabel = ([a, b]: [number, number]) => (a === b ? (a === 0 ? "today (0DTE)" : `${a} days`) : `${a}–${b} days`);
const NOTICE_HELP: Record<number, string> = {
  10197: "another login (TWS, the website or the app) is using the live data, so IBKR refused these quotes",
  10167: "delayed data shown instead of live", 10090: "part of the data isn't subscribed",
  162: "historical data request cancelled or timed out", 1100: "IB Gateway lost its link to IBKR", 1102: "link restored",
  200: "contract not found", 354: "data not subscribed",
};

export function ReceivedCard({ status, compact }: { status: Status | null; compact?: boolean }) {
  const c = status?.cycle;
  const r = c?.received;
  const when = c?.finished ? new Date(c.finished).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : null;
  const body = !r ? (
    <p className="muted small">{c ? "This cycle didn't record what it received (older version or no IBKR data)." : "No trading cycle has run yet."}</p>
  ) : (
    <>
      <div className="rx-tiles">
        <div><b>{Object.keys(r.spots).length}</b><span>stock/index prices</span></div>
        <div><b>{r.chains.filter((x) => !x.cached).length}</b><span>option chains fetched{r.chains.some((x) => x.cached) ? ` (+${r.chains.filter((x) => x.cached).length} reused)` : ""}</span></div>
        <div><b>{r.chains.filter((x) => !x.cached).reduce((a, x) => a + x.quotes, 0).toLocaleString()}</b><span>option quotes in chains</span></div>
        <div><b>{r.quotes}/{r.quote_requests}</b><span>position quotes</span></div>
        <div><b>{r.positions}</b><span>open positions checked</span></div>
      </div>
      <p className="small rx-spots">{Object.entries(r.spots).map(([k, v]) => `${k} ${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`).join(" · ")}</p>
      {!compact && r.chains.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Chain</th><th>Expiries</th><th className="num">Quotes</th><th className="num">With Greeks</th><th></th></tr></thead>
          <tbody>{r.chains.map((x, i) => (
            <tr key={i}><td><b>{x.symbol}</b> <span className="muted small">{dteLabel(x.dte)}</span></td>
              <td className="small">{x.expiries.length ? x.expiries.map((e) => e.slice(5)).join(", ") : "none"}</td>
              <td className="num">{x.quotes}</td><td className="num">{x.with_greeks}</td>
              <td className="small muted">{x.cached ? "reused (fetched <10 min ago)" : "fetched"}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {r.notices && r.notices.length > 0 && (
        <div className="rx-notices">
          <h4>Messages from IBKR</h4>
          <ul>{r.notices.map((n) => (
            <li key={n.code}><b>{n.count}×</b> code {n.code}: {NOTICE_HELP[n.code] ?? n.text}</li>
          ))}</ul>
        </div>
      )}
    </>
  );
  return (
    <div className={compact ? "rx compact" : "card rx"}>
      <h2>From IBKR last cycle{when && <span className="muted small"> · {c?.count ? `cycle ${c.count}, ` : ""}{when}</span>}</h2>
      {body}
    </div>
  );
}
