/**
 * WebMCP tools for lichess.
 *
 * Two tools are exposed to browser agents (Chrome WebMCP early preview):
 *   - lichess_search_games  : fill /games/search (or /@user/search) on the user's behalf
 *   - lichess_analyse_game  : return the biggest swings of a game as structured data
 *
 * The tools never write prose: they return JSON and let the LLM do the talking.
 *
 * WebMCP is still an early preview (navigator.modelContext / document.modelContext,
 * registerTool / provideTools / provideContext). Everything is feature-detected and
 * wrapped in try/catch, so a normal browser keeps working untouched.
 *
 * For manual testing today, the same functions are exposed on window.__ltw:
 *   await __ltw.searchGames({ white: 'thibault', perf: 'Blitz', dateMin: '2024-01-01' })
 *   await __ltw.analyseGame({ topN: 3 })
 */

// ---------------------------------------------------------------------------
// Shared types / tiny helpers
// ---------------------------------------------------------------------------

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
type JsonObject = Record<string, JsonValue>;

type ToolDef = {
  name: string;
  description: string;
  inputSchema: JsonObject;
  execute: (input: JsonValue | undefined) => Promise<JsonObject>;
};

type Skip = { field: string; reason: string };
type FillReport = { filled: string[]; skipped: Skip[] };

const asRecord = (value: JsonValue | undefined): JsonObject =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {};

const asText = (value: JsonValue | undefined): string | undefined => {
  if (typeof value === 'string') {
    const text = value.trim();
    return text.length > 0 ? text : undefined;
  }
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return undefined;
};

const asBoolean = (value: JsonValue | undefined): boolean =>
  value === true || value === 'true' || value === 1;

const normalizeText = (value: string): string =>
  value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();

// Diacritics- and punctuation-insensitive comparison: "King of the Hill" == "kingOfTheHill".
const looseText = (value: string): string => normalizeText(value).replace(/[^a-z0-9]/g, '');

const i18nText = (catalog: string, key: string): string | undefined => {
  const dictionaries = (globalThis as any).i18n;
  const dictionary = dictionaries?.[catalog];
  if (!dictionary || typeof dictionary !== 'object') return undefined;
  const value = dictionary[key];
  return typeof value === 'string' ? value : undefined;
};

const fireInput = (element: HTMLElement): void => {
  const view = element.ownerDocument.defaultView;
  const EventCtor = view ? view.Event : Event;
  element.dispatchEvent(new EventCtor('input', { bubbles: true }));
  element.dispatchEvent(new EventCtor('change', { bubbles: true }));
};

// ---------------------------------------------------------------------------
// Select helpers: match <option> by translated label first, value second.
// No numeric ids are hardcoded: labels come from the page + window.i18n.
// ---------------------------------------------------------------------------

const PERF_LABEL_ENGLISH: Record<string, string> = {
  ultrabullet: 'UltraBullet',
  bullet: 'Bullet',
  blitz: 'Blitz',
  rapid: 'Rapid',
  classical: 'Classical',
  correspondence: 'Correspondence',
  standard: 'Standard',
  chess960: 'Chess960',
  kingofthehill: 'King of the Hill',
  threecheck: 'Three-check',
  antichess: 'Antichess',
  atomic: 'Atomic',
  horde: 'Horde',
  racingkings: 'Racing Kings',
  crazyhouse: 'Crazyhouse',
};

const PERF_I18N: Record<string, [string, string][]> = {
  ultrabullet: [['site', 'ultraBullet']],
  bullet: [['site', 'bullet']],
  blitz: [['site', 'blitz']],
  rapid: [['site', 'rapid']],
  classical: [['site', 'classical']],
  correspondence: [['site', 'correspondence']],
  standard: [
    ['site', 'standard'],
    ['variant', 'standard'],
  ],
  chess960: [['variant', 'chess960']],
  kingofthehill: [['variant', 'kingOfTheHill']],
  threecheck: [['variant', 'threeCheck']],
  antichess: [['variant', 'antichess']],
  atomic: [['variant', 'atomic']],
  horde: [['variant', 'horde']],
  racingkings: [['variant', 'racingKings']],
  crazyhouse: [['variant', 'crazyhouse']],
};

const SIMPLE_ALIASES: Record<string, [string, string][]> = {
  white: [['site', 'white']],
  black: [['site', 'black']],
  casual: [['site', 'casual']],
  rated: [['site', 'rated']],
  human: [['site', 'human']],
  computer: [['site', 'computer']],
  rating: [['site', 'rating']],
};

const labelCandidates = (raw: string): string[] => {
  const labels = [raw];
  const key = looseText(raw);

  const english = PERF_LABEL_ENGLISH[key];
  if (english) labels.push(english);

  const perfKeys = PERF_I18N[key];
  if (perfKeys) {
    for (const [catalog, i18nKey] of perfKeys) {
      const translated = i18nText(catalog, i18nKey);
      if (translated) labels.push(translated);
    }
  }

  const aliases = SIMPLE_ALIASES[key];
  if (aliases) {
    for (const [catalog, i18nKey] of aliases) {
      const translated = i18nText(catalog, i18nKey);
      if (translated) labels.push(translated);
    }
  }

  return labels;
};

const numericOptionValue = (option: HTMLOptionElement): number | null => {
  if (!/^-?\d+(\.\d+)?$/.test(option.value)) return null;
  const value = Number(option.value);
  return Number.isFinite(value) ? value : null;
};

const findNumericOption = (
  select: HTMLSelectElement,
  raw: string,
  fieldName: string,
): HTMLOptionElement | null => {
  const wanted = Number(raw);
  if (!Number.isFinite(wanted)) return null;

  const options = [...select.options]
    .map(option => ({ option, value: numericOptionValue(option) }))
    .filter((entry): entry is { option: HTMLOptionElement; value: number } => entry.value !== null);
  if (options.length === 0) return null;

  const exact = options.find(entry => entry.value === wanted);
  if (exact) return exact.option;

  if (fieldName.endsWith('Max')) {
    const lower = options.filter(entry => entry.value <= wanted).sort((a, b) => b.value - a.value)[0];
    return lower?.option ?? null;
  }
  if (fieldName.endsWith('Min')) {
    const higher = options.filter(entry => entry.value >= wanted).sort((a, b) => a.value - b.value)[0];
    return higher?.option ?? null;
  }

  // Non-range numeric select (e.g. clockInc/init): pick the closest bucket.
  return options.sort((a, b) => Math.abs(a.value - wanted) - Math.abs(b.value - wanted))[0]?.option ?? null;
};

