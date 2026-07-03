import { AlertTriangle, Inbox, Mail, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { MailMessageCard } from '@/components/MailMessageCard';
import { ApiError } from '@/lib/api';
import { useMailFeed } from '@/features/mail/hooks';

export function MailPage() {
  const { messages, phase, error, hasMore, isFetchingMore, isRefreshing, loadMore, reload } =
    useMailFeed();

  const isAuthError = error instanceof ApiError && error.status === 401;
  const errorTitle =
    error instanceof ApiError && error.status === 502
      ? 'Почтовый сервис временно недоступен'
      : 'Не удалось загрузить письма';

  return (
    <>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Почты</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            {phase === 'loading'
              ? 'Загрузка…'
              : phase === 'ready'
                ? `${messages.length} ${pluralMessages(messages.length)}`
                : 'Лента писем'}
          </p>
        </div>
        {phase === 'ready' && (
          <Button
            variant="outline"
            size="sm"
            onClick={reload}
            loading={isRefreshing}
            aria-label="Обновить ленту"
          >
            <RefreshCw className="h-4 w-4" />
            Обновить
          </Button>
        )}
      </div>

      {phase === 'loading' && (
        <div className="flex flex-col gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-28 animate-pulse rounded-card border border-border-subtle bg-surface-1"
            />
          ))}
        </div>
      )}

      {phase === 'not_configured' && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <Mail className="h-10 w-10 text-text-tertiary" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">Сервис почт не настроен</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Обратитесь к администратору для настройки почтового сервиса.
            </p>
          </div>
        </div>
      )}

      {phase === 'error' && !isAuthError && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">{errorTitle}</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Проверьте соединение и попробуйте снова.
            </p>
          </div>
          <Button variant="outline" onClick={reload} loading={isRefreshing}>
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        </div>
      )}

      {phase === 'ready' && messages.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <Inbox className="h-10 w-10 text-text-tertiary" aria-hidden="true" />
          <p className="text-base font-semibold text-text-primary">Писем пока нет</p>
        </div>
      )}

      {phase === 'ready' && messages.length > 0 && (
        <div className="flex flex-col gap-3">
          {messages.map((message) => (
            <MailMessageCard key={message.id} message={message} />
          ))}
          {hasMore && (
            <div className="flex justify-center pt-2">
              <Button variant="outline" onClick={loadMore} loading={isFetchingMore}>
                Загрузить ещё
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function pluralMessages(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'письмо';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'письма';
  return 'писем';
}
