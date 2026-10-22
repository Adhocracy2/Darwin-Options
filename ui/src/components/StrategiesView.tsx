import { useEffect, useMemo, useState } from "react";
import type { FieldMeta, Settings, SettingsUpdate, StrategyPayoff } from "../api";
import { PayoffChart } from "./PayoffChart";
import { api } from "../api";

/** Local edit state. Numbers are kept as the text typed, in display units (e.g. "50" for 50%). */
type Draft = {
  allow_new_entries: boolean;
  tickers: { symbol: string; enabled: boolean }[];
  strategies: Record<string, { enabled: boolean; exit: Record<string, string | boolean> }>;
};

const UNIT: Record<string, string> = { pct: "%", mult: "× credit", days: "days", price: "$ / share" };

function toDisplay(meta: FieldMeta, v: number | boolean): string | boolean {
  if (typeof v === "boolean") return v;
  if (meta.kind === "pct") return String(+(v * 100).toFixed(2));
  return String(v);
}

function fromDisplay(meta: FieldMeta, v: string | boolean): number | boolean | null {
  if (typeof v === "boolean") return v;
  if (v.trim() === "") return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return meta.kind === "pct" ? n / 100 : n;
}

function fmt(meta: FieldMeta, v: number | boolean): string {
  if (typeof v === "boolean") return v ? "On" : "Off";
  switch (meta.kind) {
    case "pct": return `${+(v * 100).toFixed(2)}%`;
    case "mult": return `${v}×`;
    case "days": return `${v} ${v === 1 ? "day" : "days"}`;
    case "price": return `$${v.toFixed(2)}`;
    default: return String(v);
  }
}

function draftFrom(s: Settings): Draft {
  return {
    allow_new_entries: s.allow_new_entries,
    tickers: s.tickers.map((t) => ({ symbol: t.symbol, enabled: t.enabled })),
    strategies: Object.fromEntries(s.strategies.map((st) => [st.name, {
      enabled: st.enabled,
      exit: Object.fromEntries(st.exit.map((f) => [f.key, toDisplay(s.fields[f.key], f.value)])),
    }])),
  };
}

function fieldError(meta: FieldMeta, raw: string | boolean): string | null {
  if (typeof raw === "boolean") return null;
  const v = fromDisplay(meta, raw);
  if (v === null) return "Enter a number";
  if (meta.kind === "days" && !Number.isInteger(v)) return "Whole days only";
  if ((v as number) < meta.min || (v as number) > meta.max) return `${fmt(meta, meta.min)} to ${fmt(meta, meta.max)}`;
  return null;
}

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
    return server.strategies.some((st) => st.exit.some((f) => fieldError(server.fields[f.key], draft.strategies[st.name].exit[f.key])));
  }, [server, draft]);

  if (!server || !draft) return <div className="card">{error ? <div className="error">{error}</div> : <p className="muted">Loading…</p>}</div>;

  const setStrategy = (name: string, patch: Partial<Draft["strategies"][string]>) =>
    setDraft({ ...draft, strategies: { ...draft.strategies, [name]: { ...draft.strategies[name], ...patch } } });
  const setField = (name: string, key: string, v: string | boolean) =>
    setStrategy(name, { exit: { ...draft.strategies[name].exit, [key]: v } });

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

  const save = async () => {
    const turningOn = server.strategies.filter((s) => !s.enabled && draft.strategies[s.name].enabled).map((s) => s.label);
    if (live && !confirm(`This changes how LIVE trades are managed${turningOn.length ? ` and turns on ${turningOn.join(", ")}` : ""}. Save?`)) return;
    // Values equal to the config.yaml default are sent as "reset" so config.yaml stays the source of truth.
    const update: SettingsUpdate = { strategies: {} };
    if (draft.allow_new_entries !== server.allow_new_entries_default) update.allow_new_entries = draft.allow_new_entries;
    // config.yaml tickers are sent only when switched off; added tickers are always sent (dropping one removes it).
    const tickers: Record<string, boolean> = {};
    for (const t of draft.tickers) {
      if (tickerInfo[t.symbol]?.in_config ? !t.enabled : true) tickers[t.symbol] = t.enabled;
    }
    if (Object.keys(tickers).length) update.tickers = tickers;
    for (const st of server.strategies) {
      const d = draft.strategies[st.name];
      const entry: SettingsUpdate["strategies"][string] = {};
      if (d.enabled !== st.enabled_default) entry.enabled = d.enabled;
      const exit: Record<string, number | boolean | null> = {};
      for (const f of st.exit) {
        const v = fromDisplay(server.fields[f.key], d.exit[f.key]);
        if (v !== null && v !== f.default) exit[f.key] = v;
      }
      if (Object.keys(exit).length) entry.exit = exit;
      if (Object.keys(entry).length) update.strategies[st.name] = entry;
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

      {server.strategies.map((st) => {
        const d = draft.strategies[st.name];
        return (
          <div className={`card strategy ${d.enabled ? "" : "disabled"}`} key={st.name}>
            <div className="strategy-head">
              <div>
                <h2>{st.label} {st.open_positions > 0 && <span className="badge">{st.open_positions} open</span>}</h2>
                <p className="muted small">{st.description}</p>
              </div>
              <Switch checked={d.enabled} label={`${st.label} enabled`} onChange={(v) => setStrategy(st.name, { enabled: v })} />
            </div>
            {payoffs[st.name] && (
              <details className="payoff-details" open>
                <summary className="small">Payoff diagram</summary>
                <PayoffChart p={payoffs[st.name].payoff} height={200} />
                <p className="muted small payoff-source">{payoffs[st.name].source}. Exit line uses this strategy's saved close-at-DTE.</p>
              </details>
            )}
            {!d.enabled && st.open_positions > 0 && (
              <p className="small note">No new {st.label.toLowerCase()} trades. The {st.open_positions} open will still be managed until closed.</p>
            )}
            <div className="fields">
              {st.exit.map((f) => {
                const meta = server.fields[f.key];
                const raw = d.exit[f.key];
                const err = fieldError(meta, raw);
                const current = fromDisplay(meta, raw);
                const isDefault = current === f.default;
                const id = `${st.name}-${f.key}`;
                return (
                  <div className="field" key={f.key}>
                    <label htmlFor={id} title={meta.help}>{meta.label}</label>
                    {meta.kind === "bool" ? (
                      <Switch checked={raw as boolean} label={meta.label} onChange={(v) => setField(st.name, f.key, v)} />
                    ) : (
                      <div className={`input-unit ${err ? "invalid" : ""}`}>
                        <input id={id} inputMode="decimal" value={raw as string} aria-invalid={!!err}
                               onChange={(e) => setField(st.name, f.key, e.target.value)} />
                        <span className="unit">{UNIT[meta.kind]}</span>
                      </div>
                    )}
                    <div className="small">
                      {err ? <span className="neg">{err}</span> : isDefault
                        ? <span className="muted">Default</span>
                        : <>
                            <span className="muted">Default {fmt(meta, f.default)} · </span>
                            <button type="button" className="link" onClick={() => setField(st.name, f.key, toDisplay(meta, f.default))}>Reset</button>
                          </>}
                    </div>
                    {showHelp && <p className="muted small help">{meta.help}</p>}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

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
