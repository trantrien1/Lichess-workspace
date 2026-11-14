import { storedStringProp } from 'lib/storage';

/** The same ids as services/commentary/voices.py and lila's controllers/Commentary.scala. */
export const commentaryVoices: readonly { id: string; name: string }[] = [
  { id: 'piper-vais1000', name: 'Nữ (mặc định)' },
  { id: 'piper-vais1000-low', name: 'Nữ, giọng trầm' },
  { id: 'piper-vais1000-high', name: 'Nữ, giọng cao' },
  { id: 'piper-vivos-28', name: 'Nam 1 (thử nghiệm)' },
  { id: 'piper-vivos-45', name: 'Nam 2 (thử nghiệm)' },
  { id: 'piper-vivos-40', name: 'Nữ 2 (thử nghiệm)' },
];
const defaultVoice = commentaryVoices[0].id;

export interface CommentaryState {
  gameId: string;
  subscriptionId: string;
  requestId: string;
  gameRevision: number;
  ply: number;
  fenAfter: string;
  status: string;
  expiresAt: number;
  acceptedAt: number;
  /** Nobody has moved for a while; Lila asked for a remark about the game so far. */
  idle?: boolean;
}

type SpeechAudio = { mime: string; base64: string; voice: string; provider: string };
interface CommentaryResult extends CommentaryState {
  type: 'comment' | 'silent' | 'unavailable';
  kind?: 'intro' | 'move' | 'idle' | 'finish' | 'transition';
  commentId?: string;
  text?: string;
  audio?: SpeechAudio;
  transition?: { text: string; audio: SpeechAudio };
  /** 60 and more: a capture of a big piece, a check, a striking reading. */
  priority?: number;
}

export interface CommentaryDeps {
  suspendSpeech?(): () => void;
  claimAudio?(stop: () => void): () => void;
  stream(url: string): EventSource;
  audio(): HTMLAudioElement;
  now(): number;
  createUrl(data: Blob): string;
  revokeUrl(url: string): void;
  /** Site sound volume, 0..1. */
  volume?(): number;
  /** The chosen voice, remembered across games; called with an id to change it. */
  voice?(replacement?: string): string;
}

export type CommentaryPhase = 'off' | 'idle' | 'working' | 'speaking' | 'error';

const defaults: CommentaryDeps = {
  volume: () => site.sound.getVolume(),
  suspendSpeech: () => site.sound.suspendSpeech(),
  claimAudio: stop => {
    if (!window.BroadcastChannel) return () => {};
    const channel = new window.BroadcastChannel('lichess-commentary');
    channel.onmessage = () => stop();
    channel.postMessage('claim');
    return () => channel.close();
  },
  stream: url => new EventSource(url),
  audio: () => new Audio(),
  now: Date.now,
  createUrl: data => URL.createObjectURL(data),
  revokeUrl: url => URL.revokeObjectURL(url),
  voice: storedStringProp('commentary.voice', defaultVoice),
};

const silence = 'UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQIAAACAgA==';
// A comment is ready well under a second after the move (engine, then Piper); this leaves room
// for a slow machine or network without speaking about a move the players left long ago.
const maxArrivalAge = 4500;
// At most one "the moves come fast" bridge in this long: said every few moves it became a refrain.
const bridgeEvery = 45000;
// A line this important goes first: the bridge would make it late, or lose it to the next move.
const bridgeYieldsTo = 60;
// Statuses of a game still being played.
const ongoing = new Set(['created', 'started']);
let activeCommentator: CommentaryCtrl | undefined;

export class CommentaryCtrl {
  enabled = false;
  status = 'Bình luận âm thanh tiếng Việt';
  phase: CommentaryPhase = 'off';
  subtitle = '';
  /** The subtitle stays after its sentence ends, dimmed, until the next one. */
  subtitleOld = false;
  subtitlePly?: number;
  /** What the subtitle is about: 'move', 'idle', 'intro', 'finish' or 'transition'. */
  subtitleKind?: string;
  private source?: EventSource;
  private state?: CommentaryState;
  /** Server clock minus client clock, measured when the current state arrived. */
  private offset = 0;
  private media?: HTMLAudioElement;
  private url?: string;
  private generation = 0;
  private playback = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private workTimer?: ReturnType<typeof setTimeout>;
  private readonly seen = new Set<string>();
  private destroyed = false;
  private releaseSpeech?: () => void;
  private releaseAudio?: () => void;
  private active?: CommentaryResult;
  private pending?: CommentaryResult;
  private introPlayed = false;
  private lastSpokenPly?: number;
  private bridgeDone = false;
  private lastBridgeAt = -Infinity;
  voice: string = defaultVoice;
  /** The sample sentence of a voice just chosen. */
  private sample?: HTMLAudioElement;

