import type { SVGProps } from 'react';

/** Line drawings by kind of room, picked from the room's label. 24 x 24, stroke = currentColor. */
const PATHS: [RegExp, string[]][] = [
  // bed: headboard, mattress, legs
  [/bed/i, ['M3 18v-7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v7', 'M3 14h18', 'M3 18v2M21 18v2', 'M7 9V7a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v2']],
  // bath: tub with a tap
  [/bath/i, ['M3 12h18v2a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5z', 'M6 12V6a2 2 0 0 1 4 0', 'M7 19l-1 2M17 19l1 2']],
  // kitchen: pot with a lid and handles
  [/kitchen/i, ['M5 10h14v7a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3z', 'M3 12h2M19 12h2', 'M8 7h8', 'M12 7V5']],
  // dining: plate between fork and knife
  [/dining/i, ['M12 8a4 4 0 1 0 0 8a4 4 0 0 0 0-8z', 'M5 4v6a1.5 1.5 0 0 0 3 0V4M6.5 4v16', 'M18 4c-1.5 1-2 3-2 6h2zM18 10v10']],
  // living, family: sofa
  [/living|family|lounge/i, ['M5 11V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3', 'M3 13a2 2 0 0 1 4 0v2h10v-2a2 2 0 0 1 4 0v5H3z', 'M6 18v2M18 18v2']],
  // office: monitor on a stand
  [/office|study/i, ['M4 5h16v10H4z', 'M9 20h6M12 15v5']],
  // closet: hanger
  [/closet|wardrobe/i, ['M12 8a2 2 0 1 1 2-2', 'M12 8v2l9 6a1 1 0 0 1-.6 1.8H3.6A1 1 0 0 1 3 16l9-6']],
  // laundry: washing machine
  [/laundry|utility/i, ['M5 3h14v18H5z', 'M12 9a4 4 0 1 0 0 8a4 4 0 0 0 0-8z', 'M8 6h.01M11 6h.01']],
  // pantry: shelves with jars
  [/pantry|storage/i, ['M4 3h16v18H4z', 'M4 9h16M4 15h16', 'M8 6v3M12 5v4M15 12v3M9 12v3']],
  // hallway, entry, stairs: an open door
  [/hall|entry|foyer|stair|landing/i, ['M6 21V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17', 'M4 21h16', 'M14 12h.01']],
  // patio, garage, outside
  [/patio|garage|veranda|porch/i, ['M3 11l9-7 9 7', 'M5 10v10h14V10', 'M9 20v-6h6v6']],
];
const OTHER = ['M5 5h14v14H5z', 'M5 12h6v7'];

export function RoomIcon({ label, ...props }: { label: string } & SVGProps<SVGSVGElement>) {
  const paths = PATHS.find(([match]) => match.test(label))?.[1] ?? OTHER;
  return (
    <svg viewBox="0 0 24 24" width={24} height={24} fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...props}>
      {paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
