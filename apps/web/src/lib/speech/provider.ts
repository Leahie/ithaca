/**
 * Speech behind one interface. "webspeech" (the default) uses the Web Speech API for STT and
 * speechSynthesis for TTS; "deepgram" speaks with Deepgram's Aura voices through the API and
 * listens with the browser.
 */
import { config, wsOrigin } from '@/lib/config';

export interface ListenHandlers {
  /** Called with the full transcript so far. `final` is true once, when listening ends. */
  onTranscript(text: string, final: boolean): void;
  onError(message: string): void;
  /** The microphone is open and sound is arriving: 0 (silence) to 1 (loud), about ten times a second. */
  onLevel?(level: number): void;
}

export interface ListenOptions {
  lang?: string;
  auto?: boolean;
}

export interface SpeechProvider {
  readonly name: string;
  sttAvailable(): boolean;
  ttsAvailable(): boolean;
  /**
   * Start listening. Returns a function that stops and delivers the final transcript. With
   * `auto`, listening also ends by itself when the speaker pauses: no need to hold anything.
   */
  listen(handlers: ListenHandlers, opts?: ListenOptions): () => void;
  /** Say this now: whatever was being said, or was waiting to be said, is dropped. */
  speak(text: string, opts?: { lang?: string; rate?: number }): void;
  /** Say this after what is being said. For a reply that arrives sentence by sentence. */
  enqueue(text: string, opts?: { lang?: string; rate?: number }): void;
  cancelSpeech(): void;
  /** Something is being said, or is waiting to be said. */
  isSpeaking(): boolean;
  /** Listening can start now without a tap (the microphone is still open from the last time). */
  canListenUntapped(): boolean;
}

