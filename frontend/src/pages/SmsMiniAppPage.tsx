import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { AlertTriangle, Inbox, ShieldAlert } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Pill } from '@/components/ui/Pill';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import { SmsMessageCard } from '@/components/SmsMessageCard';
import { ApiError } from '@/lib/api';
import {
  channelScopeFromMe,
  shouldRenderTeamFilter,
  teamFilterOptions,
  teamFilterParams,
} from '@/features/auth/channelTeams';
import { useMiniAppAuthStore } from '@/features/sms/miniAppAuth';
import {
  useMiniAppSmsMessages,
  useMiniAppSmsNumbers,
  useSmsMiniAppMe,
} from '@/features/sms/miniAppHooks';
import { telegramAuth } from '@/features/sms/api';
import { applyTelegramTheme, loadTelegramSdk } from '@/features/sms/telegramSdk';
import type { SmsNumber } from '@/types/api';

/**
 * Нормативный словарь UI-строк Mini App (08-design-system.md «Операторская Telegram
 * Mini App (СМС)»). ЕДИНЫЙ источник строк — не выдумывать.
 */
const T = {
  title: 'СМС — уведомления',
  tabMessages: 'Сообщения',
  tabNumbers: 'Номера',
  notProvisionedTitle: 'Доступ не настроен',
  notProvisionedHint: 'Ваш Telegram не сопоставлен с оператором CRM. Обратитесь к администратору.',
  initDataError: 'Сессия Telegram устарела — откройте приложение заново через бота',
  outsideTelegram: 'Откройте это приложение по кнопке бота в Telegram',
  networkError: 'Не удалось загрузить',
  loading: 'Загрузка…',
  retry: 'Повторить',
  numbersEmpty: 'Номеров нет',
  messagesEmpty: 'Сообщений пока нет',
} as const;

/**
 * Фазы экрана Mini App (08-design-system.md «Состояния экрана»):
 * loading (SSO) · success · not_provisioned (403) · initData (401/400) ·
 * outside (пустой initData) · network (сеть/5xx + повтор).
 */
type Phase = 'loading' | 'success' | 'not_provisioned' | 'initData' | 'outside' | 'network';

/** `'-'` для пустых значений пилюль (не «прыгает», как в SmsMessageCard). */
function orDash(value: string | null | undefined): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed : '-';
}

function isForbidden(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403;
}

