import { useEffect, useRef, useState } from "react";
import { api } from "./api";

declare global { interface Window { google?: any } }

export default function Login({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<"google" | "totp" | "totp_setup">("google");
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const btn = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (step !== "google") return;
    let cancelled = false;
    api<{ google_client_id: string }>("/api/config").then(({ google_client_id }) => {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client"; s.async = true;
      s.onload = () => {
        if (cancelled || !window.google || !btn.current) return;
        window.google.accounts.id.initialize({
          client_id: google_client_id,
          callback: async (resp: { credential: string }) => {
            try {
              const r = await api<any>("/auth/google", { body: { id_token: resp.credential } });
              setErr(""); if (r.step === "totp_setup") setSetup(r); setStep(r.step);
            } catch (e: any) { setErr(e.message); }
          },
        });
        window.google.accounts.id.renderButton(btn.current, { theme: "filled_blue", size: "large", width: 260 });
      };
      document.head.appendChild(s);
    }).catch(() => setErr("Could not load sign-in config"));
    return () => { cancelled = true; };
  }, [step]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try { await api("/auth/totp", { body: { code } }); onDone(); } catch (e: any) { setErr(e.message); }
  }

  return (
    <div className="login">
      <h1>AgentCo</h1>
      {step === "google" && <><p>Sign in with your owner Google account.</p><div ref={btn} /></>}
      {step !== "google" && (
        <form onSubmit={submit}>
          {step === "totp_setup" && setup && (
            <p className="hint">First time: add this key to your authenticator app, then enter the 6-digit code.<br />
              <code>{setup.secret}</code></p>
          )}
          <input inputMode="numeric" autoComplete="one-time-code" placeholder="6-digit code" value={code}
                 onChange={(e) => setCode(e.target.value)} autoFocus />
          <button className="primary">Continue</button>
        </form>
      )}
      {err && <p className="err">{err}</p>}
    </div>
  );
}
