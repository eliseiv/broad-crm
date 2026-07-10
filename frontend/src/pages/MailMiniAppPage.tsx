import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, ReactNode } from 'react';
import { AlertTriangle, ArrowLeft, Inbox, ShieldAlert } from 'lucide-react';
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
 * Строки Mini App почты (нормативный словарь — 08-design-system.md «Telegram Mini App почты»,
 * ADR-044 «Поправка 2026-07-10»). Экран рендерится БЕЗ h1-заголовка и БЕЗ таб-лейбла
 * «Сообщения» — лента показывается напрямую, полный текст письма открывается на detail-экране.
 * - `notProvisionedTitle`/`notProvisionedHint` — 403 mail_operator_not_provisioned;
 * - `messagesEmpty` — «Писем пока нет»;
 * - `back`/`bodyUnavailable`/`bodyTruncated` — detail письма.
 */
const T = {
  notProvisionedTitle: 'Доступ не настроен',
  notProvisionedHint: 'Ваш Telegram не привязан к пользователю CRM. Обратитесь к администратору.',
  initDataError: 'Сессия Telegram устарела — откройте приложение заново через бота',
  outsideTelegram: 'Откройте это приложение по кнопке бота в Telegram',
  networkError: 'Не удалось загрузить',
  loading: 'Загрузка…',
  retry: 'Повторить',
  messagesEmpty: 'Писем пока нет',
  subjectEmpty: '(без темы)',
  back: 'Назад',
  bodyFrameTitle: 'Тело письма',
  bodyUnavailable: 'Тело письма недоступно',
  bodyTruncated: 'Письмо показано не полностью',
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
 * Успешный SSO: лента входящих писем команд пользователя под `mail:view` (без `mail:view`
 * сервер отдаёт 403 → лента пуста), показывается НАПРЯМУЮ — без h1-заголовка и без
 * таб-лейбла «Сообщения» (08-design-system.md «Telegram Mini App почты», ADR-044 поправка).
 * Клик по карточке открывает read-only full-width detail с полным текстом письма (локальный
 * `useState`, НЕ роутинг). Курсорная догрузка через IntersectionObserver (без кнопки).
 */
function AuthorizedView() {
  const messages = useMailMiniAppFeed(true);
  const [selected, setSelected] = useState<MailMessage | null>(null);
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
              <MailMiniAppCard key={m.id} message={m} onOpen={setSelected} />
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

      {selected && <MailMiniAppDetail message={selected} onBack={() => setSelected(null)} />}
    </div>
  );
}

/**
 * Кликабельная карточка письма в ленте (весь блок — область тапа, `role="button"`,
 * доступна с клавиатуры, видимый focus-ring). Тело письма в карточке НЕ показывается —
 * оно на detail-экране (08-design-system.md «Telegram Mini App почты»).
 */
function MailMiniAppCard({
  message,
  onOpen,
}: {
  message: MailMessage;
  onOpen: (message: MailMessage) => void;
}) {
  const accountLabel = message.mail_account.display_name || message.mail_account.email;
  const subject = message.subject ?? T.subjectEmpty;
  const open = () => onOpen(message);
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      open();
    }
  };
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={onKeyDown}
      className="flex cursor-pointer flex-col gap-1.5 rounded-card border border-border-subtle bg-surface-1 p-4 text-left transition-colors hover:border-border-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
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

/** Полная дата для шапки detail (08-design-system.md: `ru-RU`, dateStyle long + timeStyle short). */
function absoluteDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return formatRelativeTime(iso);
  return new Date(ts).toLocaleString('ru-RU', { dateStyle: 'long', timeStyle: 'short' });
}

/**
 * Обёртка srcDoc sandbox-iframe: инъекция серого фона `--surface-2` перед недоверенным телом
 * письма — тот же паттерн, что десктопный MailDetail. Sandbox НЕ ослабляется (без
 * allow-scripts/allow-same-origin — ADR-012).
 */
