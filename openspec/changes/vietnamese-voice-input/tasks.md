# Vietnamese voice input

## Scope and status

Feasibility review requested on 2026-11-06. Reuse Lichess's browser-based Vosk
recognizer and chess-command parser for an experimental Vietnamese input mode.
This review does not establish microphone recognition accuracy or implement the
feature. Keep the win-probability services and existing patch unchanged.

## Investigation completed

- [x] Inspect the pinned Lila checkout at
  `4eea97abce686a1cc6b07abe54cce9e10c223227` and compare the language gate with
  upstream source. `ui/voice/src/languages.ts` exposes en/fr/pl normally and adds
  vi only inside `if (site.debug)`. Do not enable global debug to ship Vietnamese.
- [x] Inspect the language assets. `ui/voice/lexicon/move-vi-lex.json` has 77
  entries; generated `ui/voice/grammar/move-vi.json` has 106 entries. The compiled
  grammar exists and its SHA256 matches the source grammar. Entries cover piece
  names, coordinates, captures, castling, promotion, confirmation and cancellation.
- [x] Trace audio and move processing through `ui/voice/src/mic.ts`,
  `voice.vosk.ts`, and `move/voice.move.ts`: microphone audio enters browser Vosk;
  recognized text is matched against chess commands and current legal moves;
  existing ambiguity/confirmation controls feed the regular move controller.
  Browser audio processing does not require an LLM service.
- [x] Check local model availability. The configured Vietnamese path is
  `public/lifat/vosk/model-vi-0.4.tar.gz`, but the file is absent. Existing en/fr/pl
  files are 133-byte Git LFS pointers, not model archives. Vosk JS/WASM files are
  present. Making Vietnamese visible alone cannot provide a working local model.
- [x] Verify an upstream model source. Vosk lists `vosk-model-small-vn-0.4` as
  32 MB, Apache 2.0. Its published 15.70 word error rate on Vivos is not a chess
  command success rate or evidence about this browser integration. Download,
  browser-compatible repackaging and load verification remain to be done.
- [x] Inspect access and input controls. Board-menu voice input requires a signed-in
  player; spectator input is disabled. Push-to-talk, multiple candidate moves,
  and move confirmation already exist. Their Vietnamese end-to-end behavior has
  not been verified.

Evidence: PowerShell source/asset audit completed with exit 0 on 2026-11-06;
`Test-Path` returned false for the Vietnamese archive and true for the compiled
grammar; source and compiled grammar hashes matched. No audio recordings,
microphone/browser flow, accuracy benchmark, frontend test run or build was
performed for this feasibility review. No running services were changed.

## Proposed first implementation

- [ ] Add a reproducible model download/repack step with source URL, SHA256,
  license and archive-layout validation. Load only when voice input is enabled;
  retain the existing IndexedDB cache and test subsequent loads.
- [ ] Expose Vietnamese explicitly as experimental without enabling all debug
  languages. Preserve the language preference and existing microphone controls.
- [ ] Establish documented short commands for coordinates, piece moves, captures,
  both castlings, promotion, confirmation and cancellation. Verify Vietnamese
  letter pronunciations and spelling variants against real model output before
  adding aliases; regenerate grammar from its lexicon instead of editing output.
- [ ] Prefer an explicit confirmation flow for the first pilot. Show the heard
  command and intended move; ambiguous commands require a choice. Reject illegal,
  out-of-turn and obsolete input after moves, takebacks, reconnects or game end.
- [ ] Add focused regression tests for parsing and move submission, then retain
  the required upstream UI checks. Do not label transcript-only tests as ASR tests.
- [ ] Exercise the browser with real Vietnamese recordings and live microphone
  input, including several speakers/accents, background noise, denied permission,
  missing/corrupt model, repeated start/stop and language switching. Record exact
  move accuracy, accidental submissions, latency and browser memory; never infer
  these from the model size or published Vivos benchmark.
- [ ] Preserve implementation changes as a separate reproducible feature patch,
  publish a local player demo and report results and remaining limitations.

## Sources

- Language visibility: https://github.com/lichess-org/lila/blob/4eea97abce686a1cc6b07abe54cce9e10c223227/ui/voice/src/languages.ts
- Vietnamese lexicon: https://github.com/lichess-org/lila/blob/4eea97abce686a1cc6b07abe54cce9e10c223227/ui/voice/lexicon/move-vi-lex.json
- Model source and published benchmark: https://alphacephei.com/vosk/models
- Upstream recognizer: https://github.com/lichess-org/lila/blob/4eea97abce686a1cc6b07abe54cce9e10c223227/ui/voice/src/voice.vosk.ts