const findOption = (select: HTMLSelectElement, raw: string): HTMLOptionElement | null => {
  const wanted = looseText(raw);
  if (wanted.length === 0) return null;

  const options = [...select.options].filter(option => option.value.length > 0);

  // 1. Value match: status/source/sort use stable string ids, numbers use numeric values.
  const byValue = options.find(option => looseText(option.value) === wanted);
  if (byValue) return byValue;

  // 2. Exact label match (current locale + canonical English).
  const labels = labelCandidates(raw)
    .map(looseText)
    .filter(label => label.length > 0);
  const exact = options.find(option => labels.includes(looseText(option.textContent ?? '')));
  if (exact) return exact;

  // 3. Partial label match: "turns" -> "Number of turns", "1" -> "Level 1".
  const partial = options.find(option => {
    const text = looseText(option.textContent ?? '');
    return labels.some(label => text.includes(label) || label.includes(text));
  });
  return partial ?? null;
};

const setSelectValue = (select: HTMLSelectElement, option: HTMLOptionElement): void => {
  select.value = option.value;
  fireInput(select);
};

// ---------------------------------------------------------------------------
// Tool 1: lichess_search_games
// ---------------------------------------------------------------------------

const PLAYER_KEYS = ['a', 'b', 'white', 'black', 'winner', 'loser'] as const;
type PlayerKey = (typeof PLAYER_KEYS)[number];

const playerValues = (input: JsonObject): Partial<Record<PlayerKey, string>> => {
  const values: Partial<Record<PlayerKey, string>> = {};
  const nested = asRecord(input.players);
  const aliases: Record<PlayerKey, string[]> = {
    a: ['a', 'playerA'],
    b: ['b', 'playerB'],
    white: ['white'],
    black: ['black'],
    winner: ['winner'],
    loser: ['loser'],
  };

  for (const key of PLAYER_KEYS) {
    const nestedValue = asText(nested[key]);
    if (nestedValue) {
      values[key] = nestedValue;
      continue;
    }
    for (const alias of aliases[key]) {
      const flatValue = asText(input[alias]);
      if (flatValue) {
        values[key] = flatValue;
        break;
      }
    }
  }

  return values;
};

// The server only keeps players.white/black/winner/loser when the username is
// also one of players.a / players.b (SearchPlayer.oneOf in GameSearchForm.scala).
// Fill the two player seats from those dependent filters so the query is not
// silently ignored. Returns warnings for filters that cannot be satisfied.
const ensurePlayerPair = (
  players: Partial<Record<PlayerKey, string>>,
  currentSeats: { a?: string; b?: string },
  autoFilled: string[],
): { warnings: string[]; dropped: PlayerKey[] } => {
  const warnings: string[] = [];
  const dropped: PlayerKey[] = [];
  const seats: PlayerKey[] = ['a', 'b'];
  const dependents: PlayerKey[] = ['white', 'black', 'winner', 'loser'];
  const seated = (seat: PlayerKey): string | undefined =>
    players[seat] ?? (seat === 'a' ? currentSeats.a : currentSeats.b);

  for (const key of dependents) {
    const name = players[key];
    if (!name) continue;
    const normalized = normalizeText(name);
    const inSeat = seats.some(seat => {
      const value = seated(seat);
      return value !== undefined && normalizeText(value) === normalized;
    });
    if (inSeat) {
      const existing = seats.some(seat => {
        const value = seat === 'a' ? currentSeats.a : currentSeats.b;
        return value !== undefined && normalizeText(value) === normalized;
      });
      if (existing) {
        warnings.push(
          `players.${key} matched an existing players.a/b seat already in the form; no seat was added.`,
        );
      }
      continue;
    }

    const free = seats.find(seat => seated(seat) === undefined);
    if (free) {
      players[free] = name;
      autoFilled.push(`players.${free}`);
      warnings.push(
        `players.${free} was set to "${name}" because players.${key} only works when that username is players.a or players.b.`,
      );
      continue;
    }

    dropped.push(key);
    delete players[key];
    warnings.push(
      `players.${key} was ignored: "${name}" is neither players.a nor players.b, and both seats are already taken.`,
    );
  }

  return { warnings, dropped };
};

const setPlayerText = (
  form: HTMLFormElement,
  name: string,
  value: string,
  report: FillReport,
): void => {
  const element = form.querySelector<HTMLElement>(`[name="${name}"]`);
  if (!element) {
    report.skipped.push({ field: name, reason: 'field not found on this page' });
    return;
  }
  if (element.tagName !== 'INPUT') {
    report.skipped.push({ field: name, reason: 'unexpected field type' });
    return;
  }
  const input = element as HTMLInputElement;
  input.value = value;
  fireInput(input);
  report.filled.push(name);
};

// players.white/black/winner/loser are <select>s populated by bits.gameSearch.ts.
// When the username is not among the generated options we add it ourselves.
const setPlayerChoice = (
  form: HTMLFormElement,
  name: string,
  username: string,
  report: FillReport,
): void => {
  const element = form.querySelector<HTMLElement>(`[name="${name}"]`);
  if (!element) {
    report.skipped.push({ field: name, reason: 'field not found on this page' });
    return;
  }

  if (element.tagName === 'SELECT') {
    const select = element as HTMLSelectElement;
    const wanted = normalizeText(username);
    let option = [...select.options].find(candidate => normalizeText(candidate.value) === wanted) ?? null;
    if (!option) {
      option = element.ownerDocument.createElement('option');
      option.value = username;
      option.textContent = username;
      select.append(option);
    }
    setSelectValue(select, option);
    report.filled.push(name);
    return;
  }

  // Some lichess versions render some of these as plain text inputs.
  if (element.tagName === 'INPUT') {
    const input = element as HTMLInputElement;
    input.value = username;
    fireInput(input);
    report.filled.push(name);
    return;
  }

  report.skipped.push({ field: name, reason: 'unexpected field type' });
};