/** ApiError SSO → фаза экрана (04-api.md#post-apismstelegramauth). */
function mapAuthError(err: unknown): Phase {
  if (err instanceof ApiError) {
    if (err.status === 403 && err.code === 'sms_operator_not_provisioned') return 'not_provisioned';
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

export function SmsMiniAppPage() {
  const [phase, setPhase] = useState<Phase>('loading');
  const setSession = useMiniAppAuthStore((s) => s.setSession);
  const initDataRef = useRef<string | null>(null);
  const startedRef = useRef(false);

  const runAuth = useCallback(
    async (initData: string) => {
      setPhase('loading');
      try {
        const res = await telegramAuth(initData);
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

/** Экран «Доступ не настроен» (403 sms_operator_not_provisioned). */
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

/** Вкладки Mini App после SSO (08-design-system.md, ADR-037). */
type MiniTab = 'messages' | 'numbers';

/**
 * Успешный SSO: две вкладки «Сообщения» (дефолт) / «Номера» с контентом под `sms:view`
 * (ADR-037). Статус-блок привязки убран — привязка технически сохраняется, из UI убрано
 * только информационное подтверждение. Без `sms:view` сервер отдаёт 403 → панель вкладки
 * пуста (08-design-system.md «Просмотр недоступен — вкладки пусты, без статус-блока»).
 */
function AuthorizedView() {
  const [tab, setTab] = useState<MiniTab>('messages');
  // Фильтр «Команда» в Mini App (ADR-055 §6 — разворот ADR-037 в этой части): опции ТОЛЬКО из
  // `GET /api/auth/me` под SSO-токеном (`GET /api/teams` из Mini App ЗАПРЕЩЁН), рендер — по
  // единому правилу пяти экранов (≥ 2 варианта канала; порог тот же, что в вебе). Выбор →
  // серверные `team_id`/`no_team` (сброс пагинации). Фильтра «Все номера» в Mini App НЕТ.
  const [teamFilter, setTeamFilter] = useState('');
  const meQuery = useSmsMiniAppMe(true);
  const smsScope = channelScopeFromMe(meQuery.data, 'sms');
  const showTeamFilter = shouldRenderTeamFilter(smsScope);

  const numbersQuery = useMiniAppSmsNumbers(true);
  const messages = useMiniAppSmsMessages(true, teamFilterParams(teamFilter));

  const numbers = numbersQuery.data?.numbers ?? [];
  const numbersForbidden = numbersQuery.isError && isForbidden(numbersQuery.error);
  const messagesForbidden = messages.phase === 'error' && isForbidden(messages.error);

  // Догрузка более старых сообщений (IntersectionObserver, без кнопки). Sentinel есть
  // только на активной вкладке «Сообщения» — на «Номерах» observer не подключается.
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
  }, [hasMore, loadMore, tab]);

  const tabs: { key: MiniTab; label: string }[] = [
    { key: 'messages', label: T.tabMessages },
    { key: 'numbers', label: T.tabNumbers },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div role="tablist" aria-label="Разделы СМС" className="flex items-center gap-1">
        {tabs.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`mini-tab-${t.key}`}
              aria-selected={active}
              aria-controls={`mini-panel-${t.key}`}
              onClick={() => setTab(t.key)}
              className={cn(
                'rounded-lg px-3 py-1.5 text-sm font-medium transition-opacity hover:opacity-90',
                'focus-visible:outline-2 focus-visible:outline-offset-2',
              )}
              style={
                active
                  ? {
                      backgroundColor: 'var(--tg-button, #4c82fb)',
                      color: 'var(--tg-button-text, #ffffff)',
                      outlineColor: 'var(--tg-button, #4c82fb)',
                    }
                  : { color: 'var(--tg-hint, #8a8f98)', outlineColor: 'var(--tg-button, #4c82fb)' }
              }
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === 'messages' ? (
        <div
          role="tabpanel"
          id="mini-panel-messages"
          aria-labelledby="mini-tab-messages"
          className="flex flex-col gap-2.5"
        >
          {showTeamFilter && (
            <Select
              aria-label="Команда"
              options={teamFilterOptions(smsScope)}
              value={teamFilter}
              onChange={(e) => setTeamFilter(e.target.value)}
            />
          )}
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
                <SmsMessageCard key={m.id} message={m} />
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
        </div>
      ) : (
        <div
          role="tabpanel"
          id="mini-panel-numbers"
          aria-labelledby="mini-tab-numbers"
          className="flex flex-col gap-2.5"
        >
          {!numbersForbidden && numbersQuery.isLoading && <SectionLoading />}
          {!numbersForbidden && !numbersQuery.isLoading && numbersQuery.isError && (
            <SectionError onRetry={() => void numbersQuery.refetch()} />
          )}
          {!numbersForbidden &&
            !numbersQuery.isLoading &&
            !numbersQuery.isError &&
            numbers.length === 0 && <SectionEmpty text={T.numbersEmpty} />}
          {!numbersForbidden &&
            !numbersQuery.isLoading &&
            !numbersQuery.isError &&
            numbers.length > 0 && (
              <div className="flex flex-col gap-2.5">
                {numbers.map((n) => (
                  <MiniAppNumberCard key={n.id} number={n} />
                ))}
              </div>
            )}
        </div>
      )}
    </div>
  );
}

/** Read-only карточка номера оператора (пилюли страницы «СМС», без правки). */
function MiniAppNumberCard({ number }: { number: SmsNumber }) {
  const team = number.team;
  const login = orDash(number.login);
  const appName = orDash(number.app_name);
  const note = orDash(number.note);
  return (
    <Card className="flex flex-col gap-2.5 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="whitespace-nowrap font-mono text-[13px] text-text-primary">
          {number.phone_number}
        </span>
        {team ? (
          <Pill tone="green" label={team.name} title={team.name} />
        ) : (
          <Pill tone="neutral" label="Команды нет" />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="accent" label={`Логин: ${login}`} title={login} wrap />
        <Pill tone="yellow" label={`Приложение: ${appName}`} title={appName} wrap />
        <Pill tone="neutral" label={`Примечание: ${note}`} title={note} wrap />
      </div>
    </Card>
  );
}

function SectionLoading() {
  return (
    <div className="flex flex-col gap-2.5" aria-hidden="true">
      {[0, 1].map((i) => (
        <div
          key={i}
          className="h-20 animate-pulse rounded-card border border-border-subtle bg-surface-1"
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
