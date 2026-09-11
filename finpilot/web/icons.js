const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  wallet:
    '<path d="M20 8V5a2 2 0 0 0-2-2H6a3 3 0 0 0 0 6h15v11H6a3 3 0 0 1-3-3V6"/><path d="M21 12h-5v5h5"/>',
  split: '<path d="M12 21V10M12 10l-7-7M12 10l7-7M5 8V3h5M14 3h5v5"/>',
  receipt:
    '<path d="M5 3h14v18l-3-2-4 2-4-2-3 2V3Z"/><path d="M9 7h6M9 11h6M9 15h3"/>',
  chart: '<path d="M3 3v18h18M7 14l4-4 4 3 6-7"/>',
  target:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  card: '<rect x="2" y="4" width="20" height="16" rx="3"/><path d="M2 10h20M6 15h3"/>',
  repeat:
    '<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3"/>',
  shield:
    '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  settings:
    '<path d="m9 3-1 3-3 1v4l-2 1 2 2v4l3 1 1 2h5l1-2 3-1v-4l2-2-2-1V7l-3-1-1-3Z"/><circle cx="11.5" cy="12" r="3"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  "arrow-up": '<path d="M12 19V5m-6 6 6-6 6 6"/>',
  up: '<path d="M5 16 16 5M6 5h10v10"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  calendar:
    '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2M14 15h2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bank: '<path d="m3 8 9-5 9 5H3ZM3 21h18M5 11v7M10 11v7M14 11v7M19 11v7"/>',
  house: '<path d="m3 10 9-7 9 7M5 9v12h14V9M9 21v-7h6v7"/>',
  car: '<path d="m4 9 2-5h12l2 5M3 10h18v8H3zM6 18v3M18 18v3M6 13h2M16 13h2"/>',
  book: '<path d="M12 5C9 3 5 3 2 4v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-3-1-7-1-10 1v15"/>',
  bolt: '<path d="m13 2-9 12h7l-1 8 10-12h-7l1-8Z"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  spark:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
};
export const icon = (name) =>
  `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24">${icons[name] || icons.wallet}</svg>`;
