import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { AlertTriangle, Inbox, ShieldAlert } from 'lucide-react';
import { MailTags } from '@/components/MailTags';
import { Spinner } from '@/components/ui/Spinner';
import { ApiError } from '@/lib/api';
import { formatRelativeTime } from '@/lib/format';
import { mailTelegramAuth } from '@/features/mail/api';
import { useMailMiniAppAuthStore } from '@/features/mail/miniAppAuth';
import { useMailMiniAppFeed } from '@/features/mail/miniAppHooks';
import { applyTelegramTheme, loadTelegramSdk } from '@/features/sms/telegramSdk';
import type { MailMessage } from '@/types/api';

/**
 * Строки Mini App почты. Нормативные:
 * - `notProvisionedHint` — ADR-044 §7 (текст ошибки «Telegram не привязан»);
 * - `tabMessages` — вкладка «Сообщения» (ADR-044 §7);
 * - `messagesEmpty` — «Писем пока нет» (единый словарь /mail, 08-design-system.md).
 * Остальные (заголовок, состояния загрузки/сети/вне-Telegram) — общий словарь Mini App,
 * согласован с операторской SMS Mini App (08-design-system.md); см. blocking_questions
 * по нормативному заголовку экрана почты.
 */
const T = {
  title: 'Почта — уведомления',
  tabMessages: 'Сообщения',
  notProvisionedTitle: 'Доступ не настроен',
  notProvisionedHint: 'Ваш Telegram не привязан к пользователю CRM. Обратитесь к администратору.',
  initDataError: 'Сессия Telegram устарела — откройте приложение заново через бота',
  outsideTelegram: 'Откройте это приложение по кнопке бота в Telegram',
  networkError: 'Не удалось загрузить',
  loading: 'Загрузка…',
  retry: 'Повторить',
  messagesEmpty: 'Писем пока нет',
} as const;

/**
 * Фазы экрана Mini App: loading (SSO) · success · not_provisioned (403) ·
 * initData (401/400) · outside (пустой initData) · network (сеть/5xx + повтор).
 */
type Phase = 'loading' | 'success' | 'not_provisioned' | 'initData' | 'outside' | 'network';

/** ApiError SSO → фаза экрана (ADR-044 §7). */
function mapAuthError(err: unknown): Phase {
  if (err instanceof ApiError) {
    if (err.status === 403 && err.code === 'mail_operator_not_provisioned')
      return 'not_provisioned';
    // 401 invalid_init_data / init_data_expired · 400 validation_error (пустой/битый init_data).
    if (err.status === 401 || err.status === 400) return 'initData';
    return 'network';
  }
  return 'network';
}

/** Инлайновые стили нативной темы Telegram (var(--tg-*) с fallback). */
const chromeStyle: CSSProperties = {
  backgroundColor: 'var(--tg-bg, #0a0c10)',
  color: 'var(--tg-text, #e6e8ec)',
};

