import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

// Side-effect import: sets window.__ltw and (if present) registers WebMCP tools.
import '../src/webmcp';

type Ltw = {
  searchGames: (input: Record<string, unknown>) => Promise<Record<string, any>>;
  analyseGame: (input: Record<string, unknown>) => Promise<Record<string, any>>;
  tools: { name: string }[];
  debug: () => Record<string, string>;
  attempts: { host: string; api: string; ok: boolean; error: string | null }[];
  register: () => { host: string; api: string; ok: boolean; error: string | null }[];
  browserTools: () => Promise<Record<string, any> | null>;
};

const ltw = (window as any).__ltw as Ltw;

const pgnWithEvals = `[Event "Test"]
[Site "https://lichess.org/abcd1234"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. e4 { [%eval 0.20] [%clk 0:05:00] } e5 { [%eval 0.10] } (1... c5 { [%eval 0.05] } 2. Nf3 { [%eval 0.30] }) 2. Qh5 { [%eval 2.00] } Nc6 { [%eval #3] } 1-0`;

const pgnWithoutEvals = `[Event "Test"]
[White "Alice"]
[Black "Bob"]

1. e4 e5 2. Nf3 Nc6 1/2-1/2`;

const stubFetch = (pgn: string): (() => void) => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(pgn, { status: 200 });
  return () => {
    globalThis.fetch = original;
  };
};

const searchFormHtml = `
<form class="search__form" action="/games/search#results" method="GET">
  <input name="players.a" type="text" />
  <input name="players.b" type="text" />
  <select name="players.white"><option value=""></option></select>
  <select name="players.black"><option value=""></option></select>
  <select name="players.winner"><option value=""></option></select>
  <select name="players.loser"><option value=""></option></select>
  <select name="perf">
    <option value=""></option>
    <option value="1">Bullet</option>
    <option value="2">Blitz</option>
    <option value="6">Rapid</option>
  </select>
  <select name="winnerColor">
    <option value=""></option>
    <option value="1">White</option>
    <option value="2">Black</option>
  </select>
  <select name="mode">
    <option value=""></option>
    <option value="0">Casual</option>
    <option value="1">Rated</option>
  </select>
  <select name="ratingMin">
    <option value=""></option>
    <option value="1000">1000</option>
    <option value="1500">1500</option>
    <option value="2000">2000</option>
  </select>
  <select name="ratingMax">
    <option value=""></option>
    <option value="1000">1000</option>
    <option value="1500">1500</option>
    <option value="1900">1900</option>
    <option value="2000">2000</option>
  </select>
  <input name="dateMin" type="date" min="2011-01-01" max="2026-12-31" />
  <input name="dateMax" type="date" min="2011-01-01" max="2026-12-31" />
  <select name="hasAi">
    <option value=""></option>
    <option value="0">Human</option>
    <option value="1">Computer</option>
  </select>
  <select name="aiLevelMin">
    <option value=""></option>
    <option value="1">level 1</option>
    <option value="5">level 5</option>
    <option value="8">level 8</option>
  </select>
  <select name="aiLevelMax">
    <option value=""></option>
    <option value="1">level 1</option>
    <option value="5">level 5</option>
    <option value="8">level 8</option>
  </select>
  <input name="analysed" type="checkbox" value="true" />
  <select name="sort.field">
    <option value="date">Date</option>
    <option value="turns">Number of turns</option>
    <option value="averageRating">Rating</option>
  </select>
  <select name="sort.order">
    <option value="desc">Descending</option>
    <option value="asc">Ascending</option>
  </select>
</form>`;

describe('window.__ltw', () => {
  test('exposes both tools', () => {
    assert.deepEqual(
      ltw.tools.map(tool => tool.name),
      ['lichess_search_games', 'lichess_analyse_game'],
    );
  });

  test('exposes registration diagnostics', () => {
    const diagnostics = ltw.debug();
    assert.ok('document.modelContext' in diagnostics);
    assert.ok('navigator.modelContext' in diagnostics);
    assert.equal(typeof ltw.register, 'function');
    assert.ok(Array.isArray(ltw.attempts));
    // jsdom has no WebMCP host, so a manual retry registers nothing and must not throw.
    assert.deepEqual(ltw.register(), []);
  });
});

