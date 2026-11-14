'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { cn } from '@/components/ui';
import { roomArea, roomName, roomsOnFloor } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { useTour } from '@/lib/store/tour';

const ITEM = 'flex w-full items-baseline justify-between gap-2 px-3 py-1.5 text-left text-[13px] transition-colors';
const ON = 'bg-sun/10 text-sun shadow-[inset_2px_0_0_var(--color-sun)]';
const OFF = 'text-muted hover:bg-raised hover:text-text';

/**
 * Where you can go. A room is a `viewer.goto`, the same event the agent and the phone send
 * (A1). In the tour, the list also offers the views around the house.
 */
export function RoomRail({ twin, tour = false }: { twin: PropertyTwin; tour?: boolean }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  const place = useTour((s) => s.place);
  const goOutside = useTour((s) => s.goOutside);
  const floors = [...twin.floors].sort((a, b) => b.index - a.index);
  // Outside the house, no room is the current one.
  const indoors = !tour || place !== 'outside';

  return (
    <nav aria-label="Rooms" className="h-full w-[188px] shrink-0 overflow-y-auto border-r border-line bg-surface py-2">
      {tour && (
        <div className="mb-2">
          <div className="px-3 py-1.5 text-[10px] font-semibold tracking-wider text-faint uppercase">The house</div>
          <button type="button" data-place="outside" aria-current={place === 'outside'} onClick={goOutside} className={cn(ITEM, place === 'outside' ? ON : OFF)}>
            <span>Outside</span>
            <span className="shrink-0 text-[10px] text-faint">drag to orbit</span>
          </button>
        </div>
      )}
      {floors.map((floor) => (
        <div key={floor.floor_id} className="mb-2">
          <div className="px-3 py-1.5 text-[10px] font-semibold tracking-wider text-faint uppercase">{floor.label ?? `Floor ${floor.index + 1}`}</div>
          {roomsOnFloor(twin, floor.floor_id).map((room) => {
            const current = indoors && room.room_id === roomId;
            return (
              <button
                key={room.room_id}
                type="button"
                data-room-id={room.room_id}
                aria-current={current}
                aria-label={roomName(twin, room.room_id)}
                onClick={() => dispatch('viewer.goto', { room_id: room.room_id })}
                className={cn(ITEM, current ? ON : OFF)}
              >
                <span className="truncate">{roomName(twin, room.room_id)}</span>
                {/* Rooms that share a label are told apart by size as well as by number. */}
                <span className="shrink-0 text-[10px] text-faint tabular-nums">{roomArea(room).toFixed(0)} m²</span>
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
