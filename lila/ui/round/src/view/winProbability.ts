import { hl, type LooseVNode } from 'lib/view/snabbdom';

import type RoundController from '../ctrl';
import { wdlPercentages } from '../winProbability';

export default function winProbability(ctrl: RoundController): LooseVNode {
  if (!ctrl.winProbability) return undefined;
  const prediction = ctrl.winProbability.data;
  const replay = ctrl.replaying();
  const ready = !replay && (prediction?.status === 'ready' || prediction?.status === 'finished');
  const human =
    ready && prediction.source === 'engine' && prediction.human?.status === 'ready'
      ? prediction.human
      : undefined;
  const p = ready && (human?.probabilities ?? prediction.probabilities);
  const values = p && wdlPercentages(p);
  const source = prediction?.source === 'official' ? 'official' : human?.probabilities ? 'human' : 'engine';
  const label =
    source === 'official'
      ? 'Kết quả chính thức'
      : source === 'human'
        ? 'Người chơi · thử nghiệm'
        : 'Ước tính từ thế cờ';
  const description =
    source === 'official'
      ? 'Kết quả ván đấu đã được xác nhận.'
      : source === 'human'
        ? 'Ước tính từ thế cờ, rating và đồng hồ. Mô hình thử nghiệm, chưa hiệu chỉnh cho cộng đồng máy chủ này.'
        : 'Xác suất theo engine từ thế cờ; không xét rating và đồng hồ của người chơi.';
  const status = replay
    ? 'Trở về nước mới nhất để xem xác suất.'
    : prediction?.status === 'stale'
      ? 'Mất kết nối · đang chờ thế cờ mới nhất.'
      : prediction?.status === 'aborted'
        ? 'Ván đã hủy · không có kết quả.'
        : prediction?.status === 'unavailable'
          ? 'Tạm thời chưa có đánh giá.'
          : prediction?.status === 'busy'
            ? 'Đang chờ đánh giá…'
            : 'Đang tính…';
  return hl(
    'section.win-probability',
    {
      attrs: {
        'aria-label': 'Xác suất thắng, hòa, thua',
        'data-state': replay ? 'replay' : (prediction?.status ?? 'pending'),
        'data-source': values ? source : false,
        'data-model': values && human ? (human.model_id ?? false) : false,
      },
    },
    [
      values
        ? [
            hl(
              'div.win-probability__bar',
              { attrs: { 'aria-hidden': 'true' } },
              ['white', 'draw', 'black'].map((color, i) =>
                hl(`span.win-probability__${color}`, {
                  attrs: { style: `width:${values[i]}%` },
                }),
              ),
            ),
            hl(
              'div.win-probability__values',
              { attrs: { 'aria-live': 'polite' } },
              ['Trắng', 'Hòa', 'Đen'].map((color, i) => hl('span', `${color} ${values[i].toFixed(1)}%`)),
            ),
            hl('div.win-probability__source', { attrs: { title: description } }, label),
          ]
        : hl('div.win-probability__status', { attrs: { role: 'status' } }, status),
    ],
  );
}
