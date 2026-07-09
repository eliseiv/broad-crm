import type { MailTagMatchMode, MailTagRule, MailTagRuleType } from '@/types/api';

/**
 * Палитра цвета тега (08-design-system.md «Вкладка Теги», нормативно) — ровно 8
 * фиксированных цветов (совпадают с PALETTE_COLORS агрегатора). Свотч-селектор предлагает
 * только их; произвольный HEX запрещён (backend/агрегатор отвергнут).
 */
export const TAG_PALETTE: { hex: string; name: string }[] = [
  { hex: '#2563eb', name: 'синий' },
  { hex: '#dc2626', name: 'красный' },
  { hex: '#f59e0b', name: 'янтарный' },
  { hex: '#16a34a', name: 'зелёный' },
  { hex: '#7c3aed', name: 'фиолетовый' },
  { hex: '#0891b2', name: 'бирюзовый' },
  { hex: '#db2777', name: 'розовый' },
  { hex: '#475569', name: 'серый' },
];

/** Человекочитаемые подписи типов правил (08-design-system.md, нормативно). */
const RULE_TYPE_LABEL: Record<MailTagRuleType, string> = {
  subject_contains: 'Тема содержит',
  body_contains: 'Тело содержит',
  sender_contains: 'Отправитель содержит',
  sender_exact: 'Отправитель равен',
};

/** Опции Select типа правила (порядок — как в словаре docs). */
export const RULE_TYPE_OPTIONS: { value: MailTagRuleType; label: string }[] = (
  ['subject_contains', 'body_contains', 'sender_contains', 'sender_exact'] as MailTagRuleType[]
).map((value) => ({ value, label: RULE_TYPE_LABEL[value] }));

/** Полная строка правила: `<подпись типа> «<pattern>»` (08-design-system.md). */
export function ruleLabel(rule: MailTagRule): string {
  return `${RULE_TYPE_LABEL[rule.type]} «${rule.pattern}»`;
}

/** Подпись режима тега: «любое правило» (any) / «все правила» (all). */
export function matchModeLabel(mode: MailTagMatchMode): string {
  return mode === 'all' ? 'все правила' : 'любое правило';
}
