'use client';

import { OCTAVE_BANDS_HZ, type NoiseScenario, type PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ColumnChart } from '@/components/charts/ColumnChart';
import { CHART, ChartTitle, Legend, TableView } from '@/components/charts/kit';
import { Button, Notice, Segmented, Spinner, Toggle, ViewState } from '@/components/ui';
import { IconPause, IconPlay } from '@/components/ui/icons';
import { AURALIZATION, Auralizer, auralizationParams } from '@/lib/audio/auralizer';
import { describeLevel as describe } from '@/lib/audio/levels';
import { useNoiseReport } from '@/lib/data/hooks';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { formatClock, fromZonedParts, toZonedIso, zonedParts } from '@/lib/time';

const AS_BUILT: NoiseScenario = { glazing: null, add_carpet: false, include_furniture: true };
type GlazingChoice = 'asbuilt' | 'single' | 'double' | 'triple';

const sameScenario = (a: NoiseScenario, b: NoiseScenario) =>
  (a.glazing ?? null) === (b.glazing ?? null) && !!a.add_carpet === !!b.add_carpet && (a.include_furniture ?? true) === (b.include_furniture ?? true);

const hourLabel = (h: number) => formatClock(h * 60).replace(':00', '');
const bandLabel = (hz: number) => (hz >= 1000 ? `${hz / 1000}k` : String(hz));

/**
 * Sound: hourly indoor level for a room, and what it sounds like.
 * Chart, numbers and audio all come from the same NoiseReport (A3, A4).
 */
