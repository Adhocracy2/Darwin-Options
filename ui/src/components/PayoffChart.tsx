import { useEffect, useMemo, useRef, useState } from "react";
import type { Payoff } from "../api";
import { signed, usd } from "../api";
import { TradeSummary } from "./TradeSummary";

const PAD = { l: 60, r: 14, t: 22, b: 30 };

/** Round step for ~n ticks across a span. */
function niceStep(span: number, n: number) {
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
}

function ticks(lo: number, hi: number, n: number) {
  const step = niceStep(hi - lo, n);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

/** Prices in text: always cents. Axis ticks: whole dollars when the tick is whole. */
const price = (v: number) => `$${v.toFixed(2)}`;
const tickPrice = (v: number) => `$${Number.isInteger(v) ? v : v.toFixed(2)}`;

/**
 * Payoff diagram: P&L across underlying prices at expiry (solid) and at the
 * planned exit date (dashed). Profit/loss regions are shaded, the current
 * price is marked, and hovering shows exact values.
 */
export function PayoffChart({ p, height = 240 }: { p: Payoff; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [W, setW] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const H = height;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const g = useMemo(() => {
    const xs = p.prices;
    const all = [...p.expiry, ...p.exit, 0];
    let lo = Math.min(...all), hi = Math.max(...all);
    const pad = (hi - lo || 100) * 0.12;
    lo -= pad; hi += pad;
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const x = (v: number) => PAD.l + ((v - x0) / (x1 - x0)) * (W - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
    const line = (ys: number[]) => ys.map((v, i) => `${i ? "L" : "M"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const area = `${line(p.expiry)}L${x(x1).toFixed(1)},${y(0).toFixed(1)}L${x(x0).toFixed(1)},${y(0).toFixed(1)}Z`;
    return { x, y, lo, hi, x0, x1, expiry: line(p.expiry), exit: line(p.exit), area,
             yt: ticks(lo, hi, 4), xt: ticks(x0, x1, W < 480 ? 4 : 6) };
  }, [p, W, H]);

  const onMove = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const v = g.x0 + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (g.x1 - g.x0);
    let best = 0;
    p.prices.forEach((q, i) => { if (Math.abs(q - v) < Math.abs(p.prices[best] - v)) best = i; });
    setHover(best);
  };

  const zeroY = g.y(0);
  const clipId = useMemo(() => `c${Math.random().toString(36).slice(2, 8)}`, []);
  const exitLabel = p.exit_dte > 0 ? `At exit (${p.exit_dte} DTE, ${p.exit_date})` : `Today`;
  const expiryLabel = p.has_later_legs ? `At front expiry (${p.expiry_date})` : `At expiry (${p.expiry_date})`;
  const hx = hover != null ? g.x(p.prices[hover]) : 0;

  return (
    <div className="payoff">
      <div className="payoff-stats">
        <span>Max profit <b className="pos">{p.max_profit == null ? "Unlimited" : signed(p.max_profit)}</b></span>
        <span>Max loss <b className="neg">{signed(p.max_loss)}</b></span>
        <span>Break-even <b>{p.breakevens.length ? p.breakevens.map(price).join(" / ") : "–"}</b></span>
        <span>{p.example ? "Price" : "Now"} <b>{price(p.spot)}</b>{!p.example && <> · P&amp;L today <b>{signed(p.pnl_now)}</b></>}</span>
      </div>
      <div className="legend small">
        <span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke="var(--series-1)" strokeWidth="2" /></svg>{expiryLabel}</span>
        <span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke="var(--series-2)" strokeWidth="2" strokeDasharray="5 3" /></svg>{exitLabel}</span>
      </div>
      <div className="chart" ref={box}>
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={`Payoff: max profit ${p.max_profit == null ? "unlimited" : usd(p.max_profit)}, max loss ${usd(p.max_loss)}, break-even ${p.breakevens.map(price).join(" and ") || "none"}`}
             onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          <defs>
            <clipPath id={`${clipId}-up`}><rect x="0" y="0" width={W} height={Math.max(0, zeroY)} /></clipPath>
            <clipPath id={`${clipId}-dn`}><rect x="0" y={zeroY} width={W} height={Math.max(0, H - zeroY)} /></clipPath>
          </defs>
          {g.yt.map((t) => (
            <g key={`y${t}`}>
              <line x1={PAD.l} x2={W - PAD.r} y1={g.y(t)} y2={g.y(t)} stroke="var(--grid)" />
              <text x={PAD.l - 6} y={g.y(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)">{usd(t)}</text>
            </g>
          ))}
          {g.xt.map((t) => (
            <text key={`x${t}`} x={g.x(t)} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--text-muted)">{tickPrice(t)}</text>
          ))}
          <path d={g.area} fill="var(--good)" opacity="0.14" clipPath={`url(#${clipId}-up)`} />
          <path d={g.area} fill="var(--critical)" opacity="0.12" clipPath={`url(#${clipId}-dn)`} />
          <line x1={PAD.l} x2={W - PAD.r} y1={zeroY} y2={zeroY} stroke="var(--text-muted)" />
          {p.strikes.map((k) => (
            <line key={`k${k}`} x1={g.x(k)} x2={g.x(k)} y1={H - PAD.b} y2={H - PAD.b + 5} stroke="var(--text-secondary)" />
          ))}
          <line x1={g.x(p.spot)} x2={g.x(p.spot)} y1={PAD.t - 6} y2={H - PAD.b} stroke="var(--text-secondary)" strokeDasharray="2 3" />
          <text x={g.x(p.spot)} y={PAD.t - 9} textAnchor="middle" fontSize="11" fill="var(--text-secondary)">
            {p.example ? "Price" : "Now"} {price(p.spot)}
          </text>
          <path d={g.exit} fill="none" stroke="var(--series-2)" strokeWidth="2" strokeDasharray="6 4" strokeLinejoin="round" />
          <path d={g.expiry} fill="none" stroke="var(--series-1)" strokeWidth="2" strokeLinejoin="round" />
          {p.breakevens.map((b) => (
            <circle key={`b${b}`} cx={g.x(b)} cy={zeroY} r="4" fill="var(--surface-1)" stroke="var(--text-primary)" strokeWidth="1.5" />
          ))}
          {hover != null && (
            <>
              <line x1={hx} x2={hx} y1={PAD.t} y2={H - PAD.b} stroke="var(--text-muted)" strokeDasharray="3 3" />
              <circle cx={hx} cy={g.y(p.exit[hover])} r="4.5" fill="var(--series-2)" stroke="var(--surface-1)" strokeWidth="2" />
              <circle cx={hx} cy={g.y(p.expiry[hover])} r="4.5" fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth="2" />
            </>
          )}
        </svg>
        {hover != null && (
          <div className="tooltip" style={{ left: `${(hx / W) * 100}%`, top: 24,
                                            transform: hx > W / 2 ? "translateX(calc(-100% - 10px))" : "translateX(10px)" }}>
            <div className="muted">{p.underlying ?? "Price"} at {price(p.prices[hover])}</div>
            <div><b>{signed(p.expiry[hover])}</b> {p.has_later_legs ? "at front expiry" : "at expiry"}</div>
            <div><b>{signed(p.exit[hover])}</b> {p.exit_dte > 0 ? `at exit (${p.exit_dte} DTE)` : "today"}</div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Loads a payoff and renders it, with loading and error states. With `refreshMs` it reloads on
 *  that interval and keeps showing the last result meanwhile. Open positions also get a summary.
 *  `canFetchLatest` adds a button that asks IBKR for new prices instead of using saved ones. */
export function PayoffPanel({ load, height, refreshMs, canFetchLatest }: {
  load: (fresh?: boolean) => Promise<Payoff>; height?: number; refreshMs?: number; canFetchLatest?: boolean;
}) {
  const [p, setP] = useState<Payoff | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [at, setAt] = useState<Date | null>(null);
  const [fetching, setFetching] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const alive = useRef(true);
  const get = (fresh = false) => load(fresh)
    .then((v) => {
      if (!alive.current) return v;
      setP(v); setErr(null); setAt(new Date());
      return v;
    })
    .catch((e) => { if (alive.current) setErr(e instanceof Error ? e.message : String(e)); return null; });
  useEffect(() => {
    alive.current = true;
    get();
    const t = refreshMs ? setInterval(() => get(), refreshMs) : undefined;
    return () => { alive.current = false; if (t) clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const fetchLatest = async () => {
    setFetching(true); setNote(null);
    const asked = Date.now();
    const v = await get(true);
    if (alive.current) {
      setFetching(false);
      // IBKR had nothing newer (market closed, gateway down): say so instead of looking stuck
      if (v?.prices_at && new Date(v.prices_at).getTime() < asked - 60_000) setNote("IB had no newer prices.");
    }
  };
  if (err && !p) return <p className="neg small">Couldn't load payoff: {err}</p>;
  if (!p) return <p className="muted small">Loading payoff…</p>;
  return (
    <>
      <PayoffChart p={p} height={height} />
      {p.summary && <TradeSummary s={p.summary} underlying={p.underlying ?? ""} />}
      {(p.prices_at || (refreshMs && at) || canFetchLatest) && (
        <div className="prices-row small">
          <span className="muted">
            {p.prices_at && <PricesAt ts={p.prices_at} />}
            {refreshMs && at && <> · checked {at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</>}
            {note && <> · {note}</>}
            {err ? ` · last refresh failed: ${err}` : ""}
          </span>
          {canFetchLatest && (
            <button type="button" className="btn secondary" disabled={fetching} onClick={fetchLatest}>
              {fetching ? "Getting prices…" : "Get latest prices"}
            </button>
          )}
        </div>
      )}
    </>
  );
}

/** "Prices from 10:42", with a warning when they're old (market closed, trader or gateway down). */
function PricesAt({ ts }: { ts: string }) {
  const t = new Date(ts);
  const old = Date.now() - t.getTime() > 10 * 60_000;
  const when = t.toLocaleString([], old ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
  return old
    ? <span className="warn-text">Prices from {when}: no newer prices (market closed, or the trader or IB Gateway is down)</span>
    : <>Prices from {when}</>;
}
