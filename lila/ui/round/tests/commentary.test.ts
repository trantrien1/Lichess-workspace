import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { init, attributesModule, type VNode } from 'snabbdom';

import { CommentaryCtrl, type CommentaryDeps } from '../src/commentary';
import { commentaryView } from '../src/view/commentary';

const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const NF3 = 'rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1';
const NF6 = 'rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2';

interface StateFlags {
  snapshot?: boolean;
  reset?: boolean;
  idle?: boolean;
}
interface StateMessage extends StateFlags {
  type: string;
  gameId: string;
  subscriptionId: string;
  requestId: string;
  gameRevision: number;
  ply: number;
  fenAfter: string;
  status: string;
  acceptedAt: number;
  expiresAt: number;
}
interface CommentMessage extends StateMessage {
  kind: string;
  commentId: string;
  text: string;
  audio?: { mime: string; base64: string; voice: string; provider: string };
}

class FakeSource {
  onmessage?: (event: { data: string }) => void;
  onerror?: () => void;
  onopen?: () => void;
  readyState = 1;
  closed = false;
  url = '';
  close() {
    this.closed = true;
  }
  send(data: StateMessage | CommentMessage) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

class FakeAudio {
  src = '';
  volume = 1;
  duration = 2.5;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = false;
  play = () => {
    this.paused = false;
    return Promise.resolve();
  };
  pause() {
    this.paused = true;
  }
  removeAttribute() {}
  load() {}
}

function setup(options: { skew?: number; volume?: number; voice?: string } = {}) {
  let clock = 1_000_000;
  const serverClock = () => clock + (options.skew ?? 0);
  const sources: FakeSource[] = [];
  const media: FakeAudio[] = [];
  const stored: string[] = [];
  let voice = options.voice ?? 'piper-vais1000';
  const board = { ply: 0, fen: START, status: 'started', live: true };
  const deps: CommentaryDeps = {
    stream: url => {
      const source = new FakeSource();
      source.url = url;
      sources.push(source);
      return source as unknown as EventSource;
    },
    voice: replacement => {
      if (replacement !== undefined) stored.push((voice = replacement));
      return voice;
    },
    audio: () => {
      const audio = new FakeAudio();
      media.push(audio);
      return audio as unknown as HTMLAudioElement;
    },
    now: () => clock,
    createUrl: () => 'blob:test',
    revokeUrl: () => {},
    volume: () => options.volume ?? 0.5,
  };
  const ctrl = new CommentaryCtrl(
    'test1234',
    () => board,
    () => {},
    deps,
  );
  // Lila's clock: the state and its result carry server timestamps.
  const state = (requestId: string, ply: number, fenAfter: string, extra: StateFlags = {}): StateMessage => {
    const acceptedAt = serverClock();
    return {
      type: 'state',
      gameId: 'test1234',
      subscriptionId: 's',
      requestId,
      gameRevision: Number(requestId.split(':')[1]),
      ply,
      fenAfter,
      status: 'started',
      acceptedAt,
      expiresAt: acceptedAt + 12000,
      ...extra,
    };
  };
  const comment = (s: StateMessage, text: string, audio = true): CommentMessage => ({
    ...s,
    type: 'comment',
    kind: 'move',
    commentId: `c-${s.requestId}`,
    text,
    ...(audio ? { audio: { mime: 'audio/wav', base64: btoa('RIFF'), voice: 'v', provider: 'p' } } : {}),
  });
  return {
    ctrl,
    board,
    sources,
    media,
    stored,
    state,
    comment,
    tick: (ms: number) => (clock += ms),
  };
}

async function moveAndComment(t: ReturnType<typeof setup>, text: string, audio = true) {
  t.ctrl.toggle();
  const source = t.sources.at(-1)!;
  source.send(t.state('s:1', 0, START, { snapshot: true }));
  assert.notEqual(t.ctrl.phase, 'working'); // idle, or speaking while a sample plays
  const move = t.state('s:2', 1, NF3);
  source.send(move);
  assert.equal(t.ctrl.phase, 'working');
  t.board.ply = 1;
  t.board.fen = NF3;
  t.tick(900);
  source.send(t.comment(move, text, audio));
  await setImmediate();
  return source;
}

test('the sentence being read is shown, then stays dimmed after it ends', async () => {
  const t = setup();
  await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.');
  assert.equal(t.ctrl.phase, 'speaking');
  assert.equal(t.ctrl.subtitle, 'Trắng đưa mã từ g1 đến f3.');
  assert.equal(t.ctrl.subtitleOld, false);
  t.media.at(-1)!.onended!();
  assert.equal(t.ctrl.phase, 'idle');
  assert.equal(t.ctrl.subtitle, 'Trắng đưa mã từ g1 đến f3.');
  assert.equal(t.ctrl.subtitleOld, true);
  t.ctrl.destroy();
  assert.equal(t.ctrl.subtitle, '');
  assert.equal(t.ctrl.phase, 'off');
});

test('a sentence whose end is never reported does not silence the next ones', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const t = setup();
  const source = await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.');
  assert.equal(t.ctrl.phase, 'speaking');
  // The 2.5 s sentence gets 2 s more for its `ended` event, which never comes.
  context.mock.timers.tick(4400);
  assert.equal(t.ctrl.phase, 'speaking');
  context.mock.timers.tick(200);
  assert.equal(t.ctrl.phase, 'idle');
  assert.equal(t.ctrl.subtitleOld, true);
  const next = t.state('s:3', 2, NF6);
  source.send(next);
  t.board.ply = 2;
  t.board.fen = NF6;
  source.send(t.comment(next, 'Đen đưa mã từ g8 đến f6.'));
  await setImmediate();
  assert.equal(t.ctrl.phase, 'speaking');
  assert.equal(t.ctrl.subtitle, 'Đen đưa mã từ g8 đến f6.');
  t.ctrl.destroy();
});

