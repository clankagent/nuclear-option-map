import type { AppContext } from "./app-context";

export class ClipboardController {
  constructor(private app: AppContext) {}
  async copyText(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      this.app.toasts.show("Copied");
    } catch {
      this.app.toasts.show("Copy unavailable");
    }
  }
}