export function SoundTab({ twin }: { twin: PropertyTwin }) {
  const viewerRoom = useSession((s) => s.viewer.room_id);
  const gotoSeq = useSession((s) => s.viewer.goto_seq);
  const soundScenario = useSession((s) => s.viewer.sound_scenario);
  const datetime = useSession((s) => s.viewer.datetime);
  const client = useSession((s) => s.client);
  const dispatch = useSession((s) => s.dispatch);

  const fallbackRoom = useMemo(() => twin.rooms.find((r) => /bedroom/i.test(r.label)) ?? twin.rooms[0], [twin]);
  const roomId = soundScenario?.room_id ?? viewerRoom ?? fallbackRoom?.room_id ?? null;
  const room = twin.rooms.find((r) => r.room_id === roomId) ?? null;
  const scenario: NoiseScenario = soundScenario?.scenario ?? AS_BUILT;
  const isBaseline = sameScenario(scenario, AS_BUILT);

  const report = useNoiseReport(twin.property_id, roomId, scenario);
  const baseline = useNoiseReport(twin.property_id, roomId, AS_BUILT);

  const hour = zonedParts(new Date(datetime), twin.timezone).hour;
  const [playing, setPlaying] = useState(false);
  const [compare, setCompare] = useState(false);
  const [fullScale, setFullScale] = useState<number>(AURALIZATION.defaultFullScaleDba);
  const [audioError, setAudioError] = useState<string | null>(null);
  const auralizer = useRef<Auralizer | null>(null);

  // A room picked on the room list while this tab is open becomes the sound room. The desktop
  // derives the event (A2), so the phone and the agent see the same scenario.
  const lastGoto = useRef(gotoSeq);
  useEffect(() => {
    if (gotoSeq === lastGoto.current) return;
    lastGoto.current = gotoSeq;
    if (client === 'desktop' && viewerRoom && viewerRoom !== roomId) dispatch('sound.setScenario', { room_id: viewerRoom, scenario });
  }, [gotoSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  const setScenario = (patch: Partial<NoiseScenario>) => {
    if (roomId) dispatch('sound.setScenario', { room_id: roomId, scenario: { ...scenario, ...patch } });
  };

  const heard = compare ? baseline.data : report.data;
  const heardHour = heard?.hourly.find((h) => h.hour === hour);
  const params = useMemo(() => (heard && heardHour ? auralizationParams(heardHour, heard.rt60_s, fullScale) : null), [heard, heardHour, fullScale]);

  useEffect(() => {
    if (params) auralizer.current?.update(params);
  }, [params]);
  useEffect(
    () => () => {
      auralizer.current?.dispose();
      auralizer.current = null;
    },
    [],
  );

  const toggle = async () => {
    setAudioError(null);
    if (playing) {
      auralizer.current?.stop();
      setPlaying(false);
      return;
    }
    try {
      auralizer.current ??= new Auralizer();
      if (params) auralizer.current.update(params);
      await auralizer.current.start();
      setPlaying(true);
    } catch (err) {
      setAudioError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!room) {
    return (
      <ViewState>
        <Notice title="No rooms">This twin has no rooms yet.</Notice>
      </ViewState>
    );
  }
  if (report.error) {
    return (
      <ViewState>
        <Notice tone="error" title="No noise report">
          {report.error.message}
        </Notice>
      </ViewState>
    );
  }

  const data = report.data;
  const name = roomName(twin, room.room_id);
  const current = data?.hourly.find((h) => h.hour === hour);
  const base = baseline.data;
  const baseCurrent = base?.hourly.find((h) => h.hour === hour);
  const delta = current && baseCurrent && !isBaseline ? current.laeq_dba - baseCurrent.laeq_dba : null;
  const levels = data?.hourly.map((h) => h.laeq_dba) ?? [];
  const floor = levels.length ? Math.max(0, Math.floor((Math.min(...levels, ...(base?.hourly.map((h) => h.laeq_dba) ?? [])) - 4) / 10) * 10) : 0;
  const glazing: GlazingChoice = scenario.glazing ?? 'asbuilt';

  return (
    <div className="h-full overflow-y-auto p-5" data-testid="sound" data-room-id={room.room_id} data-playing={playing} data-laeq={current?.laeq_dba} data-gain={params?.gain.toFixed(5)}>
      <div className="mx-auto grid max-w-5xl gap-5 lg:grid-cols-[260px_1fr]">
        <div className="space-y-4">
          <section className="rounded-xl border border-line bg-surface p-4">
            <div className="text-xs text-muted">Room</div>
            <select
              value={room.room_id}
              aria-label="Room"
              onChange={(e) => dispatch('sound.setScenario', { room_id: e.target.value, scenario })}
              className="mt-1 h-9 w-full rounded-md border border-line bg-ink px-2 text-sm"
            >
              {[...twin.rooms]
                .sort((a, b) => roomName(twin, a.room_id).localeCompare(roomName(twin, b.room_id), 'en', { numeric: true }))
                .map((r) => (
                <option key={r.room_id} value={r.room_id}>
                  {roomName(twin, r.room_id)}
                </option>
              ))}
            </select>
            <div className="mt-4 text-xs text-muted">Windows</div>
            <Segmented
              className="mt-1 w-full [&>button]:flex-1 [&>button]:justify-center"
              label="Window glazing"
              size="sm"
              value={glazing}
              options={[
                { value: 'asbuilt', label: 'As built' },
                { value: 'single', label: 'Single' },
                { value: 'double', label: 'Double' },
                { value: 'triple', label: 'Triple' },
              ]}
              onChange={(g: GlazingChoice) => setScenario({ glazing: g === 'asbuilt' ? null : g })}
            />
            <div className="mt-4 space-y-2.5">
              <Toggle label="Add carpet" checked={!!scenario.add_carpet} onChange={(add_carpet) => setScenario({ add_carpet })} />
              <Toggle label="Furnished" checked={scenario.include_furniture ?? true} onChange={(include_furniture) => setScenario({ include_furniture })} />
            </div>
          </section>

          <section className="rounded-xl border border-line bg-surface p-4">
            <div className="flex items-center gap-3">
              <Button variant="primary" size="lg" square onClick={toggle} aria-label={playing ? 'Stop the sound' : 'Play the sound of this room'} aria-pressed={playing} disabled={!params}>
                {playing ? <IconPause width={20} height={20} /> : <IconPlay width={20} height={20} />}
              </Button>
              <div className="text-sm leading-5">
                <div className="font-medium">{playing ? (compare ? 'Playing: as built' : 'Playing this scenario') : 'Hear this room'}</div>
                <div className="text-xs text-muted">Street noise at {hourLabel(hour)}, as heard inside.</div>
              </div>
            </div>
            <Button
              className="mt-3 w-full"
              variant={compare ? 'primary' : 'subtle'}
              disabled={isBaseline}
              title={isBaseline ? 'Change a setting to compare it with the house as built' : 'Hold to hear the house as built'}
              onPointerDown={() => setCompare(true)}
              onPointerUp={() => setCompare(false)}
              onPointerLeave={() => setCompare(false)}
              onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && setCompare(true)}
              onKeyUp={() => setCompare(false)}
            >
              Hold to compare with as built
            </Button>
            <label className="mt-4 block text-xs text-muted">
              Loudness calibration
              <input type="range" min={45} max={85} step={1} value={fullScale} onChange={(e) => setFullScale(Number(e.target.value))} className="mt-1 block w-full" aria-valuetext={`${fullScale} dBA at full volume`} />
              <span className="text-[11px] text-faint">{fullScale} dBA plays at full volume. Lower it if the room sounds too quiet on your speakers.</span>
            </label>
            {audioError && <p className="mt-2 text-xs text-danger">{audioError}</p>}
          </section>
        </div>

        <div className="space-y-4">
          <section className="rounded-xl border border-line bg-surface p-5">
            {!data || !current ? (
              <Spinner label="Loading the noise report…" />
            ) : (
              <>
                <div className="flex flex-wrap items-end justify-between gap-4">
                  <div>
                    <div className="text-xs text-muted">
                      {name} at {hourLabel(hour)}
                    </div>
                    <div className="mt-1 flex items-baseline gap-2">
                      <span className="text-5xl font-semibold" data-testid="laeq">
                        {current.laeq_dba.toFixed(1)}
                      </span>
                      <span className="text-sm text-muted">dBA, {describe(current.laeq_dba)}</span>
                    </div>
                    {delta !== null && (
                      <div className="mt-1 text-sm text-muted" data-testid="laeq-delta">
                        <span className="font-medium text-text">
                          {delta > 0 ? '+' : '−'}
                          {Math.abs(delta).toFixed(1)} dB
                        </span>{' '}
                        {delta > 0 ? 'louder' : 'quieter'} than as built ({baseCurrent!.laeq_dba.toFixed(1)} dBA)
                      </div>
                    )}
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-muted">Reverberation (RT60)</div>
                    <div className="text-2xl font-semibold" data-testid="rt60">
                      {data.rt60_s.toFixed(2)} s
                    </div>
                    {base && !isBaseline && <div className="text-xs text-muted">as built {base.rt60_s.toFixed(2)} s</div>}
                  </div>
                </div>

                <div className="mt-5">
                  <ChartTitle
                    title="Through the day"
                    subtitle="Indoor level per hour, dBA. Click an hour to hear it."
                    aside={
                      !isBaseline && (
                        <Legend
                          items={[
                            { label: 'This scenario', color: CHART.series1, shape: 'rect' },
                            { label: 'As built', color: CHART.series2, shape: 'line' },
                          ]}
                        />
                      )
                    }
                  />
                  <ColumnChart
                    ariaLabel={`Indoor noise level per hour in ${name}`}
                    columns={data.hourly.map((h) => ({
                      tick: h.hour % 3 === 0 ? hourLabel(h.hour) : '',
                      label: formatClock(h.hour * 60),
                      value: h.laeq_dba,
                      reference: !isBaseline ? base?.hourly.find((b) => b.hour === h.hour)?.laeq_dba : undefined,
                    }))}
                    format={(v) => `${v.toFixed(1)} dBA`}
                    seriesLabel={isBaseline ? 'indoor level' : 'this scenario'}
                    referenceLabel="as built"
                    selected={hour}
                    floor={floor}
                    onSelect={(h) => {
                      const p = zonedParts(new Date(datetime), twin.timezone);
                      dispatch('viewer.setTime', { datetime: toZonedIso(fromZonedParts({ ...p, hour: h, minute: 0, second: 0 }, twin.timezone), twin.timezone) });
                    }}
                  />
                  <TableView
                    caption={`Indoor noise level per hour in ${name}`}
                    columns={isBaseline ? ['Hour', 'Level (dBA)'] : ['Hour', 'This scenario (dBA)', 'As built (dBA)']}
                    rows={data.hourly.map((h) => [formatClock(h.hour * 60), h.laeq_dba.toFixed(1), ...(isBaseline ? [] : [base?.hourly.find((b) => b.hour === h.hour)?.laeq_dba.toFixed(1) ?? '–'])])}
                  />
                </div>
              </>
            )}
          </section>

          {data && current && (
            <div className="grid gap-4 md:grid-cols-2">
              <section className="rounded-xl border border-line bg-surface p-5">
                <ChartTitle title="Pitch" subtitle={`Level per octave band at ${hourLabel(hour)}, dB. Low rumble is on the left.`} />
                <ColumnChart
                  ariaLabel="Level per octave band"
                  height={150}
                  columns={OCTAVE_BANDS_HZ.map((hz, i) => ({ tick: bandLabel(hz), label: `${hz} Hz`, value: current.bands_db[i] ?? 0 }))}
                  format={(v) => `${v.toFixed(1)} dB`}
                  seriesLabel="band level"
                  floor={Math.max(0, Math.floor((Math.min(...current.bands_db) - 5) / 10) * 10)}
                  maxTicks={3}
                />
                <TableView caption="Level per octave band" columns={['Band (Hz)', 'Level (dB)']} rows={OCTAVE_BANDS_HZ.map((hz, i) => [hz, (current.bands_db[i] ?? 0).toFixed(1)])} />
              </section>
              <section className="rounded-xl border border-line bg-surface p-5">
                <ChartTitle title="Where it comes from" subtitle="Loudest hour, by source." />
                <ul className="space-y-2">
                  {data.sources.map((s) => (
                    <li key={s.name} className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="truncate text-muted">{s.name}</span>
                      <span className="font-medium tabular-nums">{s.contribution_dba.toFixed(1)} dBA</span>
                    </li>
                  ))}
                </ul>
                {twin.quality.traffic !== 'aadt' && (
                  <p className="mt-4 text-xs leading-5 text-sun-deep">
                    {twin.quality.traffic === 'none' ? 'No traffic data for this address.' : 'Traffic volumes are defaults for the road class, not counts for this street.'} Treat the levels as estimates.
                  </p>
                )}
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
