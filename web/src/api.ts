export class AuthError extends Error {}
export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const r = await fetch(path, {
    method: opts.method ?? (opts.body || opts.form ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.form ?? (opts.body ? JSON.stringify(opts.body) : undefined),
    credentials: "same-origin",
  });
  if (r.status === 401 && !path.startsWith("/auth/")) throw new AuthError("signed out");
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch (_) {}
    throw new Error(String(msg));
  }
  return r.json();
}
export type Scope = "all" | "adspot" | "sidadiya";
