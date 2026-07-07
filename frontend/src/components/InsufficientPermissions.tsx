import { ShieldAlert } from 'lucide-react';

/**
 * Нормативные строки заглушек «Недостаточно прав» — ЕДИНЫЙ источник в коде
 * (08-design-system.md «Заглушки „Недостаточно прав“ — ЕДИНЫЙ источник»,
 * ADR-021 §6). Обе заглушки — общий заголовок; различаются только подсказкой:
 *  - `page`   — отказ по КОНКРЕТНОЙ странице (page-level view-guard / AdminRoute):
 *               доступ к другим разделам у пользователя может быть;
 *  - `global` — НЕТ ни одного `view` (дефолт-маршрут после логина, index `/`,
 *               fallback `*`).
 */
export const INSUFFICIENT_PERMISSIONS_TITLE = 'Недостаточно прав';

export const NO_SECTION_ACCESS_HINT =
  'У вашей учётной записи нет доступа к этому разделу. Обратитесь к администратору.';

export const NO_ANY_SECTION_HINT =
  'У вашей учётной записи нет доступа ни к одному разделу. Обратитесь к администратору.';

/**
 * Страница-заглушка «Недостаточно прав» (page-level view-guard, `AdminRoute`,
 * дефолт-маршрут). UI-гейт — только UX; граница безопасности — серверный `403`
 * (ADR-021). Сессия НЕ сбрасывается, редиректа на `/login` нет.
 */
export function InsufficientPermissions({ scope = 'page' }: { scope?: 'page' | 'global' }) {
  const hint = scope === 'global' ? NO_ANY_SECTION_HINT : NO_SECTION_ACCESS_HINT;
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
      <ShieldAlert className="h-10 w-10 text-text-tertiary" aria-hidden="true" />
      <div className="max-w-md">
        <p className="text-lg font-semibold text-text-primary">{INSUFFICIENT_PERMISSIONS_TITLE}</p>
        <p className="mt-1 text-[13px] text-text-secondary">{hint}</p>
      </div>
    </div>
  );
}
