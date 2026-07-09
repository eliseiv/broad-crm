import { useState } from 'react';
import type { ReactNode } from 'react';
import { Check, Copy, Eye, EyeOff, Loader2, Pencil } from 'lucide-react';
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

const MASK = '••••••••';

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
