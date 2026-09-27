"use client";
import { useState } from "react";
import { AlertDialog } from "radix-ui";
import { Button } from "./button";

/**
 * Wraps a trigger in a confirmation that names the exact target and consequence.
 * `onConfirm` may reject; its message is shown inside the dialog.
 */
export function Confirm({
  trigger,
  title,
  description,
  confirmLabel,
  destructive = true,
  onConfirm,
}: {
  trigger: React.ReactNode;
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => Promise<unknown> | unknown;
}) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <AlertDialog.Root
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setError("");
      }}
    >
      <AlertDialog.Trigger asChild>{trigger}</AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="scrim" />
        <AlertDialog.Content className="dialog-surface confirm-dialog">
          <AlertDialog.Title className="dialog-title">{title}</AlertDialog.Title>
          <AlertDialog.Description className="dialog-description">
            {description}
          </AlertDialog.Description>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <AlertDialog.Cancel asChild>
              <Button variant="ghost" disabled={busy}>
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <Button
              variant={destructive ? "destructive" : "default"}
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await onConfirm();
                  setOpen(false);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Working…" : confirmLabel}
            </Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
