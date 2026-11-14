import { hl, bind, type LooseVNode } from 'lib/view';

import { commentaryVoices, type CommentaryCtrl } from '../commentary';

export function commentaryView(ctrl?: CommentaryCtrl): LooseVNode {
  if (!ctrl) return undefined;
  return hl(
    'section.commentary',
    { attrs: { 'aria-label': 'Bình luận viên tiếng Việt', 'data-state': ctrl.enabled ? ctrl.phase : 'off' } },
    [
      hl(
        'button.button.button-empty.commentary__toggle',
        {
          attrs: {
            type: 'button',
            'aria-pressed': String(ctrl.enabled),
          },
          hook: bind('click', ctrl.toggle),
        },
        ctrl.enabled ? 'Tắt bình luận tiếng Việt' : 'Bật bình luận tiếng Việt',
      ),
      ctrl.enabled && [
        hl('label.commentary__voice', [
          'Giọng đọc',
          hl(
            'select',
            { hook: bind('change', e => ctrl.setVoice((e.target as HTMLSelectElement).value)) },
            commentaryVoices.map(voice =>
              hl('option', { attrs: { value: voice.id, selected: voice.id === ctrl.voice } }, voice.name),
            ),
          ),
        ]),
        // Polite: a status change never interrupts a screen reader mid-sentence.
        hl('div.commentary__status', { attrs: { role: 'status', 'aria-live': 'polite' } }, ctrl.status),
        ctrl.subtitle &&
          hl('p.commentary__subtitle', { attrs: { lang: 'vi', 'data-old': String(ctrl.subtitleOld) } }, [
            subtitleLabel(ctrl) && hl('span.commentary__ply', subtitleLabel(ctrl)),
            ctrl.subtitle,
          ]),
      ],
    ],
  );
}

/** Which move the subtitle is about: "Nước 12 · Đen", or "Đang chờ" for a remark while nobody moves. */
export function subtitleLabel(ctrl: CommentaryCtrl): string | undefined {
  const ply = ctrl.subtitlePly;
  if (ctrl.subtitleKind === 'idle') return 'Đang chờ';
  if (ctrl.subtitleKind !== 'move' || !ply) return undefined;
  return `Nước ${Math.ceil(ply / 2)} · ${ply % 2 ? 'Trắng' : 'Đen'}`;
}
