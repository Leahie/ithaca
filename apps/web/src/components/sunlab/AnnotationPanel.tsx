'use client';

import { GLAZINGS, OPENING_KINDS, type Glazing, type Opening, type OpeningKind, type PropertyTwin } from '@ithaca/schema';
import { Button, Notice, Segmented, cn } from '@/components/ui';
import { IconTrash } from '@/components/ui/icons';
import { roomNames } from '@/lib/rooms';
import { openingSize } from '@/lib/sunlab/annotate';

export interface AnnotationTool {
  kind: OpeningKind;
  glazing: Glazing;
}

interface Props {
  twin: PropertyTwin;
  openings: Opening[];
  dirty: boolean;
  saving: boolean;
  error: string | null;
  hint: string | null;
  tool: AnnotationTool | null;
  onTool(tool: AnnotationTool | null): void;
  onDelete(id: string): void;
  onSave(): void;
  onDiscard(): void;
  /** Only this room's openings are listed, when set. */
  roomId: string | null;
}

/** Openings annotation, v1: mark windows and doors by clicking two corners on the mesh. */
export function AnnotationPanel({ twin, openings, dirty, saving, error, hint, tool, onTool, onDelete, onSave, onDiscard, roomId }: Props) {
  const rooms = roomNames(twin);
  const listed = roomId ? openings.filter((o) => o.room_id === roomId) : openings;

  return (
    <div className="space-y-3" data-testid="annotation-panel">
      {!tool ? (
        <Button variant="subtle" className="w-full" onClick={() => onTool({ kind: 'window', glazing: 'double' })}>
          Mark a window or door
        </Button>
      ) : (
        <div className="space-y-2 rounded-lg border border-sun/40 bg-sun/5 p-3">
          <div className="text-xs font-medium text-sun-deep">Marking: click two opposite corners on a wall</div>
          <Segmented
            label="Opening kind"
            size="sm"
            value={tool.kind}
            options={OPENING_KINDS.map((k) => ({ value: k, label: k }))}
            onChange={(kind) => onTool({ kind, glazing: kind === 'door' ? 'none' : tool.glazing === 'none' ? 'double' : tool.glazing })}
          />
          <Segmented label="Glazing" size="sm" value={tool.glazing} options={GLAZINGS.map((g) => ({ value: g, label: g }))} onChange={(glazing) => onTool({ ...tool, glazing })} />
          {hint && <div className="text-xs text-muted">{hint}</div>}
          <Button size="sm" variant="ghost" onClick={() => onTool(null)}>
            Done marking
          </Button>
        </div>
      )}

      <div>
        <div className="mb-1 flex items-center justify-between text-xs text-muted">
          <span>
            {listed.length} opening{listed.length === 1 ? '' : 's'}
            {roomId ? ` in ${rooms.get(roomId) ?? 'this room'}` : ''}
          </span>
          <span className="text-faint">source: {twin.quality.openings}</span>
        </div>
        <ul className="max-h-44 space-y-1 overflow-y-auto">
          {listed.map((o) => {
            const size = openingSize(o);
            return (
              <li key={o.opening_id} className="flex items-center justify-between gap-2 rounded-md border border-line px-2 py-1 text-xs">
                <span className="min-w-0 truncate">
                  <span className={cn('font-medium', o.kind === 'door' ? 'text-sun-deep' : 'text-sky-deep')}>{o.kind}</span>
                  <span className="text-muted">
                    {' '}
                    {size.width.toFixed(1)} × {size.height.toFixed(1)} m · faces {Math.round(o.facade_azimuth_deg)}° · {o.glazing}
                    {!roomId && ` · ${rooms.get(o.room_id) ?? o.room_id}`}
                  </span>
                </span>
                <button type="button" aria-label={`Delete this ${o.kind}`} onClick={() => onDelete(o.opening_id)} className="shrink-0 rounded p-1 text-faint hover:bg-danger/15 hover:text-danger">
                  <IconTrash width={13} height={13} />
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {error && (
        <Notice tone="error" title="Could not save">
          {error}
        </Notice>
      )}
      {dirty && (
        <div className="flex gap-2">
          <Button variant="primary" className="flex-1" disabled={saving} onClick={onSave}>
            {saving ? 'Saving…' : 'Save openings'}
          </Button>
          <Button variant="ghost" disabled={saving} onClick={onDiscard}>
            Discard
          </Button>
        </div>
      )}
    </div>
  );
}
