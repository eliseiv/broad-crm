import { useMemo, useState, type FormEvent } from 'react';
import { AlertTriangle, Megaphone, RefreshCw, Send } from 'lucide-react';
import { toast } from 'sonner';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Checkbox } from '@/components/ui/Checkbox';
import { Spinner } from '@/components/ui/Spinner';
import { Textarea } from '@/components/ui/Textarea';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useBroadcastAudience, useCreateBroadcast } from '@/features/broadcast/hooks';
import { ApiError } from '@/lib/api';
import type { BroadcastAudienceRole } from '@/types/api';

const TEXT_MAX = 4096;

function audienceSummary(
  sendAll: boolean,
  selectedIds: Set<string>,
  roles: BroadcastAudienceRole[],
  allStarted: number,
  allNotStarted: number,
): { started: number; notStarted: number } {
  if (sendAll) return { started: allStarted, notStarted: allNotStarted };
  let started = 0;
  let notStarted = 0;
  for (const role of roles) {
    if (!selectedIds.has(role.id)) continue;
    started += role.started_count;
    notStarted += role.not_started_count;
  }
  return { started, notStarted };
}

function roleCheckboxAriaLabel(role: BroadcastAudienceRole): string {
  return `${role.name} (получат: ${role.started_count}, без бота: ${role.not_started_count})`;
}

function audienceRowClass(selected: boolean): string {
  return [
    'flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5',
    selected ? 'border-accent' : '',
  ]
    .filter(Boolean)
    .join(' ');
}

/**
 * Страница «Рассылка» (08-design-system.md «Страница Рассылка», ADR-076 / ADR-077).
 * Без H1. Page-level view-guard `broadcast:view`; кнопка «Отправить» — `broadcast:send`.
 */
export function BroadcastPage() {
  const canView = useCanViewPage('broadcast');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <BroadcastForm />;
}

