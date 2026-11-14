import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setImmediate } from 'node:timers/promises';

import type { RoundData } from '../src/interfaces';
import WinProbability, { validWdl, wdlPercentages, wdlRevision } from '../src/winProbability';

const round = (moves: string[] = []): RoundData =>
  ({
    game: { id: 'test1234', status: { id: 20, name: 'started' } },
    steps: [{ san: '' }, ...moves.map(san => ({ san }))],
  }) as unknown as RoundData;

const ready = (d: RoundData, white = 0.7): Response =>
  new Response(
    JSON.stringify({
      game_id: d.game.id,
      revision: wdlRevision(d),
      status: 'ready',
      source: 'engine',
      probabilities: { white, draw: 0.2, black: 0.8 - white },
    }),
  );

test('all rounded WDL values sum to exactly 100.0%', () => {
  for (let white = 0; white <= 1; white += 0.017) {
    for (let draw = 0; draw <= 1 - white; draw += 0.019) {
      const values = wdlPercentages({ white, draw, black: 1 - white - draw });
      assert.equal(Math.round(values.reduce((a, b) => a + b, 0) * 10), 1000);
      assert.ok(values.every(v => v >= 0 && v <= 100));
    }
  }
  assert.deepEqual(wdlPercentages({ white: 1, draw: 0, black: 0 }), [100, 0, 0]);
});

test('reject invalid and nonfinite probabilities', () => {
  assert.equal(validWdl({ white: 0.3, draw: 0.3, black: 0.3 }), false);
  assert.equal(validWdl({ white: NaN, draw: 0.5, black: 0.5 }), false);
  assert.equal(validWdl({ white: 1.1, draw: 0, black: -0.1 }), false);
  assert.equal(validWdl({ white: 0.6, draw: 0.3, black: 0.1 }), true);
});

test('invalid human probabilities are hidden while the valid engine result remains usable', async t => {
  const d = round(['e4']);
  const payload = await ready(d).json();
  payload.human = { status: 'ready', model_id: 'pilot-v1', probabilities: { white: 2, draw: 0, black: -1 } };
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(payload)));
  const ctrl = new WinProbability(
    () => d,
    () => {},
  );
  t.after(ctrl.destroy);
  await setImmediate();
  assert.equal(ctrl.data?.status, 'ready');
  assert.equal(ctrl.data?.probabilities?.white, 0.7);
  assert.notEqual(ctrl.data?.human?.status, 'ready');
  assert.equal(ctrl.data?.human?.probabilities, undefined);
});

test('late response cannot replace a different move at the same ply', async t => {
  const responses: ((r: Response) => void)[] = [];
  t.mock.method(globalThis, 'fetch', () => new Promise<Response>(resolve => responses.push(resolve)));
  let d = round(['e4', 'e5']);
  const previous = d;
  const ctrl = new WinProbability(
    () => d,
    () => {},
  );
  t.after(ctrl.destroy);
  d = round(['e4', 'c5']);
  ctrl.sync();
  responses[0](ready(previous));
  await setImmediate();
  assert.equal(ctrl.data, undefined);
  responses[1](ready(d, 0.6));
  await setImmediate();
  assert.equal(ctrl.data?.probabilities?.white, 0.6);
});

test('a new move immediately clears the previous estimate', async t => {
  const responses: ((r: Response) => void)[] = [];
  t.mock.method(globalThis, 'fetch', () => new Promise<Response>(resolve => responses.push(resolve)));
  let d = round(['e4']);
  const ctrl = new WinProbability(
    () => d,
    () => {},
  );
  t.after(ctrl.destroy);
  responses[0](ready(d));
  await setImmediate();
  assert.equal(ctrl.data?.status, 'ready');
  d = round(['e4', 'e5']);
  ctrl.sync();
  assert.equal(ctrl.data, undefined);
});

test('official result takes precedence even when engine reply arrives later', async t => {
  let respond!: (r: Response) => void;
  t.mock.method(
    globalThis,
    'fetch',
    () =>
      new Promise<Response>(resolve => {
        respond = resolve;
      }),
  );
  const d = round(['e4']);
  const oldReply = ready(d);
  const ctrl = new WinProbability(
    () => d,
    () => {},
  );
  t.after(ctrl.destroy);
  d.game.status = { id: 31, name: 'resign' };
  d.game.winner = 'black';
  ctrl.sync();
  respond(oldReply);
  await setImmediate();
  assert.equal(ctrl.data?.source, 'official');
  assert.deepEqual(ctrl.data?.probabilities, { white: 0, draw: 0, black: 1 });
});

test('aborted games have no invented result or engine request', t => {
  const fetch = t.mock.method(globalThis, 'fetch');
  const d = round();
  d.game.status = { id: 25, name: 'aborted' };
  const ctrl = new WinProbability(
    () => d,
    () => {},
  );
  t.after(ctrl.destroy);
  assert.equal(ctrl.data?.status, 'aborted');
  assert.equal(ctrl.data?.probabilities, undefined);
  assert.equal(fetch.mock.callCount(), 0);
});

test('service failure shows unavailable without retaining numbers', async t => {
  t.mock.method(globalThis, 'fetch', async () => {
    throw new Error('offline');
  });
  const ctrl = new WinProbability(
    () => round(),
    () => {},
  );
  t.after(ctrl.destroy);
  await setImmediate();
  assert.equal(ctrl.data?.status, 'unavailable');
  assert.equal(ctrl.data?.probabilities, undefined);
});

test('disconnect clears estimates and a reload resumes the same position', async t => {
  const responses: ((r: Response) => void)[] = [];
  t.mock.method(globalThis, 'fetch', () => new Promise<Response>(resolve => responses.push(resolve)));
  const d = round(['e4']);
  const ctrl = new WinProbability(
    () => d,
    () => {},
  );
  t.after(ctrl.destroy);
  responses[0](ready(d));
  await setImmediate();
  ctrl.disconnected();
  assert.equal(ctrl.data?.status, 'stale');
  assert.equal(ctrl.data?.probabilities, undefined);
  ctrl.sync();
  responses[1](ready(d));
  await setImmediate();
  assert.equal(ctrl.data?.status, 'ready');
});
