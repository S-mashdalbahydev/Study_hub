"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BookOpen, Eye, EyeOff, Quote, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { BrandMark } from "./app-header";
import { AppearanceMenu } from "./theme";

type Mode = "signup" | "login";

/** Only same-site paths: never let ?next= send someone to another website. */
function destination() {
  if (typeof window === "undefined") return "/";
  const next = new URLSearchParams(window.location.search).get("next") || "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

const POINTS = [
  {
    icon: BookOpen,
    title: "Read with a tutor that shows its sources",
    text: "Every answer points to the page it came from in your own book.",
  },
  {
    icon: Quote,
    title: "Turn a topic into a study book",
    text: "Research is gathered, cited and reviewed by you before it joins your library.",
  },
  {
    icon: ShieldCheck,
    title: "Your library stays yours",
    text: "Books, notes and conversations live in your own workspace. Nobody else sees them.",
  },
];

export function AuthPage({ mode }: { mode: Mode }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [restored, setRestored] = useState(false);
  const signup = mode === "signup";

  // Already signed in (a valid cookie): go straight to the library.
  useEffect(() => {
    api
      .me()
      .then(() => router.replace(destination()))
      .catch(() => {});
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (signup) {
        await api.signup({ display_name: name, email, password });
        router.replace("/");
      } else {
        const account = await api.login({ email, password });
        if (account.deletion_cancelled) {
          setRestored(true);
          return;
        }
        router.replace(destination());
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <aside className="auth-atlas" aria-label="About StudyHub">
        <div className="auth-brand">
          <BrandMark size={30} />
          <span>StudyHub</span>
        </div>
        <div className="auth-thesis">
          <p className="auth-eyebrow">A place to understand</p>
          <h1>Your books, your questions, your space to learn.</h1>
        </div>
        <ul className="auth-points">
          {POINTS.map(({ icon: Icon, title, text }) => (
            <li key={title}>
              <Icon size={18} aria-hidden />
              <div>
                <strong>{title}</strong>
                <span>{text}</span>
              </div>
            </li>
          ))}
        </ul>
      </aside>
      <main id="main" className="auth-main">
        <div className="auth-toolbar">
          <AppearanceMenu />
        </div>
        <div className="auth-card">
          {restored ? (
            <div role="status">
              <h2>Welcome back</h2>
              <p className="auth-lede">
                Your account was scheduled for deletion. Signing in cancelled
                that, and everything is as you left it.
              </p>
              <Button className="auth-submit" onClick={() => router.replace(destination())}>
                Continue to your library
              </Button>
            </div>
          ) : (
            <form onSubmit={submit} noValidate={false}>
              <h2>{signup ? "Create your account" : "Log in"}</h2>
              <p className="auth-lede">
                {signup
                  ? "Start a private library for your books and study notes."
                  : "Welcome back. Pick up where you stopped reading."}
              </p>
              {signup && (
                <label className="field" htmlFor="auth-name">
                  Your name
                  <input
                    id="auth-name"
                    autoComplete="name"
                    required
                    maxLength={120}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
              )}
              <label className="field" htmlFor="auth-email">
                Email
                <input
                  id="auth-email"
                  type="email"
                  autoComplete="email"
                  inputMode="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </label>
              <label className="field" htmlFor="auth-password">
                Password
                <span className="auth-password">
                  <input
                    id="auth-password"
                    type={reveal ? "text" : "password"}
                    autoComplete={signup ? "new-password" : "current-password"}
                    required
                    minLength={signup ? 12 : 1}
                    maxLength={256}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    aria-describedby={signup ? "auth-password-hint" : undefined}
                  />
                  <button
                    type="button"
                    className="auth-reveal"
                    onClick={() => setReveal(!reveal)}
                    aria-label={reveal ? "Hide password" : "Show password"}
                    aria-pressed={reveal}
                  >
                    {reveal ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
                  </button>
                </span>
                {signup && (
                  <small id="auth-password-hint">
                    At least 12 characters. A short sentence is easy to remember
                    and hard to guess.
                  </small>
                )}
              </label>
              {error && (
                <p className="notice error" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" size="lg" className="auth-submit" disabled={busy}>
                {busy
                  ? signup
                    ? "Creating your account…"
                    : "Logging in…"
                  : signup
                    ? "Create account"
                    : "Log in"}
              </Button>
              <p className="auth-switch">
                {signup ? (
                  <>
                    Already have an account? <Link href="/login">Log in</Link>
                  </>
                ) : (
                  <>
                    New to StudyHub? <Link href="/signup">Create an account</Link>
                  </>
                )}
              </p>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
