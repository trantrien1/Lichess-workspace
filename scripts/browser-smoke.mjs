import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pnpm = resolve(root, 'infra/lila-docker/repos/lila/node_modules/.pnpm');
const core = (await readdir(pnpm)).find(name => /^playwright-core@\d/.test(name));
assert.ok(core, 'Build Lichess UI dependencies first');
process.env.PLAYWRIGHT_BROWSERS_PATH ??= resolve(root, '.cache/playwright');
const { chromium } = await import(pathToFileURL(resolve(pnpm, core, 'node_modules/playwright-core/index.mjs')));
const base = process.env.BASE_URL || 'http://localhost:8080';
const humanPlayers = process.env.HUMAN_PLAYERS === '1';
const prefix = humanPlayers ? 'human' : 'wdl';
const artifacts = resolve(root, 'artifacts');
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({ headless: true });
const contexts = [];
const errors = [];
const report = { base, humanPlayers, assertions: [], latency_ms: [] };
let workerStopped = false;
const check = (condition, message) => {
  assert.ok(condition, message);
  report.assertions.push(message);
};

async function page() {
  const context = await browser.newContext({ baseURL: base, locale: 'en-US', viewport: { width: 1365, height: 900 } });
  contexts.push(context);
  const tab = await context.newPage();
  tab.on('pageerror', error => errors.push(error.message));
  tab.setDefaultTimeout(15000);
  return tab;
}

async function register(tab, color) {
  // These accounts exist only in the isolated local database. Mail delivery must be mocked.
  assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
  const config = await readFile(resolve(root, 'infra/lila.conf'), 'utf8');
  assert.match(config, /^mailer\.primary\.mock = true$/m);
  const suffix = randomBytes(5).toString('hex');
  const username = `Wdl${color}${suffix}`;
  await tab.goto('/signup');
  await tab.locator('[name="username"]').fill(username);
  await tab.locator('[name="password"]').fill(`${randomBytes(18).toString('hex')}!aA3`);
  await tab.locator('[name="email"]').fill(`wdl-acceptance-${suffix}@gmail.com`);
  for (const checkbox of await tab.locator('#signup-form input[type="checkbox"]').all()) {
    const id = await checkbox.getAttribute('id');
    await tab.locator(`label.form-check__label[for="${id}"]`).click();
    assert.ok(await checkbox.isChecked());
  }
  await tab.locator('#signup-form button[type="submit"]').click();
  await tab.waitForURL(url => !url.pathname.startsWith('/signup'), { timeout: 30000 });
  check((await tab.locator('#user_tag').innerText()).toLowerCase() === username.toLowerCase(),
    `${color}: local registered player has an authenticated session`);
}

