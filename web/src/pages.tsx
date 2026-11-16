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

// ---------------- Posts (manual publish) ----------------
function CopyBtn({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="small" onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); } catch (_) { prompt("Copy this:", text); } }}>
      {done ? "Copied ✓" : label}
    </button>
  );
}

const PLATFORMS: [string, string][] = [["facebook", "Facebook"], ["instagram", "Instagram"], ["x", "X"], ["linkedin", "LinkedIn"]];

function Pack({ p, reload }: { p: any; reload: () => void }) {
  const [err, setErr] = useState("");
  const move = async (status: string) => { try { await api(`/api/content/${p.company}/${p.id}/status`, { body: { status } }); reload(); } catch (e: any) { setErr(e.message); } };
  async function upload(f?: File) {
    if (!f) return;
    const form = new FormData(); form.append("file", f);
    try { await api(`/api/content/${p.company}/${p.id}/image`, { form }); reload(); } catch (e: any) { setErr(e.message); }
  }
  const issues: string[] = p.claims_check?.issues ?? [];
  const tags = (p.hashtags ?? []).join(" ");
  return (
    <div className="card">
      <div className="row"><span className="badge">{p.company}</span><span className="badge">{p.kind}</span>
        <span className={`badge ${p.status}`}>{p.status.replace("_", " ")}</span><small>#{p.id}</small></div>
      <b>{p.topic}</b>
      {p.status === "drafting" && <small>Drafting… this usually takes under a minute.</small>}
      {p.status === "failed" && <p className="err">Failed: {p.error}</p>}
      {issues.length > 0 && <div className="confirm"><b>Check before posting:</b><ul>{issues.map((i, k) => <li key={k}>{i}</li>)}</ul></div>}
      {PLATFORMS.map(([k, label]) => p.copy?.[k] ? (
        <div className="block" key={k}><div className="row"><b>{label}</b><CopyBtn text={p.copy[k]} /></div><div className="text">{p.copy[k]}</div></div>
      ) : null)}
      {tags && <div className="block"><div className="row"><b>Hashtags</b><CopyBtn text={tags} /></div><div className="text">{tags}</div></div>}
      {p.status !== "drafting" && p.status !== "failed" && (
        <div className="block">
          <b>Image</b>
          {p.image_file ? (
            <><img className="pic" src={`/api/content/${p.company}/${p.id}/image?v=${p.updated_at}`} alt="draft" />
              <a className="btnlink" href={`/api/content/${p.company}/${p.id}/image`} download>Download image</a></>
          ) : <small>No image yet. Make it in Google Flow with the prompt below, then upload it here.</small>}
          {p.image_brief && <div className="text"><small>Brief:</small> {p.image_brief}</div>}
          {p.image_prompt && <div className="row"><div className="text grow"><small>Prompt:</small> {p.image_prompt}</div><CopyBtn text={p.image_prompt} /></div>}
          {p.aspect_ratios?.facebook && <small>Sizes: Facebook {p.aspect_ratios.facebook}, Instagram {p.aspect_ratios.instagram}</small>}
          <label className="btnlink">Upload image<input type="file" hidden accept="image/png,image/jpeg,image/webp" onChange={(e) => upload(e.target.files?.[0])} /></label>
        </div>
      )}
      {p.ad_setup && (
        <div className="block"><div className="row"><b>Ads Manager setup sheet</b>
          <CopyBtn label="Copy all" text={Object.entries(p.ad_setup).filter(([, v]) => v).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join("\n")} /></div>
          {Object.entries(p.ad_setup).filter(([, v]) => v).map(([k, v]) => <div className="kv" key={k}><small>{k.replace(/_/g, " ")}</small><div>{String(v)}</div></div>)}
          <small>Type these into Ads Manager yourself. Check the budget and audience before you publish.</small>
        </div>
      )}
      {err && <p className="err">{err}</p>}
      <div className="row">
        {(p.status === "draft" || p.status === "needs_review") && <button className="primary" onClick={() => move("approved")}>Approve</button>}
        {p.status === "approved" && <button className="primary" onClick={() => move("posted")}>I posted it</button>}
        {["draft", "needs_review", "approved", "failed"].includes(p.status) && <button className="danger" onClick={() => move("rejected")}>Reject</button>}
      </div>
    </div>
  );
}

