'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { cn } from '@/components/ui';
import { roomArea, roomName, roomsOnFloor } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { useTour } from '@/lib/store/tour';

// Rows are 44 px pills. Where you are is `main` (ink), never an accent: it is not news.
const ITEM = 'flex min-h-11 w-full items-center justify-between gap-2 rounded-full px-3 text-left text-[15px] transition-colors';
const ON = 'bg-main font-semibold text-on-main';
const OFF = 'text-text hover:bg-ink';
const LABEL = 'px-3 pb-1 text-[11px] font-semibold tracking-[0.08em] text-muted uppercase';

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
    <nav aria-label="Rooms" className="flex h-full w-[220px] shrink-0 flex-col gap-4 overflow-y-auto border-r border-line bg-surface px-2 py-3">
      {tour && (
        <div className="flex flex-col gap-0.5">
          <div className={LABEL}>The house</div>
          {/* Outside opens further, onto the whole house: lilac. */}
          <button type="button" data-place="outside" aria-current={place === 'outside'} onClick={goOutside} className={cn(ITEM, place === 'outside' ? ON : OFF)}>
            <span className="flex items-center gap-2">
              <i aria-hidden className="h-2.5 w-2.5 rounded-full bg-lilac shadow-[0_0_0_1.5px_var(--color-main)]" />
              Outside
            </span>
            <span className={cn('shrink-0 text-[13px]', place === 'outside' ? 'text-on-main/75' : 'text-lilac-deep')}>drag to orbit</span>
          </button>
        </div>
      )}
      {floors.map((floor) => (
        <div key={floor.floor_id} className="flex flex-col gap-0.5">
          <div className={LABEL}>{floor.label ?? `Floor ${floor.index + 1}`}</div>
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
                <span className={cn('shrink-0 font-mono text-[13px] tabular-nums', current ? 'text-on-main/75' : 'text-muted')}>{roomArea(room).toFixed(0)} m²</span>
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
