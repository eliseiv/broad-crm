import { ArrowRight } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Pill } from '@/components/ui/Pill';
import { formatDateTimeRu } from '@/lib/format';
import type { SmsMessage } from '@/types/api';

/** `'-'` для пустого редактируемого поля (пилюля не «прыгает», 08-design-system.md). */
function orDash(value: string | null | undefined): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed : '-';
}

interface SmsMessageCardProps {
  message: SmsMessage;
}

/**
 * Карточка входящего SMS (08-design-system.md «Карточка SmsMessageCard»):
 *  1) `from_number → to_number` (моно) + бейдж команды (зелёный / серый «Команды нет»)
 *     + дата справа (абсолютный ru-RU `DD.MM.YYYY HH:MM`);
 *  2) пилюли Логин/Приложение/Примечание (значения из текущего номера);
 *  3) текст SMS (`body`, pre-wrap).
 * Бейдж команды и пилюли берутся из `number` (текущий номер по `to_number`); при
 * удалённом номере (`number=null`) команда → «Команды нет», значения → «-».
 */
export function SmsMessageCard({ message }: SmsMessageCardProps) {
  const team = message.number?.team ?? null;
  const login = orDash(message.number?.login);
  const appName = orDash(message.number?.app_name);
  const note = orDash(message.number?.note);

  return (
    <Card className="flex flex-col gap-2.5 p-4">
      {/* Шапка переносится на узких вьюпортах (≤~390px): при нехватке ширины таймстамп
          уходит на отдельную строку (flex-wrap) вместо перекрытия nowrap-номера. На
          десктопе всё умещается в одну строку — номер слева, дата справа (justify-between). */}
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 font-mono text-[13px] text-text-primary">
            <span className="whitespace-nowrap">{message.from_number}</span>
            <ArrowRight className="h-3.5 w-3.5 shrink-0 text-text-tertiary" aria-hidden="true" />
            <span className="whitespace-nowrap">{message.to_number}</span>
          </span>
          {team ? (
            <Pill tone="green" label={team.name} title={team.name} />
          ) : (
            <Pill tone="neutral" label="Команды нет" />
          )}
        </div>
        <time
          dateTime={message.received_at}
          className="shrink-0 whitespace-nowrap font-mono text-[12px] text-text-tertiary"
        >
          {formatDateTimeRu(message.received_at)}
        </time>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="accent" label={`Логин: ${login}`} title={login} wrap />
        <Pill tone="yellow" label={`Приложение: ${appName}`} title={appName} wrap />
        <Pill tone="neutral" label={`Примечание: ${note}`} title={note} wrap />
      </div>

      <p className="whitespace-pre-wrap break-words text-[13px] text-text-primary">
        {message.body}
      </p>
    </Card>
  );
}
