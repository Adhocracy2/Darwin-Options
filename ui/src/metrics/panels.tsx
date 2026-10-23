/**
 * Panels shared by the three Metrics views: each metric with its number and a one-line meaning
 * written for how Darwin trades (it sells option premium: credit spreads, condors).
 */
import { useEffect, useState } from "react";
import type { Event, Metrics, NewsItem, SymbolMetrics } from "../api";
import { LineChart, Meter, ZeroBars } from "./charts";

export const pct = (v: number | null | undefined, d = 1) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`);
export const num = (v: number | null | undefined, d = 2) => (v == null ? "–" : v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }));
export const money = (v: number | null | undefined, d = 2) => (v == null ? "–" : `${v < 0 ? "−" : v > 0 ? "+" : ""}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}`);
/** A plain dollar amount, no sign (for "lose about $13", "$4 in fees"). */
const usd = (v: number | null | undefined, d = 2) => (v == null ? "–" : `$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}`);
const big = (v: number | null | undefined) => (v == null ? "–" : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : v.toFixed(0));
export const ago = (ts: string | null | undefined) => {
  if (!ts) return "";
  const m = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
  return m < 60 ? `${Math.max(m, 0)}m ago` : m < 2880 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
};

/** A reading: good (favours Darwin's selling), caution, or neutral. Always icon + words, never colour alone. */
export type Tone = "good" | "warn" | "neutral";
export type Signal = { label: string; value: string; meaning: string; tone: Tone };
const ICON: Record<Tone, string> = { good: "▲", warn: "!", neutral: "•" };
export const ToneTag = ({ tone }: { tone: Tone }) =>
  <span className={`mx-tone ${tone}`} aria-label={tone === "good" ? "favourable" : tone === "warn" ? "caution" : "neutral"}>{ICON[tone]}</span>;

export function volatilitySignals(s: SymbolMetrics): Signal[] {
  const v = s.vol;
  const out: Signal[] = [
    { label: "Implied volatility (IV)", value: pct(v.iv), tone: "neutral",
      meaning: v.iv == null ? "No reading." : `The market expects ${s.symbol} to move about ${pct(v.iv / Math.sqrt(12), 1)} in a month (1 standard deviation).` },
    { label: "Historical volatility (20 days)", value: pct(v.hv20), tone: "neutral",
      meaning: v.hv20 == null ? "No reading." : `${s.symbol} actually moved at ${pct(v.hv20)} a year over the last 20 trading days.` },
  ];
  if (v.iv_rank != null) out.push({ label: "IV Rank", value: pct(v.iv_rank, 0),
    tone: v.iv_rank >= 0.5 ? "good" : v.iv_rank < 0.3 ? "warn" : "neutral",
    meaning: v.iv_rank >= 0.5 ? "Options are expensive versus the past year: good for selling premium."
      : v.iv_rank < 0.3 ? "Options are cheap versus the past year: selling earns less for the same risk." : "Option prices are middling for the year." });
  if (v.iv_percentile != null) out.push({ label: "IV Percentile", value: pct(v.iv_percentile, 0),
    tone: v.iv_percentile >= 0.6 ? "good" : v.iv_percentile < 0.3 ? "warn" : "neutral",
    meaning: `IV was lower than today on ${pct(v.iv_percentile, 0)} of the past year's trading days.` });
  if (v.iv_hv != null) out.push({ label: "IV / HV ratio", value: num(v.iv_hv), tone: v.iv_hv >= 1.2 ? "good" : v.iv_hv < 0.9 ? "warn" : "neutral",
    meaning: v.iv_hv >= 1.2 ? "Options are priced for bigger moves than the stock is making: favours sellers."
      : v.iv_hv < 0.9 ? "Options are priced for smaller moves than the stock is making: risky to sell." : "Option prices roughly match actual moves." });
  return out;
}