try {
  const white = await page();
  // Lila's WarmupFilter returns 503 to headless clients for 20 seconds after first access.
  const deadline = Date.now() + 60000;
  let response;
  do {
    response = await white.goto('/');
    if (![502, 503].includes(response.status())) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  } while (Date.now() < deadline);
  check(response.status() === 200, `Lichess homepage responds 200 (received ${response.status()})`);
  await white.screenshot({ path: resolve(artifacts, 'home.png'), fullPage: true });
  if (process.env.PROBE_ONLY) {
    console.log((await white.locator('body').innerText()).slice(0, 3500));
  } else {
    const black = await page();
    if (humanPlayers) {
      await register(white, 'White');
      await register(black, 'Black');
    }
    const created = await white.request.post('/api/challenge/open', {
      form: { rated: 'false', variant: 'standard', 'clock.limit': '600', 'clock.increment': '5', name: 'WDL acceptance' },
    });
    check(created.ok(), `Create local open challenge: ${created.status()}`);
    const challenge = await created.json();
    report.gameId = challenge.challenge?.id ?? challenge.id;
    check(!!report.gameId, 'Challenge returns a game id');
    await white.goto(challenge.urlWhite);
    await white.locator('form.accept button').click();
    await black.goto(challenge.urlBlack);
    await black.locator('form.accept button').click();
    await Promise.all([white.locator('.round__app cg-board').waitFor(), black.locator('.round__app cg-board').waitFor()]);
    const spectator = await page();
    await spectator.goto(`/game/${report.gameId}/white`);
    const manifest = JSON.parse(await readFile(resolve(root,
      'infra/lila-docker/repos/lila/public/compiled/manifest.json'), 'utf8'));
    const roundHash = typeof manifest.js.round === 'string' ? manifest.js.round : manifest.js.round.hash;
    check((await spectator.content()).includes(`round.${roundHash}.js`),
      'Server serves the current built round bundle');
    report.round_bundle = `round.${roundHash}.js`;
    const panel = spectator.locator('.win-probability');
    await panel.locator('.win-probability__values').waitFor();
    check(await panel.locator('.win-probability__bar').count() === 1, 'Exactly one probability bar is shown');
    check(await panel.locator('.win-probability__heading').count() === 0, 'Engine name/depth heading is removed');
    check(await white.locator('.win-probability').count() === 0, 'Active player does not see WDL panel');
    check((await white.request.get(`/game/${report.gameId}/win-probability`)).status() === 403, 'Active player cannot call WDL endpoint');

    async function clickMove(tab, from, to, color) {
      const board = await tab.locator('cg-board').boundingBox();
      assert.ok(board);
      const point = square => {
        let file = square.charCodeAt(0) - 97;
        let rank = Number(square[1]) - 1;
        if (color === 'black') file = 7 - file;
        else rank = 7 - rank;
        return [board.x + (file + 0.5) * board.width / 8, board.y + (rank + 0.5) * board.height / 8];
      };
      await tab.mouse.click(...point(from));
      await tab.mouse.click(...point(to));
    }

    async function move(tab, from, to, color, ply) {
      const start = performance.now();
      const updated = spectator.waitForResponse(async rsp => {
        if (!rsp.url().endsWith('/win-probability') || rsp.status() !== 200) return false;
        const body = await rsp.json();
        return body.ply === ply && body.status === 'ready';
      });
      await clickMove(tab, from, to, color);
      const prediction = await (await updated).json();
      const source = prediction.human?.status === 'ready' ? 'human' : 'engine';
      const selected = source === 'human' ? prediction.human.probabilities : prediction.probabilities;
      await spectator.waitForFunction(({ source, selected }) => {
        const panel = document.querySelector('.win-probability');
        const values = [...(panel?.querySelector('.win-probability__values')?.textContent ?? '').matchAll(/(\d+\.\d)%/g)]
          .map(match => Number(match[1]));
        return panel?.getAttribute('data-source') === source && values.length === 3 &&
          values.every((value, index) => Math.abs(value - selected[index] * 100) <= 0.100001);
      }, { source, selected: [selected.white, selected.draw, selected.black] });
      report.latency_ms.push(Math.round(performance.now() - start));
      const labels = await panel.locator('.win-probability__values').innerText();
      const values = [...labels.matchAll(/(\d+\.\d)%/g)].map(m => Number(m[1]));
      check(values.length === 3 && Math.abs(values.reduce((a, b) => a + b, 0) - 100) < 0.01, `Ply ${ply}: displayed WDL sums to 100%`);
      check((await panel.innerText()).match(/\d+\.\d%/g)?.length === 3,
        `Ply ${ply}: exactly one set of percentages matches the ${source} estimate`);
      if (humanPlayers && ply >= 4) {
        check(prediction.human?.status === 'ready', `Ply ${ply}: server ratings/clocks produce a human prediction`);
        report.model_id = prediction.human.model_id;
      } else if (!humanPlayers) {
        check(prediction.human?.reason === 'missing_context', `Ply ${ply}: anonymous ratings are not invented`);
        check(await panel.getAttribute('data-source') === 'engine', `Ply ${ply}: engine fallback is identified without ratings`);
      }
    }

    const sequence = 'e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 f1e1 b7b5 a4b3 d7d6 c2c3 e8g8 h2h3 c6b8 d2d4 b8d7'.split(' ');
    for (let index = 0; index < sequence.length; index++) {
      const uci = sequence[index];
      await move(index % 2 ? black : white, uci.slice(0, 2), uci.slice(2), index % 2 ? 'black' : 'white', index + 1);
    }
    report.browser_move_to_wdl_p95_ms = [...report.latency_ms].sort((a, b) => a - b)[Math.ceil(report.latency_ms.length * 0.95) - 1];
    await spectator.screenshot({ path: resolve(artifacts, `${prefix}-live.png`), fullPage: true });
    await panel.screenshot({ path: resolve(artifacts, `${prefix}-panel.png`) });
    const beforeFlip = await panel.locator('.win-probability__values').innerText();
    await spectator.keyboard.press('f');
    check(await panel.locator('.win-probability__values').innerText() === beforeFlip, 'Board flip preserves White/draw/Black labels');
    await spectator.keyboard.press('ArrowLeft');
    await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'replay');
    check(await panel.locator('.win-probability__values').count() === 0, 'Historical replay hides live-position percentages');
    check(await panel.getAttribute('data-source') === null, 'Historical replay clears the selected prediction source');
    await spectator.keyboard.press('ArrowRight');
    await panel.locator('.win-probability__values').waitFor();
    await spectator.reload();
    await panel.locator('.win-probability__values').waitFor();
    check(true, 'Spectator reload restores current WDL');

    let reconnectNavigations = 0;
    const onReconnectNavigation = frame => {
      if (frame === spectator.mainFrame()) reconnectNavigations++;
    };
    spectator.on('framenavigated', onReconnectNavigation);
    await spectator.context().setOffline(true);
    await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'stale',
      undefined, { timeout: 30000 });
    check(await panel.locator('.win-probability__values').count() === 0, 'Network disconnect clears displayed percentages');
    check(await panel.getAttribute('data-source') === null, 'Network disconnect clears the selected prediction source');
    await spectator.context().setOffline(false);
    await panel.locator('.win-probability__values').waitFor({ timeout: 30000 });
    if (humanPlayers) await spectator.locator('.win-probability[data-source="human"]').waitFor();
    spectator.off('framenavigated', onReconnectNavigation);
    check(reconnectNavigations === 0,
      'Network reconnect restores the authoritative current position without a page reload');

    const rewound = spectator.waitForResponse(async rsp => {
      if (!rsp.url().endsWith('/win-probability') || rsp.status() !== 200) return false;
      const body = await rsp.json();
      return body.status === 'ready' && body.ply === 18;
    });
    await white.locator('button.takeback-yes').click();
    await black.locator('.rcontrols .question .yes').click();
    await rewound;
    check(true, 'Accepted takeback restores the correct historical position');
    await move(white, 'a2', 'a4', 'white', 19);
    await move(black, 'b8', 'd7', 'black', 20);
    const changed = await spectator.request.get(`/game/${report.gameId}/win-probability`);
    check((await changed.json()).revision.endsWith('h3 Nb8 a4 Nbd7|20|'), 'Same-ply replacement uses the new move history');

    await spectator.setViewportSize({ width: 390, height: 844 });
    await panel.scrollIntoViewIfNeeded();
    await spectator.screenshot({ path: resolve(artifacts, `${prefix}-mobile.png`), fullPage: true });
    check(await spectator.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile layout has no horizontal overflow');
    await spectator.setViewportSize({ width: 1365, height: 900 });

    if (process.env.TEST_OUTAGE === '1') {
      execFileSync('docker', ['compose', '-f', 'infra/compose.yml', 'stop', 'prediction'], { cwd: root, stdio: 'pipe' });
      workerStopped = true;
      await clickMove(white, 'd2', 'd4', 'white');
      await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'unavailable');
      const current = await spectator.request.get(`/game/${report.gameId}/win-probability`);
      check((await current.json()).revision.endsWith('a4 Nbd7 d4|20|'), 'Game accepts a new move while the engine service is down');
      check(await panel.locator('.win-probability__values').count() === 0, 'Engine outage does not retain stale percentages');
      check(await panel.getAttribute('data-source') === null, 'Worker outage clears the selected prediction source');
      execFileSync('docker', ['compose', '-f', 'infra/compose.yml', 'start', 'prediction'], { cwd: root, stdio: 'pipe' });
      workerStopped = false;
      await panel.locator('.win-probability__values').waitFor();
      check(true, 'WDL recovers after the worker restarts');
    }
    await black.locator('button.resign').click();
    const confirm = black.locator('.act-confirm button.yes');
    if (await confirm.isVisible()) await confirm.click();
    await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'finished');
    check((await panel.locator('.win-probability__values').innerText()).includes('Trắng 100.0%'), 'Official resignation result overrides engine WDL');
    check(await panel.getAttribute('data-source') === 'official', 'Official result overrides the human prediction');
    await spectator.screenshot({ path: resolve(artifacts, `${prefix}-finished.png`), fullPage: true });

    async function endingGame(kind, moves, initialSeconds = 600) {
      const created = await white.request.post('/api/challenge/open', {
        form: { rated: 'false', variant: 'standard', 'clock.limit': String(initialSeconds),
          'clock.increment': initialSeconds === 15 ? '0' : '5', name: `WDL ${kind}` },
      });
      assert.ok(created.ok());
      const challenge = await created.json();
      const gameId = challenge.challenge?.id ?? challenge.id;
      await white.goto(challenge.urlWhite);
      await white.locator('form.accept button').click();
      await black.goto(challenge.urlBlack);
      await black.locator('form.accept button').click();
      await Promise.all([white.locator('.round__app cg-board').waitFor(), black.locator('.round__app cg-board').waitFor()]);
      await spectator.goto(`/game/${gameId}/white`);
      await panel.waitFor();
      for (let i = 0; i < moves.length; i++) {
        const uci = moves[i];
        const tab = i % 2 ? black : white;
        // Wait for the preceding move to reach the next player's authoritative board.
        if (i) await tab.waitForFunction(ply => document.querySelectorAll('i5d z7yx').length >= ply, i);
        await clickMove(tab, uci.slice(0, 2), uci.slice(2), i % 2 ? 'black' : 'white');
      }
      return gameId;
    }
    report.endingGames = {};
    report.endingGames.checkmate = await endingGame('checkmate', ['f2f3', 'e7e5', 'g2g4', 'd8h4']);
    await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'finished');
    check((await panel.locator('.win-probability__values').innerText()).includes('Đen 100.0%'),
      'Official checkmate overrides both predictions with Black win');
    check(await panel.getAttribute('data-source') === 'official', 'Checkmate removes experimental percentages');

    report.endingGames.draw = await endingGame('draw', ['e2e4', 'e7e5', 'g1f3', 'b8c6']);
    await white.locator('button.draw-yes').click();
    const drawConfirm = white.locator('.act-confirm button.yes');
    if (await drawConfirm.isVisible()) await drawConfirm.click();
    await black.locator('.rcontrols .question .yes').click();
    await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'finished');
    check((await panel.locator('.win-probability__values').innerText()).includes('Hòa 100.0%'),
      'Agreed draw overrides both predictions with 100% draw');

    report.endingGames.aborted = await endingGame('abort', []);
    await white.locator('button.abort').click();
    await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'aborted');
    check(await panel.locator('.win-probability__values').count() === 0, 'Aborted game has no invented result');
    check(await panel.getAttribute('data-source') === null, 'Aborted game has no prediction source');
    if (!humanPlayers) {
      report.endingGames.timeout = await endingGame('timeout', ['e2e4', 'e7e5'], 15);
      await spectator.waitForFunction(() => document.querySelector('.win-probability')?.dataset.state === 'finished',
        undefined, { timeout: 45000 });
      check((await panel.locator('.win-probability__values').innerText()).includes('Đen 100.0%'),
        'Official clock timeout overrides predictions with Black win');
    }
    check(errors.length === 0, `No browser exceptions (${errors.length})`);
  }
  console.log(JSON.stringify(report, null, 2));
  await writeFile(resolve(artifacts, `${humanPlayers ? 'human-' : ''}browser-smoke.json`), JSON.stringify(report, null, 2));
} catch (error) {
  console.error(error);
  console.error('Browser errors:', errors);
  for (let index = 0; index < contexts.length; index++) {
    const tab = contexts[index].pages()[0];
    if (tab) {
      await tab.screenshot({ path: resolve(artifacts, `failure-${index}.png`), fullPage: true }).catch(() => {});
      await writeFile(resolve(artifacts, `failure-${index}.txt`), `${tab.url()}\n${await tab.locator('body').innerText()}`).catch(() => {});
    }
  }
  process.exitCode = 1;
} finally {
  if (workerStopped)
    execFileSync('docker', ['compose', '-f', 'infra/compose.yml', 'start', 'prediction'], { cwd: root, stdio: 'pipe' });
  await browser.close();
}
