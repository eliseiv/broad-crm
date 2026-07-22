import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { Check, ChevronDown, Copy, Eye, EyeOff, Loader2, Pencil } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/cn';
import type { SecretRevealResponse } from '@/types/api';

/**
 * Карандаш-кнопка в шапке detail-модалки (08-design-system.md, ADR-035): закрывает
 * detail и открывает существующую edit-модалку. Рендерится ТОЛЬКО при `<page>:edit`.
 */
export function DetailEditPencil({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Редактировать"
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-surface-3 hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <Pencil className="h-4 w-4" aria-hidden="true" />
    </button>
  );
}

/**
 * Строка read-only поля detail-модалки (08-design-system.md «Detail-view карточных
 * страниц»): label вторичным цветом + значение. Моношрифт — для технических значений
 * (IP/хост/домен/маска ключа).
 *
 * **Пустое значение (`null` / `undefined` / пустая строка) → строка НЕ рендерится вовсе**
 * (08-design-system.md «Пустые поля не рендерятся», ADR-046 §3). Прочерк «—» в detail-view
 * упразднён. Правило действует только в detail-view — таблицы сохраняют плейсхолдеры.
 */
export function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string' && value.trim() === '') return null;

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
        {label}
      </span>
      <span className={cn('break-words text-sm text-text-primary', mono && 'font-mono')}>
        {value}
      </span>
    </div>
  );
}

/**
 * Сворачиваемый блок **«Информация»** (08-design-system.md «Состав detail-view», ADR-046 §2в;
 * места — ADR-049): свёрнут по умолчанию, содержимое монтируется только при раскрытии. Паттерн
 * сворачивания — тот же, что у секции «Бэки» (`BackendsDetailSection`): кнопка-триггер,
 * `aria-expanded`/`aria-controls`, `ChevronDown` с поворотом на 180° при раскрытии.
 *
 * **Где живёт (ADR-049):** в detail-модалке — только у **ИИ-ключа** и **Прокси**; у **бэка** —
 * **на карточке `BackendCard`** (detail-модалка упразднена, §3); у **сервера** — **упразднён**
 * (креды подняты в главный блок, §1).
 *
 * Блок не рендерится, если внутри не осталось ни одной строки и ни одной секции —
 * это решает вызывающий компонент (`{hasInfo && <DetailInfoSection>…}`).
 */
export function DetailInfoSection({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <div className="rounded-sub border border-border-subtle">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
      >
        <span className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
          Информация
        </span>
        <ChevronDown
          className={cn('h-4 w-4 text-text-tertiary transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div id={id} className="flex flex-col gap-4 border-t border-border-subtle px-3 py-3">
          {children}
        </div>
      )}
    </div>
  );
}

const MASK = '••••••••';

/**
 * **Не-раскрываемый секрет** detail-view (08-design-system.md «Reveal секрета» ⛔ ИСКЛЮЧЕНИЕ,
 * ADR-067 §4/§6): у секрета нет reveal-эндпоинта **by design**, поэтому кнопка-глаз не
 * рендерится **ни при каком праве**, включая `<page>:edit` и супер-админа. Маска здесь
 * означает «материал задан», а не «нажми, чтобы увидеть».
 *
 * Поле — **статический текст**: без `role="button"`, без `tabIndex`, без focus-ring (иначе
 * a11y-дефект «фокусируемый элемент без действия»). Действующий состав — сервер при
 * `auth_method='key'` (строка «SSH-ключ»: приватный ключ и парольная фраза write-only).
 */
export function SecretStaticField({
  label,
  maskDisplay = MASK,
}: {
  label: string;
  maskDisplay?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
        {label}
      </span>
      <span className="break-all font-mono text-sm leading-8 text-text-primary">{maskDisplay}</span>
    </div>
  );
}

interface SecretRevealFieldProps {
  /** Метка поля («Пароль» / «Ключ»). */
  label: string;
  /**
   * On-demand запрос секрета к reveal-эндпоинту (ADR-035). Значение НЕ кэшируется в
   * react-query/сторе — живёт ТОЛЬКО в локальном стейте этого компонента и стирается
   * при скрытии/размонтировании (закрытии модалки).
   */
  reveal: (signal?: AbortSignal) => Promise<SecretRevealResponse>;
  /** aria-label кнопки-глаза для «показать» (словарь 08-design-system.md). */
  showAria: string;
  /** aria-label кнопки-глаза для «скрыть». */
  hideAria: string;
  /**
   * Показывать ли кнопку-глаз (гейт `<page>:edit`; для прокси — доп. `has_password`).
   * `false` → статичная маска без возможности reveal (просмотр без edit).
   */
  canReveal: boolean;
  /**
   * Что показывать в скрытом состоянии. По умолчанию `••••••••` (пароли, где маски нет).
   * Для ИИ-ключа передаётся `key_masked` (напр. `sk-p…bA3T`) — «Ключ»-поле detail-view
   * (08-design-system.md: поле `Ключ` = `key_masked`, reveal раскрывает полное значение).
   */
  maskDisplay?: string;
}

/**
 * Секретное поле detail-view с reveal по требованию (08-design-system.md, ADR-035):
 * показано как `••••••••`; кнопка-глаз делает on-demand GET к reveal-эндпоинту и
 * показывает значение (моношрифт), повторный клик — снова маска. Значимый контент
 * виден полностью (`break-all`, не усекается). Ошибка reveal → toast «Не удалось показать».
 * Значение не логируется и не кэшируется глобально.
 */
export function SecretRevealField({
  label,
  reveal,
  showAria,
  hideAria,
  canReveal,
  maskDisplay = MASK,
}: SecretRevealFieldProps) {
  const [value, setValue] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const shown = value !== null;

  // Без права reveal (нет edit / нет пароля) — статичная маска, без кнопок-глаза/копирования.
  if (!canReveal) {
    return (
      <div className="flex flex-col gap-1">
        <span className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
          {label}
        </span>
        <span className="break-all font-mono text-sm leading-8 text-text-primary">
          {maskDisplay}
        </span>
      </div>
    );
  }

  const handleToggle = async () => {
    if (shown) {
      // Скрыть — стираем значение из локального стейта (не держим plaintext).
      setValue(null);
      setCopied(false);
      return;
    }
    setLoading(true);
    try {
      const res = await reveal();
      setValue(res.value);
    } catch {
      toast.error('Не удалось показать');
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async () => {
    if (value === null) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success('Скопировано');
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error('Не удалось скопировать');
    }
  };

  const iconBtn =
    'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-surface-3 hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50';

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
        {label}
      </span>
      <div className="flex items-start gap-1">
        <span
          className="min-w-0 flex-1 break-all font-mono text-sm leading-8 text-text-primary"
          aria-live="polite"
        >
          {shown ? value : maskDisplay}
        </span>
        {shown && (
          <button type="button" onClick={handleCopy} aria-label="Скопировать" className={iconBtn}>
            {copied ? (
              <Check className="h-4 w-4 text-status-green" aria-hidden="true" />
            ) : (
              <Copy className="h-4 w-4" aria-hidden="true" />
            )}
          </button>
        )}
        <button
          type="button"
          onClick={handleToggle}
          disabled={loading}
          aria-label={shown ? hideAria : showAria}
          className={iconBtn}
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : shown ? (
            <EyeOff className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Eye className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      </div>
    </div>
  );
}
