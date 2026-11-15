import { useCallback, useEffect, useRef, useState } from "react";
import { api, Scope } from "./api";

const fmt = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
const usd = (n: number | string) => `$${Number(n).toFixed(2)}`;

function useLoad<T>(path: string, deps: unknown[] = [], every = 0) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<T>(path).then((d) => { setData(d); setErr(""); }).catch((e) => setErr(e.message)), [path]);
  useEffect(() => { load(); if (!every) return; const t = setInterval(load, every); return () => clearInterval(t); }, [load, every, ...deps]);
  return { data, err, reload: load };
}

function Rich({ text }: { text: string }) {
  return <>{text.split(/\*\*(.+?)\*\*/g).map((p, i) => (i % 2 ? <strong key={i}>{p}</strong> : <span key={i}>{p}</span>))}</>;
}

export function Chat({ scope }: { scope: Scope }) {
  const { data, reload } = useLoad<any[]>("/api/chat", [scope], 5000);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView(); }, [data]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true); setErr("");
    try { await api("/api/chat", { body: { text, scope } }); setText(""); await reload(); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  }
  async function upload(f?: File) {
    if (!f) return;
    const form = new FormData(); form.append("file", f); form.append("scope", scope);
    try { await api("/api/chat/upload", { form }); await reload(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="chat">
      <div className="msgs">
        {(data ?? []).length === 0 && <p className="hint">Try: "status", "show the AdSpot report", "approvals", "pause AdSpot Ops".</p>}
        {(data ?? []).map((m) => (
          <div key={m.id} className={`msg ${m.role}`}><div className="bubble"><Rich text={m.body} /></div><small>{fmt(m.created_at)}</small></div>
        ))}
        <div ref={end} />
      </div>
      {err && <p className="err">{err}</p>}
      <form className="composer" onSubmit={send}>
        <label className="icon" title="Upload file">📎<input type="file" hidden accept="image/*,application/pdf" onChange={(e) => upload(e.target.files?.[0])} /></label>
        <label className="icon" title="Camera">📷<input type="file" hidden accept="image/*" capture="environment" onChange={(e) => upload(e.target.files?.[0])} /></label>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Message Command" />
        <button className="primary" disabled={busy}>Send</button>
      </form>
    </div>
  );
}

export function Approvals({ scope }: { scope: Scope }) {
  const { data, err, reload } = useLoad<any[]>(`/api/approvals?scope=${scope}`, [scope], 8000);
  const [edit, setEdit] = useState<Record<number, string>>({});
  const [note, setNote] = useState("");
  async function act(a: any, decision: string) {
    setNote("");
    try {
      const r = await api<{ note?: string }>(`/api/approvals/${a.company}/${a.id}`, { body: { decision, summary: edit[a.id] } });
      if (r.note) setNote(r.note);
      setEdit((e) => { const { [a.id]: _, ...rest } = e; return rest; });
      reload();
    } catch (e: any) { setNote(e.message); reload(); }
  }
  return (
    <div className="list">
      {err && <p className="err">{err}</p>}{note && <p className="hint">{note}</p>}
      {data?.length === 0 && <p className="hint">Nothing waiting for you.</p>}
      {data?.map((a) => (
        <div className="card" key={`${a.company}-${a.id}`}>
          <div className="row"><span className="badge">{a.company}</span><span className="badge warn">{a.tier}</span><small>{fmt(a.created_at)}</small></div>
          <textarea value={edit[a.id] ?? a.summary} onChange={(e) => setEdit({ ...edit, [a.id]: e.target.value })} rows={2} />
          {(a.booking_ref || a.amount) && <p className="confirm">Booking <b>{a.booking_ref ?? "?"}</b> · Amount <b>{a.amount ?? "?"}</b></p>}
          <pre className="payload">{JSON.stringify(a.payload, null, 1)}</pre>
          <div className="row">
            <button className="primary" onClick={() => act(a, "approve")}>Approve</button>
            <button onClick={() => act(a, "edit")} disabled={edit[a.id] === undefined}>Save edit</button>
            <button className="danger" onClick={() => act(a, "reject")}>Reject</button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function Departments({ scope }: { scope: Scope }) {
  const { data, err, reload } = useLoad<any[]>(`/api/departments?scope=${scope}`, [scope], 10000);
  const [msg, setMsg] = useState("");
  async function toggle(a: any) {
    try { await api(`/api/departments/${a.company}/${a.id}/status`, { body: { status: a.status === "active" ? "paused" : "active" } }); reload(); }
    catch (e: any) { setMsg(e.message); }
  }
  async function budget(a: any) {
    const v = prompt(`Monthly budget (USD) for ${a.name}`, String(Number(a.monthly_budget_usd)));
    if (!v) return;
    try { await api(`/api/departments/${a.company}/${a.id}/budget`, { body: { monthly_budget_usd: Number(v) } }); reload(); }
    catch (e: any) { setMsg(e.message); }
  }
  return (
    <div className="list">
      {err && <p className="err">{err}</p>}{msg && <p className="err">{msg}</p>}
      {data?.map((a) => {
        const pct = Math.min(100, (a.spent_usd / Number(a.monthly_budget_usd)) * 100);
        return (
          <div className="card" key={`${a.company}-${a.id}`}>
            <div className="row"><b>{a.name}</b><span className="badge">{a.company}</span><span className={`badge ${a.status}`}>{a.status}</span></div>
            <small>{a.dept}{a.schedule_hhmm ? ` · daily ${a.schedule_hhmm}` : ""}</small>
            <div className="bar"><div style={{ width: `${pct}%` }} className={pct > 85 ? "hot" : ""} /></div>
            <small>{usd(a.spent_usd)} of {usd(a.monthly_budget_usd)} this month</small>
            <div className="row">
              <button onClick={() => toggle(a)} disabled={a.status === "budget_exhausted"}>{a.status === "active" ? "Pause" : "Resume"}</button>
              <button onClick={() => budget(a)}>Budget</button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Tasks({ scope }: { scope: Scope }) {
  const { data, err } = useLoad<any[]>(`/api/tasks?scope=${scope}`, [scope], 10000);
  return (
    <div className="list">{err && <p className="err">{err}</p>}
      {data?.length === 0 && <p className="hint">No tasks yet.</p>}
      {data?.map((t) => (
        <div className="card" key={`${t.company}-${t.id}`}>
          <div className="row"><b>{t.title}</b><span className={`badge ${t.status}`}>{t.status}</span></div>
          <small>{t.company} · {fmt(t.updated_at)}</small>
        </div>
      ))}
    </div>
  );
}

export function Activity({ scope }: { scope: Scope }) {
  const { data, err } = useLoad<any[]>(`/api/activity?scope=${scope}`, [scope], 10000);
  return (
    <div className="list">{err && <p className="err">{err}</p>}
      {data?.map((e, i) => (
        <div className="card slim" key={i}>
          <div className="row"><span className="badge">{e.company}</span><b>{e.label}</b></div>
          {e.detail && <small>{typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}</small>}
          <small>{fmt(e.at)}</small>
        </div>
      ))}
    </div>
  );
}

export function Costs({ scope }: { scope: Scope }) {
  const { data, err } = useLoad<any[]>(`/api/costs?scope=${scope}`, [scope], 15000);
  const total = (data ?? []).reduce((s, r) => s + Number(r.cost_usd), 0);
  return (
    <div className="list">{err && <p className="err">{err}</p>}
      <div className="card"><b>This month: {usd(total)}</b><br /><small>Route-by-route comparison appears here once OmniRoute is enabled (Phase 3).</small></div>
      {data?.map((r, i) => (
        <div className="card slim" key={i}>
          <div className="row"><span className="badge">{r.company}</span><b>{r.agent}</b><span className="badge">{r.data_class}</span></div>
          <small>{r.provider} / {r.route} · {r.tokens_in} in · {r.tokens_out} out · {usd(r.cost_usd)}</small>
        </div>
      ))}
    </div>
  );
}
