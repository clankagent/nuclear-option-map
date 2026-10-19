export function setupShortcuts(
  dialog: HTMLDialogElement,
  body: HTMLElement,
  close: HTMLButtonElement,
) {
  const rows = [
    ["/ or Ctrl/⌘ K", "Search"],
    ["V / M / P", "Navigate / Measure / Pin"],
    ["B / L", "Places / Layers"],
    ["G / H", "Grid / Minimap"],
    ["1 / 2 / 3", "Switch view"],
    [", / ?", "Settings / Shortcuts"],
    ["Enter", "Finish measurement or open result"],
    ["Backspace", "Remove last measure point"],
    ["Delete", "Delete selected pin"],
    ["Ctrl/⌘ Z", "Undo"],
    ["Esc", "Close or cancel"],
    ["Arrows / + / − / 0", "Pan / zoom / fit map"],
    ["Shift F10", "Map actions"],
  ];
  const grid = document.createElement("div");
  grid.className = "shortcut-grid";
  rows.forEach(([key, label]) => {
    const span = document.createElement("span");
    span.textContent = label;
    const kbd = document.createElement("kbd");
    kbd.textContent = key;
    grid.append(span, kbd);
  });
  body.append(grid);
  close.onclick = () => dialog.close();
  return () => dialog.showModal();
}
