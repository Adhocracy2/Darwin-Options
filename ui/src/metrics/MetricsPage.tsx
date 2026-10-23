/**
 * Metrics: high-level market and portfolio readings in three alternative layouts (#/metrics/1-3).
 * Same data and panels in each; only the arrangement differs.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Event, Metrics, NewsItem } from "../api";
import { api } from "../api";
import {
  ago, Charts, Gauges, greekSignals, lifecycleSignals, LogPanel, money, NewsPanel, num, pct, sentimentSignals,
  SignalList, technicalSignals, ToneTag, volatilitySignals, VideoPanel, type Signal,
} from "./panels";
import "./metrics.css";

const VIEWS = [
  { n: 1, name: "Scorecard", blurb: "Every reading with what it means, in a grid" },
  { n: 2, name: "Charts desk", blurb: "Charts first, compact readings beside them" },
  { n: 3, name: "Briefing", blurb: "Today in plain words, then the signals ranked" },
];

function useData() {
  const [m, setM] = useState<Metrics | null>(null);
  const [news, setNews] = useState<NewsItem[] | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const loadMetrics = useCallback(async (fresh = false) => {
    setBusy(true);
    try { setM(await api.metrics(fresh)); setErr(null); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  }, []);
  useEffect(() => {
    loadMetrics();
    const load = () => { api.news().then(setNews).catch(() => setNews([])); api.events().then(setEvents).catch(() => {}); };
    load();
    const a = setInterval(() => loadMetrics(), 120_000), b = setInterval(load, 60_000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [loadMetrics]);
  return { m, news, events, err, busy, refresh: () => loadMetrics(true) };
}

/** Today in one paragraph, from the readings (for the Briefing view). */
function summary(m: Metrics, i: number): string {
  const s = m.symbols[i], v = s.vol, t = s.tech, g = m.portfolio.greeks;
  const parts: string[] = [];
  if (v.iv_rank != null) parts.push(`${s.symbol} options are ${v.iv_rank >= 0.5 ? "expensive" : v.iv_rank < 0.3 ? "cheap" : "fairly priced"} for the year (IV Rank ${pct(v.iv_rank, 0)})`);
  if (v.iv_hv != null) parts.push(`priced for ${v.iv_hv >= 1.2 ? "bigger" : v.iv_hv < 0.9 ? "smaller" : "about the same"} moves than ${s.symbol} is actually making (IV/HV ${num(v.iv_hv)})`);
  let out = parts.length ? `${parts.join(", ")}.` : "";
  if (t.rsi14 != null) out += ` Momentum is ${t.macd && t.macd.hist < 0 ? "fading" : "building"} and RSI ${num(t.rsi14, 0)} is ${t.rsi14 > 70 ? "overbought" : t.rsi14 < 30 ? "oversold" : "neutral"}; the trend is ${t.trend ?? "unclear"}.`;
  if (g) out += ` Your positions earn about ${money(g.theta)} a day from time decay and ${g.delta < 0 ? "gain if SPY falls" : "gain if SPY rises"} (delta ${num(g.delta, 1)}).`;
  return out.trim();
}