interface RecognitionResult {
  isFinal: boolean;
  0: { transcript: string };
}
interface RecognitionEvent {
  results: ArrayLike<RecognitionResult>;
}
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((ev: RecognitionEvent) => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | undefined {
  if (typeof window === 'undefined') return undefined;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

const ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone access was denied.',
  'service-not-allowed': 'Speech recognition is not allowed here. It needs HTTPS or localhost.',
  'no-speech': 'No speech was heard.',
  'audio-capture': 'No microphone was found.',
  network: 'Speech recognition needs a network connection in this browser.',
};

export class WebSpeechProvider implements SpeechProvider {
  readonly name = 'webspeech';

  sttAvailable(): boolean {
    return !!recognitionCtor();
  }

  ttsAvailable(): boolean {
    return typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  listen(handlers: ListenHandlers, opts: ListenOptions = {}): () => void {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      handlers.onError('Speech recognition is not available in this browser.');
      return () => {};
    }
    const rec = new Ctor();
    rec.lang = opts.lang ?? navigator.language ?? 'en-US';
    rec.continuous = !opts.auto; // not continuous: the browser ends it at the first pause
    rec.interimResults = true;
    let text = '';
    let failed = false;
    rec.onresult = (ev) => {
      text = Array.from(ev.results, (r) => r[0].transcript).join(' ').replace(/\s+/g, ' ').trim();
      handlers.onTranscript(text, false);
    };
    rec.onerror = (ev) => {
      if (ev.error === 'aborted') return;
      failed = true;
      handlers.onError(ERRORS[ev.error] ?? `Speech recognition failed (${ev.error}).`);
    };
    rec.onend = () => {
      if (!failed) handlers.onTranscript(text, true);
    };
    try {
      rec.start();
    } catch (err) {
      handlers.onError(err instanceof Error ? err.message : String(err));
    }
    return () => rec.stop();
  }

  speak(text: string, opts: { lang?: string; rate?: number } = {}): void {
    this.cancelSpeech();
    this.enqueue(text, opts);
  }

  /** speechSynthesis keeps its own queue: an utterance waits for the ones before it. */
  enqueue(text: string, opts: { lang?: string; rate?: number } = {}): void {
    if (!this.ttsAvailable()) return;
    const clean = speakable(text);
    if (!clean) return;
    const synth = window.speechSynthesis;
    const u = new SpeechSynthesisUtterance(clean);
    if (opts.lang) u.lang = opts.lang;
    else {
      const voices = synth.getVoices().filter((v) => v.lang.startsWith('en'));
      const preferred = voiceKind() === 'male' ? /Daniel|David|Guy|Alex|Google UK English Male|Andrew|Christopher/ : /Samantha|Ava|Allison|Google US English|Jenny|Aria/;
      u.voice = voices.find((v) => preferred.test(v.name)) ?? voices[0] ?? null;
    }
    u.rate = opts.rate ?? 1.04;
    synth.speak(u);
  }

  cancelSpeech(): void {
    if (this.ttsAvailable()) window.speechSynthesis.cancel();
  }

  isSpeaking(): boolean {
    return this.ttsAvailable() && (window.speechSynthesis.speaking || window.speechSynthesis.pending);
  }

  canListenUntapped(): boolean {
    return false; // the browser's own recognition needs a tap every time on a phone
  }
}

export type VoiceKind = 'female' | 'male';
const VOICE_KEY = 'ithaca.voice';

/** The guide's voice, kept in this browser. Read it after mount: the server has no localStorage. */
export function voiceKind(): VoiceKind {
  try {
    return window.localStorage.getItem(VOICE_KEY) === 'male' ? 'male' : 'female';
  } catch {
    return 'female';
  }
}

export function setVoiceKind(kind: VoiceKind): void {
  try {
    window.localStorage.setItem(VOICE_KEY, kind);
  } catch {
    /* this page load only */
  }
}

/** Markdown and symbols read badly aloud. */
export function speakable(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*_`#>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Text cut into requests for the voice service: whole sentences, at most `max` characters each
 * (Deepgram takes 2000). The first request is one sentence, so speech starts quickly.
 */
export function speechChunks(text: string, max = 1000): string[] {
  const sentences = text.split(/(?<=[.!?]["')\]]*)\s+/).map((s) => s.trim()).filter(Boolean);
  const pieces = sentences.flatMap((s) => {
    if (s.length <= max) return [s];
    const parts: string[] = [];
    let cur = '';
    for (const word of s.split(' ')) {
      for (let w = word; w; w = w.slice(max)) {
        const head = w.slice(0, max);
        if (cur && cur.length + 1 + head.length > max) {
          parts.push(cur);
          cur = head;
        } else cur = cur ? `${cur} ${head}` : head;
      }
    }
    return cur ? [...parts, cur] : parts;
  });
  const out: string[] = [];
  let cur = '';
  for (const p of pieces) {
    if (cur && (out.length === 0 || cur.length + 1 + p.length > max)) {
      out.push(cur);
      cur = p;
    } else cur = cur ? `${cur} ${p}` : p;
  }
  if (cur) out.push(cur);
  return out;
}

class VoiceUnavailable extends Error {}

/** The noise gate: samples are 16-bit, so full scale is 32768. A voice at the phone peaks in the thousands. */
const GATE_MIN = 900;
const GATE_MAX = 2600;
/** The gate stays open this long after the last loud block, so soft ends of words get through. */
const GATE_HOLD_MS = 450;

/** How long the microphone stays open after a question, ready for the next one without a tap. */
const KEEP_MIC_MS = 120_000;

interface OpenMic {
  /** The audio context the capture runs in; null when the track is read directly. */
  ctx: AudioContext | null;
  /** Samples per second of the blocks handed to `sink`. */
  rate: number;
  stream: MediaStream;
  nodes: AudioNode[];
  /** Where blocks of samples go while someone is listening. */
  sink: ((pcm: Int16Array) => void) | null;
  /** How the samples are taken from the microphone (see openMic). */
  how: 'script' | 'worklet' | 'track';
  /** Ends a direct read of the track. */
  stop?: () => void;
}

/**
 * Chromium's reader of raw audio straight from a microphone track (not in TypeScript's DOM
 * types, and not in Safari or Firefox).
 */
type TrackProcessor = new (init: { track: MediaStreamTrack }) => { readable: ReadableStream<AudioData> };
/** Set on Android, where capture through an audio context stalls (see readTrack). Elsewhere the worklet works and stays. */
const trackProcessor = (): TrackProcessor | undefined =>
  /Android/i.test(navigator.userAgent) ? (globalThis as { MediaStreamTrackProcessor?: TrackProcessor }).MediaStreamTrackProcessor : undefined;

/** One channel of a frame of microphone sound as floats in -1..1, whatever format the browser hands over. */
function frameSamples(frame: AudioData, scratch: Float32Array<ArrayBuffer>): Float32Array<ArrayBuffer> {
  const len = frame.numberOfFrames;
  const out = scratch.length >= len ? scratch : new Float32Array(len);
  try {
    frame.copyTo(out, { planeIndex: 0, format: 'f32-planar' }); // the browser converts
    return out;
  } catch {
    // No conversion in this browser: read the frame as it is and take the first channel.
    const planar = (frame.format ?? '').endsWith('-planar');
    const step = planar ? 1 : frame.numberOfChannels;
    if (frame.format?.startsWith('s16')) {
      const raw = new Int16Array(len * step);
      frame.copyTo(raw, { planeIndex: 0 });
      for (let i = 0; i < len; i++) out[i] = raw[i * step]! / 0x8000;
    } else {
      const raw = new Float32Array(len * step);
      frame.copyTo(raw, { planeIndex: 0 });
      for (let i = 0; i < len; i++) out[i] = raw[i * step]!;
    }
    return out;
  }
}
const isApple = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
/** The first block of sound must come within this long, or the track is not delivering. */
const FIRST_SOUND_MS = 2500;

/** Closes the listen socket: the API has no Deepgram key. */
const NOT_CONFIGURED = 4503;

/** Microphone samples as 16-bit PCM, posted in 100 ms blocks. */
const PCM_WORKLET = `
class Pcm16 extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(Math.round(sampleRate / 10)); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.buffer, [this.buf.buffer]); this.buf = new Int16Array(this.buf.length); this.n = 0; }
    }
    return true;
  }
}
registerProcessor('pcm16', Pcm16);
`;

function micError(err: unknown): string {
  const name = err instanceof DOMException ? err.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'Microphone access was denied.';
  if (name === 'NotFoundError') return 'No microphone was found.';
  return `The microphone could not be opened (${err instanceof Error ? err.message : String(err)}).`;
}

/**
 * Deepgram through the API, so the key stays on the server: its voices from POST /speech/tts,
 * and transcription of the microphone streamed over /ws/speech/listen. Falls back to the
 * browser's own speech in fixture mode, when the API has no key, and when a request fails.
 */
export class DeepgramProvider implements SpeechProvider {
  readonly name = 'deepgram';
  private readonly browser = new WebSpeechProvider();
  /** Set for good once the API says it has no voice service. */
  private unavailable = config.useFixtures;
  /** Set for good once the API says it has no transcription service. */
  private sttUnavailable = config.useFixtures;
  private seq = 0;
  private abort: AbortController | null = null;
  private audio: HTMLAudioElement | null = null;
  private speaking: string | null = null;
  private queue: { text: string; audio: Promise<Blob> }[] = [];
  private draining = false;
  /** The open microphone, kept for a while between questions. */
  private mic: OpenMic | null = null;
  private micIdle = 0;

  private streams(): boolean {
    return !this.sttUnavailable && typeof AudioWorkletNode !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
  }

  sttAvailable(): boolean {
    return (typeof window !== 'undefined' && this.streams()) || this.browser.sttAvailable();
  }

  ttsAvailable(): boolean {
    return typeof window !== 'undefined' && (typeof Audio !== 'undefined' || this.browser.ttsAvailable());
  }

  /** The microphone is open and running from an earlier tap, so listening can start without one. */
  canListenUntapped(): boolean {
    const m = this.mic;
    return !!m && (!m.ctx || m.ctx.state === 'running') && m.stream.getAudioTracks()[0]?.readyState === 'live';
  }

  private closeMic(): void {
    clearTimeout(this.micIdle);
    const m = this.mic;
    this.mic = null;
    if (!m) return;
    m.sink = null;
    m.stop?.();
    m.stream.getTracks().forEach((t) => t.stop());
    m.nodes.forEach((n) => n.disconnect());
    if (m.ctx && m.ctx.state !== 'closed') void m.ctx.close();
  }

  /**
   * Read the microphone track's own samples (Chromium), with no audio context in between. On
   * Android an audio context stalls a moment after the microphone opens: the phone moves its
   * sound output to "call" mode, and a worklet or script node in that context is then never run
   * again, though the context still says "running". The track itself keeps delivering.
   * Resolves once the first sound has arrived (so the sample rate is known), or throws.
   */
  private async readTrack(mic: OpenMic, Processor: TrackProcessor): Promise<void> {
    const track = mic.stream.getAudioTracks()[0];
    if (!track) throw new Error('no audio track');
    const reader = new Processor({ track }).readable.getReader();
    let live = true;
    mic.stop = () => {
      live = false;
      void reader.cancel().catch(() => {});
    };
    let block: Int16Array | null = null;
    let n = 0;
    let scratch = new Float32Array(0);
    const take = (frame: AudioData) => {
      try {
        const len = frame.numberOfFrames;
        scratch = frameSamples(frame, scratch);
        if (!block || mic.rate !== frame.sampleRate) {
          mic.rate = frame.sampleRate;
          block = new Int16Array(Math.round(frame.sampleRate / 10)); // a tenth of a second, as the worklet sends
          n = 0;
        }
        for (let i = 0; i < len; i++) {
          const v = Math.max(-1, Math.min(1, scratch[i]!));
          block[n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
          if (n === block.length) {
            mic.sink?.(block);
            block = new Int16Array(block.length);
            n = 0;
          }
        }
      } finally {
        frame.close();
      }
    };
    const first = await Promise.race([reader.read(), new Promise<null>((r) => setTimeout(() => r(null), FIRST_SOUND_MS))]);
    if (!first || first.done || !first.value) {
      mic.stop();
      mic.stop = undefined;
      throw new Error('the microphone track delivered no sound');
    }
    try {
      take(first.value);
    } catch (err) {
      mic.stop();
      mic.stop = undefined;
      throw err;
    }
    void (async () => {
      while (live) {
        const { value, done } = await reader.read();
        if (done || !value) break;
        take(value);
      }
    })().catch((err: unknown) => console.warn('[speech] the microphone track stopped', err));
  }

  /**
   * Open the microphone into `ctx` and keep it: blocks of 16-bit samples go to whoever is
   * listening (`sink`), and to nobody in between. The nodes are held in the object on purpose:
   * audio nodes nothing refers to can be collected by the browser, and the microphone then reads
   * as silence.
   */
  private async openMic(made: AudioContext | null, apple: boolean): Promise<OpenMic> {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    const mic: OpenMic = { ctx: null, rate: stream.getAudioTracks()[0]?.getSettings().sampleRate ?? 48000, stream, nodes: [], sink: null, how: 'track' };
    // Chromium (Android phones, desktop Chrome and Edge): straight from the track.
    const Processor = apple ? undefined : trackProcessor();
    if (Processor) {
      try {
        await this.readTrack(mic, Processor);
        if (made && made.state !== 'closed') void made.close();
        return mic;
      } catch (err) {
        console.warn('[speech] reading the microphone track failed; using an audio context.', err);
      }
    }
    // Everything else goes through an audio context. Outside Apple's browsers one may be made
    // here, after the tap; an iPhone's must come from the tap itself (see listen).
    const ctx = made ?? new AudioContext();
    mic.ctx = ctx;
    mic.rate = ctx.sampleRate;
    mic.how = apple ? 'script' : 'worklet';
    // Opening the microphone switches an iPhone's audio route, which can interrupt the context.
    if (ctx.state !== 'running') await Promise.race([ctx.resume(), new Promise((r) => setTimeout(r, 800))]);
    const source = ctx.createMediaStreamSource(stream);
    let tap: AudioNode;
    if (apple) {
      // iPhone and iPad: the old script node. Their audio worklet stops being called a moment
      // after the microphone opens; this one keeps running.
      const node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (e) => {
        if (!mic.sink) return;
        const f = e.inputBuffer.getChannelData(0);
        const pcm = new Int16Array(f.length);
        for (let i = 0; i < f.length; i++) {
          const v = Math.max(-1, Math.min(1, f[i]!));
          pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
        }
        mic.sink(pcm);
      };
      tap = node;
    } else {
      const url = URL.createObjectURL(new Blob([PCM_WORKLET], { type: 'text/javascript' }));
      try {
        await ctx.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      const node = new AudioWorkletNode(ctx, 'pcm16');
      node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => mic.sink?.(new Int16Array(e.data));
      tap = node;
    }
    // Through a silent gain to the output: a node only runs while it leads somewhere.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    source.connect(tap);
    tap.connect(mute);
    mute.connect(ctx.destination);
    mic.nodes = [source, tap, mute];
    return mic;
  }

  listen(handlers: ListenHandlers, opts?: ListenOptions): () => void {
    if (!this.streams()) return this.browser.listen(handlers, opts);
    const apple = isApple();
    clearTimeout(this.micIdle);
    // A microphone still open from the last question is used again: that needs no tap, which is
    // what lets the phone listen by itself when the guide asks something. Otherwise the audio
    // context is made and started here, inside the tap: an iPhone only lets audio start from a
    // user gesture (a context made later reports "running" but stalls after a moment). Where the
    // track is read directly (Chromium) there is no context to make.
    let mic = this.canListenUntapped() ? this.mic : null;
    let fresh: AudioContext | null = null;
    const opening = !mic;
    if (!mic) {
      this.closeMic();
      if (apple || !trackProcessor()) {
        fresh = new AudioContext();
        void fresh.resume().catch(() => {});
      }
    }
    let frames = 0;
    let watchdog = 0;
    let ws: WebSocket | null = null;
    let fallback: (() => void) | null = null;
    let text = '';
    let stopping = false;
    let done = false;
    let timer = 0;

    /** Stop feeding this question; the microphone stays open for a while in case there is another. */
    const rest = () => {
      clearTimeout(watchdog);
      if (this.mic) this.mic.sink = null;
      clearTimeout(this.micIdle);
      this.micIdle = window.setTimeout(() => this.closeMic(), KEEP_MIC_MS);
    };
    const finish = (final: string) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      rest();
      ws?.close();
      handlers.onTranscript(final, true);
    };
    const fail = (message: string) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      clearTimeout(watchdog);
      this.closeMic(); // whatever went wrong, start clean next time
      if (fresh && fresh.state !== 'closed') void fresh.close();
      ws?.close();
      handlers.onError(message);
    };
    const sendStop = () => ws?.send(JSON.stringify({ type: 'stop' }));

    void (async () => {
      if (!mic) {
        try {
          mic = await this.openMic(fresh, apple);
        } catch (err) {
          return fail(micError(err));
        }
        this.mic = mic;
      }
      const open = mic;
      if (stopping) return finish(''); // stopped before the microphone opened
      const { ctx } = open;
      const track = open.stream.getAudioTracks()[0];
      const facts = () => `microphone ${track?.muted ? 'muted' : 'live'} at ${track?.getSettings().sampleRate ?? '?'} Hz, ${ctx ? `audio ${ctx.state} at ${ctx.sampleRate} Hz` : `read at ${open.rate} Hz`}, ${open.how} capture${opening ? '' : ', microphone kept open'}`;

      const socket = new WebSocket(`${wsOrigin()}/ws/speech/listen?sample_rate=${open.rate}${opts?.auto ? '&auto=1' : ''}`);
      ws = socket;
      socket.binaryType = 'arraybuffer';
      let loudest = 0;
      let lastFrame = performance.now();
      const started = lastFrame;
      // A gate for noisy rooms: only sound about as loud as someone talking into the phone goes
      // through; the rest (other people, the big screen's own voice across the room) is sent as
      // silence, so it is not written down and the end of the user's sentence is still found.
      // The threshold follows the room: three times its background level, within limits.
      let room = 300;
      let openUntil = 0;
      let held: Int16Array | null = null;
      const send = (block: Int16Array) => {
        if (socket.readyState === WebSocket.OPEN && !stopping) socket.send(block.buffer as ArrayBuffer);
      };
      open.sink = (pcm) => {
        if (done) return;
        frames += 1;
        const now = performance.now();
        lastFrame = now;
        let peak = 0;
        for (let i = 0; i < pcm.length; i += 4) peak = Math.max(peak, Math.abs(pcm[i]!));
        loudest = Math.max(loudest, peak);
        const gate = Math.max(GATE_MIN, Math.min(GATE_MAX, room * 3));
        handlers.onLevel?.(peak >= gate ? Math.min(1, 0.25 + peak / 9000) : Math.min(0.12, peak / 9000));
        if (peak >= gate) {
          // The block just before a word starts carries its first sound: send it too.
          if (now > openUntil && held) send(held);
          openUntil = now + GATE_HOLD_MS;
        } else {
          room += (peak - room) * 0.05;
        }
        if (now <= openUntil) {
          send(pcm);
          held = null;
        } else {
          send(new Int16Array(pcm.length));
          held = pcm;
        }
      };

      // The microphone has to keep delivering: no blocks at all, blocks that stop coming, or
      // blocks of exact zeros (a real microphone is never exactly silent) all mean it is broken.
      const check = () => {
        if (done || stopping) return;
        const now = performance.now();
        if (!frames && now - started > 3000) return fail(`The microphone opened but no sound arrived (${facts()}). Tap the mic to try again.`);
        if (frames && now - lastFrame > 2000) return fail(`The microphone stopped delivering sound (${facts()}). Tap the mic to try again.`);
        if (frames && loudest === 0 && now - started > 3000) return fail(`The microphone is open but sends only silence (${facts()}). Close other apps that use the microphone.`);
        if (ctx && ctx.state !== 'running') void ctx.resume().catch(() => {});
        watchdog = window.setTimeout(check, 700);
      };
      watchdog = window.setTimeout(check, 700);

      socket.onopen = () => {
        // What this browser says about its microphone, for the server's speech log.
        socket.send(JSON.stringify({ type: 'diag', facts: `${facts()}, ${navigator.userAgent.slice(0, 120)}` }));
        if (stopping) sendStop();
      };
      socket.onmessage = (e: MessageEvent<string>) => {
        const m = JSON.parse(e.data) as { text: string; final: boolean };
        text = m.text;
        if (m.final) finish(m.text);
        else if (!done) handlers.onTranscript(m.text, false);
      };
      socket.onclose = (e) => {
        if (done) return;
        if (e.code === NOT_CONFIGURED) {
          this.sttUnavailable = true;
          if (stopping) return finish('');
          done = true;
          clearTimeout(watchdog);
          this.closeMic();
          fallback = this.browser.listen(handlers, opts);
        } else if (stopping) finish(text);
        else fail(`Speech recognition stopped: the connection to the server was lost (${frames} blocks of sound sent).`);
      };
    })().catch((err: unknown) => fail(`Speech recognition could not start (${err instanceof Error ? err.message : String(err)}).`));

    return () => {
      if (fallback) return fallback();
      if (stopping) return;
      stopping = true;
      if (!ws) return; // still opening the microphone; that path finishes
      if (this.mic) this.mic.sink = null;
      if (ws.readyState === WebSocket.OPEN) sendStop();
      // The last words arrive within a second or so; don't wait forever for them.
      timer = window.setTimeout(() => finish(text), 4000);
    };
  }

  /** Something is being said, or is waiting to be said. */
  isSpeaking(): boolean {
    return this.draining || this.queue.length > 0 || this.audio !== null || this.browser.isSpeaking();
  }

  speak(text: string, opts: { lang?: string; rate?: number } = {}): void {
    // The guide and the agent dock can both read the same reply: say it once.
    if (this.speaking !== null && text === this.speaking) return;
    this.cancelSpeech();
    this.speaking = text;
    this.enqueue(text, opts);
  }

  /**
   * Each part's audio is asked for at once, so it is ready (or nearly) when its turn comes; the
   * parts play in the order they were given. A reply that streams in is spoken from its first
   * sentence, while the rest is still being written.
   */
  enqueue(text: string, opts: { lang?: string; rate?: number } = {}): void {
    const clean = speakable(text);
    if (!clean) return;
    if (this.unavailable) return this.browser.enqueue(clean, opts);
    this.abort ??= new AbortController();
    for (const part of speechChunks(clean)) {
      const audio = this.fetchAudio(part, this.abort.signal);
      audio.catch(() => {}); // handled when its turn comes
      this.queue.push({ text: part, audio });
    }
    if (!this.draining) void this.drain(this.seq, opts);
  }

  cancelSpeech(): void {
    this.speaking = null;
    this.seq++;
    this.abort?.abort();
    this.abort = null;
    this.queue = [];
    this.draining = false;
    this.audio?.pause();
    this.audio = null;
    this.browser.cancelSpeech();
  }

  private async drain(id: number, opts: { lang?: string; rate?: number }): Promise<void> {
    this.draining = true;
    while (this.queue.length && id === this.seq) {
      const item = this.queue.shift()!;
      let blob: Blob;
      try {
        blob = await item.audio;
      } catch (err) {
        if (id !== this.seq) return;
        if (err instanceof VoiceUnavailable) this.unavailable = true;
        else console.warn('[speech] Deepgram failed; using the browser voice.', err);
        for (const rest of [item, ...this.queue.splice(0)]) this.browser.enqueue(rest.text, opts);
        break;
      }
      if (id !== this.seq) return;
      await this.playBlob(blob);
    }
    if (id === this.seq) {
      this.draining = false;
      this.speaking = null;
    }
  }

  private async fetchAudio(text: string, signal: AbortSignal): Promise<Blob> {
    const res = await fetch(`${config.apiBase}/speech/tts`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text, voice: voiceKind() }),
      signal,
    });
    if (res.status === 503 || res.status === 404) throw new VoiceUnavailable(`no voice service (${res.status})`);
    if (!res.ok) throw new Error(`text to speech failed (${res.status})`);
    return res.blob();
  }

  /** Resolves when the part has played, or was stopped. Browsers only play audio after a click on the page. */
  private playBlob(blob: Blob): Promise<void> {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        URL.revokeObjectURL(url);
        if (this.audio === audio) this.audio = null;
        resolve();
      };
      audio.onended = done;
      audio.onerror = done;
      audio.onpause = done;
      this.audio = audio;
      audio.play().catch((err: unknown) => {
        // The browser refuses sound until the page has been clicked: say so, don't fail in silence.
        if (err instanceof DOMException && err.name === 'NotAllowedError') setSoundBlocked(true);
        done();
      });
    });
  }
}

/**
 * Browsers play no sound on a page that has not been clicked or tapped since it loaded (also after
 * a reload). `soundBlocked` is true while that is the case and a reply could not be played;
 * `unlockSound`, called from any click, plays a moment of silence so later replies are heard.
 */
let blocked = false;
const blockedListeners = new Set<(blocked: boolean) => void>();
function setSoundBlocked(next: boolean): void {
  if (blocked === next) return;
  blocked = next;
  blockedListeners.forEach((l) => l(next));
}
export function onSoundBlocked(cb: (blocked: boolean) => void): () => void {
  blockedListeners.add(cb);
  return () => blockedListeners.delete(cb);
}
export function pageHasBeenClicked(): boolean {
  const activation = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation;
  return activation ? activation.hasBeenActive : true;
}
export function unlockSound(): void {
  try {
    const silent = new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=');
    void silent.play().catch(() => {});
  } catch {
    /* nothing to unlock here */
  }
  setSoundBlocked(false);
}

const providers = new Map<string, () => SpeechProvider>([
  ['webspeech', () => new WebSpeechProvider()],
  ['deepgram', () => new DeepgramProvider()],
]);
let active: SpeechProvider | null = null;

/** Register another provider (e.g. ElevenLabs TTS) under a name. */
export function registerSpeechProvider(name: string, factory: () => SpeechProvider): void {
  providers.set(name, factory);
  active = null;
}

export function speechProvider(): SpeechProvider {
  if (!active) {
    const name = process.env.NEXT_PUBLIC_SPEECH_PROVIDER ?? 'webspeech';
    active = (providers.get(name) ?? providers.get('webspeech')!)();
  }
  return active;
}