export function technicalSignals(s: SymbolMetrics): Signal[] {
  const t = s.tech, out: Signal[] = [];
  if (t.rsi14 != null) out.push({ label: "RSI (14 days)", value: num(t.rsi14, 0),
    tone: t.rsi14 > 70 || t.rsi14 < 30 ? "warn" : "neutral",
    meaning: t.rsi14 > 70 ? "Overbought: a pullback is more likely than usual." : t.rsi14 < 30 ? "Oversold: a bounce is more likely than usual." : "Neither overbought nor oversold." });
  if (t.bollinger) {
    const b = t.bollinger;
    out.push({ label: "Bollinger Bands", value: b.pct_b == null ? "–" : `${num(b.pct_b * 100, 0)}% of band`,
      tone: b.pct_b != null && (b.pct_b > 0.95 || b.pct_b < 0.05) ? "warn" : "neutral",
      meaning: `${b.pct_b != null && b.pct_b < 0.2 ? "Near the lower band." : b.pct_b != null && b.pct_b > 0.8 ? "Near the upper band." : "Inside the bands."} Band width ${pct(b.width)}${b.width != null && b.width < 0.03 ? ": a squeeze, a breakout often follows" : ""}.` });
  }
  if (t.macd) out.push({ label: "MACD", value: num(t.macd.hist), tone: "neutral",
    meaning: t.macd.hist > 0 ? "Above its signal line: momentum is turning up." : "Below its signal line: momentum is turning down." });
  if (t.trend) out.push({ label: "Moving averages", value: t.trend === "up" ? "Uptrend" : t.trend === "down" ? "Downtrend" : "Mixed",
    tone: t.trend === "mixed" ? "neutral" : "good",
    meaning: `Price ${num(t.price)} vs 20-day ${num(t.sma20)}, 50-day ${num(t.sma50)}, 200-day ${num(t.sma200)}.` });
  return out;
}

export function sentimentSignals(s: SymbolMetrics): Signal[] {
  const x = s.sentiment, out: Signal[] = [];
  out.push({ label: "Open interest (all options)", value: x.call_oi == null ? "–" : `${big(x.call_oi)} calls · ${big(x.put_oi)} puts`, tone: "neutral",
    meaning: "Open option contracts on the whole chain. Rising open interest with a move means the move has conviction." });
  if (x.pcr_oi != null) out.push({ label: "Put-call ratio (open interest)", value: num(x.pcr_oi),
    tone: x.pcr_oi > 1.5 ? "warn" : "neutral",
    meaning: x.pcr_oi > 1.5 ? "Many more puts than calls: heavy hedging or bearish positioning (on SPY, hedging is normal)."
      : x.pcr_oi < 0.7 ? "More calls than puts: bullish positioning." : "Balanced positioning." });
  out.push({ label: "Put-call ratio (today's volume)", value: x.pcr_volume == null ? "–" : num(x.pcr_volume), tone: "neutral",
    meaning: x.pcr_volume == null ? "Not reported right now (usually outside market hours)." : "Today's trading: above 1 means more puts traded than calls." });
  out.push({ label: "Order flow, blocks and sweeps", value: "Not available", tone: "neutral",
    meaning: "Needs a paid tick-by-tick feed (e.g. Market Chameleon). Not connected." });
  return out;
}

export function greekSignals(m: Metrics): Signal[] {
  const g = m.portfolio.greeks;
  if (!g) return [{ label: "Portfolio Greeks", value: "–", tone: "neutral", meaning: "Couldn't price the open positions." }];
  return [
    { label: "Delta Δ", value: num(g.delta, 1), tone: Math.abs(g.delta) > 50 ? "warn" : "neutral",
      meaning: `${g.delta < 0 ? "Gains if SPY falls" : "Gains if SPY rises"}: about ${usd(g.delta)} per $1 move (like ${num(Math.abs(g.delta), 0)} shares${g.delta < 0 ? " short" : ""}).` },
    { label: "Theta Θ", value: money(g.theta), tone: g.theta > 0 ? "good" : "warn",
      meaning: g.theta > 0 ? `Time decay earns about ${money(g.theta)} a day if nothing else changes.` : `Time decay costs about ${usd(g.theta)} a day.` },
    { label: "Vega ν", value: money(g.vega), tone: "neutral",
      meaning: `If implied volatility rises 1 point, the positions ${g.vega < 0 ? "lose" : "gain"} about ${usd(g.vega)}.` },
    { label: "Gamma Γ", value: num(g.gamma, 2), tone: "neutral",
      meaning: `Delta changes by ${num(g.gamma, 2)} for each $1 SPY move${g.gamma < 0 ? ": sharp moves hurt more as they grow" : ""}.` },
    { label: "Rho ρ", value: money(g.rho), tone: "neutral",
      meaning: `A 1 percentage point rise in interest rates changes the value by about ${money(g.rho)}.` },
  ];
}