function Facts({ scope }: { scope: Scope }) {
  const { data, reload } = useLoad<any[]>(`/api/facts?scope=${scope}`, [scope]);
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [nk, setNk] = useState(""); const [nv, setNv] = useState(""); const [nc, setNc] = useState("adspot");
  const [err, setErr] = useState("");
  async function save(company: string, key: string, value: string) {
    try { await api(`/api/facts/${company}`, { method: "PUT", body: { key, value } }); setErr(""); reload(); setEdit({}); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="card">
      <button onClick={() => setOpen(!open)}>{open ? "Hide" : "Edit"} brand facts (the only facts the writer may use)</button>
      {open && <>
        <small>Public facts only. Add offers or prices here if you want them used; nothing else is ever invented.</small>
        {data?.map((f) => { const id = `${f.company}:${f.key}`; return (
          <div className="kv" key={id}><small>{f.company} · {f.key}</small>
            <textarea rows={2} value={edit[id] ?? f.value} onChange={(e) => setEdit({ ...edit, [id]: e.target.value })} />
            {edit[id] !== undefined && <button className="small primary" onClick={() => save(f.company, f.key, edit[id])}>Save</button>}
          </div>); })}
        <div className="kv"><small>Add a fact</small>
          <select value={nc} onChange={(e) => setNc(e.target.value)}><option value="adspot">AdSpot</option><option value="sidadiya">Sidadiya</option></select>
          <input placeholder="key (e.g. offer)" value={nk} onChange={(e) => setNk(e.target.value.toLowerCase())} />
          <textarea rows={2} placeholder="fact" value={nv} onChange={(e) => setNv(e.target.value)} />
          <button className="small" onClick={() => { save(nc, nk, nv); setNk(""); setNv(""); }}>Add</button></div>
        {err && <p className="err">{err}</p>}
      </>}
    </div>
  );
}

export function Posts({ scope }: { scope: Scope }) {
  const { data, err, reload } = useLoad<any[]>(`/api/content?scope=${scope}`, [scope], 4000);
  const [company, setCompany] = useState<string>(scope === "all" ? "adspot" : scope);
  const [kind, setKind] = useState("post");
  const [mode, setMode] = useState("flow_manual");
  const [topic, setTopic] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { if (scope !== "all") setCompany(scope); }, [scope]);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setMsg("");
    try { await api("/api/content", { body: { company, kind, topic, image_mode: mode } }); setTopic(""); reload(); } catch (e: any) { setMsg(e.message); }
  }
  return (
    <div className="list">
      <form className="card" onSubmit={create}>
        <b>New draft</b>
        <div className="row">
          {scope === "all" && <select value={company} onChange={(e) => setCompany(e.target.value)}><option value="adspot">AdSpot</option><option value="sidadiya">Sidadiya</option></select>}
          <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="post">Post</option><option value="ad">Ad (Ads Manager sheet)</option></select>
          <select value={mode} onChange={(e) => setMode(e.target.value)}><option value="flow_manual">Image: Google Flow (manual)</option><option value="gemini_image">Image: Gemini (auto)</option></select>
        </div>
        <input placeholder="What is it about? e.g. booking classified ads online" value={topic} onChange={(e) => setTopic(e.target.value)} />
        <button className="primary" disabled={topic.trim().length < 3}>Draft it</button>
        {msg && <p className="err">{msg}</p>}
        <small>Nothing is published by AgentCo. You copy the text and image into Facebook yourself.</small>
      </form>
      {err && <p className="err">{err}</p>}
      {data?.map((p) => <Pack key={`${p.company}-${p.id}`} p={p} reload={reload} />)}
      <Facts scope={scope} />
    </div>
  );
}