function buildHtmlSrcDoc(bodyHtml: string): string {
  return `<style>html,body{background:#161A22;color:#E6E9EF;margin:0;padding:12px}</style>${bodyHtml}`;
}

/**
 * Тело письма в detail. `body_html` (недоверенный HTML третьих лиц) рендерится ТОЛЬКО в
 * sandbox-iframe без `allow-scripts`/`allow-same-origin` + `referrerPolicy="no-referrer"`
 * (ADR-012, modules/mail «Изоляция HTML-тела» — инварианты НЕ ослабляются). Иначе — `body_text`
 * моношрифтом с переносами. Тело скроллится в своём контейнере.
 */
function MailMiniAppBody({ message }: { message: MailMessage }) {
  if (!message.body_present) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-10 text-center">
        <p className="text-[13px] text-text-secondary">{T.bodyUnavailable}</p>
      </div>
    );
  }

  const html = message.body_html;
  const hasHtml = Boolean(html && html.trim());

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 bg-surface-2 px-4 py-4">
      {hasHtml ? (
        <iframe
          title={T.bodyFrameTitle}
          srcDoc={buildHtmlSrcDoc(html ?? '')}
          sandbox=""
          referrerPolicy="no-referrer"
          className="min-h-0 w-full flex-1 rounded-lg border border-border-subtle bg-surface-2"
        />
      ) : (
        <pre className="scrollbar-none min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border-subtle bg-surface-2 p-3 font-mono text-[13px] text-text-primary">
          {message.body_text}
        </pre>
      )}
      {message.body_truncated && (
        <p className="shrink-0 text-[12px] text-text-secondary">{T.bodyTruncated}</p>
      )}
    </div>
  );
}

/**
 * Read-only full-width detail письма внутри того же webview (НЕ роутинг). Полный сохранённый
 * текст выбранного письма из уже загруженного объекта (без нового запроса/эндпоинта). Формы
 * ответа в Mini App НЕТ. 08-design-system.md «Telegram Mini App почты», ADR-044 поправка.
 */
function MailMiniAppDetail({ message, onBack }: { message: MailMessage; onBack: () => void }) {
  const backRef = useRef<HTMLButtonElement>(null);
  const { email, display_name: displayName } = message.mail_account;
  const subject = message.subject ?? T.subjectEmpty;

  // Фокус на «Назад» при открытии detail — доступность с клавиатуры.
  useEffect(() => {
    backRef.current?.focus();
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={subject}
      className="fixed inset-0 z-50 flex flex-col bg-surface-1"
    >
      <header className="shrink-0 border-b border-border-subtle px-4 py-4">
        <button
          ref={backRef}
          type="button"
          onClick={onBack}
          className="mb-3 inline-flex items-center gap-1 rounded-md text-[13px] font-medium text-text-secondary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          {T.back}
        </button>

        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <span className="text-sm font-semibold text-text-primary">
            {message.from_name || message.from_addr}
          </span>
          <time dateTime={message.internal_date} className="text-[12px] text-text-tertiary">
            {absoluteDate(message.internal_date)}
          </time>
        </div>

        {message.from_name && (
          <p className="mt-0.5 break-all font-mono text-[12px] text-text-secondary">
            {message.from_addr}
          </p>
        )}

        <h2
          className={
            message.subject === null
              ? 'mt-2 text-base font-semibold text-text-secondary'
              : 'mt-2 text-base font-semibold text-text-primary'
          }
        >
          {subject}
        </h2>

        <div className="mt-2">
          <MailTags tags={message.tags} />
        </div>

        {/*
          «Получено на: {display_name} <{email}>» — значения видны полностью (значимый контент
          не обрезаем, CLAUDE.md); длинный адрес переносится (break-words), НЕ truncate. При
          пустом display_name — только email.
        */}
        <p className="mt-2 break-words text-[12px] text-text-secondary">
          Получено на: {displayName && <span className="text-text-primary">{displayName} </span>}
          <span className="font-mono text-text-secondary">
            {displayName ? `<${email}>` : email}
          </span>
        </p>
      </header>

      <MailMiniAppBody message={message} />
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
