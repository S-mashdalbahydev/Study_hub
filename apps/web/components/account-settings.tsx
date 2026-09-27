"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type Account } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** Who is signed in, and the deliberate, reversible way to delete the account. */
export function AccountSettings() {
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.me().then(setAccount).catch(() => {});
  }, []);
  if (!account) return null;
  return (
    <section id="account" className="settings-section" aria-labelledby="account-heading">
      <h2 id="account-heading">Account</h2>
      <dl className="account-facts">
        <div>
          <dt>Name</dt>
          <dd>{account.user.display_name}</dd>
        </div>
        <div>
          <dt>Email</dt>
          <dd>{account.user.email}</dd>
        </div>
        <div>
          <dt>Library</dt>
          <dd>{account.workspace?.name ?? "—"}</dd>
        </div>
      </dl>
      <h3>Delete account</h3>
      <p>
        You are logged out everywhere straight away. Your books, notes and
        conversations are kept for 30 days and then deleted for good. To change
        your mind, log in again within those 30 days.
      </p>
      {!confirming ? (
        <Button variant="outline" onClick={() => setConfirming(true)}>
          Delete my account…
        </Button>
      ) : (
        <form
          className="account-delete"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              await api.deleteAccount(password);
              router.replace("/login");
            } catch (err) {
              setError((err as Error).message);
              setBusy(false);
            }
          }}
        >
          <label className="field" htmlFor="delete-password">
            Enter your password to confirm
            <input
              id="delete-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && (
            <p className="notice error" role="alert">
              {error}
            </p>
          )}
          <div className="account-delete-actions">
            <Button type="submit" variant="destructive" disabled={busy || !password}>
              {busy ? "Deleting…" : "Delete account"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setConfirming(false)}>
              Keep my account
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
