/*
 * No-FOUC-скрипт темы (08-design-system.md «Темизация», ADR-046 §4.1).
 *
 * ОТДЕЛЬНЫЙ СТАТИЧЕСКИЙ ФАЙЛ своего origin (не inline!): прод-CSP несёт
 * `script-src 'self'` БЕЗ 'unsafe-inline'/nonce/hash — inline-скрипт браузер не
 * исполняет, `data-theme` не проставляется, и страница детерминированно уходит в
 * тёмную. CSP при этом НЕ ослабляется (05-security.md) — меняется только способ
 * подключения. Подключается синхронно в <head> ДО бандла.
 *
 * Логика: сохранённый выбор `localStorage['crm-theme']` ∈ {light,dark} → иначе `light`
 * (дефолт ADR-041; prefers-color-scheme не участвует). Дублирует resolveTheme()/
 * applyTheme() из src/lib/theme.ts (там же — self-heal, ADR-046 §4.3).
 */
(function () {
  var t;
  try {
    t = localStorage.getItem('crm-theme');
    if (t !== 'light' && t !== 'dark') {
      t = 'light';
    }
  } catch (e) {
    t = 'light';
  }
  document.documentElement.dataset.theme = t;
  // theme-color (браузерный chrome) следует теме — значения bg-base из 08-design-system.
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', t === 'light' ? '#F2F4F7' : '#0A0C10');
})();