const isIsoDate = (value: string): boolean => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(value);
};

const setFormField = (
  form: HTMLFormElement,
  name: string,
  raw: string,
  report: FillReport,
  warnings?: string[],
): void => {
  const element = form.querySelector<HTMLElement>(`[name="${name}"]`);
  if (!element) {
    report.skipped.push({ field: name, reason: 'field not found on this page' });
    return;
  }

  if (element.tagName === 'SELECT') {
    const select = element as HTMLSelectElement;
    let option = findOption(select, raw);
    let snapped = false;
    if (!option) {
      option = findNumericOption(select, raw, name);
      snapped = option !== null;
    }
    if (!option) {
      report.skipped.push({ field: name, reason: 'option not found' });
      return;
    }
    setSelectValue(select, option);
    report.filled.push(name);
    if (snapped) {
      warnings?.push(
        `${name}=${raw} is not an exact form option; used the closest available value "${option.value}" (${option.textContent?.trim() || option.value}).`,
      );
    }
    return;
  }

  if (element.tagName !== 'INPUT') {
    report.skipped.push({ field: name, reason: 'unexpected field type' });
    return;
  }

  const input = element as HTMLInputElement;

  if (input.type === 'checkbox') {
    input.checked = asBoolean(raw);
    fireInput(input);
    report.filled.push(name);
    return;
  }

  if (input.type === 'date') {
    if (!isIsoDate(raw)) {
      report.skipped.push({ field: name, reason: 'invalid date (expected yyyy-MM-dd)' });
      return;
    }
    if (input.min && raw < input.min) {
      report.skipped.push({ field: name, reason: `date before the allowed minimum (${input.min})` });
      return;
    }
    if (input.max && raw > input.max) {
      report.skipped.push({ field: name, reason: `date after the allowed maximum (${input.max})` });
      return;
    }
  }

  input.value = raw;
  fireInput(input);
  report.filled.push(name);
};

const SIMPLE_FIELDS: [string, string][] = [
  ['winnerColor', 'winnerColor'],
  ['perf', 'perf'],
  ['source', 'source'],
  ['mode', 'mode'],
  ['status', 'status'],
  ['hasAi', 'hasAi'],
  ['aiLevelMin', 'aiLevelMin'],
  ['aiLevelMax', 'aiLevelMax'],
  ['turnsMin', 'turnsMin'],
  ['turnsMax', 'turnsMax'],
  ['ratingMin', 'ratingMin'],
  ['ratingMax', 'ratingMax'],
  ['durationMin', 'durationMin'],
  ['durationMax', 'durationMax'],
  ['clockInit', 'clockInit'],
  ['clockInc', 'clockInc'],
  ['dateMin', 'dateMin'],
  ['dateMax', 'dateMax'],
];

const SEARCH_DESCRIPTION: string =
  "Run lichess advanced game search for the user and submit the query. Use when they ask to find " +
  "their games (or someone's) by opponent, colour, result, variant/speed, rating, date range, clock, " +
  'number of moves, or analysis status. Fills the form, then submits it by default; pass submit=false ' +
  'to only fill the form. If the current page has no search form, navigates to /games/search and ' +
  'runs the saved search there automatically. Returns the query URL plus which fields were filled/skipped. ' +
  'Dependencies handled automatically: players.white/black/winner/loser only work when the username ' +
  'is also players.a or players.b (seats are filled from them); aiLevelMin/Max only apply when ' +
  'hasAi is "computer" (set automatically). Top-level white/black/winner/loser are shortcuts.';

const SEARCH_SCHEMA: JsonObject = {
  type: 'object',
  properties: {
    players: {
      type: 'object',
      properties: {
        a: { type: 'string', description: 'Player A username (one of the two required seats)' },
        b: { type: 'string', description: 'Player B username (one of the two required seats)' },
        white: {
          type: 'string',
          description:
            'White player username. Server-side it only works if this name is also players.a or players.b; the tool fills a free seat automatically.',
        },
        black: {
          type: 'string',
          description:
            'Black player username. Requires the name to be players.a or players.b; the tool fills a free seat automatically.',
        },
        winner: {
          type: 'string',
          description:
            'Winner username. Requires the name to be players.a or players.b; the tool fills a free seat automatically.',
        },
        loser: {
          type: 'string',
          description:
            'Loser username. Requires the name to be players.a or players.b; the tool fills a free seat automatically.',
        },
      },
    },
    white: {
      type: 'string',
      description: 'Shortcut for players.white (a free players.a/b seat is filled automatically)',
    },
    black: {
      type: 'string',
      description: 'Shortcut for players.black (a free players.a/b seat is filled automatically)',
    },
    winner: {
      type: 'string',
      description: 'Shortcut for players.winner (a free players.a/b seat is filled automatically)',
    },
    loser: {
      type: 'string',
      description: 'Shortcut for players.loser (a free players.a/b seat is filled automatically)',
    },
    winnerColor: { type: 'string', description: 'white or black' },
    perf: { type: 'string', description: 'bullet, blitz, rapid, classical, correspondence, chess960, ...' },
    source: { type: 'string', description: 'lobby, friend, tournament, import, ...' },
    mode: { type: 'string', description: 'casual or rated' },
    status: { type: 'string', description: 'resign, outoftime, draw, mate, ...' },
    hasAi: {
      type: 'string',
      description: 'human or computer. Set to "computer" automatically when aiLevelMin/Max is used.',
    },
    aiLevelMin: { type: 'number', description: 'Minimum AI level (1-8); only applies when hasAi is computer' },
    aiLevelMax: { type: 'number', description: 'Maximum AI level (1-8); only applies when hasAi is computer' },
    turnsMin: { type: 'number', description: 'Minimum turns. The form uses fixed buckets; non-exact values are snapped to the nearest valid option.' },
    turnsMax: { type: 'number', description: 'Maximum turns. Fixed buckets; non-exact values are snapped (e.g. to a nearby upper bound).' },
    ratingMin: { type: 'number', description: 'Minimum average rating (both players). Fixed 100-point buckets; non-exact values are snapped to the nearest valid option.' },
    ratingMax: { type: 'number', description: 'Maximum average rating (both players). Fixed 100-point buckets; non-exact values are snapped (e.g. 1999 -> 1900 for "below 2000" when 1900 is the closest valid upper bucket).' },
    durationMin: { type: 'number', description: 'Minimum game duration in seconds. Fixed buckets; non-exact values are snapped.' },
    durationMax: { type: 'number', description: 'Maximum game duration in seconds. Fixed buckets; non-exact values are snapped.' },
    clockInit: { type: 'number', description: 'Initial clock in seconds. Fixed options; non-exact values are snapped to the nearest option.' },
    clockInc: { type: 'number', description: 'Clock increment in seconds. Fixed options; non-exact values are snapped to the nearest option.' },
    dateMin: { type: 'string', description: 'yyyy-MM-dd' },
    dateMax: { type: 'string', description: 'yyyy-MM-dd' },
    analysed: { type: 'boolean', description: 'only analysed games' },
    sortField: { type: 'string', description: 'date, turns or rating' },
    sortOrder: { type: 'string', description: 'desc or asc' },
    submit: {
      type: 'boolean',
      description:
        'Submit the search (default true). Pass false to only fill the form and return the query URL.',
    },
  },
};