describe('lichess_search_games', () => {
  test('fills player text fields, selects, date, checkbox and sort', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({
      players: { a: 'Alice' },
      white: 'Alice',
      perf: 'Blitz',
      mode: 'rated',
      dateMin: '2024-01-01',
      analysed: true,
      sortField: 'rating',
      sortOrder: 'asc',
      submit: false,
    });

    assert.equal(result.status, 'filled');
    for (const field of [
      'players.a',
      'players.white',
      'perf',
      'mode',
      'dateMin',
      'analysed',
      'sort.field',
      'sort.order',
    ]) {
      assert.ok(result.filled.includes(field), `expected ${field} in filled`);
    }

    const form = document.querySelector('form.search__form') as HTMLFormElement;
    assert.equal((form.querySelector('[name="players.a"]') as HTMLInputElement).value, 'Alice');

    const white = form.querySelector('[name="players.white"]') as HTMLSelectElement;
    assert.equal(white.value, 'Alice');
    assert.ok([...white.options].some(option => option.textContent === 'Alice'));

    assert.equal((form.querySelector('[name="perf"]') as HTMLSelectElement).value, '2');
    assert.equal((form.querySelector('[name="mode"]') as HTMLSelectElement).value, '1');
    assert.equal((form.querySelector('[name="dateMin"]') as HTMLInputElement).value, '2024-01-01');
    assert.equal((form.querySelector('[name="analysed"]') as HTMLInputElement).checked, true);
    assert.equal((form.querySelector('[name="sort.field"]') as HTMLSelectElement).value, 'averageRating');
    assert.equal((form.querySelector('[name="sort.order"]') as HTMLSelectElement).value, 'asc');

    const query = new URLSearchParams(result.url.split('#')[0].split('?')[1]);
    assert.equal(query.get('players.white'), 'Alice');
    assert.equal(query.get('perf'), '2');
    assert.equal(query.get('analysed'), 'true');
    assert.ok(result.url.startsWith('/games/search?'));
    assert.ok(result.url.endsWith('#results'));
  });

  test('fills a players.a seat when only a colour shortcut is given', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({ black: 'Bob', submit: false });

    assert.equal(result.status, 'filled');
    const form = document.querySelector('form.search__form') as HTMLFormElement;
    assert.equal((form.querySelector('[name="players.a"]') as HTMLInputElement).value, 'Bob');
    const black = form.querySelector('[name="players.black"]') as HTMLSelectElement;
    assert.equal(black.value, 'Bob');
    assert.ok((result.autoFilled as string[]).includes('players.a'));
    assert.ok((result.warnings as string[]).some(warning => warning.includes('players.a')));
  });

  test('fills both seats from white and black', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({ white: 'Alice', black: 'Bob', submit: false });

    const form = document.querySelector('form.search__form') as HTMLFormElement;
    assert.equal((form.querySelector('[name="players.a"]') as HTMLInputElement).value, 'Alice');
    assert.equal((form.querySelector('[name="players.b"]') as HTMLInputElement).value, 'Bob');
    assert.equal((form.querySelector('[name="players.white"]') as HTMLSelectElement).value, 'Alice');
    assert.equal((form.querySelector('[name="players.black"]') as HTMLSelectElement).value, 'Bob');
    assert.deepEqual(result.autoFilled, ['players.a', 'players.b']);
  });

  test('uses an existing seat and fills the other for winner/loser', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({ players: { a: 'Alice' }, winner: 'Bob', submit: false });

    const form = document.querySelector('form.search__form') as HTMLFormElement;
    assert.equal((form.querySelector('[name="players.a"]') as HTMLInputElement).value, 'Alice');
    assert.equal((form.querySelector('[name="players.b"]') as HTMLInputElement).value, 'Bob');
    assert.equal((form.querySelector('[name="players.winner"]') as HTMLSelectElement).value, 'Bob');
    assert.deepEqual(result.autoFilled, ['players.b']);
  });

  test('skips winner when both seats are taken by other names', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({
      players: { a: 'Alice', b: 'Bob' },
      winner: 'Carol',
      submit: false,
    });

    const skipped = result.skipped as { field: string; reason: string }[];
    assert.ok(skipped.some(skip => skip.field === 'players.winner' && skip.reason.includes('players.a or players.b')));
    assert.ok((result.warnings as string[]).some(warning => warning.includes('players.winner was ignored')));

    const form = document.querySelector('form.search__form') as HTMLFormElement;
    assert.equal((form.querySelector('[name="players.winner"]') as HTMLSelectElement).value, '');
  });

  test('sets hasAi=computer automatically when an AI level is requested', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({
      players: { a: 'Alice' },
      aiLevelMin: 1,
      aiLevelMax: 5,
      submit: false,
    });

    const form = document.querySelector('form.search__form') as HTMLFormElement;
    assert.equal((form.querySelector('[name="hasAi"]') as HTMLSelectElement).value, '1');
    assert.equal((form.querySelector('[name="aiLevelMin"]') as HTMLSelectElement).value, '1');
    assert.equal((form.querySelector('[name="aiLevelMax"]') as HTMLSelectElement).value, '5');
    assert.ok((result.autoFilled as string[]).includes('hasAi'));
  });

  test('skips AI level when hasAi is human', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({
      players: { a: 'Alice' },
      hasAi: 'human',
      aiLevelMin: 1,
      submit: false,
    });

    const skipped = result.skipped as { field: string; reason: string }[];
    assert.ok(skipped.some(skip => skip.field === 'aiLevelMin' && skip.reason.includes('computer')));
    assert.ok((result.warnings as string[]).some(warning => warning.includes('aiLevelMin')));
  });

  test('clears stale player fields when a new player search is requested', async () => {
    document.body.innerHTML = searchFormHtml;
    const form = document.querySelector('form.search__form') as HTMLFormElement;
    (form.querySelector('[name="players.a"]') as HTMLInputElement).value = 'Kita';
    (form.querySelector('[name="players.b"]') as HTMLInputElement).value = 'Carlsen';
    for (const [name, value] of [
      ['players.winner', 'Kita'],
      ['players.loser', 'Carlsen'],
    ] as const) {
      const select = form.querySelector(`[name="${name}"]`) as HTMLSelectElement;
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      option.selected = true;
      select.append(option);
    }

    const result = await ltw.searchGames({ players: { loser: 'Astra' }, submit: false });

    assert.equal((form.querySelector('[name="players.a"]') as HTMLInputElement).value, 'Astra');
    assert.equal((form.querySelector('[name="players.b"]') as HTMLInputElement).value, '');
    assert.equal((form.querySelector('[name="players.loser"]') as HTMLSelectElement).value, 'Astra');
    assert.equal((form.querySelector('[name="players.winner"]') as HTMLSelectElement).value, '');
    assert.ok((result.query as string).includes('players.loser=Astra'));
    assert.ok(!(result.query as string).includes('players.winner=Kita'));
    assert.ok(!(result.query as string).includes('players.b=Carlsen'));
  });

  test('clears stale player fields when no player is specified', async () => {
    document.body.innerHTML = searchFormHtml;
    const form = document.querySelector('form.search__form') as HTMLFormElement;
    (form.querySelector('[name="players.a"]') as HTMLInputElement).value = 'Kita';
    (form.querySelector('[name="players.b"]') as HTMLInputElement).value = 'Carlsen';

    const result = await ltw.searchGames({ perf: 'Blitz', hasAi: 'computer', submit: false });

    assert.equal((form.querySelector('[name="players.a"]') as HTMLInputElement).value, '');
    assert.equal((form.querySelector('[name="players.b"]') as HTMLInputElement).value, '');
    assert.ok(!(result.query as string).includes('players.a=Kita'));
    assert.ok(!(result.query as string).includes('players.b=Carlsen'));
    assert.ok((result.query as string).includes('hasAi=1'));
  });

  test('clears stale non-player filters when the next search does not request them', async () => {
    document.body.innerHTML = searchFormHtml;
    const form = document.querySelector('form.search__form') as HTMLFormElement;
    (form.querySelector('[name="dateMin"]') as HTMLInputElement).value = '2026-11-07';
    (form.querySelector('[name="dateMax"]') as HTMLInputElement).value = '2026-11-07';
    (form.querySelector('[name="ratingMax"]') as HTMLSelectElement).value = '1900';
    (form.querySelector('[name="players.a"]') as HTMLInputElement).value = 'Old';

    const result = await ltw.searchGames({ players: { loser: 'astra' }, submit: false });

    assert.ok((result.query as string).includes('players.loser=astra'));
    assert.ok(!(result.query as string).includes('dateMin=2026-11-07'));
    assert.ok(!(result.query as string).includes('ratingMax=1900'));
    assert.ok(!(result.query as string).includes('players.a=Old'));
  });

  test('snaps numeric rating selects to the closest available bucket', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({
      dateMin: '2026-11-07',
      dateMax: '2026-11-07',
      ratingMax: 1999,
      submit: false,
    });

    assert.ok((result.filled as string[]).includes('ratingMax'));
    assert.ok(!(result.skipped as any[]).some((s: any) => s.field === 'ratingMax'));
    assert.ok((result.warnings as string[]).some(w => w.includes('ratingMax=1999') && w.includes('1900')));
    assert.ok((result.query as string).includes('ratingMax=1900'));
    assert.ok((result.query as string).includes('dateMin=2026-11-07'));
  });

  test('keeps winner/loser when a/b changes rebuild dependent selects (bits.gameSearch)', async () => {
    document.body.innerHTML = searchFormHtml;
    const form = document.querySelector('form.search__form') as HTMLFormElement;
    const rebuild = () => {
      const users = ['players.a', 'players.b']
        .map(n => (form.querySelector(`[name="${n}"]`) as HTMLInputElement).value.trim())
        .filter(Boolean);
      for (const [name, dataKey] of [
        ['players.winner', 'reqWinner'],
        ['players.loser', 'reqLoser'],
      ] as const) {
        const select = form.querySelector(`[name="${name}"]`) as HTMLSelectElement;
        const wanted = (form.dataset as any)[dataKey] || '';
        select.innerHTML = '<option value=""></option>';
        for (const u of users) {
          const opt = document.createElement('option');
          opt.value = u;
          opt.textContent = u;
          if (u === wanted) opt.selected = true;
          select.append(opt);
        }
      }
    };
    form.querySelector('[name="players.a"]')?.addEventListener('input', rebuild);
    form.querySelector('[name="players.b"]')?.addEventListener('input', rebuild);

    const result = await ltw.searchGames(
      { players: { a: 'astra', b: 'calsen' }, winner: 'astra', loser: 'calsen', submit: false },
    );

    rebuild(); // simulate late bits.site-load rebuild
    assert.equal((form.querySelector('[name="players.winner"]') as HTMLSelectElement).value, 'astra');
    assert.equal((form.querySelector('[name="players.loser"]') as HTMLSelectElement).value, 'calsen');
    assert.ok((result.query as string).includes('players.winner=astra'));
    assert.ok((result.query as string).includes('players.loser=calsen'));
  });

  test('submits the search by default', async () => {
    document.body.innerHTML = searchFormHtml;
    const form = document.querySelector('form.search__form') as HTMLFormElement;
    let submitted = 0;
    (form as any).requestSubmit = () => {
      submitted += 1;
    };

    const result = await ltw.searchGames({ players: { a: 'Alice' } });

    assert.equal(result.submitted, true);
    await new Promise(resolve => setTimeout(resolve, 80));
    assert.equal(submitted, 1);
  });

  test('does not submit when submit is false', async () => {
    document.body.innerHTML = searchFormHtml;
    const form = document.querySelector('form.search__form') as HTMLFormElement;
    let submitted = 0;
    (form as any).requestSubmit = () => {
      submitted += 1;
    };

    const result = await ltw.searchGames({ players: { a: 'Alice' }, submit: false });

    assert.equal(result.submitted, false);
    await new Promise(resolve => setTimeout(resolve, 80));
    assert.equal(submitted, 0);
  });

  test('reports skipped fields instead of throwing', async () => {
    document.body.innerHTML = searchFormHtml;

    const result = await ltw.searchGames({ perf: 'NotAVariant', dateMin: '2024-13-40', submit: false });

    assert.equal(result.status, 'filled');
    assert.deepEqual(result.filled, []);

    const skipped = result.skipped as { field: string; reason: string }[];
    assert.ok(skipped.some(skip => skip.field === 'perf' && skip.reason === 'option not found'));
    assert.ok(skipped.some(skip => skip.field === 'dateMin' && skip.reason.includes('invalid date')));
  });

  test('works when the page has no search form (navigates once)', async () => {
    sessionStorage.removeItem('__ltw_pending_search');
    document.body.innerHTML = '';

    const result = await ltw.searchGames({ perf: 'Blitz' });

    // jsdom shares the /games/search path check with the browser, but its URL is
    // https://lichess.org/ in tests, so we always get the navigation response.
    assert.equal(result.status, 'navigating');
    assert.equal(result.url, '/games/search');
    assert.equal(result.pending, true);
    assert.ok(sessionStorage.getItem('__ltw_pending_search')?.includes('Blitz'));
    sessionStorage.removeItem('__ltw_pending_search');
  });
});