function BroadcastForm() {
  const audienceQuery = useBroadcastAudience();
  const sendMutation = useCreateBroadcast();
  const canSend = useCan('broadcast', 'send');

  const [text, setText] = useState('');
  const [sendAll, setSendAll] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [botNotConfigured, setBotNotConfigured] = useState(false);

  const roles = audienceQuery.data?.roles;
  const allStarted = audienceQuery.data?.all_started_count ?? 0;
  const allNotStarted = audienceQuery.data?.all_not_started_count ?? 0;

  const summary = useMemo(
    () => audienceSummary(sendAll, selectedIds, roles ?? [], allStarted, allNotStarted),
    [sendAll, selectedIds, roles, allStarted, allNotStarted],
  );

  const trimmed = text.trim();
  const hasAudience = sendAll || selectedIds.size > 0;
  const canSubmit = trimmed.length >= 1 && trimmed.length <= TEXT_MAX && hasAudience;

  const forbidden = audienceQuery.error instanceof ApiError && audienceQuery.error.status === 403;
  const audienceNotConfigured =
    audienceQuery.error instanceof ApiError &&
    audienceQuery.error.status === 503 &&
    audienceQuery.error.code === 'knowledge_bot_not_configured';

  const toggleRole = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !canSend) return;
    sendMutation.mutate(
      {
        text: trimmed,
        all: sendAll,
        role_ids: sendAll ? [] : [...selectedIds],
      },
      {
        onSuccess: (data) => {
          toast.success(
            `Отправлено: ${data.sent}. Не доставлено: ${data.failed}. Без бота: ${data.skipped_not_started}`,
          );
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            if (err.status === 503 && err.code === 'knowledge_bot_not_configured') {
              setBotNotConfigured(true);
              return;
            }
            if (err.status === 403) {
              toast.error('Недостаточно прав');
              return;
            }
            if (err.status === 422) {
              toast.error('Проверьте текст и аудиторию');
              return;
            }
            toast.error(err.message);
            return;
          }
          toast.error('Не удалось отправить рассылку');
        },
      },
    );
  };

  if (audienceQuery.isLoading) {
    return (
      <div className="flex items-center justify-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-[13px] text-text-secondary">
        <Spinner className="text-text-secondary" />
        Загрузка…
      </div>
    );
  }

  if (audienceQuery.isError && forbidden) {
    return <InsufficientPermissions />;
  }

  if (botNotConfigured || audienceNotConfigured) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
        <Megaphone className="h-8 w-8 text-text-tertiary" aria-hidden="true" />
        <p className="text-base font-semibold text-text-primary">ИИ-бот не настроен</p>
        <p className="text-[13px] text-text-secondary">
          Обратитесь к администратору для настройки ИИ-бота базы знаний.
        </p>
      </div>
    );
  }

  if (audienceQuery.isError) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
        <AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />
        <div>
          <p className="text-base font-semibold text-text-primary">
            Не удалось загрузить аудиторию
          </p>
          <p className="mt-1 text-[13px] text-text-secondary">
            Проверьте соединение с сервером и попробуйте снова.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => void audienceQuery.refetch()}
          loading={audienceQuery.isFetching}
        >
          <RefreshCw className="h-4 w-4" />
          Повторить
        </Button>
      </div>
    );
  }

  return (
    <form className="flex w-full max-w-3xl flex-col gap-6" onSubmit={handleSubmit} noValidate>
      <Card className="flex flex-col gap-6 p-5 sm:p-6">
        <Textarea
          label="Сообщение"
          value={text}
          maxLength={TEXT_MAX}
          rows={8}
          hint={`${text.length} / ${TEXT_MAX}`}
          onChange={(e) => setText(e.target.value)}
        />

        <fieldset className="flex min-w-0 flex-col gap-3">
          <legend className="text-[13px] font-medium text-text-secondary">Аудитория</legend>
          <div className={audienceRowClass(sendAll)}>
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
                <Megaphone className="h-5 w-5" aria-hidden="true" />
              </span>
              <Checkbox
                label="Всем"
                checked={sendAll}
                onChange={(e) => setSendAll(e.target.checked)}
              />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone="green">Получат: {allStarted}</Badge>
              <Badge tone="red">Без бота: {allNotStarted}</Badge>
            </div>
          </div>
          {!roles || roles.length === 0 ? (
            <p className="text-[13px] text-text-secondary">Ролей для выбора нет.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {roles.map((role) => {
                const selected = selectedIds.has(role.id);
                return (
                  <li key={role.id} className={audienceRowClass(selected)}>
                    <Checkbox
                      label={
                        <span className="min-w-0 break-words text-sm font-medium text-text-primary">
                          {role.name}
                        </span>
                      }
                      aria-label={roleCheckboxAriaLabel(role)}
                      checked={selected}
                      disabled={sendAll}
                      onChange={() => toggleRole(role.id)}
                    />
                    <div className="flex flex-wrap items-center gap-3">
                      <Badge tone="green">Получат: {role.started_count}</Badge>
                      <Badge tone="red">Без бота: {role.not_started_count}</Badge>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </fieldset>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="rounded-sub border border-border-subtle bg-surface-2 px-4 py-3">
            <div className="flex flex-wrap gap-6">
              <div aria-hidden="true" className="flex min-w-0 flex-col gap-0.5">
                <span className="text-[13px] text-text-secondary">Получат</span>
                <span className="font-mono font-semibold text-text-primary">{summary.started}</span>
              </div>
              <div aria-hidden="true" className="flex min-w-0 flex-col gap-0.5">
                <span className="text-[13px] text-text-secondary">Без бота</span>
                <span className="font-mono font-semibold text-text-primary">
                  {summary.notStarted}
                </span>
              </div>
            </div>
            <p className="sr-only" aria-live="polite">
              Получат: {summary.started} · Без бота: {summary.notStarted}
            </p>
          </div>
          {canSend && (
            <Button
              type="submit"
              loading={sendMutation.isPending}
              disabled={!canSubmit}
              className="w-full sm:w-auto"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
              Отправить
            </Button>
          )}
        </div>
      </Card>
    </form>
  );
}
