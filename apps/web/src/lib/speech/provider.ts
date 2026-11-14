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

  private streams(): boolean {
    return !this.sttUnavailable && typeof AudioWorkletNode !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
  }

  sttAvailable(): boolean {
    return (typeof window !== 'undefined' && this.streams()) || this.browser.sttAvailable();
  }

  ttsAvailable(): boolean {
    return typeof window !== 'undefined' && (typeof Audio !== 'undefined' || this.browser.ttsAvailable());
  }

  listen(handlers: ListenHandlers, opts?: ListenOptions): () => void {
    if (!this.streams()) return this.browser.listen(handlers, opts);
    // The audio context is made after the microphone opens, not here in the tap. On an iPhone a
    // context that exists before the microphone is granted is interrupted when the audio route
    // switches to recording, and then delivers silence; one made while capturing starts running.
    let ctx: AudioContext | null = null;
    let frames = 0;
    let watchdog = 0;
    let stream: MediaStream | null = null;
    let ws: WebSocket | null = null;
    let fallback: (() => void) | null = null;
    let text = '';
    let stopping = false;
    let done = false;
    let timer = 0;

    const release = () => {
      stream?.getTracks().forEach((t) => t.stop());
      stream = null;
      clearTimeout(watchdog);
      if (ctx && ctx.state !== 'closed') void ctx.close();
    };
    const finish = (final: string) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      release();
      ws?.close();
      handlers.onTranscript(final, true);
    };
    const fail = (message: string) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      release();
      ws?.close();
      handlers.onError(message);
    };
    const sendStop = () => ws?.send(JSON.stringify({ type: 'stop' }));

    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      } catch (err) {
        return fail(micError(err));
      }
      if (stopping) return finish(''); // released before the microphone opened
      const audio = new AudioContext();
      ctx = audio;
      const url = URL.createObjectURL(new Blob([PCM_WORKLET], { type: 'text/javascript' }));
      try {
        // resume() can hang on a phone; the watchdog below catches a context that never runs.
        await Promise.race([audio.resume(), new Promise((r) => setTimeout(r, 800))]);
        await audio.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      if (stopping || done) return finish('');
      const node = new AudioWorkletNode(audio, 'pcm16');
      audio.createMediaStreamSource(stream).connect(node);
      node.connect(audio.destination); // silent; keeps the node running
      // No sound at all within three seconds means the microphone is not really delivering.
      watchdog = window.setTimeout(() => {
        if (!frames && !done) fail(`The microphone opened but no sound arrived (audio is ${audio.state}). Tap the mic to try again.`);
      }, 3000);

      const socket = new WebSocket(`${wsOrigin()}/ws/speech/listen?sample_rate=${audio.sampleRate}${opts?.auto ? '&auto=1' : ''}`);
      ws = socket;
      socket.binaryType = 'arraybuffer';
      node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        frames += 1;
        if (handlers.onLevel) {
          const pcm = new Int16Array(e.data);
          let peak = 0;
          for (let i = 0; i < pcm.length; i += 8) peak = Math.max(peak, Math.abs(pcm[i]!));
          handlers.onLevel(Math.min(1, peak / 12000));
        }
        if (socket.readyState === WebSocket.OPEN && !stopping) socket.send(e.data);
      };
      socket.onopen = () => {
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
          release();
          fallback = this.browser.listen(handlers, opts);
        } else if (stopping) finish(text);
        else fail('Speech recognition stopped: the connection to the server was lost.');
      };
    })().catch((err: unknown) => fail(`Speech recognition could not start (${err instanceof Error ? err.message : String(err)}).`));

    return () => {
      if (fallback) return fallback();
      if (stopping) return;
      stopping = true;
      if (!ws) return; // still opening the microphone; that path finishes
      release();
      if (ws.readyState === WebSocket.OPEN) sendStop();
      // The last words arrive within a second or so; don't wait forever for them.
      timer = window.setTimeout(() => finish(text), 4000);
    };
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
      audio.play().catch(done);
    });
  }
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
