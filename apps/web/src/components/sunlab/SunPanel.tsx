'use client';

import type { PropertyTwin, Room } from '@ithaca/schema';
import { useMemo } from 'react';
import { CHART, ChartTitle, SUN_RAMP, TableView } from '@/components/charts/kit';
import { LineChart } from '@/components/charts/LineChart';
import { Notice, Spinner, Toggle } from '@/components/ui';
import { useSunReport } from '@/lib/data/hooks';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { localDate, withDate } from '@/lib/time';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const dayLabel = (iso: string) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${Number(iso.slice(8, 10))}`;

export function useRoomSunReport(twin: PropertyTwin, room: Room | null) {
  const datetime = useSession((s) => s.viewer.datetime);
  const year = localDate(datetime, twin.timezone).slice(0, 4);
  return useSunReport(twin.property_id, room?.room_id ?? null, `${year}-01-01`, `${year}-12-31`);
}

/**
 * Direct-sun numbers for the room in view. They come from the server's SunReport (A3):
 * the frontend draws them, it does not compute them.
 */
export function SunPanel({ twin, room, heatmapOn, onHeatmap }: { twin: PropertyTwin; room: Room | null; heatmapOn: boolean; onHeatmap(on: boolean): void }) {
  const datetime = useSession((s) => s.viewer.datetime);
  const dispatch = useSession((s) => s.dispatch);
  const report = useRoomSunReport(twin, room);
  const date = localDate(datetime, twin.timezone);

  const daily = report.data?.daily ?? [];
  const points = useMemo(() => daily.map((d) => ({ label: dayLabel(d.date), value: d.direct_sun_hours })), [daily]);
  const markerIndex = daily.findIndex((d) => d.date.slice(5) === date.slice(5));
  const today = markerIndex >= 0 ? daily[markerIndex] : undefined;
  const xTicks = useMemo(() => daily.flatMap((d, i) => (d.date.endsWith('-15') && Number(d.date.slice(5, 7)) % 2 === 1 ? [{ index: i, label: MONTHS[Number(d.date.slice(5, 7)) - 1]! }] : [])), [daily]);
  const heat = report.data?.heatmap;
  const heatMax = heat ? Math.max(0, ...heat.hours.flat()) : 0;

  const name = roomName(twin, room?.room_id);
  if (!room) return <p className="text-sm leading-5 text-muted">Pick a room to see how much direct sun it gets through the year.</p>;
  if (report.error) {
    return (
      <Notice tone="error" title="No sun report">
        {report.error.message}
      </Notice>
    );
  }
  if (!report.data) return <Spinner label="Loading the sun report…" />;

  return (
    <div className="space-y-4" data-testid="sun-panel">
      <div>
        <div className="text-xs text-muted">Direct sun in {name}</div>
        <div className="mt-0.5 flex items-baseline gap-2">
          <span className="text-4xl font-semibold" data-testid="sun-hours">
            {today ? today.direct_sun_hours.toFixed(1) : '–'}
          </span>
          <span className="text-sm text-muted">hours on {dayLabel(date)}</span>
        </div>
      </div>

      <div>
        <ChartTitle title="Through the year" subtitle="Hours of direct sun per day. Click a day to go there." />
        <LineChart
          points={points}
          ariaLabel={`Direct sun hours per day in ${name}`}
          color={CHART.sun}
          format={(v) => `${v.toFixed(1)} h`}
          seriesLabel="direct sun"
          xTicks={xTicks}
          marker={markerIndex >= 0 ? markerIndex : undefined}
          onPick={(i) => {
            const d = daily[i];
            if (d) dispatch('viewer.setTime', { datetime: withDate(datetime, twin.timezone, `${date.slice(0, 4)}${d.date.slice(4)}`) });
          }}
        />
        <TableView caption={`Direct sun hours per day in ${name}`} columns={['Date', 'Direct sun (h)']} rows={daily.map((d) => [d.date, d.direct_sun_hours.toFixed(2)])} />
      </div>

      <div className="space-y-2 border-t border-line pt-3">
        <Toggle label="Floor heatmap" checked={heatmapOn && !!heat} disabled={!heat} onChange={onHeatmap} />
        {heat ? (
          <div>
            <div className="h-2 rounded-full" style={{ background: `linear-gradient(to right, ${SUN_RAMP.join(', ')})` }} />
            <div className="mt-1 flex justify-between text-[10px] text-muted tabular-nums">
              <span>0 h</span>
              <span>average direct sun per day</span>
              <span>{heatMax.toFixed(1)} h</span>
            </div>
          </div>
        ) : (
          <p className="text-xs text-faint">This report has no floor heatmap.</p>
        )}
      </div>

      {(twin.quality.alignment === 'gps_only' || twin.quality.context === 'none' || twin.quality.openings === 'none') && (
        <Notice tone="warn" title="Read these numbers with care">
          {twin.quality.alignment === 'gps_only' && 'The compass heading of this twin is from GPS only, so the sun may be rotated. '}
          {twin.quality.context === 'none' && 'No neighbors or trees are modeled, so nothing outside casts a shadow. '}
          {twin.quality.openings === 'none' && 'No windows are marked yet: annotate them to let light in.'}
        </Notice>
      )}
    </div>
  );
}
