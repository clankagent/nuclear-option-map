export const el = <T extends HTMLElement>(tag: string, text?: string, className?: string): T => {
  const node = document.createElement(tag) as T;
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
export const section = (body: HTMLElement, title: string) => {
  const node = el<HTMLElement>("section");
  node.append(el("h3", title, "eyebrow"));
  body.append(node);
  return node;
};
export const button = (parent: HTMLElement, label: string, run: () => void, className = "") => {
  const item = el<HTMLButtonElement>("button", label, className);
  item.type = "button";
  item.onclick = run;
  parent.append(item);
  return item;
};
export const row = (parent: HTMLElement) => {
  const item = el<HTMLDivElement>("div", undefined, "row");
  parent.append(item);
  return item;
};
export const field = (parent: HTMLElement, name: string, control: HTMLElement) => {
  const item = el<HTMLLabelElement>("label");
  item.append(el("span", name), control);
  parent.append(item);
};
export const select = (
  options: [string, string][],
  value: string,
  onChange: (value: string) => void,
) => {
  const node = el<HTMLSelectElement>("select");
  options.forEach(([key, label]) => node.add(new Option(label, key)));
  node.value = value;
  node.onchange = () => onChange(node.value);
  return node;
};
