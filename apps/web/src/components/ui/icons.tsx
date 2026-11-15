import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement>;
const base = (p: P) => ({ width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true, ...p });

export const IconTour = (p: P) => (
  <svg {...base(p)}>
    <path d="M3 11l9-7 9 7v9a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z" />
  </svg>
);
export const IconSun = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);
export const IconSound = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 9v6h4l5 4V5L8 9z" />
    <path d="M16.5 8.5a5 5 0 010 7M19 6a8.5 8.5 0 010 12" />
  </svg>
);
export const IconMute = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 9v6h4l5 4V5L8 9z" />
    <path d="M16.5 9.5l5 5M21.5 9.5l-5 5" />
  </svg>
);
export const IconMap = (p: P) => (
  <svg {...base(p)}>
    <path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z" />
    <path d="M9 4v14M15 6v14" />
  </svg>
);
export const IconDesign = (p: P) => (
  <svg {...base(p)}>
    <rect x="3" y="3" width="8" height="8" rx="1.5" />
    <rect x="13" y="3" width="8" height="5" rx="1.5" />
    <rect x="13" y="12" width="8" height="9" rx="1.5" />
    <rect x="3" y="15" width="8" height="6" rx="1.5" />
  </svg>
);
export const IconPhone = (p: P) => (
  <svg {...base(p)}>
    <rect x="7" y="2" width="10" height="20" rx="2.5" />
    <path d="M11 18h2" />
  </svg>
);
export const IconMic = (p: P) => (
  <svg {...base(p)}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0014 0M12 18v3" />
  </svg>
);
export const IconSend = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 12l16-8-6 16-3-7z" />
  </svg>
);
export const IconPlay = (p: P) => (
  <svg {...base(p)} fill="currentColor" stroke="none">
    <path d="M7 4.5v15a.8.8 0 001.2.7l12-7.5a.8.8 0 000-1.4l-12-7.5A.8.8 0 007 4.5z" />
  </svg>
);
export const IconPause = (p: P) => (
  <svg {...base(p)} fill="currentColor" stroke="none">
    <rect x="6" y="4" width="4.5" height="16" rx="1" />
    <rect x="13.5" y="4" width="4.5" height="16" rx="1" />
  </svg>
);
export const IconTrash = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
  </svg>
);
export const IconUpload = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 16V4M7 9l5-5 5 5M4 20h16" />
  </svg>
);
export const IconUser = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21a8 8 0 0116 0" />
  </svg>
);
export const IconClose = (p: P) => (
  <svg {...base(p)}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);
export const IconCheck = (p: P) => (
  <svg {...base(p)}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </svg>
);
export const IconChat = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 5h16v11H9l-5 4z" />
  </svg>
);
export const IconCamera = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
    <circle cx="12" cy="13" r="3.5" />
  </svg>
);
export const IconPin = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 21s7-6.2 7-11.5A7 7 0 005 9.5C5 14.8 12 21 12 21z" />
    <circle cx="12" cy="9.5" r="2.5" />
  </svg>
);
export const IconPlus = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);
export const IconList = (p: P) => (
  <svg {...base(p)}>
    <path d="M9 6h11M9 12h11M9 18h11" />
    <circle cx="4.5" cy="6" r="1" />
    <circle cx="4.5" cy="12" r="1" />
    <circle cx="4.5" cy="18" r="1" />
  </svg>
);
export const IconSparkle = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />
    <path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z" />
  </svg>
);
export const IconTurnLeft = (p: P) => (
  <svg {...base(p)}>
    <path d="M9 4L5 8l4 4" />
    <path d="M5 8h9a5 5 0 010 10h-3" />
  </svg>
);
export const IconTurnRight = (p: P) => (
  <svg {...base(p)}>
    <path d="M15 4l4 4-4 4" />
    <path d="M19 8h-9a5 5 0 000 10h3" />
  </svg>
);
export const IconArrowUp = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </svg>
);
export const IconEye = (p: P) => (
  <svg {...base(p)}>
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);
export const IconHand = (p: P) => (
  <svg {...base(p)}>
    <path d="M8 13V5.5a1.5 1.5 0 013 0V11M11 10.5v-6a1.5 1.5 0 013 0V11M14 10.5V6a1.5 1.5 0 013 0v7.5" />
    <path d="M8 12.5a1.5 1.5 0 00-3 0v1.5a7 7 0 007 7h1a5 5 0 005-5v-2.5" />
  </svg>
);
export const IconBackpack = (p: P) => (
  <svg {...base(p)}>
    <path d="M9 5V4a3 3 0 016 0v1" />
    <path d="M5 11a6 6 0 016-6h2a6 6 0 016 6v8a2 2 0 01-2 2H7a2 2 0 01-2-2z" />
    <path d="M9 21v-5a1 1 0 011-1h4a1 1 0 011 1v5M9 11h6" />
  </svg>
);
export const IconBag = (p: P) => (
  <svg {...base(p)}>
    <path d="M5 8h14l-1 12a1 1 0 01-1 1H7a1 1 0 01-1-1z" />
    <path d="M9 10V7a3 3 0 016 0v3" />
  </svg>
);
export const IconSearch = (p: P) => (
  <svg {...base(p)}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="M20 20l-4.2-4.2" />
  </svg>
);
export const IconPeople = (p: P) => (
  <svg {...base(p)}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3 20a6 6 0 0112 0" />
    <path d="M16 5.2a3 3 0 010 5.6M18 14.2a6 6 0 013 5.8" />
  </svg>
);
/** The house from above, as a cube: the big screen's view down on the room. */
export const IconCube = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
    <path d="M4 7.5l8 4.5 8-4.5M12 12v9" />
  </svg>
);
export const IconGear = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" />
  </svg>
);
export const IconChevronLeft = (p: P) => (
  <svg {...base(p)}>
    <path d="M15 5l-7 7 7 7" />
  </svg>
);
export const IconChevronRight = (p: P) => (
  <svg {...base(p)}>
    <path d="M9 5l7 7-7 7" />
  </svg>
);

