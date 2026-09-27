"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DropdownMenu } from "radix-ui";
import { LogOut, Settings } from "lucide-react";
import { api, type Account } from "@/lib/api";

/** Who is signed in, and the way out. */
export function AccountMenu() {
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  useEffect(() => {
    api.me().then(setAccount).catch(() => {});
  }, []);
  if (!account) return null;
  const initial = account.user.display_name.trim().charAt(0).toUpperCase() || "?";
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="account-trigger" aria-label={`Account: ${account.user.display_name}`}>
          <span aria-hidden>{initial}</span>
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="menu" align="end" sideOffset={8}>
          <DropdownMenu.Label className="menu-label account-label">
            <strong>{account.user.display_name}</strong>
            <span>{account.user.email}</span>
          </DropdownMenu.Label>
          <DropdownMenu.Separator className="menu-separator" />
          <DropdownMenu.Item className="menu-item" asChild>
            <Link href="/settings#account">
              <Settings size={15} aria-hidden /> Account settings
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Item
            className="menu-item"
            onSelect={async () => {
              await api.logout().catch(() => {});
              router.replace("/login");
            }}
          >
            <LogOut size={15} aria-hidden /> Log out
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
