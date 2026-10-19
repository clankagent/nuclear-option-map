const paths: Record<string, string> = {
  map: '<path d="M3 5 9 2l6 3 6-3v17l-6 3-6-3-6 3zM9 2v17M15 5v17"/>',
  chevron: '<path d="m5 8 5 5 5-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M4.9 4.9 7 7m10 10 2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1"/>',
  hand:
    '<path d="M8 13V5a1.5 1.5 0 0 1 3 0v6-7a1.5 1.5 0 0 1 3 0v7-5' +
    "a1.5 1.5 0 0 1 3 0v5-2a1.5 1.5 0 0 1 3 0v5c0 5-3 8-7 8h-2" +
    'c-2 0-3-1-4-3l-3-4a1.5 1.5 0 0 1 2.4-1.8z"/>',
  measure: '<path d="M3 17 17 3l4 4L7 21zM11 9l2 2m1-5 2 2M8 12l2 2"/>',
  pin: '<path d="M12 22s7-7.1 7-13a7 7 0 1 0-14 0c0 5.9 7 13 7 13Z"/><circle cx="12" cy="9" r="2.2"/>',
  places: '<path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  minus: '<path d="M4 12h16"/>',
  fit: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  close: '<path d="M5 5 19 19M19 5 5 19"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  link: '<path d="M10 13a5 5 0 0 0 7 .1l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7-.1l-3 3a5 5 0 0 0 7 7l2-2"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 18v3h16v-3"/>',
  upload: '<path d="M12 21V9m-5 5 5-5 5 5M4 6V3h16v3"/>',
};
export function icon(name: keyof typeof paths): string {
  return (
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" ' +
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    paths[name] +
    "</svg>"
  );
}