describe('lichess_analyse_game', () => {
  test('defaults to chronological order and flags the biggest turning point', async () => {
    const restore = stubFetch(pgnWithEvals);
    try {
      const result = await ltw.analyseGame({ gameId: 'abcd1234' });

      assert.equal(result.hasEval, true);
      assert.equal(result.gameId, 'abcd1234');
      assert.equal(result.plies, 4);
      assert.equal(result.order, 'chronological');
      assert.equal(result.mover, 'both');
      assert.equal(result.topN, 10);

      const points = result.turningPoints as any[];
      assert.deepEqual(
        points.map(point => point.san),
        ['e5', 'Qh5', 'Nc6'],
      );

      const biggest = points.find(point => point.isBiggest);
      assert.equal(biggest.ply, 4);
      assert.equal(biggest.moveNumber, 2);
      assert.equal(biggest.mover, 'black');
      assert.equal(biggest.san, 'Nc6');
      assert.equal(biggest.mateAfter, 3);
      assert.equal(biggest.kind, 'blunder');
      assert.equal(biggest.importanceRank, 1);
      assert.ok(biggest.delta > 0.3);
      assert.ok(Math.abs(biggest.evalAfter - 18) < 0.01);

      assert.equal(points[1].san, 'Qh5');
      assert.equal(points[1].importanceRank, 2);
      assert.equal(points[0].importanceRank, 3);
      assert.equal(points.filter(point => point.isBiggest).length, 1);

      const best = result.bestMove;
      assert.equal(best.san, 'Qh5');
      assert.equal(best.mover, 'white');
      assert.equal(best.ply, 3);

      const game = result.game as Record<string, string>;
      assert.equal(game.white, 'Alice');
      assert.equal(game.black, 'Bob');
    } finally {
      restore();
    }
  });

  test('sorts by importance when order=importance', async () => {
    const restore = stubFetch(pgnWithEvals);
    try {
      const result = await ltw.analyseGame({ gameId: 'abcd1234', topN: 3, order: 'importance' });
      const points = result.turningPoints as any[];
      assert.deepEqual(
        points.map(point => point.san),
        ['Nc6', 'Qh5', 'e5'],
      );
      assert.equal(points[0].isBiggest, true);
      assert.equal(points[0].importanceRank, 1);
    } finally {
      restore();
    }
  });

  test('honours topN and can filter by mover', async () => {
    const restore = stubFetch(pgnWithEvals);
    try {
      const limited = await ltw.analyseGame({ gameId: 'abcd1234', topN: 2 });
      assert.deepEqual(
        (limited.turningPoints as any[]).map(point => point.san),
        ['Qh5', 'Nc6'],
      );

      const white = await ltw.analyseGame({ gameId: 'abcd1234', mover: 'white' });
      const whitePoints = white.turningPoints as any[];
      assert.deepEqual(
        whitePoints.map(point => point.san),
        ['Qh5'],
      );
      assert.equal(whitePoints[0].isBiggest, true);
      assert.equal(white.bestMove.san, 'Qh5');
      assert.equal(white.bestMove.mover, 'white');
    } finally {
      restore();
    }
  });

  test('ignores variations and keeps only the main line', async () => {
    const restore = stubFetch(pgnWithEvals);
    try {
      const result = await ltw.analyseGame({ gameId: 'abcd1234', topN: 50 });
      const sans = (result.turningPoints as any[]).map(point => point.san);
      assert.deepEqual(sans.slice().sort(), ['Nc6', 'Qh5', 'e5']);
    } finally {
      restore();
    }
  });

  test('reads the winner of a delivered mate from "#0" on a mating move', async () => {
    const pgn = `[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. d4 { [%eval 0.26] } d5 { [%eval 0.24] } 2. Qd3 { [%eval 0.0] } Na6 { [%eval 0.0] } 3. Nc3# { [%eval #0] } 1-0`;
    const restore = stubFetch(pgn);
    try {
      const result = await ltw.analyseGame({ gameId: 'abcd1234', topN: 5 });
      const points = result.turningPoints as any[];
      const last = points[points.length - 1];
      assert.equal(last.san, 'Nc3#');
      assert.equal(last.mateAfter, 1);
      assert.ok(last.delta > 0);
      assert.equal(last.isBiggest, true);
    } finally {
      restore();
    }
  });

  test('returns NO_EVAL without throwing for an unanalysed game', async () => {
    const restore = stubFetch(pgnWithoutEvals);
    try {
      const result = await ltw.analyseGame({ gameId: 'abcd1234' });

      assert.equal(result.hasEval, false);
      assert.equal(result.error, 'NO_EVAL');
      assert.equal(result.plies, 4);
    } finally {
      restore();
    }
  });

  test('derives the game id from a URL', async () => {
    const restore = stubFetch(pgnWithEvals);
    try {
      const result = await ltw.analyseGame({ url: 'https://lichess.org/analyse/abcd1234/white' });
      assert.equal(result.gameId, 'abcd1234');
      assert.equal(result.hasEval, true);
    } finally {
      restore();
    }
  });

  test('auto-detects the game from the current page URL', async () => {
    window.history.pushState({}, '', '/game/abcd1234');
    const restore = stubFetch(pgnWithEvals);
    try {
      const result = await ltw.analyseGame({ topN: 1 });
      assert.equal(result.gameId, 'abcd1234');
      assert.equal(result.hasEval, true);
    } finally {
      restore();
      window.history.pushState({}, '', '/');
    }
  });

  test('auto-detects the game from page-init-data when the URL has no id', async () => {
    window.history.pushState({}, '', '/analysis');
    document.body.innerHTML = `<script id="page-init-data" type="application/json">{"cfg":{"data":{"game":{"id":"abcd1234"}}}}</script>`;
    const restore = stubFetch(pgnWithEvals);
    try {
      const result = await ltw.analyseGame({ topN: 1 });
      assert.equal(result.gameId, 'abcd1234');
      assert.equal(result.hasEval, true);
    } finally {
      restore();
      document.body.innerHTML = '';
      window.history.pushState({}, '', '/');
    }
  });

  test('returns skipped when no game is open', async () => {
    window.history.pushState({}, '', '/');
    document.body.innerHTML = '';

    const result = await ltw.analyseGame({});

    assert.equal(result.status, 'skipped');
    assert.equal(result.hasEval, false);
    assert.equal(result.error, undefined);
  });

  test('rejects an explicitly malformed game id', async () => {
    const result = await ltw.analyseGame({ gameId: 'nope' });
    assert.equal(result.error, 'INVALID_GAME_ID');
  });
});