export default function MetricsPage() {
  const initial = Number(location.hash.match(/#\/metrics\/(\d)/)?.[1] ?? 1);
  const [n, setN] = useState(VIEWS.some((v) => v.n === initial) ? initial : 1);
  const [sym, setSym] = useState(0);
  const { m, news, events, err, busy, refresh } = useData();
  useEffect(() => { history.replaceState(null, "", `#/metrics/${n}`); }, [n]);
  useEffect(() => {
    const h = () => { const v = Number(location.hash.match(/#\/metrics\/(\d)/)?.[1]); if (VIEWS.some((x) => x.n === v)) setN(v); };
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  const s = m?.symbols[sym] ?? m?.symbols[0];
  const all: { group: string; sig: Signal }[] = useMemo(() => !m || !s ? [] : [
    ...volatilitySignals(s).map((sig) => ({ group: "Volatility", sig })),
    ...greekSignals(m).map((sig) => ({ group: "Greeks", sig })),
    ...sentimentSignals(s).map((sig) => ({ group: "Sentiment", sig })),
    ...technicalSignals(s).map((sig) => ({ group: "Technicals", sig })),
    ...lifecycleSignals(m).map((sig) => ({ group: "Portfolio", sig })),
  ], [m, s]);

  return (
    <div className="mx-root">
      <header className="mx-bar">
        <a className="mx-back" href="#/">← Dashboard</a>
        <div className="mx-title">Darwin <span>Metrics</span></div>
        <nav className="mx-switch">
          {VIEWS.map((v) => <button key={v.n} className={v.n === n ? "on" : ""} onClick={() => setN(v.n)} title={v.blurb}><span>{v.n}</span>{v.name}</button>)}
        </nav>
        {m && m.symbols.length > 1 && (
          <nav className="mx-switch">{m.symbols.map((x, i) => <button key={x.symbol} className={i === sym ? "on" : ""} onClick={() => setSym(i)}>{x.symbol}</button>)}</nav>
        )}
        <div className="mx-asof">
          {m && <>Updated {ago(m.as_of)}{s?.spot ? ` · ${s.symbol} $${num(s.spot)}` : ""}</>}
          <button onClick={refresh} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button>
        </div>
      </header>
      {err && <div className="mx-error">{err}</div>}
      {!m || !s ? <p className="mx-loading">{err ? "Couldn't load metrics." : "Loading metrics… (the first load asks IBKR for a year of history)"}</p> : (
        <main className={`mx-view v${n}`}>
          {n === 1 && (
            <>
              <div className="mx-grid">
                <Gauges s={s} />
                <SignalList title="Volatility" signals={volatilitySignals(s)} />
                <SignalList title="The Greeks (your open positions)" signals={greekSignals(m)} />
                <SignalList title="Positioning & sentiment" signals={sentimentSignals(s)} />
                <SignalList title="Technicals & momentum" signals={technicalSignals(s)} />
                <SignalList title="Portfolio & lifecycle" signals={lifecycleSignals(m)} />
              </div>
              <div className="mx-grid three">
                <LogPanel events={events} />
                <NewsPanel news={news} />
                <VideoPanel />
              </div>
            </>
          )}
          {n === 2 && (
            <div className="mx-desk">
              <div className="mx-desk-main">
                <Charts s={s} />
                <div className="mx-grid two"><NewsPanel news={news} limit={8} /><LogPanel events={events} limit={8} /></div>
              </div>
              <aside className="mx-desk-side">
                <Gauges s={s} />
                <SignalList compact title="Volatility" signals={volatilitySignals(s)} />
                <SignalList compact title="Greeks" signals={greekSignals(m)} />
                <SignalList compact title="Sentiment" signals={sentimentSignals(s)} />
                <SignalList compact title="Technicals" signals={technicalSignals(s)} />
                <SignalList compact title="Portfolio" signals={lifecycleSignals(m)} />
                <VideoPanel />
              </aside>
            </div>
          )}
          {n === 3 && (
            <div className="mx-brief">
              <section className="mx-panel mx-lead"><h3>Today in short</h3><p>{summary(m, sym)}</p></section>
              <section className="mx-panel"><h3>Signals, most important first</h3>
                <ul className="mx-ranked">
                  {[...all].sort((a, b) => ({ warn: 0, good: 1, neutral: 2 }[a.sig.tone] - { warn: 0, good: 1, neutral: 2 }[b.sig.tone])).map(({ group, sig }) => (
                    <li key={group + sig.label}><ToneTag tone={sig.tone} /><span className="mx-group">{group}</span>
                      <div><span className="mx-sig-label">{sig.label}</span> <b>{sig.value}</b><p>{sig.meaning}</p></div></li>
                  ))}
                </ul>
              </section>
              <div className="mx-grid two"><Charts s={s} which={["price"]} /><Charts s={s} which={["vol"]} /></div>
              <div className="mx-grid three"><NewsPanel news={news} /><LogPanel events={events} /><VideoPanel /></div>
            </div>
          )}
          {m.unavailable.length > 0 && (
            <details className="mx-missing"><summary>Not available ({m.unavailable.length})</summary>
              <ul>{m.unavailable.map((u) => <li key={u}>{u}</li>)}</ul></details>
          )}
          <p className="mx-legend-tones"><ToneTag tone="good" /> favours Darwin's premium selling · <ToneTag tone="warn" /> caution · <ToneTag tone="neutral" /> neutral or for information</p>
        </main>
      )}
    </div>
  );
}
