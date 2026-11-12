import { hl, bind, type LooseVNode } from 'lib/view';

import type { CommentaryCtrl } from '../commentary';

export function commentaryView(ctrl?: CommentaryCtrl): LooseVNode {
  if (!ctrl) return undefined;
  return hl('section.commentary', { attrs: { 'aria-label': 'Bình luận viên tiếng Việt' } }, [
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
  ]);
}
