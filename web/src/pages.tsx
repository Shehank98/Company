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
  async function upload(f?: File, kind = "") {
    if (!f) return;
    const form = new FormData(); form.append("file", f); form.append("scope", scope === "all" ? "adspot" : scope);
    if (kind === "receipt") { form.append("kind", "receipt"); form.append("booking_ref", prompt("Which booking? Reference (leave empty to say it in chat)") ?? ""); }
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
        <label className="icon" title="Publication receipt">🧾<input type="file" hidden accept="image/*,application/pdf" onChange={(e) => upload(e.target.files?.[0], "receipt")} /></label>
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
  const [ok, setOk] = useState<Record<number, boolean>>({});
  const [note, setNote] = useState("");
  async function act(a: any, decision: string) {
    setNote("");
    try {
      const r = await api<{ note?: string }>(`/api/approvals/${a.company}/${a.id}`, { body: { decision, summary: edit[a.id], accept_mismatch: !!ok[a.id] } });
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
          {(a.booking_ref || a.amount) && (
            <p className="confirm">Booking <b>{a.booking_ref ?? "?"}</b>{a.payload?.customer ? <> · {a.payload.customer}</> : null} · Amount <b>Rs. {Number(a.amount).toLocaleString()}</b>
              {a.payload?.expected_total != null && <><br /><small>Booking total: Rs. {Number(a.payload.expected_total).toLocaleString()}</small></>}
              {a.payload?.matched_exactly === false && <><br /><small>Matched from a partial reference. Check the booking above is the right one.</small></>}</p>)}
          {a.payload?.mismatch && (
            <label className="confirm bad"><input type="checkbox" checked={!!ok[a.id]} onChange={(e) => setOk({ ...ok, [a.id]: e.target.checked })} style={{ width: 22, minHeight: 22 }} />
              {" "}Amounts differ. Confirm anyway.</label>)}
          <pre className="payload">{JSON.stringify(a.payload, null, 1)}</pre>
          <div className="row">
            <button className="primary" onClick={() => act(a, "approve")} disabled={!!a.payload?.mismatch && !ok[a.id]}>{a.tier === "OWNER_COMMAND" ? "Confirm" : "Approve"}</button>
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


// ---------------- Bookings + payouts ----------------
const STATE_LABEL: Record<string, string> = { awaiting_payment: "awaiting payment", paid: "paid", sent_to_publication: "sent to paper", receipt_received: "receipt in", customer_receipt_sent: "customer has receipt", published: "published", closed: "closed", exception: "exception", enquiry: "enquiry" };

export function Bookings({ scope }: { scope: Scope }) {
  const co = scope === "sidadiya" ? "sidadiya" : "adspot";
  const { data, err, reload } = useLoad<any[]>(`/api/bookings?scope=${co}`, [co], 10000);
  const pay = useLoad<any[]>(`/api/payouts?scope=${co}`, [co], 15000);
  const [msg, setMsg] = useState("");
  if (co === "sidadiya") return <div className="list"><p className="hint">Booking payments and payouts are AdSpot only. Switch to AdSpot.</p></div>;
  const run = async (fn: () => Promise<any>, ok?: (r: any) => string) => { setMsg(""); try { const r = await fn(); if (ok) setMsg(ok(r)); reload(); pay.reload(); } catch (e: any) { setMsg(e.message); } };
  const markPaid = (b: any) => run(() => api(`/api/bookings/adspot/${b.booking_ref}/mark-paid`, { body: {} }), (r) => r.reply);
  const receipt = (b: any, f?: File) => { if (!f) return; const form = new FormData(); form.append("file", f); run(() => api(`/api/bookings/adspot/${b.booking_ref}/receipt`, { form }), () => "Receipt saved. Review the customer mail in the Mail tab."); };
  return (
    <div className="list">
      {(err || msg) && <p className={err ? "err" : "hint"} style={{ whiteSpace: "pre-wrap" }}>{err || msg}</p>}
      <button onClick={() => run(() => api("/api/bookings/adspot/sync", { method: "POST" }), (r) => `Synced ${r.synced} booking(s).`)}>Refresh from AdSpot</button>
      {data?.length === 0 && <p className="hint">No bookings yet. They appear within 2 minutes of the worker running.</p>}
      {data?.map((b) => (
        <div className="card" key={b.booking_ref}>
          <div className="row"><b>{b.booking_ref}</b><span className={`badge ${b.state === "exception" ? "failed" : ""}`}>{STATE_LABEL[b.state] ?? b.state}</span></div>
          <small>{b.quotation_ref} · {b.customer_name} · {b.customer_email}</small>
          <div>Rs. {Number(b.total_amount).toLocaleString()} {b.payment_recorded && <span className="badge done">payment recorded</span>}</div>
          {b.exception_reason && <small className="err">{b.exception_reason}</small>}
          <div className="row">
            {!b.payment_recorded && b.state !== "exception" && <button className="primary" onClick={() => markPaid(b)}>Mark paid</button>}
            {b.payment_recorded && <button onClick={() => run(() => api(`/api/bookings/adspot/${b.booking_ref}/publication-drafts`, { method: "POST" }), (r) => (r.problems?.[0] ?? `${r.drafts.length} draft(s) in Mail.`))}>Publication draft</button>}
            {b.payment_recorded && <label className="btnlink">Upload receipt<input type="file" hidden accept="image/*,application/pdf" onChange={(e) => receipt(b, e.target.files?.[0])} /></label>}
            {["customer_receipt_sent", "published"].includes(b.state) && <button onClick={() => run(() => api(`/api/bookings/adspot/${b.booking_ref}/state`, { body: { to: b.state === "published" ? "closed" : "published" } }))}>{b.state === "published" ? "Close" : "Mark published"}</button>}
          </div>
        </div>
      ))}
      <h3>Payouts to publications (you pay by hand)</h3>
      {pay.data?.slice(0, 2).map((p) => (
        <div className="card" key={p.id}>
          <div className="row"><b>Batch #{p.id}</b><span className={`badge ${p.status === "open" ? "warn" : "done"}`}>{p.status.replace("_", " ")}</span></div>
          {Object.entries(p.totals as Record<string, number>).map(([k, v]) => <div className="row" key={k} style={{ justifyContent: "space-between" }}><span>{k}</span><b>Rs. {Number(v).toLocaleString()}</b></div>)}
          {p.items.length === 0 && <small>Nothing waiting.</small>}
          {p.items.length > 0 && <small>{p.items.length} booking(s) · total Rs. {Number(p.grand_total).toLocaleString()}</small>}
          <div className="row">
            {p.items.length > 0 && <a className="btnlink" href={`/api/payouts/adspot/${p.id}.csv`} download>Download CSV</a>}
            {p.status === "open" && p.items.length > 0 && <button onClick={() => confirm("Only press this AFTER you have paid the publications by bank transfer. It just records it.") && run(() => api(`/api/payouts/adspot/${p.id}/mark-paid`, { method: "POST" }), (r) => `Recorded ${r.recorded} payout line(s).`)}>I paid them (record it)</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------- Mail ----------------
const CAT: Record<string, string> = { booking_enquiry: "enquiry", payment_proof: "payment proof", artwork_or_ad_copy: "artwork / ad copy", publication_reply: "publication reply", complaint: "complaint", spam: "spam", other: "other" };

function Countdown({ until }: { until: string }) {
  const [n, setN] = useState(0);
  useEffect(() => { const t = setInterval(() => setN((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const left = Math.max(0, Math.ceil((new Date(until).getTime() - Date.now()) / 1000));
  void n;
  return <span className="badge warn">{left > 0 ? `sends in ${left}s` : "sending…"}</span>;
}

function Outbox({ m, undo, reload, setMsg }: { m: any; undo: number; reload: () => void; setMsg: (s: string) => void }) {
  const [subject, setSubject] = useState(m.subject);
  const [body, setBody] = useState(m.body);
  const call = async (path: string, method = "POST", b?: unknown) => { setMsg(""); try { await api(path, { method, body: b }); reload(); } catch (e: any) { setMsg(e.message); } };
  const id = `/api/mail/adspot/${m.id}`;
  const draft = m.status === "draft";
  return (
    <div className="card">
      <div className="row"><span className="badge">{m.kind.replace("_", " ")}</span><span className={`badge ${m.status === "sent" ? "done" : m.status === "failed" ? "failed" : "warn"}`}>{m.status}</span>
        {m.status === "scheduled" && m.send_after && <Countdown until={m.send_after} />}<small>{m.booking_ref}</small></div>
      <small>To: <b>{m.to_addr}</b></small>
      {draft ? <><input value={subject} onChange={(e) => setSubject(e.target.value)} /><textarea rows={9} value={body} onChange={(e) => setBody(e.target.value)} /></>
        : <><b>{m.subject}</b><div className="text">{m.body}</div></>}
      {(m.attachments ?? []).length > 0 && <small>Attachments: {m.attachments.map((a: any) => a.name).join(", ")}</small>}
      {m.error && <p className="err">{m.error}</p>}
      <div className="row">
        {draft && <button onClick={() => call(id, "PUT", { subject, body })} disabled={subject === m.subject && body === m.body}>Save edit</button>}
        {draft && <button className="primary" onClick={() => confirm(`Send this mail to ${m.to_addr} now?`) && call(`${id}/send`)}>Send</button>}
        {m.status === "scheduled" && <button className="danger" onClick={() => call(`${id}/undo`)}>Undo</button>}
        {["cancelled", "failed"].includes(m.status) && m.kind.startsWith("customer") && <button onClick={() => call(`${id}/reschedule`)}>Send again</button>}
      </div>
    </div>
  );
}

function Publications() {
  const { data, reload } = useLoad<any[]>("/api/publications?scope=adspot");
  const settings = useLoad<Record<string, string>>("/api/settings/adspot");
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const save = async (p: any) => { try { await api("/api/publications/adspot", { method: "PUT", body: { group_ref: p.group_ref, name: p.name, email: edit[p.group_ref] ?? p.email, active: p.active } }); setErr(""); setEdit({}); reload(); } catch (e: any) { setErr(e.message); } };
  const toggle = async (k: string, v: boolean) => { await api(`/api/settings/adspot/${k}`, { method: "PUT", body: { value: String(v) } }); settings.reload(); };
  return (
    <div className="card">
      <button onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} publication addresses and mail settings</button>
      {open && <>
        <small>These are the ONLY addresses AgentCo will ever mail a publication. They are never taken from an email.</small>
        {data?.map((p) => (
          <div className="kv" key={p.group_ref}><small>{p.name} ({p.group_ref})</small>
            <input placeholder="newspaper desk email" value={edit[p.group_ref] ?? p.email} onChange={(e) => setEdit({ ...edit, [p.group_ref]: e.target.value })} />
            {edit[p.group_ref] !== undefined && <button className="small primary" onClick={() => save(p)}>Save</button>}</div>))}
        {err && <p className="err">{err}</p>}
        <label className="row"><input type="checkbox" style={{ width: 22, minHeight: 22 }} checked={settings.data?.send_payment_confirmation === "true"} onChange={(e) => toggle("send_payment_confirmation", e.target.checked)} /> Send my own "payment received" mail to customers (off: AdSpot's mail already does it)</label>
        <label className="row"><input type="checkbox" style={{ width: 22, minHeight: 22 }} checked={settings.data?.auto_send_receipts === "true"} onChange={(e) => toggle("auto_send_receipts", e.target.checked)} /> Auto-send publication receipts to customers (after the undo window)</label>
      </>}
    </div>
  );
}

export function Mail({ scope }: { scope: Scope }) {
  const co = scope === "sidadiya" ? "sidadiya" : "adspot";
  const { data, err, reload } = useLoad<any>(`/api/mail?scope=${co}`, [co], 4000);
  const [msg, setMsg] = useState("");
  if (co === "sidadiya") return <div className="list"><p className="hint">The mailbox workflow is AdSpot only. Switch to AdSpot.</p></div>;
  const out: any[] = data?.outbox ?? [];
  const todo = out.filter((m) => ["draft", "scheduled", "failed", "sending"].includes(m.status));
  const done = out.filter((m) => ["sent", "cancelled"].includes(m.status));
  return (
    <div className="list">
      {err && <p className="err">{err}</p>}{msg && <p className="err">{msg}</p>}
      {data && !data.gmail_configured && <p className="confirm">Gmail is not connected yet, so no mail can be read or sent. Drafts still appear here. See the README (Gmail setup).</p>}
      <Publications />
      <h3>Needs you</h3>
      {todo.length === 0 && <p className="hint">No drafts or scheduled mail.</p>}
      {todo.map((m) => <Outbox key={m.id} m={m} undo={data.undo_window_seconds} reload={reload} setMsg={setMsg} />)}
      <h3>Inbox summaries</h3>
      {data?.inbox.length === 0 && <p className="hint">No mail triaged yet.</p>}
      {data?.inbox.map((m: any) => (
        <div className="card slim" key={m.id}>
          <div className="row"><span className="badge">{CAT[m.category] ?? m.category}</span>{m.booking_ref && <span className={`badge ${m.link_confidence === "verified" ? "done" : "warn"}`}>{m.booking_ref}{m.link_confidence === "verified" ? "" : " · sender unverified"}</span>}</div>
          <b>{m.subject}</b><small>{m.from_addr}</small><div>{m.summary}</div>{m.draft_id && <small>A reply draft is waiting in Gmail.</small>}
        </div>))}
      <h3>Sent / cancelled</h3>
      {done.map((m) => <Outbox key={m.id} m={m} undo={0} reload={reload} setMsg={setMsg} />)}
    </div>
  );
}

// ---------------- YouTube Shorts ----------------
const VSTATUS: Record<string, string> = { drafting_script: "writing script", script_review: "review script", assets: "add pictures", rendering: "rendering", review: "watch & upload", uploaded: "on YouTube (private)", rejected: "rejected", failed: "failed" };

function ShortCard({ p, caps, reload }: { p: any; caps: any; reload: () => void }) {
  const base = `/api/video/${p.company}/${p.id}`;
  const [msg, setMsg] = useState("");
  const [shots, setShots] = useState<Record<number, any>>({});
  // the form follows the project (title arrives after the script is written) until the owner edits it
  const [metaEdit, setMetaEdit] = useState<{ title: string; description: string; tags: string } | null>(null);
  const meta: { title: string; description: string; tags: string } = metaEdit ?? { title: p.title ?? "", description: p.description ?? "", tags: (p.tags ?? []).join(", ") };
  const setMeta = (m: { title: string; description: string; tags: string }) => setMetaEdit(m);
  useEffect(() => { setMsg(""); }, [p.status]);
  const [ph, setPh] = useState(false);
  const call = async (path: string, body?: unknown, ok?: string) => { setMsg(""); try { await api(`${base}${path}`, { method: "POST", body: body ?? {} }); if (ok) setMsg(ok); reload(); } catch (e: any) { setMsg(e.message); } };
  const saveShot = async (s: any) => { const v = { ...s, ...shots[s.beat_no] }; try { await api(`${base}/shots/${s.beat_no}`, { method: "PUT", body: { spoken: v.spoken, on_screen: v.on_screen, visual_prompt: v.visual_prompt } }); setShots((x) => { const { [s.beat_no]: _, ...r } = x; return r; }); reload(); } catch (e: any) { setMsg(e.message); } };
  const saveMeta = async () => { try { await api(`${base}/meta`, { method: "PUT", body: { title: meta.title, description: meta.description, tags: meta.tags.split(",").map((t) => t.trim()).filter(Boolean) } }); setMetaEdit(null); setMsg("Saved."); reload(); } catch (e: any) { setMsg(e.message); } };
  const upAsset = async (beat: number, f?: File) => { if (!f) return; const form = new FormData(); form.append("file", f); setMsg("Checking the file…"); try { await api(`${base}/shots/${beat}/asset`, { form }); setMsg(""); reload(); } catch (e: any) { setMsg(e.message); } };
  const words = (p.shots ?? []).reduce((n: number, s: any) => n + (shots[s.beat_no]?.spoken ?? s.spoken).split(/\s+/).filter(Boolean).length, 0);
  const issues: string[] = p.claims_check?.issues ?? [];
  const stuck = (p.shots ?? []).some((s: any) => s.error || s.status === "failed");
  const active = !["rejected", "uploaded"].includes(p.status);
  return (
    <div className="card">
      <div className="row"><span className="badge">{p.company}</span><span className={`badge ${p.status === "failed" ? "failed" : p.status === "uploaded" ? "done" : "warn"}`}>{VSTATUS[p.status] ?? p.status}</span><small>#{p.id} · {p.voice_mode === "tts" ? "AI voice" : "no voice"} · {p.clip_mode === "veo_api" ? "Veo clips" : "Flow clips"}</small></div>
      <b>{p.topic}</b>
      {p.error && <p className="err">{p.error}</p>}
      {p.status === "drafting_script" && <small>Writing hooks and a script… about a minute.</small>}

      {p.status === "script_review" && <>
        {issues.length > 0 && <div className="confirm"><b>Check before you go on:</b><ul>{issues.map((i, k) => <li key={k}>{i}</li>)}</ul></div>}
        {p.hook_options?.[0]?.band === "WEAK" && <div className="confirm"><b>The best hook only scored {p.hook_options[0].score} (WEAK).</b> The first 3 seconds decide whether people stay. Rewrite beat 1 below, or reject this and write a new one.</div>}
        <div className="block"><b>Hooks (scored by the skill's hook tool)</b>
          {(p.hook_options ?? []).map((h: any, k: number) => (
            <div key={k} className="kv"><div className="row"><span className={`badge ${h.band === "STRONG" ? "done" : h.band === "WEAK" ? "failed" : "warn"}`}>{h.score} {h.band}</span>{k === 0 && <span className="badge done">used</span>}<small>{h.detected_formula}</small></div><div className="text">{h.line}</div></div>))}
        </div>
        {(p.shots ?? []).map((s: any) => { const v = { ...s, ...shots[s.beat_no] }; const set = (k: string, val: string) => setShots({ ...shots, [s.beat_no]: { ...shots[s.beat_no], [k]: val } });
          return (<div className="block" key={s.beat_no}><b>Beat {s.beat_no}</b>
            <small>Voice says</small><textarea rows={2} value={v.spoken} onChange={(e) => set("spoken", e.target.value)} />
            <small>On screen (different words)</small><input value={v.on_screen} onChange={(e) => set("on_screen", e.target.value)} />
            <small>Picture prompt for Google Flow / Veo</small><textarea rows={3} value={v.visual_prompt} onChange={(e) => set("visual_prompt", e.target.value)} />
            {shots[s.beat_no] && <button className="small primary" onClick={() => saveShot(s)}>Save beat</button>}</div>); })}
        <small>{words} spoken words (keep under ~125 so it stays under a minute)</small>
        <div className="block"><b>YouTube title, description, tags</b>
          <input value={meta.title} onChange={(e) => setMeta({ ...meta, title: e.target.value })} />
          <textarea rows={3} value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} />
          <input placeholder="tags, comma separated" value={meta.tags} onChange={(e) => setMeta({ ...meta, tags: e.target.value })} />
          <button className="small" onClick={saveMeta}>Save details</button></div>
        <div className="row"><button className="primary" onClick={() => call("/approve-script", {}, "Script approved.")}>Approve script</button></div>
      </>}

      {(p.status === "assets" || p.status === "rendering" || p.status === "review") && p.status !== "review" && (p.shots ?? []).map((s: any) => (
        <div className="block" key={s.beat_no}>
          <div className="row"><b>Beat {s.beat_no}</b><span className={`badge ${s.status === "ready" ? "done" : s.status === "failed" ? "failed" : "warn"}`}>{s.status === "ready" ? `${s.asset_kind} ready` : s.status}</span>{p.voice_mode === "tts" && <span className={`badge ${s.voice_file ? "done" : "warn"}`}>{s.voice_file ? "voice ✓" : "voice…"}</span>}</div>
          <div className="text">{s.spoken}</div>
          {s.status !== "ready" && p.clip_mode === "flow_manual" && <div className="row"><div className="text grow"><small>Flow prompt:</small> {s.visual_prompt}</div><CopyBtn text={s.visual_prompt} /></div>}
          {s.error && <small className="err">{s.error}</small>}
          {p.status === "assets" && <label className="btnlink">{s.asset_file ? "Replace picture" : "Upload clip or image"}<input type="file" hidden accept="video/mp4,video/quicktime,video/webm,image/png,image/jpeg,image/webp" onChange={(e) => upAsset(s.beat_no, e.target.files?.[0])} /></label>}
        </div>))}
      {p.status === "assets" && <>
        <label className="row"><input type="checkbox" style={{ width: 22, minHeight: 22 }} checked={ph} onChange={(e) => setPh(e.target.checked)} /> Use plain placeholder cards for beats with no picture</label>
        <div className="row"><button className="primary" onClick={() => call("/render", { allow_placeholders: ph }, "Rendering started.")}>Render video</button>{stuck && <button onClick={() => call("/retry-assets", {}, "Retrying.")}>Retry voice / clips</button>}</div></>}
      {p.status === "rendering" && <small>Rendering with ffmpeg… under a minute.</small>}

      {p.status === "review" && <>
        <video className="pic" controls playsInline preload="metadata" poster={`${base}/preview.jpg?v=${p.updated_at}`} src={`${base}/file?v=${p.updated_at}`} style={{ maxHeight: 520 }} />
        <small>{Number(p.duration_s).toFixed(0)} seconds · 1080×1920</small>
        <div className="block"><b>YouTube title, description, tags</b>
          <input value={meta.title} onChange={(e) => setMeta({ ...meta, title: e.target.value })} />
          <textarea rows={3} value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} />
          <input placeholder="tags, comma separated" value={meta.tags} onChange={(e) => setMeta({ ...meta, tags: e.target.value })} />
          <button className="small" onClick={saveMeta}>Save details</button></div>
        <small>Want a different picture? Go back and replace it, then render again.</small>
        <div className="row">
          <button className="primary" disabled={!caps?.youtube?.[p.company]} onClick={() => confirm("Upload to YouTube as PRIVATE? You will publish it yourself in YouTube Studio.") && call("/upload", {}, "Uploaded as private.")}>Upload to YouTube (private)</button>
        </div>
        {!caps?.youtube?.[p.company] && <small className="err">YouTube is not connected for this company yet (see docs/youtube.md). You can still download the video below.</small>}
        <a className="btnlink" href={`${base}/file`} download={`short-${p.id}.mp4`}>Download video</a>
      </>}

      {p.status === "uploaded" && p.upload && <div className="confirm"><b>On YouTube as PRIVATE</b><br /><a href={p.upload.url} target="_blank" rel="noreferrer" style={{ color: "var(--acc)" }}>{p.upload.url}</a><br /><small>Open YouTube Studio &gt; Content and set it to Public when you are happy. AgentCo never publishes for you.</small></div>}
      {msg && <p className={/saved|approved|started|retrying|uploaded/i.test(msg) ? "hint" : "err"}>{msg}</p>}
      {active && <div className="row"><button className="danger" onClick={() => confirm("Throw this video away?") && call("/reject")}>Reject</button></div>}
    </div>
  );
}

export function Shorts({ scope }: { scope: Scope }) {
  const { data, err, reload } = useLoad<any>(`/api/video?scope=${scope}`, [scope], 4000);
  const caps = data?.capabilities;
  const [company, setCompany] = useState<string>(scope === "all" ? "adspot" : scope);
  const [topic, setTopic] = useState("");
  const [voice, setVoice] = useState("tts");
  const [clips, setClips] = useState("flow_manual");
  const [msg, setMsg] = useState("");
  useEffect(() => { if (scope !== "all") setCompany(scope); }, [scope]);
  useEffect(() => { if (caps && !caps.tts && voice === "tts") setVoice("none"); }, [caps]);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setMsg("");
    try { await api("/api/video", { body: { company, topic, voice_mode: voice, clip_mode: clips } }); setTopic(""); reload(); } catch (e: any) { setMsg(e.message); }
  }
  return (
    <div className="list">
      <form className="card" onSubmit={create}>
        <b>New YouTube Short</b>
        <div className="row">
          {scope === "all" && <select value={company} onChange={(e) => setCompany(e.target.value)}><option value="adspot">AdSpot</option><option value="sidadiya">Sidadiya</option></select>}
          <select value={voice} onChange={(e) => setVoice(e.target.value)}><option value="tts" disabled={!caps?.tts}>Voice: AI (Gemini){caps?.tts ? "" : " - not set up"}</option><option value="none">Voice: none (captions only)</option></select>
          <select value={clips} onChange={(e) => setClips(e.target.value)}><option value="flow_manual">Clips: Google Flow (I upload)</option><option value="veo_api" disabled={!caps?.veo}>Clips: Veo (auto, paid){caps?.veo ? "" : " - not set up"}</option></select>
        </div>
        <input placeholder="What is the Short about? e.g. booking a newspaper ad online" value={topic} onChange={(e) => setTopic(e.target.value)} />
        <button className="primary" disabled={topic.trim().length < 3}>Write the script</button>
        {msg && <p className="err">{msg}</p>}
        <small>You review the script, add the pictures, watch the video, and upload as private. You publish it in YouTube Studio.</small>
      </form>
      {err && <p className="err">{err}</p>}
      {data?.projects.map((p: any) => <ShortCard key={`${p.company}-${p.id}`} p={p} caps={caps} reload={reload} />)}
    </div>
  );
}
