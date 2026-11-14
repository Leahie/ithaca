'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { cn } from '@/components/ui';
import { useSession } from '@/lib/store/session';

/** Room list. A click dispatches the same `viewer.goto` the agent and the phone send (A1). */
export function RoomRail({ twin }: { twin: PropertyTwin }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  const floors = [...twin.floors].sort((a, b) => b.index - a.index);

  return (
    <nav aria-label="Rooms" className="h-full w-[188px] shrink-0 overflow-y-auto border-r border-line bg-surface py-2">
      {floors.map((floor) => (
        <div key={floor.floor_id} className="mb-2">
          <div className="px-3 py-1.5 text-[10px] font-semibold tracking-wider text-faint uppercase">Floor {floor.index + 1}</div>
          {twin.rooms
            .filter((r) => r.floor_id === floor.floor_id)
            .map((room) => (
              <button
                key={room.room_id}
                type="button"
                data-room-id={room.room_id}
                aria-current={room.room_id === roomId}
                onClick={() => dispatch('viewer.goto', { room_id: room.room_id })}
                className={cn(
                  'block w-full truncate px-3 py-1.5 text-left text-[13px] transition-colors',
                  room.room_id === roomId ? 'bg-sun/10 text-sun shadow-[inset_2px_0_0_var(--color-sun)]' : 'text-muted hover:bg-raised hover:text-text',
                )}
              >
                {room.label}
              </button>
            ))}
        </div>
      ))}
    </nav>
  );
}