  constructor(
    readonly gameId: string,
    private readonly current: () => { ply: number; fen: string; status: string; live: boolean },
    private readonly redraw: () => void,
    private readonly deps: CommentaryDeps = defaults,
  ) {
    const stored = deps.voice?.();
    if (commentaryVoices.some(v => v.id === stored)) this.voice = stored!;
  }

  setVoice = (voice: string): void => {
    if (!commentaryVoices.some(v => v.id === voice)) return;
    const changed = voice !== this.voice;
    this.voice = voice;
    this.deps.voice?.(voice);
    // A sentence being read finishes in the old voice; the stream asks for the next ones in the new one.
    if (changed && this.enabled) this.reset(true);
    this.playSample();
    this.redraw();
  };

  /** Never over a comment being read, which also cuts the sample short when it starts. */
  private playSample(): void {
    this.stopSample();
    if (this.active || this.destroyed) return;
    const media = (this.sample = this.deps.audio());
    media.src = `/commentary/voice/${this.voice}`;
    media.volume = this.volume();
    media.onended = () => this.stopSample();
    media.onerror = () => this.stopSample();
    if (this.enabled) this.setStatus('Đang phát giọng mẫu…', 'speaking');
    void media.play().catch(() => this.stopSample());
  }

  /** The status between sentences. */
  private ready(): void {
    const current = this.current();
    // The reconnection after a voice change must not hide that its sample is playing.
    if (this.sample) this.setStatus('Đang phát giọng mẫu…', 'speaking');
    else if (!ongoing.has(current.status)) this.setStatus('Ván cờ đã kết thúc', 'idle');
    else if (current.live) this.setStatus('Sẵn sàng, chờ nước mới', 'idle');
    else this.setStatus('Đang xem lại nước cũ', 'idle');
  }

  private stopSample(): void {
    const media = this.sample;
    if (!media) return;
    this.sample = undefined;
    media.onended = null;
    media.onerror = null;
    media.pause();
    media.removeAttribute('src');
    media.load();
    if (this.enabled && !this.active && this.phase === 'speaking') {
      this.ready();
      this.redraw();
    }
  }

  private setStatus(status: string, phase: CommentaryPhase): void {
    this.status = status;
    this.phase = phase;
  }

  /** Server time; the arrival window must not depend on the player's clock being right. */
  private serverNow(): number {
    return this.deps.now() + this.offset;
  }

  private volume(): number {
    const volume = this.deps.volume?.() ?? 0.8;
    return Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 0.8;
  }

  private showSubtitle(result: CommentaryResult): void {
    this.subtitle = result.text!;
    this.subtitleOld = false;
    this.subtitlePly = result.ply;
    this.subtitleKind = result.kind || 'move';
  }

  private clearSubtitle(): void {
    this.subtitle = '';
    this.subtitleOld = false;
    this.subtitlePly = undefined;
    this.subtitleKind = undefined;
  }

  toggle = (): void => {
    if (this.enabled) {
      this.disable();
      return;
    }
    if (this.destroyed) return;
    activeCommentator?.disable();
    activeCommentator = this;
    this.releaseSpeech = this.deps.suspendSpeech?.();
    this.releaseAudio = this.deps.claimAudio?.(this.disable);
    this.enabled = true;
    this.media = this.deps.audio();
    this.url = this.deps.createUrl(
      new Blob([Uint8Array.from(atob(silence), char => char.charCodeAt(0))], { type: 'audio/wav' }),
    );
    this.media.src = this.url;
    this.media.volume = this.volume();
    const generation = ++this.generation;
    void this.media.play().catch((error: DOMException) => {
      if (error.name !== 'AbortError' && this.enabled && generation === this.generation) {
        this.setStatus('Trình duyệt chặn âm thanh. Tắt rồi bật lại để cho phép.', 'error');
        this.redraw();
      }
    });
    if (!this.current().live || !ongoing.has(this.current().status)) this.ready();
    else
      this.setStatus(this.volume() ? 'Đang kết nối…' : 'Âm lượng trang đang tắt; chỉ có phụ đề', 'working');
    this.connect();
    this.redraw();
  };

