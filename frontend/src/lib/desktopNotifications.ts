import { invoke, isTauri } from '@tauri-apps/api/core';

/** The in-app toast remains available when Windows notifications are denied. */
export function notifyDesktop(title: string, body: string): void {
  if (!isTauri()) return;
  void invoke('desktop_notify', { title, body }).catch(() => {
    // Notification delivery is optional; it must not interrupt job/result updates.
  });
}