const PENDING_SEARCH_KEY = '__ltw_pending_search';

const savePendingSearch = (record: JsonObject): void => {
  try {
    sessionStorage.setItem(PENDING_SEARCH_KEY, JSON.stringify(record));
  } catch {
    // sessionStorage may be unavailable; navigation still happens, agent can retry.
  }
};

const takePendingSearch = (): JsonObject | null => {
  try {
    const value = sessionStorage.getItem(PENDING_SEARCH_KEY);
    if (!value) return null;
    sessionStorage.removeItem(PENDING_SEARCH_KEY);
    return asRecord(JSON.parse(value) as JsonValue);
  } catch {
    try {
      sessionStorage.removeItem(PENDING_SEARCH_KEY);
    } catch {
      /* ignore */
    }
    return null;
  }
};

const searchGames = async (input: JsonValue | undefined): Promise<JsonObject> => {
  const record = asRecord(input);
  const form = document.querySelector<HTMLFormElement>('form.search__form');

  if (!form) {
    const target = '/games/search';
    if (window.location.pathname !== target) {
      savePendingSearch(record);
      window.location.assign(target);
      return {
        status: 'navigating',
        url: target,
        pending: true,
        message:
          'No search form on this page. Navigating to /games/search and the search will run there automatically. ' +
          'If it does not run (session storage unavailable), call lichess_search_games again on /games/search.',
      };
    }
    return {
      status: 'error',
      error: 'NO_FORM',
      message: 'The advanced search form was not found on /games/search.',
    };
  }

  const report: FillReport = { filled: [], skipped: [] };
  const warnings: string[] = [];
  const autoFilled: string[] = [];
  const players = playerValues(record);

  const seatInput = (name: string): HTMLInputElement | null =>
    form.querySelector<HTMLInputElement>(`[name="${name}"]`);

    const reqData: Record<string, string> = {
    'players.white': 'reqWhite',
    'players.black': 'reqBlack',
    'players.winner': 'reqWinner',
    'players.loser': 'reqLoser',
  };

  // Each search is a fresh query: clear the form so filters from a previous search/results
  // page don't leak (e.g. old dates/rating/players). Do NOT use form.reset(): on a results
  // page lila pre-fills values as HTML defaults, so reset() would restore them. Preserve
  // hidden inputs (CSRF, fixed players.a on /@user/games) unless the request overrides.
  for (const attr of Object.values(reqData)) delete form.dataset[attr];
  form.querySelectorAll<HTMLInputElement>('input').forEach(input => {
    if (input.type === 'hidden') return;
    if (input.type === 'checkbox' || input.type === 'radio') input.checked = false;
    else input.value = '';
  });
  form.querySelectorAll<HTMLSelectElement>('select').forEach(select => {
    select.value = '';
  });
  form.querySelectorAll<HTMLTextAreaElement>('textarea').forEach(area => {
    area.value = '';
  });

  // white/black/winner/loser are ignored by the server unless they match a/b,
  // so fill the two seats from them before touching the form. Seats already
  // present in the form (hidden/fixed, or explicitly requested) count as taken.
  const hiddenSeat = (name: string): string | undefined => {
    const input = seatInput(name);
    return input?.type === 'hidden' ? input.value.trim() || undefined : undefined;
  };
  const pair = ensurePlayerPair(
    players,
    { a: hiddenSeat('players.a'), b: hiddenSeat('players.b') },
    autoFilled,
  );
  warnings.push(...pair.warnings);
  for (const key of pair.dropped) {
    report.skipped.push({
      field: `players.${key}`,
      reason: 'requires the username to be players.a or players.b, but both are already taken',
    });
  }

  // a/b first: bits.gameSearch.ts rebuilds the colour/winner/loser <select>s from
  // these two text inputs, and would wipe our selection if we ran in the other order.
  // Also set data-req-* so bits preserves the selection if it rebuilds after us (e.g.
  // on site.load when auto-running a pending search).
  if (players.a) setPlayerText(form, 'players.a', players.a, report);
  if (players.b) setPlayerText(form, 'players.b', players.b, report);
  for (const key of ['white', 'black', 'winner', 'loser'] as const) {
    const value = players[key];
    if (!value) continue;
    const name = `players.${key}`;
    form.dataset[reqData[name]] = value;
    setPlayerChoice(form, name, value, report);
  }

  // The aiLevel row is hidden and has no effect unless Opponent = Computer.
  const aiLevelRequested =
    asText(record.aiLevelMin) !== undefined || asText(record.aiLevelMax) !== undefined;
  let hasAi = asText(record.hasAi);
  let aiLevelAllowed = true;
  if (aiLevelRequested) {
    if (!hasAi) {
      hasAi = 'computer';
      autoFilled.push('hasAi');
      warnings.push(
        'hasAi was set to "computer" because aiLevelMin/aiLevelMax only apply to computer opponents.',
      );
    } else if (normalizeText(hasAi) === 'human') {
      aiLevelAllowed = false;
      warnings.push('aiLevelMin/aiLevelMax were ignored because hasAi is "human".');
    }
  }

  for (const [inputKey, formName] of SIMPLE_FIELDS) {
    const value = inputKey === 'hasAi' ? hasAi : asText(record[inputKey]);

    if ((inputKey === 'aiLevelMin' || inputKey === 'aiLevelMax') && !aiLevelAllowed) {
      if (value !== undefined) {
        report.skipped.push({ field: formName, reason: 'only applies when hasAi is "computer"' });
      }
      continue;
    }

    if (value !== undefined) setFormField(form, formName, value, report, warnings);
  }

  if (record.analysed !== undefined && record.analysed !== null) {
    setFormField(form, 'analysed', String(asBoolean(record.analysed)), report, warnings);
  }

  const sort = asRecord(record.sort);
  const sortField = asText(record.sortField) ?? asText(sort.field);
  const sortOrder = asText(record.sortOrder) ?? asText(sort.order);
  if (sortField) setFormField(form, 'sort.field', sortField, report, warnings);
  if (sortOrder) setFormField(form, 'sort.order', sortOrder, report, warnings);

  const view = document.defaultView;
  const params = new (view?.URLSearchParams ?? URLSearchParams)();
  const formData = new (view?.FormData ?? FormData)(form);
  formData.forEach((value, key) => {
    if (typeof value === 'string' && value.length > 0) params.set(key, value);
  });
  const query = params.toString();

  const action = form.getAttribute('action') ?? '/games/search';
  const path = action.split('#')[0].split('?')[0];
  const url = query.length > 0 ? `${path}?${query}#results` : `${path}#results`;

  // Run the search by default: this is a search tool, not a form filler.
  // A small delay lets the tool result reach the caller before the page unloads.
  const submitted =
    record.submit === undefined || record.submit === null ? true : asBoolean(record.submit);
  if (submitted) {
    const run = (): void => {
      if (typeof form.requestSubmit === 'function') form.requestSubmit();
      else form.submit();
    };
    if (view?.setTimeout) view.setTimeout(run, 50);
    else run();
  }

  return {
    status: 'filled',
    filled: report.filled,
    autoFilled,
    warnings,
    skipped: report.skipped,
    query,
    url,
    submitted,
  };
};

