import { useEffect, useMemo, useState } from "react";
import type { FieldMeta, FieldValue, Settings, SettingsUpdate, StrategyUpdate, StrategyPayoff } from "../api";
import { PayoffChart } from "./PayoffChart";
import { api } from "../api";

/** Local edit state. Values are kept as typed, in display units (e.g. "50" for 50%, "2, 3, 5" for widths). */
type Raw = string | boolean;
type DraftStrategy = {
  name: string;
  type: string;
  label: string;
  enabled: boolean;
  builtin: boolean;
  removable: boolean;
  isNew: boolean;
  entry: Record<string, Raw>;
  exit: Record<string, Raw>;
};
type Draft = {
  allow_new_entries: boolean;
  tickers: { symbol: string; enabled: boolean }[];
  strategies: DraftStrategy[];
};

const UNIT: Record<string, string> = { pct: "%", mult: "× credit", days: "days", price: "$ / share", widths: "$", time: "New York", delta: "" };

function toDisplay(meta: FieldMeta, v: FieldValue): Raw {
  if (typeof v === "boolean") return v;
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "string") return v;
  if (meta.kind === "pct") return String(+(v * 100).toFixed(2));
  return String(v);
}

function fromDisplay(meta: FieldMeta, v: Raw): FieldValue | null {
  if (typeof v === "boolean") return v;
  const t = v.trim();
  if (t === "") return null;
  if (meta.kind === "time") return /^\d{1,2}:\d{2}$/.test(t) ? t.padStart(5, "0") : null;
  if (meta.kind === "widths") {
    const parts = t.split(/[,\s]+/).filter(Boolean).map(Number);
    if (!parts.length || parts.some((n) => !Number.isFinite(n))) return null;
    return [...new Set(parts)].sort((a, b) => a - b);
  }
  const n = Number(t);
  if (!Number.isFinite(n)) return null;
  return meta.kind === "pct" ? n / 100 : n;
}