const NC3 = 'rnbqkb1r/pppppppp/5n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R b KQkq - 3 2';
const BRIDGE = 'Ván cờ trôi đi rất nhanh.';

for (const [priority, first] of [
  [30, BRIDGE],
  [80, 'Trắng chiếu vua!'],
] as const)
  test(`after skipped moves the bridge goes first only before a minor line (priority ${priority})`, async () => {
    const t = setup();
    const source = await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.');
    t.media.at(-1)!.onended!();
    const next = t.state('s:4', 3, NC3);
    source.send(next);
    t.board.ply = 3;
    t.board.fen = NC3;
    source.send({
      ...t.comment(next, 'Trắng chiếu vua!'),
      priority,
      transition: {
        text: BRIDGE,
        audio: { mime: 'audio/wav', base64: btoa('RIFF'), voice: 'v', provider: 'p' },
      },
    });
    await setImmediate();
    assert.equal(t.ctrl.subtitle, first);
    t.ctrl.destroy();
  });

test('without a voice the verified text is still shown', async () => {
  const t = setup();
  await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.', false);
  assert.equal(t.ctrl.subtitle, 'Trắng đưa mã từ g1 đến f3.');
  assert.match(t.ctrl.status, /chỉ có phụ đề/);
  t.ctrl.destroy();
});

test('a wrong player clock neither drops nor accepts late comments', async () => {
  for (const skew of [-3_600_000, 3_600_000]) {
    const t = setup({ skew });
    await moveAndComment(t, 'Kịp lúc.');
    assert.equal(t.ctrl.phase, 'speaking', `skew ${skew}`);
    t.ctrl.destroy();
  }
  const late = setup({ skew: 3_600_000 });
  const drops: unknown[] = [];
  const onDrop = (event: Event) => drops.push(event);
  window.addEventListener('blv:drop', onDrop);
  late.ctrl.toggle();
  const source = late.sources[0];
  const move = late.state('s:2', 1, NF3);
  source.send(move);
  late.board.ply = 1;
  late.board.fen = NF3;
  late.tick(5000);
  source.send(late.comment(move, 'Quá muộn.'));
  await setImmediate();
  window.removeEventListener('blv:drop', onDrop);
  assert.equal(drops.length, 1);
  assert.notEqual(late.ctrl.phase, 'speaking');
  late.ctrl.destroy();
});

test('playback follows the site volume', async () => {
  const t = setup({ volume: 0.3 });
  await moveAndComment(t, 'Âm lượng.');
  assert.equal(t.media.at(-1)!.volume, 0.3);
  t.ctrl.destroy();
});

test('a stream the browser gave up on is reported as an error', () => {
  const t = setup();
  t.ctrl.toggle();
  const source = t.sources[0];
  source.readyState = 2;
  source.onerror!();
  assert.equal(t.ctrl.phase, 'error');
  assert.match(t.ctrl.status, /Tắt rồi bật lại/);
  t.ctrl.destroy();
});

const render = (ctrl: CommentaryCtrl): HTMLElement =>
  init([attributesModule])(document.createElement('div'), commentaryView(ctrl) as VNode).elm as HTMLElement;

test('the panel shows the status and subtitle only while enabled', async () => {
  const t = setup();
  let view = render(t.ctrl);
  assert.equal(view.dataset.state, 'off');
  assert.equal(view.querySelector('.commentary__status'), null);
  await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.');
  view = render(t.ctrl);
  assert.equal(view.dataset.state, 'speaking');
  assert.equal(view.querySelector('.commentary__status')!.getAttribute('role'), 'status');
  const subtitle = view.querySelector('.commentary__subtitle')!;
  // The move it is about, then the sentence.
  assert.equal(subtitle.querySelector('.commentary__ply')!.textContent, 'Nước 1 · Trắng');
  assert.equal(subtitle.textContent, 'Nước 1 · TrắngTrắng đưa mã từ g1 đến f3.');
  assert.equal(subtitle.getAttribute('lang'), 'vi');
  assert.equal(subtitle.getAttribute('data-old'), 'false');
  assert.equal(view.querySelector('.commentary__toggle')!.getAttribute('aria-pressed'), 'true');
  t.ctrl.destroy();
});

