/** The Market Data team's store: what's been saved for backtests, newest first. Opens from the database on its platform. */
import { useEffect, useRef, useState } from "react";
import { api, type MarketDataPage, type MarketDataRecent } from "../api";
import { ago } from "./teams";

const time = (ts: string) => new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const num = (v: number | null | undefined, d = 2) => (v == null ? "–" : v.toFixed(d));

/**
 * Everything saved for one ticker, newest first. Loads 50 rows, then the next 50 each time you scroll to the
 * bottom (so opening it stays quick even with tens of thousands of rows). Rows from the newest save are highlighted.
 */
function TickerView({ symbol, onBack }: { symbol: string; onBack: () => void }) {
  const [kind, setKind] = useState<"quotes" | "spots">("quotes");
  const [rows, setRows] = useState<MarketDataPage["rows"]>([]);
  const [page, setPage] = useState<MarketDataPage | null>(null);
  const [totals, setTotals] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const busy = useRef(false);
  const load = (reset: boolean) => {
    if (busy.current) return;
    busy.current = true; setLoading(true);
    api.marketDataHistory(symbol, kind, reset ? null : page?.next)
      .then((p) => { setPage(p); setRows((r) => (reset ? p.rows : [...r, ...p.rows])); setTotals((t) => ({ ...t, [kind]: p.total })); setErr(null); })
      .catch((e) => setErr(String(e.message ?? e)))
      .finally(() => { busy.current = false; setLoading(false); });
  };
  useEffect(() => { setRows([]); setPage(null); load(true); }, [symbol, kind]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { // the next 50 when the bottom of the list scrolls into view
    const el = end.current;
    if (!el) return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting && page?.next) load(false); }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }); // re-armed after every render so it sees the latest page
  useEffect(() => { // count for the other tab, so both show their size
    const other = kind === "quotes" ? "spots" : "quotes";
    if (totals[other] == null) api.marketDataHistory(symbol, other).then((p) => setTotals((t) => ({ ...t, [other]: p.total }))).catch(() => undefined);
  }, [symbol, kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const latest = page?.latest;
  const isNew = (ts: string) => !!latest && ts.slice(0, 16) === latest;
  return (
    <>
      <button className="ds-back" onClick={onBack}>← All saved data</button>
      <h3>{symbol}</h3>
      <div className="ds-tabs">
        <button className={kind === "quotes" ? "on" : ""} onClick={() => setKind("quotes")}>Option quotes{totals.quotes != null ? ` (${totals.quotes.toLocaleString()})` : ""}</button>
        <button className={kind === "spots" ? "on" : ""} onClick={() => setKind("spots")}>Stock prices{totals.spots != null ? ` (${totals.spots.toLocaleString()})` : ""}</button>
      </div>
      <p className="c3-job">Newest first. <span className="ds-latest-key">Highlighted</span> = the latest save{latest ? ` (${time(latest + ":00Z")})` : ""}. Scroll down for more.</p>
      {err && <p className="ds-err">{err}</p>}
      {kind === "quotes" ? (
        <table className="ds-table">
          <thead><tr><th>Saved</th><th>Expiry</th><th></th><th className="r">Strike</th><th className="r">Bid</th><th className="r">Ask</th><th className="r">IV</th><th className="r">Delta</th></tr></thead>
          <tbody>{rows.map((q) => (
            <tr key={q.id} className={isNew(q.ts) ? "latest" : ""}><td>{time(q.ts)}</td><td>{q.expiry}</td><td>{q.right}</td><td className="r">{q.strike}</td>
              <td className="r">{num(q.bid)}</td><td className="r">{num(q.ask)}</td>
              <td className="r">{q.iv == null ? "–" : `${(q.iv * 100).toFixed(1)}%`}</td><td className="r">{num(q.delta)}</td></tr>
          ))}</tbody>
        </table>
      ) : (
        <table className="ds-table">
          <thead><tr><th>Saved</th><th className="r">Price</th></tr></thead>
          <tbody>{rows.map((x) => <tr key={x.id} className={isNew(x.ts) ? "latest" : ""}><td>{time(x.ts)}</td><td className="r">{num(x.price)}</td></tr>)}</tbody>
        </table>
      )}
      <div ref={end} className="ds-more">
        {loading ? "Loading…" : page?.next ? "Scroll for more" : rows.length ? `All ${rows.length.toLocaleString()} shown` : page ? "Nothing saved for this ticker." : ""}
      </div>
    </>
  );
}

export function DataStorePanel({ onClose }: { onClose: () => void }) {
  const [ticker, setTicker] = useState<string | null>(null);
  const [d, setD] = useState<MarketDataRecent | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [und, setUnd] = useState<string>("");
  useEffect(() => {
    let alive = true;
    const load = () => api.marketDataRecent().then((r) => { if (alive) { setD(r); setErr(null); } }).catch((e) => alive && setErr(String(e.message ?? e)));
    load();
    const t = setInterval(load, 10_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const unds = d ? Object.keys(d.quotes) : [];
  const shown = und && d?.quotes[und] ? und : unds[0] ?? "";
  const s = d?.summary;
  return (
    <aside className="c3-panel ds">
      <button className="c3-x" onClick={onClose} aria-label="Close">×</button>
      {ticker ? <TickerView symbol={ticker} onBack={() => setTicker(null)} /> : <>
      <h3>Saved prices</h3>
      <p className="c3-job">Every price the trader gets from IBKR is stored here for backtests and replays. Refreshes every 10 seconds.</p>
      {err && <p className="ds-err">{err}</p>}
      {!d && !err && <p className="c3-job">Loading…</p>}
      {d && !d.exists && <p className="c3-job">Nothing saved yet.</p>}
      {d?.exists && (
        <>
          <div className="ds-stats">
            <div><b>{ago(d.last_write)}</b><span>last saved</span></div>
            <div><b>{(s?.quotes ?? 0).toLocaleString()}</b><span>option quotes</span></div>
            <div><b>{s?.days ?? 0}</b><span>days</span></div>
            <div><b>{s?.size_mb ?? 0} MB</b><span>on disk</span></div>
          </div>

          <h4>Tickers <span className="ds-hint">click one to see everything saved</span></h4>
          <div className="ds-tabs">
            {[...new Set([...Object.keys(s?.by_underlying ?? {}), ...d.spots.map((x) => x.symbol)])].map((u) => (
              <button key={u} onClick={() => setTicker(u)}>{u}{s?.by_underlying?.[u] != null ? ` · ${s.by_underlying[u].toLocaleString()}` : ""}</button>
            ))}
          </div>

          <h4>Recent saves</h4>
          <table className="ds-table">
            <thead><tr><th>Time</th><th>Ticker</th><th className="r">Quotes</th></tr></thead>
            <tbody>{d.saves.map((x, i) => <tr key={i}><td>{time(x.ts)}</td><td><button className="ds-link" onClick={() => setTicker(x.underlying)}>{x.underlying}</button></td><td className="r">{x.quotes}</td></tr>)}</tbody>
          </table>

          <h4>Latest stock prices</h4>
          <table className="ds-table">
            <thead><tr><th>Time</th><th>Ticker</th><th className="r">Price</th></tr></thead>
            <tbody>{d.spots.slice(0, 8).map((x, i) => <tr key={i}><td>{time(x.ts)}</td><td><button className="ds-link" onClick={() => setTicker(x.symbol)}>{x.symbol}</button></td><td className="r">{num(x.price)}</td></tr>)}</tbody>
          </table>

          <h4>Newest option quotes</h4>
          {unds.length === 0 ? <p className="c3-job">No option quotes yet.</p> : (
            <>
              <div className="ds-tabs">
                {unds.map((u) => <button key={u} className={u === shown ? "on" : ""} onClick={() => setUnd(u)}>{u}</button>)}
                <button className="ds-all" onClick={() => setTicker(shown)}>All {shown} data →</button>
              </div>
              {d.quotes[shown]?.[0] && <p className="c3-job">Saved {time(d.quotes[shown][0].ts)}</p>}
              <table className="ds-table">
                <thead><tr><th>Expiry</th><th>Type</th><th className="r">Strike</th><th className="r">Bid</th><th className="r">Ask</th><th className="r">IV</th><th className="r">Delta</th></tr></thead>
                <tbody>{(d.quotes[shown] ?? []).map((q) => (
                  <tr key={q.symbol}><td>{q.expiry}</td><td>{q.right === "C" ? "Call" : "Put"}</td><td className="r">{q.strike}</td>
                    <td className="r">{num(q.bid)}</td><td className="r">{num(q.ask)}</td>
                    <td className="r">{q.iv == null ? "–" : `${(q.iv * 100).toFixed(1)}%`}</td><td className="r">{num(q.delta)}</td></tr>
                ))}</tbody>
              </table>
            </>
          )}
        </>
      )}
      </>}
    </aside>
  );
}