export function lifecycleSignals(m: Metrics): Signal[] {
  const l = m.portfolio.lifecycle;
  return [
    { label: "Average DTE at entry", value: l.avg_dte_at_entry == null ? "–" : `${l.avg_dte_at_entry} days`, tone: "neutral", meaning: "How far out trades are opened." },
    { label: "Average delta at entry", value: num(l.avg_delta_at_entry, 2), tone: l.avg_delta_at_entry != null && l.avg_delta_at_entry > 0.25 ? "warn" : "neutral",
      meaning: l.avg_delta_at_entry != null && l.avg_delta_at_entry > 0.25 ? "Short strikes are fairly close: they get threatened often (simulation favoured 0.10–0.16)." : "Distance of short strikes from the price." },
    { label: "Assignments / early exercise", value: `${l.assignments} / ${l.early_exercises}`, tone: "good", meaning: "Shorts are closed before expiry, so assignment should stay at zero." },
    { label: "Net P&L (after fees)", value: money(l.realized_net), tone: l.realized_net >= 0 ? "good" : "neutral", meaning: `Realised so far, after ${usd(l.fees)} in fees, on ${l.closed} closed of ${l.trades} trades.` },
    { label: "Annualised ROI", value: l.annualized_roi == null ? "–" : pct(l.annualized_roi), tone: "neutral",
      meaning: l.annualized_roi == null ? `Shown after 30 days of trading (${l.days_trading ?? 0} so far): earlier, it's noise.` : "Return on the $10k account, scaled to a year." },
  ];
}

/** A titled list of readings. */
export function SignalList({ title, signals, compact }: { title: string; signals: Signal[]; compact?: boolean }) {
  return (
    <section className={`mx-panel ${compact ? "compact" : ""}`}>
      <h3>{title}</h3>
      <ul className="mx-signals">
        {signals.map((s) => (
          <li key={s.label}><ToneTag tone={s.tone} /><div><span className="mx-sig-label">{s.label}</span><b>{s.value}</b>
            {!compact && <p>{s.meaning}</p>}</div></li>
        ))}
      </ul>
    </section>
  );
}

/** Price with Bollinger bands, RSI, MACD histogram and IV vs HV history. */
export function Charts({ s, which = ["price", "rsi", "macd", "vol"] }: { s: SymbolMetrics; which?: string[] }) {
  const p = s.tech.price_series ?? [];
  const dates = p.map((x) => x.d);
  const vd = s.vol.iv_history.map((x) => x.d);
  const hvBy = new Map(s.vol.hv_history.map((x) => [x.d, x.hv]));
  return (
    <>
      {which.includes("price") && p.length > 0 && (
        <section className="mx-panel"><h3>{s.symbol} price and Bollinger Bands (6 months)</h3>
          <LineChart label={`${s.symbol} price with Bollinger bands`} dates={dates} fmt={(v) => `$${v.toFixed(0)}`}
            series={[{ name: "Price", color: "var(--series-1)", values: p.map((x) => x.close) }]}
            band={{ name: "Bollinger band (20d, 2σ)", lower: p.map((x) => x.lower), upper: p.map((x) => x.upper) }} />
        </section>
      )}
      {which.includes("rsi") && p.length > 0 && (
        <section className="mx-panel"><h3>RSI (14 days)</h3>
          <LineChart label="RSI" dates={dates} height={130} fmt={(v) => v.toFixed(0)} refs={[{ y: 70, label: "70 overbought" }, { y: 30, label: "30 oversold" }]}
            series={[{ name: "RSI", color: "var(--series-1)", values: p.map((x) => x.rsi) }]} />
        </section>
      )}
      {which.includes("macd") && p.length > 0 && (
        <section className="mx-panel"><h3>MACD histogram (momentum)</h3>
          <ZeroBars label="MACD histogram" dates={dates} values={p.map((x) => x.macd_hist)} fmt={(v) => v.toFixed(1)} />
        </section>
      )}
      {which.includes("vol") && vd.length > 0 && (
        <section className="mx-panel"><h3>Implied vs historical volatility (1 year)</h3>
          <LineChart label="Implied and historical volatility" dates={vd} fmt={(v) => `${(v * 100).toFixed(0)}%`}
            series={[{ name: "Implied (IV)", color: "var(--series-1)", values: s.vol.iv_history.map((x) => x.iv) },
                     { name: "Historical (HV)", color: "var(--series-2)", values: vd.map((d) => hvBy.get(d) ?? null) }]} />
        </section>
      )}
    </>
  );
}

