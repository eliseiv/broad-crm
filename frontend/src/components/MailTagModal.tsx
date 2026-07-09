import { useState } from 'react';
import { Check } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { TAG_PALETTE } from '@/features/mail/tags';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useCreateTag, useUpdateTag } from '@/features/mail/hooks';
import type { MailTagFull, MailTagMatchMode } from '@/types/api';

interface MailTagModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'add' | 'edit';
  /** Обязателен в режиме edit — префил и id для PATCH. */
  tag?: MailTagFull;
}

const MATCH_MODE_OPTIONS: SelectOption[] = [
  { value: 'any', label: 'любое правило' },
  { value: 'all', label: 'все правила' },
];

/** Ремоунт по ключу mode+id+open → чистый сброс формы. */
export function MailTagModal({ open, onOpenChange, mode, tag }: MailTagModalProps) {
  const key = `${mode}-${tag?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  return <TagDialog key={key} open={open} onOpenChange={onOpenChange} mode={mode} tag={tag} />;
}

function TagDialog({
  open,
  onOpenChange,
  mode,
  tag,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'add' | 'edit';
  tag?: MailTagFull;
}) {
  const isEdit = mode === 'edit';
  const [name, setName] = useState(tag?.name ?? '');
  const [color, setColor] = useState(tag?.color ?? TAG_PALETTE[0].hex);
  const [matchMode, setMatchMode] = useState<MailTagMatchMode>(tag?.match_mode ?? 'any');
  const [nameError, setNameError] = useState<string | undefined>(undefined);

  const createMutation = useCreateTag();
  const updateMutation = useUpdateTag();
  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  const mapError = (err: unknown): void => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        setNameError('Тег с таким именем уже существует');
        return;
      }
      if (err.status === 422 || err.status === 400) {
        const nameDetail = err.details?.find((d) => d.field === 'name');
        if (nameDetail) setNameError(nameDetail.message);
        else toast.error('Проверьте корректность полей');
        return;
      }
      toast.error(err.message);
      return;
    }
    toast.error('Не удалось сохранить тег');
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError('Укажите имя тега');
      return;
    }
    if (isEdit && tag) {
      const payload: {
        name?: string;
        color?: string;
        match_mode?: MailTagMatchMode;
      } = {};
      if (trimmed !== tag.name) payload.name = trimmed;
      if (color !== tag.color) payload.color = color;
      if (matchMode !== tag.match_mode) payload.match_mode = matchMode;
      if (Object.keys(payload).length === 0) {
        onOpenChange(false);
        return;
      }
      updateMutation.mutate(
        { id: tag.id, payload },
        {
          onSuccess: () => {
            toast.success('Тег обновлён');
            onOpenChange(false);
          },
          onError: mapError,
        },
      );
      return;
    }
    createMutation.mutate(
      { name: trimmed, color, match_mode: matchMode },
      {
        onSuccess: () => {
          toast.success('Тег создан');
          onOpenChange(false);
        },
        onError: mapError,
      },
    );
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title={isEdit ? 'Изменить тег' : 'Добавить тег'}
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="tag-form" loading={isSubmitting}>
            {isEdit ? 'Сохранить' : 'Добавить'}
          </Button>
        </>
      }
    >
      <form id="tag-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <Input
          label="Имя тега"
          value={name}
          error={nameError}
          autoFocus
          maxLength={64}
          autoComplete="off"
          onChange={(e) => {
            setName(e.target.value);
            if (nameError) setNameError(undefined);
          }}
        />
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-text-secondary">Цвет</span>
          <div className="flex flex-wrap gap-2">
            {TAG_PALETTE.map((c) => {
              const selected = color.toLowerCase() === c.hex.toLowerCase();
              return (
                <button
                  key={c.hex}
                  type="button"
                  onClick={() => setColor(c.hex)}
                  aria-label={c.name}
                  aria-pressed={selected}
                  title={c.name}
                  className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-full border-2 transition-transform',
                    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                    selected ? 'border-text-primary' : 'border-transparent hover:scale-105',
                  )}
                  style={{ backgroundColor: c.hex }}
                >
                  {selected && <Check className="h-4 w-4 text-white" aria-hidden="true" />}
                </button>
              );
            })}
          </div>
        </div>
        <Select
          label="Срабатывает при совпадении"
          options={MATCH_MODE_OPTIONS}
          value={matchMode}
          onChange={(e) => setMatchMode(e.target.value as MailTagMatchMode)}
        />
      </form>
    </Modal>
  );
}
