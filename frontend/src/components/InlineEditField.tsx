import { useState } from 'react';
import type { KeyboardEvent } from 'react';
import { Check, Pencil, X } from 'lucide-react';
import { Input } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Spinner';
import { Textarea } from '@/components/ui/Textarea';
import { cn } from '@/lib/cn';

interface InlineEditFieldProps {
  /** Текущее значение (`null`/пусто → «—»). */
  value: string | null;
  /** Подпись для aria (напр. «Логин»). */
  label: string;
  /** Право на правку (useCan('sms','edit')). Без права — только просмотр, без карандаша. */
  canEdit: boolean;
  /** Сохранение: новое значение (пустая строка → затирание NULL на сервере). */
  onSave: (next: string) => void;
  saving?: boolean;
  /** Многострочное поле (для «Примечание») — Textarea вместо Input. */
  multiline?: boolean;
}

/**
 * Инлайн-редактируемое поле ячейки таблицы номеров (08-design-system.md «Вкладка
 * Номера»): просмотр `значение / «—»` + карандаш → `Input`/`Textarea` + `Check`/`X`.
 * Enter (в Input) — сохранить, Esc — отмена. Пустое значение сохраняется как затирание
 * (presence-семантика PATCH, 04-api.md). Значимое значение не усекается — переносится.
 */
export function InlineEditField({
  value,
  label,
  canEdit,
  onSave,
  saving = false,
  multiline = false,
}: InlineEditFieldProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  const startEdit = () => {
    setDraft(value ?? '');
    setEditing(true);
  };
  const cancel = () => setEditing(false);
  const commit = () => {
    onSave(draft.trim());
    setEditing(false);
  };

  const onInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancel();
    }
  };
  const onAreaKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      cancel();
    }
  };

  if (editing) {
    return (
      <div className="flex items-start gap-1.5">
        <div className="min-w-[8rem] flex-1">
          {multiline ? (
            <Textarea
              aria-label={label}
              rows={2}
              value={draft}
              maxLength={200}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onAreaKeyDown}
            />
          ) : (
            <Input
              aria-label={label}
              value={draft}
              maxLength={200}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onInputKeyDown}
            />
          )}
        </div>
        <button
          type="button"
          onClick={commit}
          disabled={saving}
          aria-label={`Сохранить: ${label}`}
          className="mt-1 rounded-md p-1 text-status-green transition-colors hover:bg-surface-3 disabled:opacity-60"
        >
          {saving ? <Spinner className="text-status-green" /> : <Check className="h-4 w-4" />}
        </button>
        <button
          type="button"
          onClick={cancel}
          disabled={saving}
          aria-label={`Отмена: ${label}`}
          className="mt-1 rounded-md p-1 text-text-tertiary transition-colors hover:bg-surface-3 hover:text-text-primary disabled:opacity-60"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }

  const display = value?.trim() ? value : '—';
  const isEmpty = !value?.trim();

  return (
    <div className="group flex items-start gap-1.5">
      <span
        className={cn(
          'min-w-0 break-words text-[13px]',
          isEmpty ? 'text-text-tertiary' : 'text-text-primary',
        )}
      >
        {display}
      </span>
      {canEdit && (
        <button
          type="button"
          onClick={startEdit}
          aria-label={`Изменить: ${label}`}
          className="shrink-0 rounded-md p-1 text-text-tertiary transition-colors hover:bg-surface-3 hover:text-text-primary"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
