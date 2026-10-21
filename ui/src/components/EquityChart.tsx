import { useEffect, useMemo, useRef, useState } from "react";
import type { Snapshot } from "../api";
import { usd } from "../api";

const H = 240, PAD = { l: 64, r: 16, t: 12, b: 28 };

/** Single-series equity line with a crosshair tooltip. */
export function EquityChart({ data }: { data: Snapshot[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(800);

  // Draw at the container's real width so text stays readable on phones.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [data.length >= 2]);

  const geo = useMemo(() => {
    if (data.length < 2) return null;
    const ys = data.map((d) => d.equity);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi - lo < 1) { lo -= 50; hi += 50; }
    const pad = (hi - lo) * 0.1;
    lo -= pad; hi += pad;
    const x = (i: number) => PAD.l + (i / (data.length - 1)) * (W - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
    const ticks = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * i) / 3);
    const path = data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.equity).toFixed(1)}`).join("");
    return { x, y, ticks, path };
  }, [data, W]);

  const sameDay = data.length > 1 && data[0].ts.slice(0, 10) === data[data.length - 1].ts.slice(0, 10);
  const xLabel = (ts: string) => (sameDay ? ts.slice(11, 16) : ts.slice(0, 10));

  if (!geo) {
    return (
      <div className="chart" ref={box}>
        <p className="muted">Not enough history yet. Equity is recorded after each trading cycle.</p>
      </div>
    );
  }

  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (data.length - 1));
    setHover(Math.max(0, Math.min(data.length - 1, i)));
  };

  const h = hover != null ? data[hover] : null;
  return (
    <div className="chart" ref={box}>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Account equity over time"
           onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={geo.y(t)} y2={geo.y(t)} stroke="var(--grid)" />
            <text x={PAD.l - 8} y={geo.y(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)">{usd(t)}</text>
          </g>
        ))}
        <text x={PAD.l} y={H - 8} fontSize="11" fill="var(--text-muted)">{xLabel(data[0].ts)}</text>
        <text x={W - PAD.r} y={H - 8} fontSize="11" fill="var(--text-muted)" textAnchor="end">{xLabel(data[data.length - 1].ts)}</text>
        <path d={geo.path} fill="none" stroke="var(--series-1)" strokeWidth="2" strokeLinejoin="round" />
        {hover != null && (
          <>
            <line x1={geo.x(hover)} x2={geo.x(hover)} y1={PAD.t} y2={H - PAD.b} stroke="var(--text-muted)" strokeDasharray="3 3" />
            <circle cx={geo.x(hover)} cy={geo.y(data[hover].equity)} r="5" fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth="2" />
          </>
        )}
      </svg>
      {h && (
        <div className="tooltip" style={{ left: `${(geo.x(hover!) / W) * 100}%`, top: 0, transform: hover! > data.length / 2 ? "translateX(-105%)" : "translateX(8px)" }}>
          <div className="muted">{h.ts.replace("T", " ").slice(0, 16)}</div>
          <div><b>{usd(h.equity)}</b> equity</div>
          <div className="muted">open risk {usd(h.open_risk)} · unrealized {usd(h.unrealized)}</div>
        </div>
      )}
    </div>
  );
}