  disable = (): void => {
    this.enabled = false;
    ++this.generation;
    this.source?.close();
    this.source = undefined;
    this.state = undefined;
    this.pending = undefined;
    this.lastSpokenPly = undefined;
    this.bridgeDone = false;
    this.clearWorkTimer();
    this.stopAudio();
    this.media = undefined;
    this.releaseSpeech?.();
    this.releaseSpeech = undefined;
    this.releaseAudio?.();
    this.releaseAudio = undefined;
    if (activeCommentator === this) activeCommentator = undefined;
    this.clearSubtitle();
    this.setStatus('Đã tắt bình luận', 'off');
    this.redraw();
  };

  private connect(): void {
    if (!this.current().live) return;
    const generation = this.generation;
    const source = (this.source = this.deps.stream(
      `/commentary/${this.gameId}?voice=${encodeURIComponent(this.voice)}`,
    ));
    source.onmessage = event => {
      if (!this.enabled || generation !== this.generation || source !== this.source) return;
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'heartbeat') return;
        if (data.type === 'superseded') {
          this.disable();
          return;
        }
        if (data.gameId !== this.gameId) return;
        if (data.type !== 'state') {
          this.receive(data, generation);
          return;
        }
        this.clearWorkTimer();
        this.pending = undefined;
        if (data.reset) {
          this.stopAudio();
          this.lastSpokenPly = undefined;
          this.bridgeDone = false;
          this.clearSubtitle();
        }
        this.state = data;
        // The state leaves Lila the moment it is accepted, so this is the clock
        // offset (minus a little network latency) for judging its result.
        if (Number.isFinite(data.acceptedAt)) this.offset = data.acceptedAt - this.deps.now();
        // A move replaces only the waiting result; it never interrupts speech.
        // A remark while nobody moves is optional: no "working" status, no "no comment" notice.
        if (!this.active) {
          if (data.snapshot || data.reset) this.ready();
          else if (!data.idle) this.setStatus('Đang tạo lời bình…', 'working');
        }
        if (!data.snapshot && !data.reset && !data.idle)
          this.workTimer = setTimeout(
            () => {
              if (!this.enabled || generation !== this.generation || this.state?.requestId !== data.requestId)
                return;
              this.pending = undefined;
              if (!this.active) this.setStatus('Không có lời bình cho nước này', 'idle');
              this.redraw();
            },
            Math.max(0, data.expiresAt - this.serverNow()),
          );
        this.redraw();
      } catch {
        this.setStatus('Dữ liệu bình luận không hợp lệ', 'error');
        this.redraw();
      }
    };
    source.onerror = () => {
      if (source !== this.source) return;
      this.clearWorkTimer();
      this.state = undefined;
      this.pending = undefined;
      // A temporary stream disconnect must not cut a sentence already playing.
      if (!this.active) {
        // CLOSED (2) means the browser gave up; CONNECTING means it retries by itself.
        if (source.readyState === 2)
          this.setStatus('Mất kết nối bình luận. Tắt rồi bật lại để thử.', 'error');
        else this.setStatus('Mất kết nối bình luận; đang thử lại…', 'error');
      }
      this.redraw();
    };
    source.onopen = () => {
      if (source !== this.source || this.active || this.phase !== 'error') return;
      this.ready();
      this.redraw();
    };
  }

  private valid(result: CommentaryResult, generation: number, fresh: boolean, allowAhead = false): boolean {
    const current = this.current();
    return (
      this.enabled &&
      !this.destroyed &&
      generation === this.generation &&
      result.gameId === this.gameId &&
      result.subscriptionId === this.state?.subscriptionId &&
      result.requestId === this.state.requestId &&
      result.gameRevision === this.state.gameRevision &&
      result.expiresAt > this.serverNow() &&
      (!fresh || this.serverNow() - result.acceptedAt <= maxArrivalAge) &&
      current.live &&
      current.status === result.status &&
      ((current.ply === result.ply && current.fen.split(' ')[0] === result.fenAfter.split(' ')[0]) ||
        (allowAhead &&
          result.ply === current.ply + 1 &&
          this.state?.ply === result.ply &&
          this.state.fenAfter === result.fenAfter))
    );
  }

  private receive(result: CommentaryResult, generation: number): void {
    if (!this.valid(result, generation, !this.active, result.type === 'comment')) {
      window.dispatchEvent(
        new window.CustomEvent('blv:drop', {
          detail: {
            requestId: result.requestId,
            reason: 'stale_or_mismatched_state',
            expected: this.state,
            current: this.current(),
            result,
            now: this.deps.now(),
          },
        }),
      );
      return;
    }
    this.clearWorkTimer();
    if (result.type !== 'comment') {
      if (!this.active) {
        if (result.type === 'silent' || this.state?.idle) this.ready();
        else this.setStatus('Dịch vụ bình luận chưa sẵn sàng', 'error');
      }
      this.redraw();
      return;
    }
    if (
      !result.commentId ||
      !result.text ||
      this.seen.has(result.commentId) ||
      result.commentId === this.active?.commentId ||
      (result.kind === 'intro' && this.introPlayed)
    )
      return;
    if (!result.audio) {
      // Never interrupt a sentence being read; otherwise show the text alone.
      if (!this.active) {
        this.showSubtitle(result);
        this.markSeen(result);
        this.setStatus('Giọng đọc chưa sẵn sàng; chỉ có phụ đề', 'idle');
      }
      this.redraw();
      return;
    }
    this.pending = result;
    this.drain();
  }

  private drain(): void {
    if (this.active || !this.pending) return;
    const result = this.pending;
    // Queue age is deliberate waiting for a sentence, not provider latency.
    // Revalidate the actual latest position at the playback boundary.
    if (!this.valid(result, this.generation, false)) {
      // SSE may lead the WebSocket by one move. Keep only that authenticated,
      // unexpired latest response until the matching board is rendered.
      if (!this.valid(result, this.generation, false, true)) this.pending = undefined;
      return;
    }
    if (
      result.kind !== 'finish' &&
      result.kind !== 'idle' &&
      result.transition &&
      (result.priority ?? 0) < bridgeYieldsTo &&
      !this.bridgeDone &&
      this.lastSpokenPly !== undefined &&
      result.ply - this.lastSpokenPly >= 2 &&
      this.deps.now() - this.lastBridgeAt >= bridgeEvery
    ) {
      this.bridgeDone = true;
      this.lastBridgeAt = this.deps.now();
      void this.play({
        ...result,
        ...result.transition,
        kind: 'transition',
        commentId: `transition:${result.requestId}`,
      });
      return;
    }
    this.pending = undefined;
    this.bridgeDone = false;
    void this.play(result);
  }

  private async play(result: CommentaryResult): Promise<void> {
    if (!this.media || !result.audio) return;
    this.stopSample();
    this.stopAudio();
    const playback = this.playback;
    const generation = this.generation;
    const media = this.media;
    this.active = result;
    const previous = {
      subtitle: this.subtitle,
      old: this.subtitleOld,
      ply: this.subtitlePly,
      kind: this.subtitleKind,
    };
    this.showSubtitle(result);
    try {
      const bytes = Uint8Array.from(atob(result.audio.base64), char => char.charCodeAt(0));
      if (bytes.length > 1_500_000 || !['audio/mpeg', 'audio/wav'].includes(result.audio.mime))
        throw new Error('invalid audio');
      this.url = this.deps.createUrl(new Blob([bytes], { type: result.audio.mime }));
      media.src = this.url;
      media.volume = this.volume();
      let started = false;
      const finished = (natural: boolean) => {
        if (playback !== this.playback) return;
        if (natural)
          window.dispatchEvent(
            new window.CustomEvent('blv:ended', {
              detail: { ply: result.ply, kind: result.kind || 'move', requestId: result.requestId },
            }),
          );
        this.stopAudio();
        // A spoken sentence stays readable (dimmed); one that never started is not shown.
        if (started) this.subtitleOld = true;
        else
          [this.subtitle, this.subtitleOld, this.subtitlePly, this.subtitleKind] = [
            previous.subtitle,
            previous.old,
            previous.ply,
            previous.kind,
          ];
        if (this.enabled) this.ready();
        this.redraw();
        this.drain();
      };
      media.onended = () => finished(true);
      media.onerror = () => finished(false);
      // A failure to START has a watchdog. Playing sentences finish via onended,
      // even if their originating position changes several times.
      this.timer = setTimeout(() => finished(false), 5000);
      await media.play();
      if (playback !== this.playback || !this.enabled || generation !== this.generation) return;
      // Native playback can wait for decoding while the board moves ahead.
      // Validate again when playback actually starts; already-started speech
      // still finishes normally through subsequent moves.
      if (result.kind !== 'intro' && !this.valid(result, generation, false)) {
        finished(false);
        return;
      }
      clearTimeout(this.timer);
      // A stalled decoder may never report the end; that must not silence the rest of the game.
      const seconds = Number.isFinite(media.duration) && media.duration > 0 ? media.duration : 15;
      this.timer = setTimeout(() => finished(false), seconds * 1000 + 2000);
      started = true;
      if (result.kind !== 'transition') this.lastSpokenPly = result.ply;
      this.markSeen(result);
      this.setStatus('Đang đọc bình luận', 'speaking');
      window.dispatchEvent(
        new window.CustomEvent('blv:play', {
          detail: {
            requestId: result.requestId,
            ply: result.ply,
            kind: result.kind || 'move',
            acceptedAt: result.acceptedAt,
            startedAt: this.deps.now(),
            text: result.text,
            voice: result.audio.voice,
          },
        }),
      );
    } catch {
      if (playback !== this.playback) return;
      this.stopAudio();
      // The text is still verified; keep it on screen when the voice fails.
      this.subtitleOld = true;
      this.setStatus('Không phát được âm thanh. Tắt rồi bật lại để thử.', 'error');
      this.drain();
    }
    this.redraw();
  }

  private markSeen(result: CommentaryResult): void {
    if (result.kind === 'transition') return;
    this.seen.add(result.commentId!);
    if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value!);
    if (result.kind === 'intro') this.introPlayed = true;
  }

  positionChanged = (newPly?: number): void => {
    if (!this.enabled) return;
    if (newPly === undefined || this.state?.ply !== newPly) {
      this.state = undefined;
      this.pending = undefined;
    }
    this.clearWorkTimer();
    if (!this.active) {
      this.stopAudio();
      this.setStatus('Đang chờ bình luận mới…', 'working');
    }
    // Round invokes this before updating its ply/FEN synchronously.
    queueMicrotask(() => this.drain());
  };

  seek = (): void => this.reset();

  reset = (preserveSpeech = false): void => {
    if (!this.enabled) return;
    if (!preserveSpeech) ++this.generation;
    this.source?.close();
    this.source = undefined;
    this.state = undefined;
    this.pending = undefined;
    this.clearWorkTimer();
    if (!preserveSpeech) {
      this.lastSpokenPly = undefined;
      this.bridgeDone = false;
      this.stopAudio();
      this.clearSubtitle();
    }
    if (!this.active) this.ready();
    this.connect();
  };

  private clearWorkTimer(): void {
    clearTimeout(this.workTimer);
    this.workTimer = undefined;
  }

  private stopAudio(): void {
    ++this.playback;
    clearTimeout(this.timer);
    this.timer = undefined;
    this.active = undefined;
    if (this.media) {
      this.media.onended = null;
      this.media.onerror = null;
      this.media.pause();
      this.media.removeAttribute('src');
      this.media.load();
    }
    if (this.url) this.deps.revokeUrl(this.url);
    this.url = undefined;
  }

  destroy = (): void => {
    this.disable();
    this.stopSample();
    this.destroyed = true;
  };
}
