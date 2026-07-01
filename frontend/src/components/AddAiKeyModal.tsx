import { useState } from 'react';
import { Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useCreateAiKey } from '@/features/ai-keys/hooks';
import type { AiProvider, CreateAiKeyRequest } from '@/types/api';

interface AddAiKeyModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Field = keyof CreateAiKeyRequest;
type Errors = Partial<Record<Field, string>>;

const PROVIDER_OPTIONS: SelectOption[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
];

const EMPTY: CreateAiKeyRequest = { name: '', provider: 'openai', key: '' };

function validate(values: CreateAiKeyRequest): Errors {
  const errors: Errors = {};
  const name = values.name.trim();
  if (!name) errors.name = 'Укажите название';
  else if (name.length > 64) errors.name = 'Не более 64 символов';

  if (values.provider !== 'openai' && values.provider !== 'anthropic') {
    errors.provider = 'Выберите провайдера';
  }

  if (!values.key.trim()) errors.key = 'Укажите ключ';
  else if (values.key.length > 512) errors.key = 'Не более 512 символов';

  return errors;
}

/**
 * Обёртка ремоунтит внутренний диалог по ключу open → чистый сброс формы
 * без эффекта (паттерн AddServerModal).
 */
export function AddAiKeyModal({ open, onOpenChange }: AddAiKeyModalProps) {
  return <AddAiKeyDialog key={open ? 'open' : 'closed'} open={open} onOpenChange={onOpenChange} />;
}

function AddAiKeyDialog({ open, onOpenChange }: AddAiKeyModalProps) {
  const [values, setValues] = useState<CreateAiKeyRequest>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [checking, setChecking] = useState(false);
  const createMutation = useCreateAiKey();

  const update = (field: Field, value: string) => {
    const next = { ...values, [field]: value } as CreateAiKeyRequest;
    setValues(next);
    if (touched) setErrors(validate(next));
  };

  const applyApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 422) {
        setErrors((prev) => ({ ...prev, provider: 'Недопустимый провайдер' }));
        toast.error('Недопустимый провайдер');
        return;
      }
      if (err.status === 400 && err.details) {
        const mapped: Errors = {};
        for (const d of err.details) {
          if (d.field in EMPTY) mapped[d.field as Field] = d.message;
        }
        setErrors((prev) => ({ ...prev, ...mapped }));
        toast.error('Проверьте корректность полей');
        return;
      }
      toast.error(err.message);
      return;
    }
    toast.error('Не удалось добавить ключ');
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nextErrors = validate(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    createMutation.mutate(
      {
        name: values.name.trim(),
        provider: values.provider,
        key: values.key.trim(),
      },
      {
        onSuccess: () => {
          toast.success('Ключ добавлен');
          setChecking(true);
        },
        onError: applyApiError,
      },
    );
  };

  const isSubmitting = createMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title={checking ? 'Ключ добавлен' : 'Добавить AI-ключ'}
      description={
        checking ? undefined : 'Ключ будет зашифрован. Валидность проверяется автоматически.'
      }
      dismissible={!isSubmitting}
      footer={
        checking ? (
          <Button onClick={() => onOpenChange(false)}>Готово</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" form="add-ai-key-form" loading={isSubmitting}>
              Добавить
            </Button>
          </>
        )
      }
    >
      {checking ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <Loader2 className="h-10 w-10 animate-spin text-accent" aria-hidden="true" />
          <p className="text-sm font-medium text-text-primary">Проверка ключа…</p>
          <p className="text-[13px] text-text-secondary">
            Статус проверки отображается на карточке ключа и обновляется автоматически.
          </p>
        </div>
      ) : (
        <form
          id="add-ai-key-form"
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <Input
            label="Название"
            placeholder="OpenAI Prod"
            value={values.name}
            error={errors.name}
            autoFocus
            maxLength={64}
            onChange={(e) => update('name', e.target.value)}
          />
          <Select
            label="Провайдер"
            options={PROVIDER_OPTIONS}
            value={values.provider}
            error={errors.provider}
            onChange={(e) => update('provider', e.target.value as AiProvider)}
          />
          <Input
            label="Ключ"
            type={showKey ? 'text' : 'password'}
            placeholder="sk-proj-…"
            mono
            value={values.key}
            error={errors.key}
            maxLength={512}
            autoComplete="off"
            onChange={(e) => update('key', e.target.value)}
            trailing={
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                aria-label={showKey ? 'Скрыть ключ' : 'Показать ключ'}
                className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            }
          />
        </form>
      )}
    </Modal>
  );
}
