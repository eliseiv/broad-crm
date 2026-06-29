import { useState } from 'react';
import { CheckCircle2, Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { useCreateServer } from '@/features/servers/hooks';
import type { CreateServerRequest } from '@/types/api';

interface AddServerModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Field = keyof CreateServerRequest;
type Errors = Partial<Record<Field, string>>;

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const IPV6 =
  /^(([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|::|([0-9a-fA-F]{1,4}:){1,7}:|(:[0-9a-fA-F]{1,4}){1,7}|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5})$/;

function isValidIp(value: string): boolean {
  return IPV4.test(value) || IPV6.test(value);
}

const EMPTY: CreateServerRequest = { name: '', ip: '', ssh_user: '', ssh_password: '' };

function validate(values: CreateServerRequest): Errors {
  const errors: Errors = {};
  const name = values.name.trim();
  if (!name) errors.name = 'Укажите название';
  else if (name.length > 64) errors.name = 'Не более 64 символов';

  const ip = values.ip.trim();
  if (!ip) errors.ip = 'Укажите IP-адрес';
  else if (!isValidIp(ip)) errors.ip = 'Некорректный IPv4/IPv6-адрес';

  const user = values.ssh_user.trim();
  if (!user) errors.ssh_user = 'Укажите пользователя';
  else if (user.length > 64) errors.ssh_user = 'Не более 64 символов';

  if (!values.ssh_password) errors.ssh_password = 'Укажите пароль';
  else if (values.ssh_password.length > 256) errors.ssh_password = 'Не более 256 символов';

  return errors;
}

/**
 * Тонкая обёртка: ремоунтит внутренний диалог по ключу open, что даёт
 * чистый сброс состояния формы без эффекта (и без подавления линтера).
 */
export function AddServerModal({ open, onOpenChange }: AddServerModalProps) {
  return <AddServerDialog key={open ? 'open' : 'closed'} open={open} onOpenChange={onOpenChange} />;
}

function AddServerDialog({ open, onOpenChange }: AddServerModalProps) {
  const [values, setValues] = useState<CreateServerRequest>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [provisioning, setProvisioning] = useState(false);
  const createMutation = useCreateServer();

  const update = (field: Field, value: string) => {
    setValues((prev) => ({ ...prev, [field]: value }));
    if (touched) setErrors(validate({ ...values, [field]: value }));
  };

  const applyApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        setErrors((prev) => ({ ...prev, ip: 'Сервер с таким IP уже добавлен' }));
        toast.error('Сервер с таким IP уже добавлен');
        return;
      }
      if (err.status === 422) {
        setErrors((prev) => ({ ...prev, ip: 'Некорректный IP-адрес' }));
        toast.error('Некорректный IP-адрес');
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
    toast.error('Не удалось добавить сервер');
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
        ip: values.ip.trim(),
        ssh_user: values.ssh_user.trim(),
        ssh_password: values.ssh_password,
      },
      {
        onSuccess: () => {
          toast.success('Сервер добавлен');
          setProvisioning(true);
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
      title={provisioning ? 'Сервер добавлен' : 'Добавить сервер'}
      description={
        provisioning
          ? undefined
          : 'Укажите данные для подключения по SSH. Агент мониторинга установится автоматически.'
      }
      dismissible={!isSubmitting}
      footer={
        provisioning ? (
          <Button onClick={() => onOpenChange(false)}>Готово</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" form="add-server-form" loading={isSubmitting}>
              Добавить
            </Button>
          </>
        )
      }
    >
      {provisioning ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <div className="relative">
            <Loader2 className="h-10 w-10 animate-spin text-accent" aria-hidden="true" />
            <CheckCircle2
              className="absolute -bottom-1 -right-1 h-5 w-5 text-status-green"
              aria-hidden="true"
            />
          </div>
          <p className="text-sm font-medium text-text-primary">Установка агента…</p>
          <p className="text-[13px] text-text-secondary">
            Статус установки отображается на карточке сервера и обновляется автоматически.
          </p>
        </div>
      ) : (
        <form
          id="add-server-form"
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <Input
            label="Название"
            placeholder="Сервер 02"
            value={values.name}
            error={errors.name}
            autoFocus
            maxLength={64}
            onChange={(e) => update('name', e.target.value)}
          />
          <Input
            label="IP-адрес"
            placeholder="10.0.0.13"
            mono
            value={values.ip}
            error={errors.ip}
            onChange={(e) => update('ip', e.target.value)}
          />
          <Input
            label="Пользователь"
            placeholder="root"
            value={values.ssh_user}
            error={errors.ssh_user}
            maxLength={64}
            autoComplete="off"
            onChange={(e) => update('ssh_user', e.target.value)}
          />
          <Input
            label="Пароль"
            type={showPassword ? 'text' : 'password'}
            placeholder="••••••••"
            value={values.ssh_password}
            error={errors.ssh_password}
            maxLength={256}
            autoComplete="new-password"
            onChange={(e) => update('ssh_password', e.target.value)}
            trailing={
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            }
          />
        </form>
      )}
    </Modal>
  );
}
