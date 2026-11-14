/**
 * Speech behind one interface, so Deepgram / ElevenLabs can replace the browser later.
 * The default provider uses the Web Speech API for STT and speechSynthesis for TTS.
 */
export interface ListenHandlers {
  /** Called with the full transcript so far. `final` is true once, when listening ends. */
  onTranscript(text: string, final: boolean): void;
  onError(message: string): void;
}

export interface SpeechProvider {
  readonly name: string;
  sttAvailable(): boolean;
  ttsAvailable(): boolean;
  /** Start listening. Returns a function that stops and delivers the final transcript. */
  listen(handlers: ListenHandlers, opts?: { lang?: string }): () => void;
  speak(text: string, opts?: { lang?: string; rate?: number }): void;
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

  listen(handlers: ListenHandlers, opts: { lang?: string } = {}): () => void {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      handlers.onError('Speech recognition is not available in this browser.');
      return () => {};
    }
    const rec = new Ctor();
    rec.lang = opts.lang ?? navigator.language ?? 'en-US';
    rec.continuous = true;
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
    if (!this.ttsAvailable()) return;
    const u = new SpeechSynthesisUtterance(text);
    if (opts.lang) u.lang = opts.lang;
    u.rate = opts.rate ?? 1.05;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  cancelSpeech(): void {
    if (this.ttsAvailable()) window.speechSynthesis.cancel();
  }
}

const providers = new Map<string, () => SpeechProvider>([['webspeech', () => new WebSpeechProvider()]]);
let active: SpeechProvider | null = null;

/** Register another provider (e.g. Deepgram STT + ElevenLabs TTS) under a name. */
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
