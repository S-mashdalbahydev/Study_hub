"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Library, Layers3, Settings } from "lucide-react";
import { AccountMenu } from "./account-menu";
import { AppearanceMenu } from "./theme";

/** Open page with a single coral margin stroke; the StudyHub mark. */
export function BrandMark({ size = 26 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden
      className="brand-mark"
    >
      <path
        d="M9 6h9.5A4.5 4.5 0 0 1 23 10.5V26H12.5A3.5 3.5 0 0 1 9 22.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      <path
        d="M13 12h6M13 16h6"
        stroke="var(--action)"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M4.5 10v11"
        stroke="var(--annotation)"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

const NAV = [
  ["/", "Library", Library],
  ["/spaces", "Study spaces", Layers3],
  ["/settings", "Settings", Settings],
] as const;

export function AppHeader({ action }: { action?: React.ReactNode }) {
  const path = usePathname();
  const active = (href: string) =>
    href === "/"
      ? path === "/"
      : path.startsWith(href) || (href === "/spaces" && path.startsWith("/sessions"));
  return (
    <header className="app-header">
      <Link href="/" className="brand" aria-label="StudyHub home">
        <BrandMark />
        <span>StudyHub</span>
      </Link>
      <nav className="app-nav" aria-label="Main">
        {NAV.map(([href, label, Icon]) => (
          <Link
            key={href}
            href={href}
            aria-current={active(href) ? "page" : undefined}
          >
            <Icon size={16} aria-hidden />
            <span>{label}</span>
          </Link>
        ))}
      </nav>
      <div className="app-header-actions">
        <AppearanceMenu />
        {action}
        <AccountMenu />
      </div>
    </header>
  );
}