// The furniture menu: one drawing per section, each a piece seen from the front. The sofa is also
// the Furniture button's icon.
export const IconGrid = (p: P) => (
  <svg {...base(p)}>
    <rect x="4" y="4" width="6.5" height="6.5" rx="1.5" />
    <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" />
    <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" />
    <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" />
  </svg>
);
export const IconSofa = (p: P) => (
  <svg {...base(p)}>
    <path d="M5 11V8a2 2 0 012-2h10a2 2 0 012 2v3" />
    <path d="M3 13a2 2 0 014 0v2h10v-2a2 2 0 014 0v5H3z" />
    <path d="M6 18v2M18 18v2" />
  </svg>
);
export const IconArmchair = (p: P) => (
  <svg {...base(p)}>
    <path d="M7 11V7a2 2 0 012-2h6a2 2 0 012 2v4" />
    <path d="M5 13a2 2 0 014 0v2h6v-2a2 2 0 014 0v5H5z" />
    <path d="M7 18v2M17 18v2" />
  </svg>
);
export const IconChair = (p: P) => (
  <svg {...base(p)}>
    <path d="M8 12V5a1 1 0 011-1h6a1 1 0 011 1v7M8 8h8" />
    <path d="M6 12h12v3H6z" />
    <path d="M7 15v5M17 15v5" />
  </svg>
);
export const IconTable = (p: P) => (
  <svg {...base(p)}>
    <rect x="3" y="7" width="18" height="3" rx="1" />
    <path d="M6 10v9M18 10v9" />
  </svg>
);
export const IconDesk = (p: P) => (
  <svg {...base(p)}>
    <path d="M3 7h18M5 7v12" />
    <path d="M12 7v12h7V7M12 13h7" />
  </svg>
);
export const IconBed = (p: P) => (
  <svg {...base(p)}>
    <path d="M3 20V6M3 17h18M21 20v-5a2 2 0 00-2-2H3" />
    <path d="M6 13v-2a1 1 0 011-1h4a1 1 0 011 1v2" />
  </svg>
);
export const IconStorage = (p: P) => (
  <svg {...base(p)}>
    <rect x="5" y="3" width="14" height="18" rx="1.5" />
    <path d="M5 9h14M5 15h14M12 15v6" />
  </svg>
);
export const IconLamp = (p: P) => (
  <svg {...base(p)}>
    <path d="M8.5 3h7l3 8h-13z" />
    <path d="M12 11v9.5M8 20.5h8" />
  </svg>
);
export const IconRug = (p: P) => (
  <svg {...base(p)}>
    <rect x="5" y="6" width="14" height="12" rx="1" />
    <rect x="8.5" y="9.5" width="7" height="5" rx="0.5" />
    <path d="M2.5 8H5M2.5 12H5M2.5 16H5M19 8h2.5M19 12h2.5M19 16h2.5" />
  </svg>
);
/** A picture in its frame: wall art and mirrors. */
export const IconFrame = (p: P) => (
  <svg {...base(p)}>
    <rect x="4" y="5" width="16" height="14" rx="1.5" />
    <path d="M7 16l3.5-4 2.5 2.5 1.5-1.5L17 16" />
    <circle cx="15" cy="9.5" r="1" />
  </svg>
);
export const IconPlant = (p: P) => (
  <svg {...base(p)}>
    <path d="M8 15h8l-1 6H9z" />
    <path d="M12 15V8" />
    <path d="M12 12c0-3-2-5-5.5-5 0 3 2 5 5.5 5zM12 9c0-3 2-5 5.5-5 0 3-2 5-5.5 5z" />
  </svg>
);
/** A vase: decorations. */
export const IconVase = (p: P) => (
  <svg {...base(p)}>
    <path d="M10 3h4M10.5 3v3c-2.5 1.5-4 4-4 7a5.5 5.5 0 0011 0c0-3-1.5-5.5-4-7V3" />
  </svg>
);
/** A garden parasol: outdoor furniture. */
export const IconParasol = (p: P) => (
  <svg {...base(p)}>
    <path d="M3 12a9 9 0 0118 0z" />
    <path d="M12 12v9M8.5 21h7M12 3V2" />
  </svg>
);
/** Toy blocks: children's furniture. */
export const IconBlocks = (p: P) => (
  <svg {...base(p)}>
    <rect x="3" y="13" width="8" height="7.5" rx="1" />
    <rect x="13" y="13" width="8" height="7.5" rx="1" />
    <rect x="8" y="3.5" width="8" height="7.5" rx="1" />
  </svg>
);
