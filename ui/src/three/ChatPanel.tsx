/** Chat with the Supervisor (Claude, read-only lookups). The conversation stays in this browser. */
import { Fragment, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../api";
import { api } from "../api";

const KEY = "darwin.supervisor.chat";
const SUGGESTIONS = [
  "How are we doing today?",
  "Why aren't any new trades opening?",
  "How much money is at risk right now?",
  "Explain my open positions simply.",
];
const TOOL_NAMES: Record<string, string> = {
  get_overview: "overview", get_positions: "positions", get_position_detail: "position detail",
  get_last_scan: "last scan", get_recent_events: "journal", get_risk: "risk", get_settings: "settings",
  get_market_data: "saved prices",
};

function load(): ChatMessage[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]"); } catch { return []; }
}
function save(m: ChatMessage[]) {
  try { localStorage.setItem(KEY, JSON.stringify(m.slice(-40))); } catch { /* private window: fine */ }
}

/** Plain text with **bold**, "- " bullets and paragraphs. No HTML from the model is rendered. */
function Rich({ text }: { text: string }) {
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <b key={i}>{part.slice(2, -2)}</b> : <Fragment key={i}>{part}</Fragment>);
  const blocks = text.split(/\n{2,}/);
  return <>{blocks.map((b, i) => {
    const lines = b.split("\n");
    if (lines.every((l) => /^\s*[-•*] /.test(l))) {
      return <ul key={i}>{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-•*] /, ""))}</li>)}</ul>;
    }
    return <p key={i}>{lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l)}</Fragment>)}</p>;
  })}</>;
}

export function ChatPanel({ onClose, onBusy, onTools }: {
  onClose: () => void; onBusy: (b: boolean) => void; onTools?: (tools: string[]) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>(load);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [avail, setAvail] = useState<{ available: boolean; reason?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api.supervisor().then(setAvail).catch((e) => setAvail({ available: false, reason: String(e) }));
    box.current?.focus();
  }, []);
  useEffect(() => { save(messages); end.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);
  useEffect(() => () => onBusy(false), [onBusy]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    const next: ChatMessage[] = [...messages, { role: "user", content: q }];
    setMessages(next); setInput(""); setError(null); setBusy(true); onBusy(true);
    try {
      const r = await api.supervisorChat(next);
      setMessages([...next, { role: "assistant", content: r.reply, tools: r.tools_used }]);
      onTools?.(r.tools_used);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setMessages(messages); // put the question back so it can be resent
      setInput(q);
    }
    setBusy(false); onBusy(false);
  };

  const off = avail && !avail.available;
  return (
    <aside className="c3-panel c3-chat">
      <button className="c3-x" onClick={onClose} aria-label="Close">×</button>
      <div className="c3-panel-head"><i style={{ background: "#ffd43b" }} />Supervisor</div>
      <p className="c3-job">Ask about anything in the system. I look things up live; I can't trade or change settings.</p>
      {off && <div className="c3-note">{avail.reason}</div>}
      <div className="chat-log">
        {messages.length === 0 && !off && (
          <div className="chat-suggest">
            {SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)} disabled={busy}>{s}</button>)}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}`}>
            <Rich text={m.content} />
            {m.tools && m.tools.length > 0 && (
              <div className="chat-tools">Looked at: {[...new Set(m.tools.map((t) => TOOL_NAMES[t] ?? t))].join(", ")}</div>
            )}
          </div>
        ))}
        {busy && <div className="chat-msg assistant typing"><span /><span /><span /> Looking into it…</div>}
        {error && <div className="c3-note">{error}</div>}
        <div ref={end} />
      </div>
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
        <textarea ref={box} rows={2} value={input} placeholder={off ? "Supervisor unavailable" : "Ask the Supervisor…"}
          disabled={!!off} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } e.stopPropagation(); }} />
        <button type="submit" disabled={busy || !input.trim() || !!off}>Send</button>
      </form>
      {messages.length > 0 && (
        <button className="chat-clear" onClick={() => { setMessages([]); setError(null); }} disabled={busy}>New conversation</button>
      )}
    </aside>
  );
}
