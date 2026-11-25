// Node icons (the Icon PropertyType values) and a few UI glyphs, as inline SVG.

const P = {
  box: 'M3 7l9-4 9 4v10l-9 4-9-4V7zm0 0l9 4 9-4M12 11v10',
  merge: 'M6 3v6a6 6 0 006 6h0a6 6 0 016 6M6 21v-6M18 3v4M6 3a2 2 0 100 0M18 7a2 2 0 100-4 2 2 0 000 4z',
  pair: 'M8 7a4 4 0 100 8 4 4 0 000-8zM16 9a4 4 0 100 8 4 4 0 000-8z',
  folder: 'M3 6h6l2 2h10v11H3z',
  globe: 'M12 3a9 9 0 100 18 9 9 0 000-18zm-9 9h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  cube: 'M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zm0 9l8-4.5M12 12v9M12 12L4 7.5',
  table: 'M3 5h18v14H3zM3 10h18M3 15h18M9 5v14',
  file: 'M6 3h8l4 4v14H6zM14 3v4h4',
  building: 'M4 21V5l8-2v18M12 8h8v13M7 8h2M7 12h2M7 16h2M15 12h2M15 16h2M2 21h20',
  star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',
  sliders: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4',
  tag: 'M3 12V3h9l9 9-9 9zM7.5 7.5h0',
  lock: 'M6 11h12v10H6zM8 11V7a4 4 0 018 0v4',
  unlock: 'M6 11h12v10H6zM8 11V7a4 4 0 017.5-2',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  hash: 'M5 9h15M4 15h15M10 3L8 21M16 3l-2 18',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  calendar: 'M4 5h16v16H4zM4 10h16M8 3v4M16 3v4',
  flag: 'M5 21V4M5 4h11l-2 4 2 4H5',
  pin: 'M12 21s-6-6-6-11a6 6 0 0112 0c0 5-6 11-6 11zm0-9a2 2 0 100-4 2 2 0 000 4z',
  server: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h0M8 17h0',
  book: 'M4 4h7a3 3 0 013 3v13a2 2 0 00-2-2H4zM20 4h-6M20 4v14h-6',
  user: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0',
  link: 'M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-5-5',
  chevron: 'M9 6l6 6-6 6',
  down: 'M6 9l6 6 6-6',
  close: 'M6 6l12 12M18 6L6 18',
  plus: 'M12 5v14M5 12h14',
  ell: 'M7 3v10h12',
  info: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v6M12 7h0',
  user_def: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0',
  filter: 'M3 5h18l-7 9v6l-4-2v-4z',
  sort: 'M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm10 3a3 3 0 100-6 3 3 0 000 6z',
  up: 'M12 19V5M5 12l7-7 7 7',
  caretUp: 'M6 15l6-6 6 6',
  pencil: 'M4 20h4L19 9l-4-4L4 16z',
  download: 'M12 3v12M7 10l5 5 5-5M4 19h16',
  refresh: 'M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5',
  truck: 'M3 6h11v10H3zM14 9h4l3 3v4h-7M7 19a2 2 0 100-4 2 2 0 000 4zM17 19a2 2 0 100-4 2 2 0 000 4z',
  users: 'M9 11a4 4 0 100-8 4 4 0 000 8zM2 21a7 7 0 0114 0M16 3.5a4 4 0 010 7.5M18 14a6 6 0 014 7',
  wrench: 'M14.7 6.3a4 4 0 00-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 005.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z',
  leaf: 'M5 19c0-8 6-14 15-14 0 9-6 15-14 15M5 19l7-7',
  arrowRight: 'M5 12h14M13 6l6 6-6 6',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  star2: 'M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z',
}

export const ICON_NAMES = ['box', 'folder', 'globe', 'cube', 'table', 'file', 'building', 'star', 'sliders', 'tag',
  'lock', 'bolt', 'hash', 'list', 'shield', 'calendar', 'flag', 'pin', 'server', 'book', 'user', 'users', 'truck', 'wrench', 'leaf']

export function Icon({ name, size = 14, className = '', title }) {
  const d = P[name] || P.box
  return (
    <svg className={`ico ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden={!title}>
      {title && <title>{title}</title>}
      <path d={d} />
    </svg>
  )
}

// Icon tile: colour by category (def | source | reference | folder | entity), dashed border = loaded.
export function Tile({ name, cat = 'entity', loaded = false, size = 22 }) {
  const lg = size > 30
  return (
    <span className={`tile t-${cat} ${loaded ? 'dashed' : ''} ${lg ? 'lg' : ''}`} style={{ width: size, height: size }} aria-hidden="true">
      <Icon name={name} size={lg ? 22 : Math.round(size * 0.6)} />
    </span>
  )
}