export function Gauges({ s }: { s: SymbolMetrics }) {
  return (
    <section className="mx-panel"><h3>{s.symbol} at a glance</h3>
      <div className="mx-gauges">
        <div><span>IV Rank</span><b>{pct(s.vol.iv_rank, 0)}</b><Meter label="IV Rank" value={s.vol.iv_rank == null ? null : s.vol.iv_rank * 100} zones={[{ to: 30, label: "cheap" }, { to: 50, label: "avg" }, { to: 100, label: "rich" }]} /></div>
        <div><span>IV Percentile</span><b>{pct(s.vol.iv_percentile, 0)}</b><Meter label="IV Percentile" value={s.vol.iv_percentile == null ? null : s.vol.iv_percentile * 100} zones={[{ to: 30, label: "low" }, { to: 60, label: "mid" }, { to: 100, label: "high" }]} /></div>
        <div><span>RSI</span><b>{num(s.tech.rsi14, 0)}</b><Meter label="RSI" value={s.tech.rsi14} zones={[{ to: 30, label: "oversold" }, { to: 70, label: "neutral" }, { to: 100, label: "overbought" }]} /></div>
      </div>
    </section>
  );
}

export function LogPanel({ events, limit = 12 }: { events: Event[]; limit?: number }) {
  return (
    <section className="mx-panel"><h3>Log</h3>
      {events.length === 0 ? <p className="muted">Nothing logged yet.</p> : (
        <ul className="mx-log">
          {events.slice(0, limit).map((e) => (
            <li key={e.id}><span className={`mx-kind ${e.kind}`}>{e.kind.replace("_", " ")}</span><span className="mx-log-msg">{e.message}</span><span className="muted">{ago(e.ts)}</span></li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function NewsPanel({ news, limit = 10 }: { news: NewsItem[] | null; limit?: number }) {
  return (
    <section className="mx-panel"><h3>News</h3>
      {news == null ? <p className="muted">Loading…</p> : news.length === 0 ? <p className="muted">No headlines right now.</p> : (
        <ul className="mx-news">
          {news.slice(0, limit).map((n, i) => (
            <li key={i}>
              {n.link ? <a href={n.link} target="_blank" rel="noopener noreferrer">{n.title}</a> : <span>{n.title}</span>}
              <span className="muted">{n.source} · {ago(n.ts)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const PRESETS = [
  { name: "Bloomberg TV", id: "UCIALMKvObZNtJ6AmdCLP7Lg" },
  { name: "Yahoo Finance", id: "UCEAZeUIeJs0IjQiqTCdVSIg" },
  { name: "CNBC", id: "UCrp_UI8XtuYfpiqluWLD7Lw" },
];

/** YouTube URL or channel -> embeddable URL. Accepts watch?v=, youtu.be/, /live/, /shorts/ links. */
function embedUrl(src: string): string | null {
  if (src.startsWith("channel:")) return `https://www.youtube.com/embed/live_stream?channel=${encodeURIComponent(src.slice(8))}&autoplay=0`;
  try {
    const u = new URL(src);
    let id = u.searchParams.get("v");
    if (!id && u.hostname.endsWith("youtu.be")) id = u.pathname.slice(1);
    if (!id) id = u.pathname.match(/\/(?:live|shorts|embed)\/([\w-]{6,})/)?.[1] ?? null;
    return id && /^[\w-]{6,}$/.test(id) ? `https://www.youtube-nocookie.com/embed/${id}` : null;
  } catch { return null; }
}

export function VideoPanel() {
  const KEY = "darwin.metrics.video";
  const [src, setSrc] = useState<string>(() => { try { return localStorage.getItem(KEY) ?? `channel:${PRESETS[0].id}`; } catch { return `channel:${PRESETS[0].id}`; } });
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { try { localStorage.setItem(KEY, src); } catch { /* private window */ } }, [src]);
  const url = embedUrl(src);
  return (
    <section className="mx-panel"><h3>Video</h3>
      <div className="mx-video">{url ? <iframe src={url} title="YouTube video" allow="encrypted-media; picture-in-picture; fullscreen" allowFullScreen /> : <p className="muted">No video.</p>}</div>
      <div className="mx-video-bar">
        {PRESETS.map((p) => <button key={p.id} className={src === `channel:${p.id}` ? "on" : ""} onClick={() => setSrc(`channel:${p.id}`)}>{p.name} live</button>)}
        <form onSubmit={(e) => { e.preventDefault(); if (embedUrl(draft)) { setSrc(draft); setDraft(""); setErr(null); } else setErr("Paste a YouTube video link."); }}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Paste a YouTube link…" />
          <button type="submit">Play</button>
        </form>
      </div>
      {err && <p className="mx-err">{err}</p>}
      <p className="muted small">Live channels only play while that channel is broadcasting.</p>
    </section>
  );
}
