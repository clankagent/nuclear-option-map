export type ContextAction = { label: string; run: () => void };
export class ContextMenu {
  private returnFocus: HTMLElement | null = null;
  constructor(private host: HTMLElement) {
    host.addEventListener("keydown", (event) => {
      const buttons = [...host.querySelectorAll("button")];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        this.close();
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        buttons[
          (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length
        ]?.focus();
      }
    });
  }
  open(x: number, y: number, actions: ContextAction[], returnFocus: HTMLElement) {
    this.returnFocus = returnFocus;
    this.host.replaceChildren();
    actions.forEach((action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "menuitem";
      button.textContent = action.label;
      button.onclick = () => {
        this.close();
        action.run();
      };
      this.host.append(button);
    });
    this.host.hidden = false;
    const rect = this.host.getBoundingClientRect();
    this.host.style.left = `${Math.max(8, Math.min(x, innerWidth - rect.width - 8))}px`;
    this.host.style.top = `${Math.max(8, Math.min(y, innerHeight - rect.height - 8))}px`;
    this.host.querySelector("button")?.focus();
  }
  close() {
    if (this.host.hidden) return;
    this.host.hidden = true;
    this.host.replaceChildren();
    this.returnFocus?.focus({ preventScroll: true });
  }
  get isOpen() {
    return !this.host.hidden;
  }
}
