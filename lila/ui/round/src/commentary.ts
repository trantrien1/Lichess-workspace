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
}

type SpeechAudio = { mime: string; base64: string; voice: string; provider: string };
interface CommentaryResult extends CommentaryState {
  type: 'comment' | 'silent' | 'unavailable';
  kind?: 'intro' | 'move' | 'finish' | 'transition';
  commentId?: string;
  text?: string;
  audio?: SpeechAudio;
  transition?: { text: string; audio: SpeechAudio };
  generator?: string;
}

export interface CommentaryDeps {
  suspendSpeech?(): () => void;
  claimAudio?(stop: () => void): () => void;
  stream(url: string): EventSource;
  audio(): HTMLAudioElement;
  now(): number;
  createUrl(data: Blob): string;
  revokeUrl(url: string): void;
}

const defaults: CommentaryDeps = {
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
};

const silence = 'UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQIAAACAgA==';
const maxArrivalAge = 2500;
let activeCommentator: CommentaryCtrl | undefined;

export class CommentaryCtrl {
  enabled = false;
  status = 'Bình luận âm thanh tiếng Việt';
  subtitle = '';
  subtitlePly?: number;
  generator?: string;
  private source?: EventSource;
  private state?: CommentaryState;
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

  constructor(
    readonly gameId: string,
    private readonly current: () => { ply: number; fen: string; status: string; live: boolean },
    private readonly redraw: () => void,
    private readonly deps: CommentaryDeps = defaults,
  ) {}

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
    this.media.volume = 0.8;
    const generation = ++this.generation;
    void this.media.play().catch((error: DOMException) => {
      if (error.name !== 'AbortError' && this.enabled && generation === this.generation) {
        this.status = 'Trình duyệt chặn âm thanh. Tắt rồi bật lại để cho phép.';
        this.redraw();
      }
    });
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
    this.subtitle = '';
    this.status = 'Đã tắt bình luận';
    this.redraw();
  };

  private connect(): void {
    if (!this.current().live) return;
    const generation = this.generation;
    const source = (this.source = this.deps.stream(`/commentary/${this.gameId}`));
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
          this.subtitle = '';
        }
        this.state = data;
        // A move replaces only the waiting result; it never interrupts speech.
        if (!this.active)
          this.status = data.snapshot || data.reset ? 'Sẵn sàng, chờ nước mới' : 'Đang tạo lời bình…';
        if (!data.snapshot && !data.reset)
          this.workTimer = setTimeout(
            () => {
              if (!this.enabled || generation !== this.generation || this.state?.requestId !== data.requestId)
                return;
              this.pending = undefined;
              if (!this.active) this.status = 'Hết thời gian chờ bình luận';
              this.redraw();
            },
            Math.max(0, data.expiresAt - this.deps.now()),
          );
        this.redraw();
      } catch {
        this.status = 'Dữ liệu bình luận không hợp lệ';
        this.redraw();
      }
    };
    source.onerror = () => {
      if (source !== this.source) return;
      this.clearWorkTimer();
      this.state = undefined;
      this.pending = undefined;
      // A temporary stream disconnect must not cut a sentence already playing.
      if (!this.active) this.status = 'Mất kết nối bình luận; đang thử lại…';
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
      result.expiresAt > this.deps.now() &&
      (!fresh || this.deps.now() - result.acceptedAt <= maxArrivalAge) &&
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
      if (!this.active)
        this.status = result.type === 'silent' ? 'Sẵn sàng, chờ nước mới' : 'Dịch vụ bình luận chưa sẵn sàng';
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
      if (!this.active) this.status = 'Giọng đọc chưa sẵn sàng; hiện chỉ có phụ đề';
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
      result.transition &&
      !this.bridgeDone &&
      this.lastSpokenPly !== undefined &&
      result.ply - this.lastSpokenPly >= 2 &&
      this.deps.now() - this.lastBridgeAt >= 15000
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
    this.stopAudio();
    const playback = this.playback;
    const generation = this.generation;
    const media = this.media;
    this.active = result;
    this.subtitle = result.text!;
    this.subtitlePly = result.ply;
    this.generator = result.generator;
    try {
      const bytes = Uint8Array.from(atob(result.audio.base64), char => char.charCodeAt(0));
      if (bytes.length > 1_500_000 || !['audio/mpeg', 'audio/wav'].includes(result.audio.mime))
        throw new Error('invalid audio');
      this.url = this.deps.createUrl(new Blob([bytes], { type: result.audio.mime }));
      media.src = this.url;
      media.volume = 0.8;
      const finished = (natural: boolean) => {
        if (playback !== this.playback) return;
        if (natural)
          window.dispatchEvent(
            new window.CustomEvent('blv:ended', {
              detail: { ply: result.ply, kind: result.kind || 'move', requestId: result.requestId },
            }),
          );
        this.stopAudio();
        this.subtitle = '';
        this.status = 'Sẵn sàng, chờ nước mới';
        this.redraw();
        this.drain();
      };
      media.onended = () => finished(true);
      media.onerror = () => finished(false);
      // Only a failure to START has a watchdog. Playing sentences finish via
      // onended, even if their originating position changes several times.
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
      this.timer = undefined;
      if (result.kind !== 'transition') this.lastSpokenPly = result.ply;
      if (result.kind !== 'transition') {
        this.seen.add(result.commentId!);
        if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value!);
      }
      if (result.kind === 'intro') this.introPlayed = true;
      this.status = 'Đang đọc bình luận';
      window.dispatchEvent(
        new window.CustomEvent('blv:play', {
          detail: {
            requestId: result.requestId,
            ply: result.ply,
            kind: result.kind || 'move',
            acceptedAt: result.acceptedAt,
            startedAt: this.deps.now(),
            generator: result.generator,
            text: result.text,
            voice: result.audio.voice,
          },
        }),
      );
    } catch {
      if (playback !== this.playback) return;
      this.stopAudio();
      this.status = 'Không phát được âm thanh. Tắt rồi bật lại để thử.';
      this.drain();
    }
    this.redraw();
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
      this.status = 'Đang chờ bình luận mới…';
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
      this.subtitle = '';
    }
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
    this.destroyed = true;
  };
}