// ---------------------------------------------------------------------------
// Tool 2: lichess_analyse_game
// ---------------------------------------------------------------------------

type AnalyseLimits = { defaultTopN: number; maxTopN: number };
type AnalyseOrder = 'chronological' | 'importance';
type AnalyseMover = 'both' | 'white' | 'black';
type AnalysePatterns = {
  gameId: RegExp;
  gamePath: RegExp;
  evalComment: RegExp;
  moveNumber: RegExp;
};

const ANALYSE_LIMITS: AnalyseLimits = { defaultTopN: 10, maxTopN: 100 };
const ANALYSE_PATTERNS: AnalysePatterns = {
  gameId: /^[A-Za-z0-9]{8}$/,
  gamePath: /^\/(?:game|analyse|analysis)\/([A-Za-z0-9]{8})(?:[/?#]|$)/,
  evalComment: /\[%eval\s+(#[+-]?\d+|[+-]?\d+(?:\.\d+)?)\s*\]/,
  moveNumber: /^(?:\d+\.+|\.+)$/,
};
const RESULT_TOKENS: Set<string> = new Set(['1-0', '0-1', '1/2-1/2', '*']);
const KIND_THRESHOLDS: Record<string, number> = { blunder: 0.3, mistake: 0.15, inaccuracy: 0.07 };

type ParsedMove = { san: string; comment: string };
type EvaluatedMove = {
  ply: number;
  san: string;
  evalValue: number | null;
  mate: number | null;
  expectedScore: number | null;
};

const gameIdFromText = (value: string): string | undefined => {
  if (ANALYSE_PATTERNS.gameId.test(value)) return value;
  const match = /\/(?:game|analyse|analysis)\/([A-Za-z0-9]{8})(?:[/?#]|$)/.exec(value);
  return match ? match[1] : undefined;
};

const gameIdFromLocation = (): string | undefined => {
  const match = ANALYSE_PATTERNS.gamePath.exec(window.location.pathname);
  return match ? match[1] : undefined;
};

// Analysis boards embed their config in <script id="page-init-data" type="application/json">.
// This covers pages like /analysis where the URL does not carry a game id.
const gameIdFromPageData = (): string | undefined => {
  const element = document.getElementById('page-init-data');
  if (!element?.textContent) return undefined;
  try {
    const data = JSON.parse(element.textContent);
    const id = data?.cfg?.data?.game?.id ?? data?.data?.game?.id;
    return typeof id === 'string' && ANALYSE_PATTERNS.gameId.test(id) ? id : undefined;
  } catch {
    return undefined;
  }
};

const parseHeaders = (pgn: string): Record<string, string> => {
  const headers: Record<string, string> = {};
  const headerRegex = /^\s*\[(\w+)\s+"((?:[^"\\]|\\.)*)"\]\s*$/gm;
  for (const match of pgn.matchAll(headerRegex)) {
    headers[match[1]] = match[2].replace(/\\"/g, '"').replace(/\\\\/g, '\\');
  }
  return headers;
};

// Remove variations "( ... )" and line comments, keeping main-line comments "{ ... }".
const stripVariations = (text: string): string => {
  const kept: string[] = [];
  let depth = 0;
  let commentDepth = -1;

  for (let index = 0; index < text.length; index++) {
    const character = text[index];

    if (commentDepth >= 0) {
      if (commentDepth === 0) kept.push(character);
      if (character === '}') commentDepth = -1;
      continue;
    }

    if (character === '{') {
      commentDepth = depth;
      if (depth === 0) kept.push(character);
      continue;
    }
    if (character === '(') {
      depth += 1;
      continue;
    }
    if (character === ')') {
      if (depth > 0) depth -= 1;
      continue;
    }
    if (character === ';') {
      const newline = text.indexOf('\n', index);
      if (newline === -1) break;
      index = newline;
      continue;
    }
    if (depth === 0) kept.push(character);
  }

  return kept.join('');
};

const parseMoves = (pgn: string): ParsedMove[] => {
  const movetext = pgn
    .split(/\r?\n/)
    .filter(line => !/^\s*\[/.test(line))
    .join('\n');
  const text = stripVariations(movetext);
  const moves: ParsedMove[] = [];
  let index = 0;

  while (index < text.length) {
    const character = text[index];

    if (/\s/.test(character)) {
      index += 1;
      continue;
    }

    if (character === '{') {
      const end = text.indexOf('}', index + 1);
      const comment = text.slice(index + 1, end === -1 ? text.length : end);
      const last = moves[moves.length - 1];
      if (last) last.comment += comment;
      index = end === -1 ? text.length : end + 1;
      continue;
    }

    let end = index;
    while (end < text.length && !/[\s{};]/.test(text[end])) end += 1;
    const token = text.slice(index, end);
    index = end;

    if (token.length === 0 || ANALYSE_PATTERNS.moveNumber.test(token) || RESULT_TOKENS.has(token) || token.startsWith('$')) {
      continue;
    }

    const san = token.replace(/[!?]+$/u, '');
    if (san.length > 0) moves.push({ san, comment: '' });
  }

  return moves;
};

const parseEval = (comment: string): { cpPawns: number | null; mate: number | null } => {
  const match = ANALYSE_PATTERNS.evalComment.exec(comment);
  if (!match) return { cpPawns: null, mate: null };

  const raw = match[1];
  if (raw.startsWith('#')) {
    const mate = Number.parseInt(raw.slice(1), 10);
    return { cpPawns: null, mate: Number.isFinite(mate) ? mate : null };
  }

  const cpPawns = Number.parseFloat(raw);
  return { cpPawns: Number.isFinite(cpPawns) ? cpPawns : null, mate: null };
};

// PGN writes "#0" for a mate that has just been delivered, without a sign. The
// sign of a mate score is White POV, so recover it from the SAN. If "#0" is not
// on a mating move (e.g. the final position), treat it as unknown and let the
// previous eval stand.
const signedMate = (mate: number | null, san: string, ply: number): number | null => {
  if (mate === null || mate !== 0) return mate;
  if (!san.endsWith('#')) return null;
  return ply % 2 === 1 ? 1 : -1;
};

const rawWinningChances = (cp: number): number => 2 / (1 + Math.exp(-0.00368208 * cp)) - 1;

const expectedScore = (evalValue: number, mate: number | null): number => {
  if (mate !== null) {
    const cp = (21 - Math.min(10, Math.abs(mate))) * 100 * (mate > 0 ? 1 : -1);
    return rawWinningChances(cp);
  }
  const cp = Math.min(Math.max(-1000, evalValue * 100), 1000);
  return rawWinningChances(cp);
};

const mateAsPawns = (mate: number): number => (21 - Math.min(10, Math.abs(mate))) * (mate > 0 ? 1 : -1);

const round = (value: number, digits: number): number => {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
};

const kindOf = (delta: number): string => {
  const magnitude = Math.abs(delta);
  if (magnitude >= KIND_THRESHOLDS.blunder) return 'blunder';
  if (magnitude >= KIND_THRESHOLDS.mistake) return 'mistake';
  if (magnitude >= KIND_THRESHOLDS.inaccuracy) return 'inaccuracy';
  return 'fine';
};

const moverOfPly = (ply: number): 'white' | 'black' => (ply % 2 === 1 ? 'white' : 'black');

const gameMeta = (headers: Record<string, string>): JsonObject => {
  const tags: [string, string][] = [
    ['white', 'White'],
    ['black', 'Black'],
    ['result', 'Result'],
    ['variant', 'Variant'],
    ['opening', 'Opening'],
    ['eco', 'ECO'],
    ['date', 'Date'],
    ['event', 'Event'],
    ['timeControl', 'TimeControl'],
    ['termination', 'Termination'],
  ];
  const meta: JsonObject = {};
  for (const [key, tag] of tags) {
    const value = headers[tag];
    if (value) meta[key] = value;
  }
  return meta;
};

const ANALYSE_DESCRIPTION: string =
  'Inspect a server-analysed lichess game as structured data for answering player questions. ' +
  'Returns the requested number of turning points (topN, default 10, max 100) with ply, SAN, ' +
  'eval before/after, White-POV expected score, delta and kind. Each point carries importanceRank; ' +
  'exactly one point is flagged isBiggest. `order` defaults to "chronological" (by ply) and can be ' +
  '"importance" (|delta| descending). `mover` filters to "white" or "black". Also returns bestMove, ' +
  'the move after which the mover had the highest expected score ("best move of the game"). ' +
  'Requires server-side analysis stored on the server (client-side analysis-board evals are not ' +
  'available); if none, returns error NO_EVAL with a hint about running fishnet/server analysis. ' +
  'Does not write prose. When gameId and url are omitted it uses the game currently open in the page; ' +
  'if there is no such game it returns status skipped.';

const ANALYSE_SCHEMA: JsonObject = {
  type: 'object',
  properties: {
    gameId: {
      type: 'string',
      description: '8-character lichess game id. Optional: defaults to the game open in the current page.',
    },
    url: { type: 'string', description: 'A lichess game/analysis URL (used when gameId is absent)' },
    topN: {
      type: 'number',
      description: 'How many turning points to return (default 10, max 100). Use a larger value to inspect the whole game.',
    },
    order: {
      type: 'string',
      enum: ['chronological', 'importance'],
      description:
        'Output order. "chronological" (default) sorts by ply so the game is easy to follow; "importance" sorts by |delta| descending.',
    },
    mover: {
      type: 'string',
      enum: ['both', 'white', 'black'],
      description:
        'Only consider moves by this side (default "both"). Affects turning points and bestMove.',
    },
  },
};

const analyseGame = async (input: JsonValue | undefined): Promise<JsonObject> => {
  const record = asRecord(input);
  const urlText = asText(record.url);
  const explicitId = asText(record.gameId) ?? (urlText ? gameIdFromText(urlText) : undefined);

  if (explicitId && !ANALYSE_PATTERNS.gameId.test(explicitId)) {
    return {
      gameId: explicitId,
      hasEval: false,
      error: 'INVALID_GAME_ID',
      message: 'gameId must be exactly 8 letters or digits.',
    };
  }

  // When the caller does not name a game, use the one currently open:
  // first from the URL, then from the analysis board's page-init-data.
  const gameId = explicitId ?? gameIdFromLocation() ?? gameIdFromPageData();

  if (!gameId) {
    return {
      gameId: null,
      status: 'skipped',
      hasEval: false,
      message: 'No game is currently open. Pass gameId or url to analyse a specific game.',
    };
  }

  const rawTopN = typeof record.topN === 'number' ? record.topN : Number(asText(record.topN));
  const topN = Number.isFinite(rawTopN)
    ? Math.min(ANALYSE_LIMITS.maxTopN, Math.max(1, Math.trunc(rawTopN)))
    : ANALYSE_LIMITS.defaultTopN;
  const order: AnalyseOrder = asText(record.order) === 'importance' ? 'importance' : 'chronological';
  const moverText = asText(record.mover);
  const mover: AnalyseMover = moverText === 'white' || moverText === 'black' ? moverText : 'both';

  let response: Response;
  try {
    response = await fetch(`/game/export/${gameId}?evals=1`, {
      headers: { Accept: 'application/x-chess-pgn' },
    });
  } catch (error) {
    return {
      gameId,
      hasEval: false,
      error: 'FETCH_FAILED',
      message: error instanceof Error ? error.message : 'Could not fetch the PGN.',
    };
  }

  if (!response.ok) {
    return {
      gameId,
      hasEval: false,
      error: 'FETCH_FAILED',
      message: `GET /game/export/${gameId} returned HTTP ${response.status}.`,
    };
  }

  const pgn = await response.text();
  const headers = parseHeaders(pgn);
  const moves = parseMoves(pgn);

  if (moves.length === 0) {
    return {
      gameId,
      hasEval: false,
      error: 'EMPTY_PGN',
      message: 'The exported PGN contains no moves.',
    };
  }

  const evaluated: EvaluatedMove[] = moves.map((move, index) => {
    const { cpPawns, mate: rawMate } = parseEval(move.comment);
    const mate = signedMate(rawMate, move.san, index + 1);
    const evalValue = mate !== null ? mateAsPawns(mate) : cpPawns;
    return {
      ply: index + 1,
      san: move.san,
      evalValue,
      mate,
      expectedScore: evalValue === null ? null : expectedScore(evalValue, mate),
    };
  });

  const evaluatedPlies = evaluated.filter(move => move.expectedScore !== null).length;
  if (evaluatedPlies === 0) {
    return {
      gameId,
      hasEval: false,
      plies: moves.length,
      error: 'NO_EVAL',
      message:
        'Ván chưa có eval do server lưu. Phân tích bằng engine trong browser (analysis board) không được ghi vào DB.',
      hint: 'Cần server analysis (fishnet) hoặc một ván đã được phân tích phía server; kiểm tra PGN export có [%eval] chưa.',
      game: gameMeta(headers),
    };
  }

  const points: JsonObject[] = [];
  let previous: EvaluatedMove | null = null;

  for (const move of evaluated) {
    if (move.expectedScore === null || move.evalValue === null) continue;

    // Missing evals are bridged with the last valid sample, per the tool contract.
    if (previous && previous.expectedScore !== null && previous.evalValue !== null) {
      const delta = move.expectedScore - previous.expectedScore;
      points.push({
        ply: move.ply,
        moveNumber: Math.ceil(move.ply / 2),
        mover: moverOfPly(move.ply),
        san: move.san,
        evalBefore: round(previous.evalValue, 2),
        evalAfter: round(move.evalValue, 2),
        mateBefore: previous.mate,
        mateAfter: move.mate,
        esBefore: round(previous.expectedScore, 3),
        esAfter: round(move.expectedScore, 3),
        delta: round(delta, 3),
        kind: kindOf(delta),
      });
    }

    previous = move;
  }

  // "best move" = the move after which the mover had the highest expected score.
  // This is a different question from "biggest swing"; it answers e.g.
  // "which move was the best move of the game?".
  let bestMove: JsonObject | null = null;
  let bestScore = -Infinity;
  for (const move of evaluated) {
    if (move.expectedScore === null || move.evalValue === null) continue;
    const side = moverOfPly(move.ply);
    if (mover !== 'both' && side !== mover) continue;
    const moverScore = side === 'white' ? move.expectedScore : -move.expectedScore;
    if (moverScore > bestScore) {
      bestScore = moverScore;
      bestMove = {
        ply: move.ply,
        moveNumber: Math.ceil(move.ply / 2),
        mover: side,
        san: move.san,
        evalAfter: round(move.evalValue, 2),
        mateAfter: move.mate,
        expectedScoreForMover: round(moverScore, 3),
      };
    }
  }

  // Pick the N most important moves by |delta|, then present them in the requested
  // order. importanceRank and isBiggest are kept even when sorted chronologically.
  const ranked = points
    .filter(point => mover === 'both' || point.mover === mover)
    .sort(
      (left, right) =>
        Math.abs(Number(right.delta)) - Math.abs(Number(left.delta)) ||
        Number(left.ply) - Number(right.ply),
    )
    .slice(0, topN)
    .map((point, index): JsonObject => ({
      ...point,
      importanceRank: index + 1,
      isBiggest: index === 0,
    }));

  const turningPoints =
    order === 'chronological'
      ? ranked.sort((left, right) => Number(left.ply) - Number(right.ply))
      : ranked;

  return {
    gameId,
    hasEval: true,
    plies: moves.length,
    evaluatedPlies,
    order,
    mover,
    topN,
    bestMove,
    turningPoints,
    game: gameMeta(headers),
  };
};

// ---------------------------------------------------------------------------
// WebMCP registration + console test hook
// ---------------------------------------------------------------------------
//
// The API has changed several times during the preview:
//   - document.modelContext.registerTool(tool)      (current spec)
//   - navigator.modelContext.registerTool(tool)     (older Chrome builds)
//   - navigator.modelContext.provideTools([tools])  (older preview)
//   - navigator.modelContext.provideContext({tools})(older preview)
//
// We try every host we can find, independently, and keep the results in
// window.__ltw.debug() so a missing tool is diagnosable from the console.

type RegistrationAttempt = {
  host: string;
  api: string;
  ok: boolean;
  error: string | null;
};

const tools: ToolDef[] = [
  {
    name: 'lichess_search_games',
    description: SEARCH_DESCRIPTION,
    inputSchema: SEARCH_SCHEMA,
    execute: searchGames,
  },
  {
    name: 'lichess_analyse_game',
    description: ANALYSE_DESCRIPTION,
    inputSchema: ANALYSE_SCHEMA,
    execute: analyseGame,
  },
];

const registrationAttempts: RegistrationAttempt[] = [];
const triedHosts: any[] = [];

const errorText = (error: unknown): string => {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return 'unknown error';
};

const hostCandidates = (): [string, any][] => {
  const candidates: [string, any][] = [];
  const navHost = (navigator as any)?.modelContext;
  const docHost = (document as any)?.modelContext;
  const winHost = (window as any).modelContext;

  if (navHost) candidates.push(['navigator.modelContext', navHost]);
  if (docHost && docHost !== navHost) candidates.push(['document.modelContext', docHost]);
  if (winHost && winHost !== navHost && winHost !== docHost) candidates.push(['window.modelContext', winHost]);

  return candidates;
};

const describeHost = (host: any): string => {
  if (!host || typeof host !== 'object') return typeof host;
  const methods = ['registerTool', 'provideTools', 'provideContext', 'getTools', 'executeTool']
    .filter(method => typeof host[method] === 'function')
    .join(', ');
  return methods.length > 0 ? `object with ${methods}` : 'object without known methods';
};

const hostDiagnostics = (): Record<string, string> => {
  const diagnostics: Record<string, string> = {
    'navigator.modelContext': describeHost((navigator as any)?.modelContext),
    'document.modelContext': describeHost((document as any)?.modelContext),
    'window.modelContext': describeHost((window as any)?.modelContext),
  };
  return diagnostics;
};

const trackRegistration = (attempt: RegistrationAttempt, result: any): void => {
  if (result && typeof result.then === 'function') {
    (result as Promise<any>).then(
      () => {
        attempt.ok = true;
        attempt.error = null;
      },
      (error: Error) => {
        attempt.ok = false;
        attempt.error = errorText(error);
      },
    );
    return;
  }
  attempt.ok = true;
};

const registerHost = (name: string, host: any): void => {
  if (!host || (typeof host !== 'object' && typeof host !== 'function')) return;
  if (triedHosts.includes(host)) return;
  triedHosts.push(host);

  const attempt: RegistrationAttempt = { host: name, api: 'none', ok: false, error: null };
  registrationAttempts.push(attempt);

  try {
    if (typeof host.registerTool === 'function') {
      attempt.api = 'registerTool';
      for (const tool of tools) {
        try {
          trackRegistration(attempt, host.registerTool(tool));
        } catch (error) {
          attempt.ok = false;
          attempt.error = errorText(error);
        }
      }
      return;
    }

    if (typeof host.provideTools === 'function') {
      attempt.api = 'provideTools';
      trackRegistration(attempt, host.provideTools(tools));
      return;
    }

    if (typeof host.provideContext === 'function') {
      attempt.api = 'provideContext';
      trackRegistration(attempt, host.provideContext({ tools }));
      return;
    }

    attempt.error = 'host has no registerTool / provideTools / provideContext';
  } catch (error) {
    attempt.error = errorText(error);
  }
};

const registerWithHosts = (): RegistrationAttempt[] => {
  try {
    for (const [name, host] of hostCandidates()) registerHost(name, host);
  } catch {
    // WebMCP is an early preview; never break the page because its API changed.
  }
  return registrationAttempts;
};

const browserTools = async (): Promise<JsonValue> => {
  const hosts = hostCandidates();
  for (const [name, host] of hosts) {
    if (typeof host.getTools === 'function') {
      try {
        const found = await host.getTools();
        if (!Array.isArray(found)) return { host: name, tools: found ?? null };
        const toolsJson = found.map((tool: any) => ({
          name: tool?.name ?? null,
          description: tool?.description ?? null,
          inputSchema: tool?.inputSchema ?? null,
        }));
        return { host: name, tools: toolsJson };
      } catch (error) {
        return { host: name, error: errorText(error) };
      }
    }
  }
  return null;
};

(window as any).__ltw = {
  searchGames,
  analyseGame,
  tools,
  debug: hostDiagnostics,
  attempts: registrationAttempts,
  register: registerWithHosts,
  browserTools,
};

// If a search was requested from a page without the search form, we navigated here
// and saved it in sessionStorage. Run it once the form is available.
void (async () => {
  const runPending = async (): Promise<void> => {
    if (window.location.pathname !== '/games/search') return;
    if (!document.querySelector('form.search__form')) return;
    const pending = takePendingSearch();
    if (!pending || Object.keys(pending).length === 0) return;
    try {
      await searchGames(pending);
    } catch {
      // Never break page load because of a pending search.
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => void runPending(), { once: true });
  } else {
    await runPending();
  }
})();

registerWithHosts();