test('the stream asks for the chosen voice; a new choice reconnects without cutting the sentence', async () => {
  const t = setup({ voice: 'piper-vais1000-low' });
  const first = await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.');
  assert.match(first.url, /^\/commentary\/test1234\?voice=piper-vais1000-low$/);
  assert.equal(t.ctrl.phase, 'speaking');
  const players = t.media.length;
  t.ctrl.setVoice('piper-vivos-28');
  assert.deepEqual(t.stored, ['piper-vivos-28']);
  assert.ok(first.closed);
  assert.match(t.sources.at(-1)!.url, /\?voice=piper-vivos-28$/);
  // No sample over the sentence being read, which goes on.
  assert.equal(t.media.length, players);
  assert.equal(t.ctrl.phase, 'speaking');
  assert.equal(t.ctrl.subtitle, 'Trắng đưa mã từ g1 đến f3.');
  t.ctrl.destroy();
});

test('choosing a voice plays its sample, which a comment cuts short', async () => {
  const t = setup();
  t.ctrl.setVoice('piper-vais1000-high');
  const sample = t.media.at(-1)!;
  assert.equal(sample.src, '/commentary/voice/piper-vais1000-high');
  assert.equal(sample.volume, 0.5);
  assert.equal(sample.paused, false);
  await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.');
  assert.equal(sample.paused, true);
  assert.equal(t.ctrl.phase, 'speaking');
  assert.equal(t.media.at(-1)!.paused, false);
  t.ctrl.destroy();
});

test('the status says while a sample plays, and once the game is over', () => {
  const t = setup();
  t.ctrl.toggle();
  t.ctrl.setVoice('piper-vais1000-high');
  assert.equal(t.ctrl.status, 'Đang phát giọng mẫu…');
  // The new stream's snapshot arrives while the sample plays.
  t.sources.at(-1)!.send(t.state('s:1', 0, START, { snapshot: true }));
  assert.equal(t.ctrl.status, 'Đang phát giọng mẫu…');
  t.media.at(-1)!.onended!();
  assert.equal(t.ctrl.status, 'Sẵn sàng, chờ nước mới');
  t.board.live = false;
  t.ctrl.setVoice('piper-vais1000');
  t.media.at(-1)!.onended!();
  assert.equal(t.ctrl.status, 'Đang xem lại nước cũ');
  // The game ended on the board the player is looking at: no "waiting for a move".
  t.board.live = true;
  t.board.status = 'draw';
  t.ctrl.setVoice('piper-vais1000-low');
  t.media.at(-1)!.onended!();
  assert.equal(t.ctrl.status, 'Ván cờ đã kết thúc');
  t.ctrl.destroy();
});

test('an unknown voice is neither used nor remembered', () => {
  const t = setup({ voice: 'piper-gone' });
  assert.equal(t.ctrl.voice, 'piper-vais1000');
  t.ctrl.setVoice('piper-nope');
  assert.equal(t.ctrl.voice, 'piper-vais1000');
  assert.deepEqual(t.stored, []);
  t.ctrl.toggle();
  assert.match(t.sources[0].url, /\?voice=piper-vais1000$/);
  t.ctrl.destroy();
});

test('the voice picker shows while enabled, with the chosen voice selected', () => {
  const t = setup({ voice: 'piper-vivos-45' });
  assert.equal(render(t.ctrl).querySelector('select'), null);
  t.ctrl.toggle();
  const options = [...render(t.ctrl).querySelectorAll('.commentary__voice option')] as HTMLOptionElement[];
  assert.equal(options.length, 6);
  assert.deepEqual(
    options.filter(option => option.selected).map(option => option.value),
    ['piper-vivos-45'],
  );
  t.ctrl.destroy();
});

test('a remark while nobody moves is spoken without a working status', async () => {
  const t = setup();
  const source = await moveAndComment(t, 'Trắng đưa mã từ g1 đến f3.');
  t.media.at(-1)!.onended!();
  t.tick(10_000);
  const idle = t.state('s:3', 1, NF3, { idle: true });
  source.send(idle);
  assert.equal(t.ctrl.phase, 'idle');
  source.send({ ...idle, type: 'unavailable' } as unknown as StateMessage);
  assert.equal(t.ctrl.phase, 'idle', 'an optional remark failing is not an error');
  t.tick(1500);
  source.send({ ...t.comment(idle, 'Đen đang suy nghĩ khá lâu ở nước này.'), kind: 'idle' });
  await setImmediate();
  assert.equal(t.ctrl.phase, 'speaking');
  assert.equal(t.ctrl.subtitle, 'Đen đang suy nghĩ khá lâu ở nước này.');
  t.ctrl.destroy();
});
