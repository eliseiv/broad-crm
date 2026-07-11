import type { Theme } from '@/lib/theme';

/**
 * ЕДИНЫЙ источник srcDoc тела письма (ADR-047 §6, нормативно): билдер обязан быть ОДИН —
 * его импортируют и `MailDetail` (админ-SPA `/mail`), и `MailMiniAppPage` (`/tg/mail`).
 * Дублирование билдера в двух файлах запрещено — именно оно и породило фикс, применённый
 * в одном месте и забытый в другом.
 *
 * **Тело письма следует теме** (хардкод тёмного фона снят). Iframe — собственный документ,
 * CSS-переменные родителя в него НЕ наследуются, поэтому цвета подставляются литералами,
 * синхронизированными с токенами `index.css` (08-design-system.md «Цветовые токены»):
 *
 * | Тема    | background            | color                    |
 * |---------|-----------------------|--------------------------|
 * | `dark`  | `#161A22` (--surface-2) | `#E6E9EF` (--text-primary) |
 * | `light` | `#F7F8FA` (--surface-2) | `#111827` (--text-primary) |
 *
 * **Инвариант изоляции НЕ ослабляется** (ADR-012/ADR-015): вызывающий рендерит iframe с
 * `sandbox=""` (без `allow-scripts` и `allow-same-origin`) и `referrerPolicy="no-referrer"`;
 * инъекция — чисто стилевая (`<style>` перед недоверенным телом), best-effort для писем с
 * собственным фоном.
 */
const BODY_COLORS: Record<Theme, { background: string; color: string }> = {
  dark: { background: '#161A22', color: '#E6E9EF' },
  light: { background: '#F7F8FA', color: '#111827' },
};

export function buildMailBodySrcDoc(bodyHtml: string, theme: Theme): string {
  const { background, color } = BODY_COLORS[theme];
  return `<style>html,body{background:${background};color:${color};margin:0;padding:12px}</style>${bodyHtml}`;
}