const hhmm = (mins: number) => `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;

function fmt(meta: FieldMeta, v: FieldValue): string {
  if (typeof v === "boolean") return v ? "On" : "Off";
  if (Array.isArray(v)) return v.map((x) => `$${x}`).join(", ");
  if (typeof v === "string") return v;
  switch (meta.kind) {
    case "pct": return `${+(v * 100).toFixed(2)}%`;
    case "mult": return `${v}×`;
    case "days": return `${v} ${v === 1 ? "day" : "days"}`;
    case "price": return `$${v.toFixed(2)}`;
    case "widths": return `$${v}`;
    case "time": return hhmm(v);
    default: return String(v);
  }
}

function same(a: FieldValue | null, b: FieldValue): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function fieldError(meta: FieldMeta, raw: Raw): string | null {
  if (typeof raw === "boolean") return null;
  const v = fromDisplay(meta, raw);
  if (v === null) return meta.kind === "time" ? "Like 15:30" : meta.kind === "widths" ? "Like 2, 3, 5" : "Enter a number";
  if (meta.kind === "time") {
    const [h, m] = (v as string).split(":").map(Number);
    const mins = h * 60 + m;
    if (m > 59 || mins < meta.min || mins > meta.max) return `${hhmm(meta.min)} to ${hhmm(meta.max)}`;
    return null;
  }
  const nums = Array.isArray(v) ? v : [v as number];
  if (meta.kind === "widths" && nums.length > 6) return "At most 6 widths";
  if (meta.kind === "days" && nums.some((n) => !Number.isInteger(n))) return "Whole days only";
  if (nums.some((n) => n < meta.min || n > meta.max)) return `${fmt(meta, meta.min)} to ${fmt(meta, meta.max)}`;
  return null;
}

function slug(label: string, taken: Set<string>): string {
  let base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!/^[a-z]/.test(base)) base = `s_${base}`;
  base = base.slice(0, 32) || "strategy";
  let id = base.length < 2 ? `${base}_1` : base;
  for (let i = 2; taken.has(id); i++) id = `${base}_${i}`;
  return id;
}

function draftFrom(s: Settings): Draft {
  return {
    allow_new_entries: s.allow_new_entries,
    tickers: s.tickers.map((t) => ({ symbol: t.symbol, enabled: t.enabled })),
    strategies: s.strategies.map((st) => ({
      name: st.name, type: st.type, label: st.label, enabled: st.enabled, builtin: st.builtin,
      removable: st.removable, isNew: false,
      entry: Object.fromEntries(st.entry.map((f) => [f.key, toDisplay(s.fields[f.key], f.value)])),
      exit: Object.fromEntries(st.exit.map((f) => [f.key, toDisplay(s.fields[f.key], f.value)])),
    })),
  };
}

/** Same-day when "days to expiry to" is 0. */
const isSameDay = (d: DraftStrategy) => d.entry.dte_max !== undefined && String(d.entry.dte_max).trim() === "0";

function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
            className={`switch ${checked ? "on" : ""}`} onClick={() => onChange(!checked)}>
      <span className="knob" />
      <span className="switch-text">{checked ? "On" : "Off"}</span>
    </button>
  );
}

export function StrategiesView({ live, onSaved }: { live: boolean; onSaved: () => void }) {
  const [server, setServer] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [newTicker, setNewTicker] = useState("");
  const [adding, setAdding] = useState(false);
  const [addType, setAddType] = useState("credit_spread");
  const [addLabel, setAddLabel] = useState("");
  const [addSameDay, setAddSameDay] = useState(false);
  const [payoffs, setPayoffs] = useState<Record<string, StrategyPayoff>>({});
  const [showHelp, setShowHelp] = useState<boolean>(() => {
    try { return localStorage.getItem("darwin.showHelp") === "1"; } catch { return false; }
  });
  const toggleHelp = (v: boolean) => {
    setShowHelp(v);
    try { localStorage.setItem("darwin.showHelp", v ? "1" : "0"); } catch { /* storage unavailable */ }
  };

  // Diagrams are fetched separately so the settings form never waits on market data.
  useEffect(() => {
    api.strategyPayoffs()
      .then((list) => setPayoffs(Object.fromEntries(list.map((x) => [x.name, x]))))
      .catch(() => setPayoffs({}));
  }, [savedAt]);

  useEffect(() => {
    api.settings().then((s) => { setServer(s); setDraft(draftFrom(s)); }).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const baseline = useMemo(() => (server ? draftFrom(server) : null), [server]);
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(baseline), [draft, baseline]);
  const invalid = useMemo(() => {
    if (!server || !draft) return false;
    return draft.strategies.some((d) =>
      [...Object.entries(d.entry), ...Object.entries(d.exit)].some(([k, v]) => server.fields[k] && fieldError(server.fields[k], v)));
  }, [server, draft]);

  if (!server || !draft) return <div className="card">{error ? <div className="error">{error}</div> : <p className="muted">Loading…</p>}</div>;

  const serverBy = Object.fromEntries(server.strategies.map((s) => [s.name, s]));
  const typeBy = Object.fromEntries(server.types.map((t) => [t.type, t]));
  const setStrategy = (name: string, patch: Partial<DraftStrategy>) =>
    setDraft({ ...draft, strategies: draft.strategies.map((d) => (d.name === name ? { ...d, ...patch } : d)) });
  const setEntry = (d: DraftStrategy, key: string, v: Raw) => setStrategy(d.name, { entry: { ...d.entry, [key]: v } });
  const setExit = (d: DraftStrategy, key: string, v: Raw) => setStrategy(d.name, { exit: { ...d.exit, [key]: v } });

  /** Exit fields to show: the same-day ones (close-by time) or multi-day ones (close at DTE). */
  const exitKeys = (d: DraftStrategy): string[] => {
    const t = typeBy[d.type];
    const want = isSameDay(d) ? t.same_day_exit : t.exit;
    return Object.keys(want ?? d.exit);
  };
  const exitValue = (d: DraftStrategy, key: string): Raw => {
    if (key in d.exit) return d.exit[key];
    const t = typeBy[d.type];
    const v = (isSameDay(d) ? t.same_day_exit : t.exit)?.[key];
    return v === undefined ? "" : toDisplay(server.fields[key], v);
  };
  const exitDefault = (d: DraftStrategy, key: string): FieldValue | undefined => {
    const f = serverBy[d.name]?.exit.find((x) => x.key === key);
    if (f && !d.isNew && isSameDay(d) === serverBy[d.name].same_day) return f.default;
    const t = typeBy[d.type];
    return (isSameDay(d) ? t.same_day_exit : t.exit)?.[key];
  };
  const entryDefault = (d: DraftStrategy, key: string): FieldValue | undefined => {
    const f = serverBy[d.name]?.entry.find((x) => x.key === key);
    if (f && !d.isNew) return f.default;
    const t = typeBy[d.type];
    return (isSameDay(d) ? t.same_day_entry : t.entry)?.[key];
  };

  const tickerInfo = Object.fromEntries(server.tickers.map((t) => [t.symbol, t]));
  const setTicker = (symbol: string, enabled: boolean) =>
    setDraft({ ...draft, tickers: draft.tickers.map((t) => (t.symbol === symbol ? { ...t, enabled } : t)) });
  const removeTicker = (symbol: string) => setDraft({ ...draft, tickers: draft.tickers.filter((t) => t.symbol !== symbol) });
  const tickerInputError = (() => {
    const t = newTicker.trim().toUpperCase();
    if (!t) return null;
    if (!/^[A-Z]{1,5}$/.test(t)) return "1–5 letters, like XLF";
    if (draft.tickers.some((x) => x.symbol === t)) return "Already in the list";
    return null;
  })();
  const addTicker = () => {
    const t = newTicker.trim().toUpperCase();
    if (!t || tickerInputError) return;
    setDraft({ ...draft, tickers: [...draft.tickers, { symbol: t, enabled: true }] });
    setNewTicker("");
  };

  // ---- add strategy ----
  const taken = new Set(draft.strategies.map((d) => d.name));
  const labels = new Set(draft.strategies.map((d) => d.label.toLowerCase()));
  const addT = typeBy[addType];
  const addNameError = !addLabel.trim() ? null : addLabel.trim().length > 40 ? "Up to 40 characters"
    : labels.has(addLabel.trim().toLowerCase()) ? "Another strategy has this name" : null;
  const addStrategy = () => {
    if (!addT || !addLabel.trim() || addNameError) return;
    const sd = addSameDay && addT.same_day_ok;
    const entry = (sd ? addT.same_day_entry : addT.entry) ?? addT.entry;
    const exit = (sd ? addT.same_day_exit : addT.exit) ?? addT.exit;
    const d: DraftStrategy = {
      name: slug(addLabel.trim(), taken), type: addT.type, label: addLabel.trim(), enabled: true, builtin: false,
      removable: true, isNew: true,
      entry: Object.fromEntries(Object.entries(entry).map(([k, v]) => [k, toDisplay(server.fields[k], v)])),
      exit: Object.fromEntries(Object.entries(exit).map(([k, v]) => [k, toDisplay(server.fields[k], v)])),
    };
    setDraft({ ...draft, strategies: [...draft.strategies, d] });
    setAdding(false);
    setAddLabel("");
    setAddSameDay(false);
  };
  const removeStrategy = (name: string) => setDraft({ ...draft, strategies: draft.strategies.filter((d) => d.name !== name) });

  const save = async () => {
    const turningOn = draft.strategies.filter((d) => d.enabled && (d.isNew || !serverBy[d.name]?.enabled)).map((d) => d.label);
    if (live && !confirm(`This changes how LIVE trades are opened and managed${turningOn.length ? ` and turns on ${turningOn.join(", ")}` : ""}. Save?`)) return;
    const update: SettingsUpdate = { strategies: {} };
    if (draft.allow_new_entries !== server.allow_new_entries_default) update.allow_new_entries = draft.allow_new_entries;
    // config.yaml tickers are sent only when switched off; added tickers are always sent (dropping one removes it).
    const tickers: Record<string, boolean> = {};
    for (const t of draft.tickers) {
      if (tickerInfo[t.symbol]?.in_config ? !t.enabled : true) tickers[t.symbol] = t.enabled;
    }
    if (Object.keys(tickers).length) update.tickers = tickers;
    for (const d of draft.strategies) {
      const st = serverBy[d.name];
      const full = d.removable; // added from the dashboard: always sent in full
      const u: StrategyUpdate = {};
      if (full) { u.type = d.type; u.label = d.label; u.enabled = d.enabled; }
      else if (st && d.enabled !== st.enabled_default) u.enabled = d.enabled;
      const entry: Record<string, FieldValue> = {};
      for (const [k, raw] of Object.entries(d.entry)) {
        const v = fromDisplay(server.fields[k], raw);
        if (v === null) continue;
        const dflt = entryDefault(d, k);
        if (full || dflt === undefined || !same(v, dflt)) entry[k] = v;
      }
      const exit: Record<string, FieldValue> = {};
      for (const k of exitKeys(d)) {
        const v = fromDisplay(server.fields[k], exitValue(d, k));
        const dflt = exitDefault(d, k);
        if (v !== null && (dflt === undefined || !same(v, dflt))) exit[k] = v;
      }
      if (Object.keys(entry).length) u.entry = entry;
      if (Object.keys(exit).length) u.exit = exit;
      if (Object.keys(u).length) update.strategies[d.name] = u;
    }
    setSaving(true);
    try {
      const s = await api.saveSettings(update);
      setServer(s);
      setDraft(draftFrom(s));
      setError(null);
      setSavedAt(new Date());
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setSaving(false);
  };

  const fieldInput = (d: DraftStrategy, key: string, raw: Raw, dflt: FieldValue | undefined, onChange: (v: Raw) => void) => {
    const meta = server.fields[key];
    const err = fieldError(meta, raw);
    const current = fromDisplay(meta, raw);
    const isDefault = dflt === undefined || same(current, dflt);
    const id = `${d.name}-${key}`;
    return (
      <div className="field" key={key}>
        <label htmlFor={id} title={meta.help}>{meta.label}</label>
        {meta.kind === "bool" ? (
          <Switch checked={raw as boolean} label={meta.label} onChange={onChange} />
        ) : (
          <div className={`input-unit ${err ? "invalid" : ""}`}>
            <input id={id} inputMode={meta.kind === "time" || meta.kind === "widths" ? "text" : "decimal"} value={raw as string}
                   aria-invalid={!!err} onChange={(e) => onChange(e.target.value)} />
            {UNIT[meta.kind] && <span className="unit">{UNIT[meta.kind]}</span>}
          </div>
        )}
        <div className="small">
          {err ? <span className="neg">{err}</span> : isDefault
            ? <span className="muted">Default</span>
            : <>
                <span className="muted">Default {fmt(meta, dflt!)} · </span>
                <button type="button" className="link" onClick={() => onChange(toDisplay(meta, dflt!))}>Reset</button>
              </>}
        </div>
        {showHelp && <p className="muted small help">{meta.help}</p>}
      </div>
    );
  };

  return (
    <div className="settings">
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="strategy-head">
          <div>
            <h2>New entries</h2>
            <p className="muted small">Master switch. When off, nothing new is opened; open positions are still managed and closed by their exit rules.</p>
          </div>
          <Switch checked={draft.allow_new_entries} label="New entries" onChange={(v) => setDraft({ ...draft, allow_new_entries: v })} />
        </div>
      </div>

      <div className="card">
        <h2>Tickers</h2>
        <p className="muted small">Underlyings scanned for new trades. Switching one off stops new trades in it; open positions are still managed.</p>
        <div className="tickers">
          {draft.tickers.map((t) => {
            const info = tickerInfo[t.symbol];
            return (
              <div className={`ticker ${t.enabled ? "on" : ""}`} key={t.symbol}>
                <button type="button" role="switch" aria-checked={t.enabled} className="ticker-toggle"
                        onClick={() => setTicker(t.symbol, !t.enabled)} title={t.enabled ? "On: click to switch off" : "Off: click to switch on"}>
                  {t.symbol}
                  {info?.open_positions ? <span className="badge">{info.open_positions} open</span> : null}
                </button>
                {!info?.in_config && (
                  <button type="button" className="link ticker-remove" aria-label={`Remove ${t.symbol}`}
                          onClick={() => removeTicker(t.symbol)}>×</button>
                )}
              </div>
            );
          })}
        </div>
        <form className="ticker-add" onSubmit={(e) => { e.preventDefault(); addTicker(); }}>
          <input value={newTicker} placeholder="Add ticker, e.g. XLF" maxLength={5} aria-label="Add ticker"
                 aria-invalid={!!tickerInputError} onChange={(e) => setNewTicker(e.target.value)} />
          <button className="btn secondary" type="submit" disabled={!newTicker.trim() || !!tickerInputError}>Add</button>
          {tickerInputError && <span className="small neg">{tickerInputError}</span>}
        </form>
        {!draft.tickers.some((t) => t.enabled) && <p className="small note">No tickers on: nothing new will be opened.</p>}
        <p className="muted small">Pick ones with busy option markets (tight bid/ask). Strategies still skip trades that fail the liquidity filters.</p>
      </div>

      <div className="toolbar">
        <p className="muted small" style={{ margin: 0, flex: 1, minWidth: 240 }}>
          Changes are picked up by the trader at its next cycle (within 5 minutes). Never-naked and max-loss limits
          are not editable here. Values left at their default follow <code>config.yaml</code>.
        </p>
        <label className="small"><input type="checkbox" checked={showHelp} onChange={(e) => toggleHelp(e.target.checked)} /> Show explanations</label>
      </div>

      {draft.strategies.map((d) => {
        const st = serverBy[d.name];
        const sd = isSameDay(d);
        const open = st?.open_positions ?? 0;
        const t = typeBy[d.type];
        return (
          <div className={`card strategy ${d.enabled ? "" : "disabled"}`} key={d.name}>
            <div className="strategy-head">
              <div>
                <h2>
                  {d.label}{" "}
                  {sd && <span className="badge zero-dte" title="Trades options expiring today">0DTE</span>}{" "}
                  {!d.builtin && <span className="badge">{t?.label}</span>}{" "}
                  {open > 0 && <span className="badge">{open} open</span>}
                  {d.isNew && <span className="badge">new, not saved</span>}
                </h2>
                <p className="muted small">{t?.description}</p>
              </div>
              <Switch checked={d.enabled} label={`${d.label} enabled`} onChange={(v) => setStrategy(d.name, { enabled: v })} />
            </div>
            {sd && (
              <p className="small note">
                Trades options expiring today: opens only {server.same_day.entry_start}–{server.same_day.entry_end} New York,
                closes everything by its close-by time, skips half-days, and never trades on delayed quotes.
                {!server.same_day.live_data && <> <strong>Won't trade yet:</strong> your IBKR quotes are delayed (needs an options data subscription).</>}
              </p>
            )}
            {payoffs[d.name] && (
              <details className="payoff-details">
                <summary className="small">Payoff diagram</summary>
                <PayoffChart p={payoffs[d.name].payoff} height={200} />
                <p className="muted small payoff-source">{payoffs[d.name].source}.</p>
              </details>
            )}
            {!d.enabled && open > 0 && (
              <p className="small note">No new trades. The {open} open will still be managed until closed.</p>
            )}
            <h3 className="small field-group">When to open</h3>
            <div className="fields">
              {Object.keys(d.entry).map((k) => fieldInput(d, k, d.entry[k], entryDefault(d, k), (v) => setEntry(d, k, v)))}
            </div>
            {t?.same_day_ok && <p className="muted small">Days to expiry 0 to 0 = options expiring today (0DTE).</p>}
            <h3 className="small field-group">When to close</h3>
            <div className="fields">
              {exitKeys(d).map((k) => fieldInput(d, k, exitValue(d, k), exitDefault(d, k), (v) => setExit(d, k, v)))}
            </div>
            {d.removable && (
              <div className="strategy-foot">
                <button type="button" className="link neg" disabled={open > 0} onClick={() => removeStrategy(d.name)}
                        title={open > 0 ? "Has open positions: switch it off instead" : undefined}>
                  Delete strategy
                </button>
                {open > 0 && <span className="muted small"> Has open positions: switch it off instead.</span>}
              </div>
            )}
          </div>
        );
      })}

      <div className="card">
        {!adding ? (
          <button type="button" className="btn" onClick={() => setAdding(true)}>+ Add strategy</button>
        ) : (
          <form className="add-strategy" onSubmit={(e) => { e.preventDefault(); addStrategy(); }}>
            <h2>Add strategy</h2>
            <div className="fields">
              <div className="field">
                <label htmlFor="add-type">Strategy</label>
                <select id="add-type" value={addType} onChange={(e) => { setAddType(e.target.value); if (!typeBy[e.target.value]?.same_day_ok) setAddSameDay(false); }}>
                  {server.types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
                </select>
                <p className="muted small help">{addT?.description}</p>
              </div>
              <div className="field">
                <label htmlFor="add-name">Name</label>
                <input id="add-name" className="text-input" value={addLabel} maxLength={40} placeholder={`e.g. ${addT?.label} 0DTE`}
                       onChange={(e) => setAddLabel(e.target.value)} aria-invalid={!!addNameError} />
                {addNameError && <span className="small neg">{addNameError}</span>}
              </div>
              <div className="field">
                <label className="small">
                  <input type="checkbox" checked={addSameDay} disabled={!addT?.same_day_ok} onChange={(e) => setAddSameDay(e.target.checked)} />{" "}
                  Options expiring today (0DTE)
                </label>
                <p className="muted small help">
                  {addT?.same_day_ok ? "Starts with same-day settings: far out-of-the-money shorts, narrow widths, out by 15:30."
                    : `${addT?.label} needs a later expiry, so it can't be same-day.`}
                </p>
              </div>
            </div>
            <div className="add-actions">
              <button className="btn" type="submit" disabled={!addLabel.trim() || !!addNameError}>Add</button>
              <button className="btn secondary" type="button" onClick={() => setAdding(false)}>Cancel</button>
            </div>
            <p className="muted small">It's added below; adjust its settings, then Save.</p>
          </form>
        )}
      </div>

      {(dirty || savedAt) && (
        <div className="savebar">
          <span className="small">{dirty ? (invalid ? "Fix the highlighted values to save" : "Unsaved changes") : `Saved ${savedAt!.toLocaleTimeString()}`}</span>
          {dirty && <>
            <button className="btn secondary" onClick={() => setDraft(baseline)} disabled={saving}>Discard</button>
            <button className="btn" onClick={save} disabled={saving || invalid}>{saving ? "Saving…" : "Save"}</button>
          </>}
        </div>
      )}
    </div>
  );
}