export function MailMiniAppPage() {
  const [phase, setPhase] = useState<Phase>('loading');
  const setSession = useMailMiniAppAuthStore((s) => s.setSession);
  const initDataRef = useRef<string | null>(null);
  const startedRef = useRef(false);

  const runAuth = useCallback(
    async (initData: string) => {
      setPhase('loading');
      try {
        const res = await mailTelegramAuth(initData);
        setSession(res.access_token, res.telegram_user_id);
        setPhase('success');
      } catch (err) {
        setPhase(mapAuthError(err));
      }
    },
    [setSession],
  );

  useEffect(() => {
    // StrictMode dev double-mount / повторный вход — SSO стартуем один раз.
    if (startedRef.current) return;
    startedRef.current = true;

    let disposed = false;
    let themeCleanup: (() => void) | undefined;

    loadTelegramSdk()
      .then((wa) => {
        if (disposed) return;
        // Вне Telegram (обычный браузер) — пустой/битый initData.
        if (!wa || !wa.initData) {
          applyTelegramTheme(undefined);
          setPhase('outside');
          return;
        }
        try {
          wa.ready();
          wa.expand();
        } catch {
          // ready/expand best-effort — не критично для SSO.
        }
        applyTelegramTheme(wa);
        const onThemeChanged = () => applyTelegramTheme(wa);
        wa.onEvent?.('themeChanged', onThemeChanged);
        themeCleanup = () => wa.offEvent?.('themeChanged', onThemeChanged);

        initDataRef.current = wa.initData;
        void runAuth(wa.initData);
      })
      .catch(() => {
        if (!disposed) {
          applyTelegramTheme(undefined);
          setPhase('network');
        }
      });

    return () => {
      disposed = true;
      themeCleanup?.();
    };
  }, [runAuth]);

  const retryAuth = () => {
    if (initDataRef.current) void runAuth(initDataRef.current);
  };

  return (
    <div className="min-h-screen w-full px-4 py-5" style={chromeStyle}>
      <div className="mx-auto flex w-full max-w-xl flex-col gap-4">
        <h1 className="text-lg font-semibold" style={{ color: 'var(--tg-text, #e6e8ec)' }}>
          {T.title}
        </h1>

        {phase === 'loading' && <LoadingState />}
        {phase === 'outside' && <MessageState icon="info" text={T.outsideTelegram} />}
        {phase === 'initData' && <MessageState icon="warn" text={T.initDataError} />}
        {phase === 'not_provisioned' && <NotProvisionedState />}
        {phase === 'network' && (
          <MessageState icon="warn" text={T.networkError} onRetry={retryAuth} />
        )}
        {phase === 'success' && <AuthorizedView />}
      </div>
    </div>
  );
}

/** Индикатор загрузки (SSO). */
function LoadingState() {
  return (
    <div
      className="flex items-center justify-center gap-2 py-16 text-sm"
      style={{ color: 'var(--tg-hint, #8a8f98)' }}
      role="status"
    >
      <Spinner className="h-5 w-5" />
      {T.loading}
    </div>
  );
}

/** Экран-сообщение (вне Telegram / initData / сеть) с опциональным повтором. */
function MessageState({
  icon,
  text,
  onRetry,
}: {
  icon: 'info' | 'warn';
  text: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-14 text-center">
      {icon === 'warn' ? (
        <AlertTriangle
          className="h-10 w-10"
          style={{ color: 'var(--tg-hint, #8a8f98)' }}
          aria-hidden="true"
        />
      ) : (
        <Inbox
          className="h-10 w-10"
          style={{ color: 'var(--tg-hint, #8a8f98)' }}
          aria-hidden="true"
        />
      )}
      <p className="max-w-sm text-[15px] font-medium" style={{ color: 'var(--tg-text, #e6e8ec)' }}>
        {text}
      </p>
      {onRetry && <TgButton onClick={onRetry}>{T.retry}</TgButton>}
    </div>
  );
}

/** Экран «Доступ не настроен» (403 mail_operator_not_provisioned, ADR-044 §7). */
function NotProvisionedState() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-14 text-center">
      <ShieldAlert
        className="h-10 w-10"
        style={{ color: 'var(--tg-hint, #8a8f98)' }}
        aria-hidden="true"
      />
      <div className="max-w-sm">
        <p className="text-[15px] font-semibold" style={{ color: 'var(--tg-text, #e6e8ec)' }}>
          {T.notProvisionedTitle}
        </p>
        <p className="mt-1.5 text-[13px]" style={{ color: 'var(--tg-hint, #8a8f98)' }}>
          {T.notProvisionedHint}
        </p>
      </div>
    </div>
  );
}

/** Нативная кнопка Telegram (button_color/button_text_color). */
function TgButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2"
      style={{
        backgroundColor: 'var(--tg-button, #4c82fb)',
        color: 'var(--tg-button-text, #ffffff)',
        outlineColor: 'var(--tg-button, #4c82fb)',
      }}
    >
      {children}
    </button>
  );
}

