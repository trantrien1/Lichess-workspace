import assert from 'node:assert/strict';
import { test } from 'node:test';
import { init, attributesModule, type VNode } from 'snabbdom';

import type RoundController from '../src/ctrl';
import render from '../src/view/winProbability';

const prediction = {
  status: 'ready',
  source: 'engine',
  engine: 'Stockfish 15.1',
  depth: 17,
  probabilities: { white: 0.1, draw: 0.8, black: 0.1 },
  human: { status: 'ready', model_id: 'pilot-v1', probabilities: { white: 0.6, draw: 0.1, black: 0.3 } },
};

function panel(data: unknown = prediction, replay = false): HTMLElement {
  const ctrl = { winProbability: { data }, replaying: () => replay } as unknown as RoundController;
  return init([attributesModule])(document.createElement('div'), render(ctrl) as VNode).elm as HTMLElement;
}

test('one bar prefers the human estimate without engine diagnostics or duplicate percentages', () => {
  const view = panel();
  assert.equal(view.querySelectorAll('.win-probability__bar').length, 1);
  assert.equal(view.querySelectorAll('.win-probability__values').length, 1);
  assert.equal(view.textContent.match(/\d+\.\d%/g)?.length, 3);
  assert.equal(view.dataset.source, 'human');
  assert.match(view.textContent, /Người chơi · thử nghiệm/);
  assert.match(view.querySelector('.win-probability__values')!.textContent, /Trắng 60.0%/);
  assert.equal(view.querySelector('.win-probability__white')!.getAttribute('style'), 'width:60%');
  assert.doesNotMatch(view.textContent, /Theo engine|Stockfish|d17/);
});

test('missing or unsupported human context falls back to one clearly identified engine estimate', () => {
  for (const reason of ['missing_context', 'outside_scope', 'model_unavailable']) {
    const view = panel({ ...prediction, human: { status: 'unavailable', reason } });
    assert.equal(view.dataset.source, 'engine');
    assert.match(view.textContent, /Ước tính từ thế cờ/);
    assert.match(view.querySelector('.win-probability__values')!.textContent, /Trắng 10.0%/);
    assert.match(view.querySelector('.win-probability__source')!.getAttribute('title')!, /engine/);
    assert.equal(view.textContent.match(/\d+\.\d%/g)?.length, 3);
  }
});

test('official result overrides the preferred human estimate', () => {
  const view = panel({
    ...prediction,
    status: 'finished',
    source: 'official',
    probabilities: { white: 0, draw: 1, black: 0 },
  });
  assert.equal(view.dataset.source, 'official');
  assert.match(view.textContent, /Kết quả chính thức/);
  assert.match(view.querySelector('.win-probability__values')!.textContent, /Hòa 100.0%/);
  assert.doesNotMatch(view.textContent, /thử nghiệm/);
});

test('replay and unavailable positions never retain either source of probabilities', () => {
  const views = [panel(prediction, true)];
  for (const status of ['pending', 'busy', 'stale', 'unavailable', 'aborted'])
    views.push(panel({ ...prediction, status }));
  for (const view of views) {
    assert.equal(view.querySelector('.win-probability__bar'), null);
    assert.equal(view.querySelector('.win-probability__values'), null);
    assert.equal(view.querySelector('.win-probability__source'), null);
    assert.equal(view.dataset.source, undefined);
  }
});
