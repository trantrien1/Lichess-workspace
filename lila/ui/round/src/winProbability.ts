import type { RoundData } from './interfaces';

export interface Wdl {
  white: number;
  draw: number;
  black: number;
}

export interface Prediction {
  game_id: string;
  revision: string;
  status: 'pending' | 'ready' | 'finished' | 'aborted' | 'unavailable' | 'busy' | 'stale';
  probabilities?: Wdl;
  source: 'engine' | 'official';
  engine?: string;
  depth?: number;
  human?: {
    status: 'ready' | 'unavailable';
    probabilities?: Wdl;
    model_id?: string;
    reason?:
      | 'missing_context'
      | 'outside_scope'
      | 'model_unavailable'
      | 'score_unavailable'
      | 'invalid_response';
  };
}

// History + result, not ply alone: a takeback followed by a different move can reuse a ply.
export const wdlRevision = (d: RoundData): string =>
  `${d.game.id}|${d.steps
    .map(s => s.san)
    .filter(Boolean)
    .join(' ')}|${d.game.status.id}|${d.game.winner ?? ''}`;

export function validWdl(p?: Wdl): p is Wdl {
  if (!p) return false;
  const values = [p.white, p.draw, p.black];
  return (
    values.every(x => Number.isFinite(x) && x >= 0 && x <= 1) &&
    Math.abs(values.reduce((a, b) => a + b, 0) - 1) < 0.00001
  );
}

// Largest-remainder rounding keeps the three displayed percentages at exactly 100.0%.
export function wdlPercentages(p: Wdl): number[] {
  const raw = [p.white, p.draw, p.black].map(x => x * 1000);
  const rounded = raw.map(Math.floor);
  const order = [0, 1, 2].sort((a, b) => raw[b] - rounded[b] - (raw[a] - rounded[a]));
  const missing = 1000 - rounded.reduce((a, b) => a + b, 0);
  for (let i = 0; i < missing; i++) rounded[order[i]]++;
  return rounded.map(x => x / 10);
}

export default class WinProbability {
  data?: Prediction;
  private revision = '';
  private timer?: ReturnType<typeof setTimeout>;
  private request?: AbortController;

  constructor(
    private readonly round: () => RoundData,
    private readonly redraw: () => void,
  ) {
    document.addEventListener('visibilitychange', this.visibility);
    this.sync();
  }

  sync = (): void => {
    const d = this.round();
    const revision = wdlRevision(d);
    if (revision === this.revision) return;
    this.cancel();
    this.revision = revision;
    this.data = undefined;
    if (d.game.status.id >= 25) {
      this.data = {
        game_id: d.game.id,
        revision,
        status: d.game.status.id === 25 ? 'aborted' : 'finished',
        source: 'official',
        probabilities:
          d.game.status.id === 25
            ? undefined
            : {
                white: Number(d.game.winner === 'white'),
                draw: Number(!d.game.winner),
                black: Number(d.game.winner === 'black'),
              },
      };
    } else if (!document.hidden) void this.load();
  };

  destroy = (): void => {
    this.cancel();
    document.removeEventListener('visibilitychange', this.visibility);
  };

  disconnected = (): void => {
    if (this.data?.source === 'official') return;
    this.cancel();
    this.data = { game_id: this.round().game.id, revision: this.revision, status: 'stale', source: 'engine' };
    this.revision = '';
    this.redraw();
  };

  private cancel(): void {
    clearTimeout(this.timer);
    const request = this.request;
    this.request = undefined;
    request?.abort();
  }

  private readonly visibility = (): void => {
    if (document.hidden) this.cancel();
    else if (!this.data || ['pending', 'busy', 'unavailable'].includes(this.data.status)) void this.load();
  };

  private async load(): Promise<void> {
    const revision = this.revision;
    const gameId = this.round().game.id;
    const request = (this.request = new AbortController());
    const timeout = setTimeout(() => request.abort(), 1500);
    let retry = 2000;
    try {
      const response = await fetch(`/game/${gameId}/win-probability`, {
        signal: request.signal,
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (this.request !== request || this.revision !== revision) return;
      if (response.status === 403 || response.status === 404) {
        this.data = { game_id: gameId, revision, status: 'unavailable', source: 'engine' };
        retry = 0;
      } else {
        if (!response.ok) throw new Error('WDL service unavailable');
        const data: Prediction = await response.json();
        if (this.request !== request || this.revision !== revision) return;
        if (data.game_id !== gameId || data.revision !== revision) {
          retry = 200;
        } else {
          if (['ready', 'finished'].includes(data.status) && !validWdl(data.probabilities))
            throw new Error('Invalid WDL');
          if (!['pending', 'ready', 'finished', 'aborted', 'unavailable', 'busy'].includes(data.status))
            throw new Error('Invalid WDL status');
          if (data.human) {
            if (
              data.human.status === 'ready' &&
              validWdl(data.human.probabilities) &&
              typeof data.human.model_id === 'string' &&
              data.human.model_id.length > 0
            ) {
              // The human estimate is optional; malformed data must not hide valid engine WDL.
            } else if (data.human.status === 'unavailable') {
              data.human = { status: 'unavailable', reason: data.human.reason };
            } else data.human = { status: 'unavailable', reason: 'invalid_response' };
          }
          this.data = data;
          retry = ['ready', 'finished', 'aborted'].includes(data.status)
            ? 0
            : data.status === 'pending'
              ? 200
              : 2000;
        }
      }
    } catch {
      if (this.request !== request || this.revision !== revision) return;
      this.data = { game_id: gameId, revision, status: 'unavailable', source: 'engine' };
    } finally {
      clearTimeout(timeout);
      if (this.request === request && this.revision === revision) {
        this.redraw();
        if (retry && !document.hidden) this.timer = setTimeout(() => void this.load(), retry);
      }
    }
  }
}