/**
 * Успешный SSO: вкладка «Сообщения» (дефолт и единственная, ADR-044 §7) — лента входящих
 * писем команд пользователя под `mail:view`. Без `mail:view` сервер отдаёт 403 → лента
 * пуста. Курсорная догрузка через IntersectionObserver (без кнопки).
 */
function AuthorizedView() {
  const messages = useMailMiniAppFeed(true);
  const messagesForbidden =
    messages.phase === 'error' &&
    messages.error instanceof ApiError &&
    messages.error.status === 403;

  const sentinelRef = useRef<HTMLDivElement>(null);
  const { hasMore, loadMore } = messages;
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasMore) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore();
      },
      { rootMargin: '200px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, loadMore]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-1">
        <span
          className="rounded-lg px-3 py-1.5 text-sm font-medium"
          style={{
            backgroundColor: 'var(--tg-button, #4c82fb)',
            color: 'var(--tg-button-text, #ffffff)',
          }}
        >
          {T.tabMessages}
        </span>
      </div>

      <div className="flex flex-col gap-2.5">
        {!messagesForbidden && messages.phase === 'loading' && <SectionLoading />}
        {!messagesForbidden && messages.phase === 'error' && (
          <SectionError onRetry={messages.reload} />
        )}
        {!messagesForbidden && messages.phase === 'ready' && messages.messages.length === 0 && (
          <SectionEmpty text={T.messagesEmpty} />
        )}
        {!messagesForbidden && messages.phase === 'ready' && messages.messages.length > 0 && (
          <div className="flex flex-col gap-2.5">
            {messages.messages.map((m) => (
              <MailMiniAppCard key={m.id} message={m} />
            ))}
            <div ref={sentinelRef} aria-hidden="true" className="h-px" />
            {messages.isFetchingMore && (
              <div className="flex items-center justify-center gap-2 py-3 text-[12px] text-text-secondary">
                <Spinner className="text-text-secondary" />
                {T.loading}
              </div>
            )}
          </div>
        )}
        {messagesForbidden && <SectionEmpty text={T.messagesEmpty} />}
      </div>
    </div>
  );
}

/** Read-only карточка письма (без detail/reply — Mini App только читает ленту). */
function MailMiniAppCard({ message }: { message: MailMessage }) {
  const accountLabel = message.mail_account.display_name || message.mail_account.email;
  const subject = message.subject ?? '(без темы)';
  return (
    <div className="flex flex-col gap-1.5 rounded-card border border-border-subtle bg-surface-1 p-4">
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 flex-1 break-words text-sm font-semibold text-text-primary">
          {message.from_name || message.from_addr}
        </span>
        <time dateTime={message.internal_date} className="shrink-0 text-[12px] text-text-tertiary">
          {formatRelativeTime(message.internal_date)}
        </time>
      </div>
      {message.from_name && (
        <span className="break-all font-mono text-[12px] text-text-secondary">
          {message.from_addr}
        </span>
      )}
      <p
        className={
          message.subject === null
            ? 'break-words text-[13px] text-text-secondary'
            : 'break-words text-[13px] text-text-primary'
        }
      >
        {subject}
      </p>
      <MailTags tags={message.tags} max={4} />
      <p className="break-words text-[12px] text-text-secondary">
        Получено на: <span className="text-text-primary">{accountLabel}</span>
      </p>
    </div>
  );
}

function SectionLoading() {
  return (
    <div className="flex flex-col gap-2.5" aria-hidden="true">
      {[0, 1].map((i) => (
        <div
          key={i}
          className="h-24 animate-pulse rounded-card border border-border-subtle bg-surface-1"
        />
      ))}
    </div>
  );
}

function SectionEmpty({ text }: { text: string }) {
  return (
    <div className="rounded-card border border-border-subtle bg-surface-1 px-4 py-8 text-center text-[13px] text-text-secondary">
      {text}
    </div>
  );
}

function SectionError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-4 py-8 text-center">
      <p className="text-[13px] text-text-secondary">{T.networkError}</p>
      <TgButton onClick={onRetry}>{T.retry}</TgButton>
    </div>
  );
}
