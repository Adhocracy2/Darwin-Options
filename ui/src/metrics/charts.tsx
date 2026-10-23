/**
 * Small SVG charts for the Metrics views. One y-axis per chart (never two), thin marks, recessive
 * grid, a crosshair + tooltip on hover, legend when there are 2+ series. Colours come from the
 * dashboard's tokens so light and dark mode both work.
 */
import { useEffect, useMemo, useRef, useState } from "react";

const PAD = { l: 48, r: 12, t: 12, b: 24 };

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(480);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceTicks(lo: number, hi: number, n = 4) {
  const span = hi - lo || 1, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(8));
  return out;
}

const shortDate = (d: string, year = false) => {
  const t = new Date(d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d);
  return isNaN(t.getTime()) ? d : t.toLocaleDateString("en-US", year ? { month: "short", day: "numeric", year: "numeric" } : { month: "short", day: "numeric" });
};

export type Series = { name: string; color: string; values: (number | null)[]; dashed?: boolean };

/** Line chart over dates, with an optional shaded band (e.g. Bollinger) and reference lines. */
export function LineChart({ dates, series, band, refs = [], fmt, height = 180, label }: {
  dates: string[]; series: Series[]; band?: { lower: (number | null)[]; upper: (number | null)[]; name: string };
  refs?: { y: number; label: string }[]; fmt: (v: number) => string; height?: number; label: string;
}) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const H = height;
  const g = useMemo(() => {
    const all = [...series.flatMap((s) => s.values), ...(band ? [...band.lower, ...band.upper] : []), ...refs.map((r) => r.y)]
      .filter((v): v is number => v != null);
    let lo = Math.min(...all), hi = Math.max(...all);
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
    lo -= pad; hi += pad;
    const n = dates.length;
    const x = (i: number) => PAD.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - PAD.l - PAD.r));
    const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
    const path = (vals: (number | null)[]) => {
      let d = "", pen = false;
      vals.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    };
    let area = "";
    if (band) {
      const idx = band.upper.map((v, i) => (v != null && band.lower[i] != null ? i : -1)).filter((i) => i >= 0);
      if (idx.length) {
        area = idx.map((i, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(band.upper[i]!).toFixed(1)}`).join("")
          + [...idx].reverse().map((i) => `L${x(i).toFixed(1)},${y(band.lower[i]!).toFixed(1)}`).join("") + "Z";
      }
    }
    const xt = [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => n > 0 && a.indexOf(v) === i);
    return { x, y, path, area, yt: niceTicks(lo, hi), xt };
  }, [dates, series, band, refs, W, H]);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const n = dates.length;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  };
  const hx = hover != null ? g.x(hover) : 0;

  return (
    <div className="mx-chart" ref={box}>
      {series.length > 1 && (
        <div className="mx-legend">
          {series.map((s) => <span key={s.name}><i style={{ background: s.color }} className={s.dashed ? "dash" : ""} />{s.name}</span>)}
          {band && <span><i className="band" />{band.name}</span>}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {g.yt.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={g.y(t)} y2={g.y(t)} stroke="var(--grid)" />
            <text x={PAD.l - 6} y={g.y(t) + 4} textAnchor="end" className="mx-tick">{fmt(t)}</text>
          </g>
        ))}
        {g.xt.map((i) => (
          <text key={i} x={g.x(i)} y={H - 6} textAnchor={i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle"} className="mx-tick">{shortDate(dates[i], i === 0 || i === dates.length - 1)}</text>
        ))}
        {g.area && <path d={g.area} fill="var(--series-1)" opacity="0.1" />}
        {refs.map((r) => (
          <g key={r.label}>
            <line x1={PAD.l} x2={W - PAD.r} y1={g.y(r.y)} y2={g.y(r.y)} stroke="var(--text-muted)" strokeDasharray="4 4" />
            <text x={W - PAD.r} y={g.y(r.y) - 4} textAnchor="end" className="mx-tick">{r.label}</text>
          </g>
        ))}
        {series.map((s) => (
          <path key={s.name} d={g.path(s.values)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round"
            strokeDasharray={s.dashed ? "5 4" : undefined} />
        ))}
        {hover != null && (
          <>
            <line x1={hx} x2={hx} y1={PAD.t} y2={H - PAD.b} stroke="var(--text-muted)" strokeDasharray="3 3" />
            {series.map((s) => s.values[hover] != null && (
              <circle key={s.name} cx={hx} cy={g.y(s.values[hover]!)} r="4.5" fill={s.color} stroke="var(--surface-1)" strokeWidth="2" />
            ))}
          </>
        )}
      </svg>
      {hover != null && (
        <div className="mx-tip" style={{ left: `${(hx / W) * 100}%`, transform: hx > W / 2 ? "translateX(calc(-100% - 10px))" : "translateX(10px)" }}>
          <div className="muted">{shortDate(dates[hover], true)}</div>
          {series.map((s) => s.values[hover] != null && <div key={s.name}><i style={{ background: s.color }} />{s.name} <b>{fmt(s.values[hover]!)}</b></div>)}
          {band && band.upper[hover] != null && <div className="muted">{band.name} {fmt(band.lower[hover]!)}–{fmt(band.upper[hover]!)}</div>}
        </div>
      )}
    </div>
  );
}

/** Bars above/below zero (e.g. MACD histogram): blue above, red below (the diverging pair). */
export function ZeroBars({ dates, values, fmt, height = 110, label }: {
  dates: string[]; values: (number | null)[]; fmt: (v: number) => string; height?: number; label: string;
}) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const H = height;
  const vals = values.map((v) => v ?? 0);
  const m = Math.max(...vals.map(Math.abs), 1e-9);
  const y = (v: number) => PAD.t + (1 - (v + m) / (2 * m)) * (H - PAD.t - PAD.b);
  const n = vals.length, bw = Math.max(1, (W - PAD.l - PAD.r) / n - 1);
  const x = (i: number) => PAD.l + (i / n) * (W - PAD.l - PAD.r);
  return (
    <div className="mx-chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}
        onPointerMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); const i = Math.floor((((e.clientX - r.left) / r.width) * W - PAD.l) / ((W - PAD.l - PAD.r) / n)); setHover(i >= 0 && i < n ? i : null); }}
        onPointerLeave={() => setHover(null)}>
        <line x1={PAD.l} x2={W - PAD.r} y1={y(0)} y2={y(0)} stroke="var(--text-muted)" />
        <text x={PAD.l - 6} y={y(m) + 8} textAnchor="end" className="mx-tick">{fmt(m)}</text>
        <text x={PAD.l - 6} y={y(-m)} textAnchor="end" className="mx-tick">{fmt(-m)}</text>
        {vals.map((v, i) => (
          <rect key={i} x={x(i)} width={bw} y={v >= 0 ? y(v) : y(0)} height={Math.max(0.5, Math.abs(y(v) - y(0)))}
            fill={v >= 0 ? "var(--div-pos)" : "var(--div-neg)"} opacity={hover == null || hover === i ? 1 : 0.55} />
        ))}
      </svg>
      {hover != null && (
        <div className="mx-tip" style={{ left: `${(x(hover) / W) * 100}%`, transform: x(hover) > W / 2 ? "translateX(calc(-100% - 10px))" : "translateX(10px)" }}>
          <div className="muted">{shortDate(dates[hover])}</div><b>{fmt(vals[hover])}</b>
        </div>
      )}
    </div>
  );
}

/** A 0-100 meter with zones (e.g. RSI: oversold below 30, overbought above 70). */
export function Meter({ value, zones, label }: { value: number | null; zones: { to: number; label: string }[]; label: string }) {
  const v = value == null ? null : Math.max(0, Math.min(100, value));
  return (
    <div className="mx-meter" role="img" aria-label={`${label}: ${v == null ? "no data" : v.toFixed(0)} of 100`}>
      <div className="mx-meter-track">
        {zones.map((z, i) => {
          const from = i ? zones[i - 1].to : 0;
          return <span key={z.label} style={{ left: `${from}%`, width: `${z.to - from}%` }} title={z.label} />;
        })}
        {v != null && <b style={{ left: `${v}%` }} />}
      </div>
      <div className="mx-meter-scale">{zones.map((z, i) => <span key={z.label} style={{ left: `${((i ? zones[i - 1].to : 0) + z.to) / 2}%` }}>{z.label}</span>)}</div>
    </div>
  );
}
