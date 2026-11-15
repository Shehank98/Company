import { useEffect, useState } from "react";
import { api, AuthError, Scope } from "./api";
import Login from "./Login";
import { Activity, Approvals, Chat, Costs, Departments, Tasks } from "./pages";

const TABS = [
  ["chat", "💬", "Chat"], ["approvals", "✅", "Approvals"], ["depts", "🏢", "Depts"],
  ["tasks", "📋", "Tasks"], ["activity", "📜", "Activity"], ["costs", "💲", "Costs"],
] as const;
type Tab = (typeof TABS)[number][0];

function urlB64(s: string) {
  const p = "=".repeat((4 - (s.length % 4)) % 4), b = atob((s + p).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...b].map((c) => c.charCodeAt(0)));
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [scope, setScope] = useState<Scope>("all");
  const [pending, setPending] = useState(0);

  useEffect(() => { api("/auth/me").then(() => setAuthed(true)).catch(() => setAuthed(false)); }, []);
  useEffect(() => {
    if (!authed) return;
    const load = () => api<any[]>(`/api/approvals?scope=${scope}`).then((a) => setPending(a.length)).catch((e) => e instanceof AuthError && setAuthed(false));
    load(); const t = setInterval(load, 10000); return () => clearInterval(t);
  }, [authed, scope, tab]);

  async function enablePush() {
    try {
      const { vapid_public_key } = await api<{ vapid_public_key: string }>("/api/config");
      if (!vapid_public_key) return alert("Push is not configured on the server yet (VAPID keys).");
      if ((await Notification.requestPermission()) !== "granted") return;
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64(vapid_public_key) });
      await api("/api/push/subscribe", { body: sub.toJSON() });
      alert("Notifications enabled.");
    } catch (e: any) { alert(`Could not enable notifications: ${e.message}`); }
  }
  async function logout() { await api("/auth/logout", { method: "POST" }); setAuthed(false); }

  if (authed === null) return <div className="login"><p>Loading…</p></div>;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  const Page = { chat: Chat, approvals: Approvals, depts: Departments, tasks: Tasks, activity: Activity, costs: Costs }[tab];
  return (
    <div className="app">
      <header>
        <div className="seg">
          {(["adspot", "sidadiya", "all"] as Scope[]).map((s) => (
            <button key={s} className={scope === s ? "on" : ""} onClick={() => setScope(s)}>{s === "all" ? "All" : s === "adspot" ? "AdSpot" : "Sidadiya"}</button>
          ))}
        </div>
        <button className="icon" onClick={enablePush} title="Enable notifications">🔔</button>
        <button className="icon" onClick={logout} title="Sign out">⎋</button>
      </header>
      <main><Page scope={scope} /></main>
      <nav>
        {TABS.map(([k, ico, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
            <span>{ico}{k === "approvals" && pending > 0 && <i className="dot">{pending}</i>}</span><small>{label}</small>
          </button>
        ))}
      </nav>
    </div>
  );
}
