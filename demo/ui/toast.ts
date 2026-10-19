export class Toasts {
  constructor(private host: HTMLElement) {}
  show(message: string, action?: { label: string; run: () => void }) {
    const item = document.createElement("div");
    item.className = "toast";
    const span = document.createElement("span");
    span.textContent = message;
    item.append(span);
    if (action) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      button.onclick = () => {
        action.run();
        item.remove();
      };
      item.append(button);
    }
    this.host.append(item);
    while (this.host.children.length > 2) this.host.firstElementChild?.remove();
    setTimeout(() => item.remove(), 3000);
  }
}
