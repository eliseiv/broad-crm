import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';
import { describe, expect, it } from 'vitest';
import baseConfig from '../../../../tailwind.config';

/**
 * Контракт/регресс токенов дизайн-системы (ADR-064).
 *
 * jsdom не компилирует Tailwind, поэтому проверяем НАСТОЯЩИЙ вывод компилятора: берём
 * реальный `tailwind.config.ts` (его определения токенов — предмет проверки), подменяем
 * только `content` строкой классов, и ассертим сгенерированный CSS. Так тест ловит именно
 * тот класс дефекта, что был у пользователя: `bg-status-red/90` на var-цвете без channel-
 * формата давал невалидный `rgb(#DC2626 / 0.9)` → свойство «тихо» отбрасывалось →
 * прозрачная (невидимая) danger-кнопка.
 */

/** Компилирует утилиты Tailwind реальным конфигом проекта против набора классов. */
async function compileUtilities(classes: string[]): Promise<string> {
  const config = {
    ...baseConfig,
    content: [{ raw: `<div class="${classes.join(' ')}"></div>`, extension: 'html' }],
    corePlugins: { preflight: false },
  };
  const result = await postcss([tailwindcss(config)]).process('@tailwind utilities;', {
    from: undefined,
  });
  return result.css;
}

/** 15 конвертируемых токенов §B (ADR-064) — единственные, что проходят через `colors`. */
const CHANNEL_TOKENS = [
  'bg-base',
  'surface-1',
  'surface-2',
  'surface-3',
  'border-subtle',
  'border-strong',
  'text-primary',
  'text-secondary',
  'text-tertiary',
  'accent',
  'accent-hover',
  'status-green',
  'status-yellow',
  'status-red',
  'gauge-track',
] as const;

describe('Button danger — регресс невидимой кнопки (ADR-064 §D)', () => {
  it('вариант danger (bg-status-red/90) компилируется в непрозрачный rgb(var(--status-red) / 0.9), а не в отсутствующий фон', async () => {
    const css = await compileUtilities(['bg-status-red/90']);

    // Утилита сгенерирована и её background-color присутствует (не отброшена как невалидная).
    expect(css).toMatch(/\.bg-status-red\\\/90\s*\{/);
    expect(css).toMatch(/background-color:\s*rgb\(var\(--status-red\)\s*\/\s*0?\.9\)/);

    // Ключевой инвариант фикса: НЕТ невалидного `rgb(#...)` (симптом старого формата).
    expect(css).not.toContain('rgb(#');
  });

  it('сплошной bg-status-red (без альфы) продолжает работать: rgb(var(--status-red) / <opacity>=1)', async () => {
    const css = await compileUtilities(['bg-status-red']);
    expect(css).toMatch(
      /background-color:\s*rgb\(var\(--status-red\)\s*\/\s*var\(--tw-bg-opacity\)\)/,
    );
    expect(css).not.toContain('rgb(#');
  });

  it('латентный primary-disabled (disabled:bg-accent/60) теперь тоже даёт видимый фон', async () => {
    const css = await compileUtilities(['disabled:bg-accent/60']);
    expect(css).toMatch(/background-color:\s*rgb\(var\(--accent\)\s*\/\s*0?\.6\)/);
    expect(css).not.toContain('rgb(#');
  });
});

describe('Channel-формат токенов — smoke компиляции (ADR-064 §B/§C)', () => {
  it('alpha-модификаторы всех 15 токенов компилируются в rgb(var(--x) / α), без невалидного rgb(#...)', async () => {
    // Репрезентативные alpha-места из §Контекст ADR-064 + по одному alpha-классу на токен.
    const alphaClasses = [
      'bg-status-red/90',
      'bg-status-red/10',
      'border-status-red/40',
      'bg-status-yellow/5',
      'bg-accent/15',
      'ring-accent/40',
      'bg-surface-1/40',
      'bg-bg-base/80',
      'bg-surface-2/50',
      'bg-surface-3/50',
      'border-border-subtle/70',
      'border-border-strong/70',
      'text-text-primary/80',
      'text-text-secondary/80',
      'text-text-tertiary/80',
      'bg-accent-hover/50',
      'bg-status-green/10',
      'bg-gauge-track/50',
    ];
    const css = await compileUtilities(alphaClasses);

    // Нигде не просочился HEX внутрь rgb()/color-функции — корневой симптом бага.
    expect(css).not.toContain('rgb(#');
    expect(css).not.toContain('(#');
    // Каждый токен реально попал в вывод обёрнутым в rgb(var(--x) / …).
    for (const cls of alphaClasses) {
      const token = cls.replace(/^(bg|text|border|ring)-/, '').replace(/\/\d+$/, '');
      expect(css).toContain(`rgb(var(--${token})`);
    }
  });
});

describe('index.css — токены заданы триплетами в обеих темах (ADR-064 §B)', () => {
  // vitest запускается из каталога frontend (cwd) — index.css лежит в src/.
  const indexCss = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

  it.each(CHANNEL_TOKENS)(
    'токен --%s определён space-separated RGB-триплетом ровно дважды (light + dark)',
    (token) => {
      // Триплет: три канала 0–255, разделённые пробелами, без #, без запятых, без rgb().
      const re = new RegExp(`--${token}:\\s*\\d{1,3} \\d{1,3} \\d{1,3};`, 'g');
      const matches = indexCss.match(re) ?? [];
      // Две записи: :root/[data-theme='light'] и [data-theme='dark'].
      expect(matches).toHaveLength(2);
    },
  );

  it('ни один из 15 конвертируемых токенов не задан как HEX (#RRGGBB)', () => {
    for (const token of CHANNEL_TOKENS) {
      const hexRe = new RegExp(`--${token}:\\s*#`, 'g');
      expect(indexCss).not.toMatch(hexRe);
    }
  });
});
