"use client";
import { useEffect, useState } from "react";
import { ThemeProvider, useTheme } from "next-themes";
import { Sun, Moon, Monitor, Check } from "lucide-react";
import { DropdownMenu } from "radix-ui";
export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      {children}
    </ThemeProvider>
  );
}
const THEMES = [
  ["light", "Light", Sun],
  ["dark", "Dark", Moon],
  ["system", "System", Monitor],
] as const;
// next-themes only knows the stored theme after hydration.
function useCurrentTheme() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return { theme: mounted ? theme || "system" : undefined, setTheme };
}
/** Full labelled choice, used in Settings → Appearance. */
export function AppearanceControl() {
  const { theme, setTheme } = useCurrentTheme();
  return (
    <div className="segmented" role="radiogroup" aria-label="Theme">
      {THEMES.map(([value, label, Icon]) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={theme === value}
          onClick={() => setTheme(value)}
        >
          <Icon size={15} aria-hidden />
          {label}
        </button>
      ))}
    </div>
  );
}
/** Compact header menu that still names the active theme. */
export function AppearanceMenu() {
  const { theme, setTheme } = useCurrentTheme();
  const active = THEMES.find(([value]) => value === theme) ?? THEMES[2];
  const Icon = active[2];
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        className="icon-button"
        aria-label={`Appearance: ${active[1]}`}
        title={`Appearance: ${active[1]}`}
      >
        <Icon size={17} aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="menu" align="end" sideOffset={8}>
          <DropdownMenu.Label className="menu-label">Appearance</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={theme} onValueChange={setTheme}>
            {THEMES.map(([value, label, ItemIcon]) => (
              <DropdownMenu.RadioItem
                key={value}
                value={value}
                className="menu-item"
                aria-label={`${label} theme`}
              >
                <ItemIcon size={15} aria-hidden />
                <span>{label}</span>
                <DropdownMenu.ItemIndicator className="menu-check">
                  <Check size={15} aria-hidden />
                </DropdownMenu.ItemIndicator>
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
