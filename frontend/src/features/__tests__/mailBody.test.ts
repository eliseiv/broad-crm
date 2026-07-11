import { describe, expect, it } from 'vitest';
import { buildMailBodySrcDoc } from '@/features/mail/mailBody';

/**
 * ЕДИНЫЙ билдер srcDoc тела письма (ADR-047 §6, нормативно): один модуль на оба
 * потребителя — `MailDetail` (админ-SPA `/mail`) и `MailMiniAppPage` (`/tg/mail`).
 * Дублирование билдера запрещено — именно оно и породило фикс, применённый в одном месте
 * и забытый в другом.
 *
 * Тело письма СЛЕДУЕТ ТЕМЕ (хардкод тёмного фона снят). Iframe — собственный документ,
 * CSS-переменные родителя в него не наследуются → цвета подставляются литералами,
 * синхронизированными с токенами index.css (08-design-system.md «Цветовые токены»):
 *   dark  → background #161A22 (--surface-2), color #E6E9EF (--text-primary)
 *   light → background #F7F8FA (--surface-2), color #111827 (--text-primary)
 */
describe('buildMailBodySrcDoc — тело письма следует теме (ADR-047 §6)', () => {
  const BODY = '<p>Привет</p>';

  it('dark: подставляет тёмные литералы токенов', () => {
    const srcDoc = buildMailBodySrcDoc(BODY, 'dark');

    expect(srcDoc).toContain('background:#161A22');
    expect(srcDoc).toContain('color:#E6E9EF');
    expect(srcDoc).not.toContain('#F7F8FA');
    expect(srcDoc).not.toContain('#111827');
  });

  it('light: подставляет светлые литералы токенов (прежний тёмный хардкод снят)', () => {
    const srcDoc = buildMailBodySrcDoc(BODY, 'light');

    expect(srcDoc).toContain('background:#F7F8FA');
    expect(srcDoc).toContain('color:#111827');
    // Безусловный тёмный фон больше НЕ подставляется — это и был баг фикса 10.
    expect(srcDoc).not.toContain('#161A22');
    expect(srcDoc).not.toContain('#E6E9EF');
  });

  it('стилевая инъекция стоит ПЕРЕД недоверенным телом письма (в обеих темах)', () => {
    for (const theme of ['dark', 'light'] as const) {
      const srcDoc = buildMailBodySrcDoc(BODY, theme);
      expect(srcDoc.indexOf('<style>')).toBe(0);
      expect(srcDoc.indexOf('</style>')).toBeLessThan(srcDoc.indexOf(BODY));
      expect(srcDoc).toContain(BODY);
    }
  });

  it('инъекция чисто стилевая: ни скриптов, ни ослабления изоляции', () => {
    const srcDoc = buildMailBodySrcDoc(BODY, 'light');

    expect(srcDoc).not.toContain('<script');
    expect(srcDoc).not.toContain('allow-scripts');
    expect(srcDoc).not.toContain('allow-same-origin');
  });

  it('тело письма не экранируется и не переписывается билдером (передаётся как есть)', () => {
    const raw = '<div style="background:#fff">письмо<img src="https://x/y.png"></div>';
    const srcDoc = buildMailBodySrcDoc(raw, 'dark');

    expect(srcDoc.endsWith(raw)).toBe(true);
  });

  it('смена темы даёт РАЗНЫЙ srcDoc (iframe перерисуется)', () => {
    expect(buildMailBodySrcDoc(BODY, 'dark')).not.toBe(buildMailBodySrcDoc(BODY, 'light'));
  });
});
