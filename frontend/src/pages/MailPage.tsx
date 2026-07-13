import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { AlertTriangle, Inbox, Mail, MailOpen, RefreshCw, Tag } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { MailboxesTab } from '@/components/MailboxesTab';
import { MailDetail } from '@/components/MailDetail';
import { MailListItem } from '@/components/MailListItem';
import { MailNotificationsToggle } from '@/components/MailNotificationsToggle';
import { TagsTab } from '@/components/TagsTab';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useCanViewPage, useIsSuperadmin, useSeesAllMailTeams } from '@/features/auth/hooks';
import {
  useMailFeed,
  useMailMailboxes,
  useMarkMailRead,
  useUnmarkMailRead,
} from '@/features/mail/hooks';
import { useTeams } from '@/features/teams/hooks';

type Tab = 'messages' | 'mailboxes' | 'tags';

/**
 * Высота двухпанельного блока: наследуется от flex-fill `<main>` (`flex-1 min-h-0`) —
 * панель заполняет остаток вьюпорта под хэдером БЕЗ магического `calc` (08-design-system.md
 * «Full-bleed layout» — flex-fill). Внутренний скролл — в панелях master-detail.
 */
const PANEL_HEIGHT = 'h-full';

/** Skeleton-строки списка при начальной загрузке (левая панель). */
function ListSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-3">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="h-20 animate-pulse rounded-lg border border-border-subtle bg-surface-1"
        />
      ))}
    </div>
  );
}

/** Центрированная заглушка (не настроено / ошибка / пустая правая панель). */
function CenteredState({
  icon,
  title,
  hint,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 py-16 text-center">
      {icon}
      <div>
        <p className="text-base font-semibold text-text-primary">{title}</p>
        {hint && <p className="mt-1 text-[13px] text-text-secondary">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

export function MailPage() {
  // Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
  // прямой URL/навигация без `mail:view` → заглушка «Недостаточно прав» (page-scoped),
  // а не контент. Супер-админ/admin — всегда доступ. Единственный хук до раннего
  // возврата — гейт; лента (useMailFeed и др.) не запрашивается без права.
  const canView = useCanViewPage('mail');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <MailTabs />;
}

/**
 * Три вкладки страницы «Почты» (08-design-system.md «Вкладки», ADR-038): «Сообщения»
 * (master-detail ленты, переезжает как есть), «Почты» (CRUD ящиков), «Теги» (каталог).
 * Локальный `useState<Tab>` + ARIA tablist/tab/tabpanel (не роутинг, образец SmsPage).
 * Full-bleed: shell `h-full` flex-column; tablist `shrink-0`; панель `flex-1 min-h-0`.
 */
function MailTabs() {
  const [tab, setTab] = useState<Tab>('messages');
  const seesAllMailTeams = useSeesAllMailTeams();

  const tabs: { key: Tab; label: string }[] = [
    { key: 'messages', label: 'Сообщения' },
    { key: 'mailboxes', label: 'Почты' },
    { key: 'tags', label: 'Теги' },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
        <div role="tablist" aria-label="Разделы почты" className="flex items-center gap-1">
          {tabs.map((t) => {
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                type="button"
                role="tab"
                id={`mail-tab-${t.key}`}
                aria-selected={active}
                aria-controls={`mail-panel-${t.key}`}
                onClick={() => setTab(t.key)}
                className={cn(
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                  active
                    ? 'bg-surface-2 text-text-primary'
                    : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
                )}
              >
                {t.label}
              </button>
            );
          })}
        </div>
        {/* Персональный opt-out Telegram-уведомлений — виден на всех вкладках (ADR-044 §2). */}
        <MailNotificationsToggle />
      </div>

      {tab === 'messages' && (
        <div
          role="tabpanel"
          id="mail-panel-messages"
          aria-labelledby="mail-tab-messages"
          className="min-h-0 flex-1 p-3"
        >
          <MailInbox seesAllMailTeams={seesAllMailTeams} />
        </div>
      )}
      {tab === 'mailboxes' && (
        <div
          role="tabpanel"
          id="mail-panel-mailboxes"
          aria-labelledby="mail-tab-mailboxes"
          className="scrollbar-none min-h-0 flex-1 overflow-y-auto p-4"
        >
          <MailboxesTab />
        </div>
      )}
      {tab === 'tags' && (
        <div
          role="tabpanel"
          id="mail-panel-tags"
          aria-labelledby="mail-tab-tags"
          className="scrollbar-none min-h-0 flex-1 overflow-y-auto p-4"
        >
          <TagsTab />
        </div>
      )}
    </div>
  );
}

function MailInbox({ seesAllMailTeams }: { seesAllMailTeams: boolean }) {
  // Серверные фильтры ленты — комбинируемы (AND, ADR-044 §7): выбор одного НЕ сбрасывает
  // другой. Входят в queryKey ленты: смена фильтра ре-запрашивает ленту, сбрасывает
  // пагинацию и авто-выбор — ADR-017. `teamId` — UUID CRM-команды (групп агрегатора нет).
  const [mailAccountId, setMailAccountId] = useState<number | undefined>(undefined);
  const [teamId, setTeamId] = useState<string | undefined>(undefined);
  // Тумблер «Непрочитанные» — СЕРВЕРНЫЙ (ADR-050 §2.8): `unread=true` уходит в запрос ленты
  // и сбрасывает пагинацию. Клиентская фильтрация непрочитанных ЗАПРЕЩЕНА (сломала бы
  // курсорную догрузку — известный дефект тумблера «С тегами»).
  const [unreadOnly, setUnreadOnly] = useState(false);

  // Супер-админ из `.env` не имеет строки в `users` ⇒ личного состояния прочитанности нет
  // (ADR-050 §2.5): индикатор, фильтр «Непрочитанные» и кнопка отката ему НЕ рендерятся,
  // POST/DELETE …/read не вызываются (backend вернул бы 403).
  const isSuperadmin = useIsSuperadmin();
  const readStateEnabled = !isSuperadmin;

  const { messages, phase, error, hasMore, isFetchingMore, isReloading, loadMore, reload } =
    useMailFeed({ mailAccountId, teamId, unread: readStateEnabled && unreadOnly });
  const mailboxesQuery = useMailMailboxes();
  const { mutate: markRead } = useMarkMailRead();
  const unmarkMutation = useUnmarkMailRead();
  // Справочник CRM-команд — только admin-уровню (фильтр «Команда» рендерится по
  // `sees_all_mail_teams`); прочим ролям не грузим (анти-энумерация, ADR-044 §7).
  const teamsQuery = useTeams(seesAllMailTeams);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  // Узкие вьюпорты: показываем деталь письма поверх списка (одна колонка).
  const [mobileDetail, setMobileDetail] = useState(false);
  // Клиентский фильтр «С тегами» поверх серверного набора (теги внешний API не фильтрует).
  const [onlyTagged, setOnlyTagged] = useState(false);

  // Опции дропдаунов. «Все почты»/«Все команды» — первая опция (сброс серверного фильтра).
  const mailboxOptions = useMemo(() => {
    const boxes = mailboxesQuery.data?.mailboxes ?? [];
    return [
      { value: '', label: 'Все почты' },
      ...boxes.map((mb) => ({
        value: String(mb.id),
        label: mb.display_name ? `${mb.display_name} ${mb.email}` : mb.email,
      })),
    ];
  }, [mailboxesQuery.data]);

  const teamOptions = useMemo(() => {
    const teams = teamsQuery.data?.items ?? [];
    return [
      { value: '', label: 'Все команды' },
      ...teams.map((t) => ({ value: t.id, label: t.name })),
    ];
  }, [teamsQuery.data]);

  // Комбинируемы (AND): выбор одного не сбрасывает другой (ADR-044 §7).
  const handleMailboxChange = (e: ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    setMailAccountId(v ? Number(v) : undefined);
  };
  const handleTeamChange = (e: ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    setTeamId(v || undefined);
  };

  const visibleMessages = useMemo(
    () => (onlyTagged ? messages.filter((m) => m.tags.length > 0) : messages),
    [messages, onlyTagged],
  );

  // Авто-выбор самого свежего письма (первое в desc-ленте) при первой загрузке / смене
  // фильтра. Опираемся на ВИДИМЫЙ (клиентски отфильтрованный) список, а не на `messages`:
  // при активном «С тегами» selectedId всегда остаётся в пределах видимого списка — иначе
  // в detail могло открыться письмо без тегов, отсутствующее в ленте (ADR-044 §7).
  useEffect(() => {
    if (visibleMessages.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    const stillExists = selectedId !== null && visibleMessages.some((m) => m.id === selectedId);
    if (!stillExists) setSelectedId(visibleMessages[0].id);
  }, [visibleMessages, selectedId]);

  const selected = useMemo(
    () => visibleMessages.find((m) => m.id === selectedId) ?? null,
    [visibleMessages, selectedId],
  );

  const handleSelect = (id: number) => {
    setSelectedId(id);
    setMobileDetail(true);
  };

  // Пометка ПРИ ОТКРЫТИИ (ADR-050 §2.6). Триггер — СМЕНА выбранного письма: и клик по строке,
  // и АВТО-ВЫБОР самого свежего письма (его тело полностью отрендерено справа ⇒ оно открыто).
  // Повторные рендеры/ре-фетчи ленты при неизменном `selectedId` POST не шлют — иначе шторм
  // запросов на поллинге и кнопка «Отметить непрочитанным» стала бы бесполезной (её эффект
  // затирался бы авто-пометкой). Best-effort: ошибка не блокирует показ письма и не даёт
  // toast-спама (максимум индикатор останется гореть). Кэш ленты правится локально, без
  // инвалидэйта — поэтому при активном фильтре «Непрочитанные» строка остаётся на месте.
  const lastMarkedIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (!readStateEnabled) return;
    if (selectedId === null) {
      lastMarkedIdRef.current = null;
      return;
    }
    if (lastMarkedIdRef.current === selectedId) return;
    lastMarkedIdRef.current = selectedId;
    markRead(selectedId);
  }, [selectedId, readStateEnabled, markRead]);

  // IntersectionObserver на sentinel в конце списка — догрузка более старых (без кнопки).
  const sentinelRef = useRef<HTMLDivElement>(null);
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

  const shell = (children: React.ReactNode) => (
    <div
      className={cn(
        'overflow-hidden rounded-card border border-border-subtle bg-surface-1 shadow-card',
        PANEL_HEIGHT,
      )}
    >
      {children}
    </div>
  );

  // 401 — сессия истекла; редирект выполняет роутер, спец-UI не показываем.
  const isAuthError = error instanceof ApiError && error.status === 401;

  if (phase === 'loading') {
    return shell(<ListSkeleton />);
  }

  if (phase === 'not_configured') {
    return shell(
      <CenteredState
        icon={<Mail className="h-10 w-10 text-text-tertiary" aria-hidden="true" />}
        title="Сервис почт не настроен"
        hint="Обратитесь к администратору для настройки почтового сервиса."
      />,
    );
  }

  if (phase === 'error') {
    if (isAuthError) return shell(<ListSkeleton />);
    return shell(
      <CenteredState
        icon={<AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />}
        title="Почтовый сервис временно недоступен"
        hint="Проверьте соединение и попробуйте снова."
        action={
          <Button variant="outline" onClick={reload} loading={isReloading}>
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        }
      />,
    );
  }

  // phase === 'ready'. Тулбар фильтров показывается всегда (в т.ч. при пустом результате
  // серверного фильтра — чтобы фильтр можно было сбросить).
  const isEmpty = messages.length === 0;

  return shell(
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      {/* Левая панель — список (~30%). На узких скрыта, когда открыта деталь. */}
      <div
        className={cn(
          'min-h-0 flex-col border-border-subtle md:flex md:w-[32%] md:flex-none md:border-r',
          mobileDetail ? 'hidden' : 'flex',
        )}
      >
        {/* Тулбар: клиентский «С тегами» + серверные дропдауны «Почта»/«Команда». */}
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-2">
          <Button
            variant={onlyTagged ? 'primary' : 'ghost'}
            size="sm"
            aria-pressed={onlyTagged}
            onClick={() => setOnlyTagged((v) => !v)}
          >
            <Tag className="h-4 w-4" />С тегами
          </Button>
          {/* «Непрочитанные» — СЕРВЕРНЫЙ тумблер (ADR-050 §2.8): включение уходит в
              `unread=true`, сбрасывает пагинацию. Не рендерится супер-админу из `.env`. */}
          {readStateEnabled && (
            <Button
              variant={unreadOnly ? 'primary' : 'ghost'}
              size="sm"
              aria-pressed={unreadOnly}
              onClick={() => setUnreadOnly((v) => !v)}
            >
              <MailOpen className="h-4 w-4" aria-hidden="true" />
              Непрочитанные
            </Button>
          )}
          <div className="w-40">
            <Select
              aria-label="Почта"
              options={mailboxOptions}
              value={mailAccountId != null ? String(mailAccountId) : ''}
              onChange={handleMailboxChange}
            />
          </div>
          {seesAllMailTeams && (
            <div className="w-40">
              <Select
                aria-label="Команда"
                options={teamOptions}
                value={teamId ?? ''}
                onChange={handleTeamChange}
              />
            </div>
          )}
        </div>

        <div className="scrollbar-none flex min-h-0 flex-1 flex-col overflow-y-auto">
          {isEmpty ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-10 text-center">
              <Inbox className="h-9 w-9 text-text-tertiary" aria-hidden="true" />
              {/* Пустой результат серверного фильтра «Непрочитанные» — своя строка (ADR-050). */}
              <p className="text-sm font-semibold text-text-primary">
                {unreadOnly && readStateEnabled ? 'Непрочитанных писем нет' : 'Писем пока нет'}
              </p>
            </div>
          ) : (
            <>
              {visibleMessages.map((message) => (
                <MailListItem
                  key={message.id}
                  message={message}
                  isActive={message.id === selectedId}
                  onSelect={handleSelect}
                  showUnread={readStateEnabled}
                />
              ))}
              {onlyTagged && visibleMessages.length === 0 && !hasMore && !isFetchingMore && (
                <p className="px-4 py-6 text-center text-[13px] text-text-secondary">
                  Нет писем с тегами среди загруженных
                </p>
              )}
              {/* Sentinel: пока hasMore, короткий отфильтрованный список держит его видимым и
                  догрузка старых батчей продолжается автоматически, наполняя фильтр. */}
              <div ref={sentinelRef} aria-hidden="true" className="h-px shrink-0" />
              {isFetchingMore && (
                <div className="flex shrink-0 items-center justify-center gap-2 py-4 text-[12px] text-text-secondary">
                  <Spinner className="text-text-secondary" />
                  Загрузка…
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Правая панель — деталь (~70%). На узких скрыта, пока не выбрано письмо. */}
      <div className={cn('min-h-0 flex-1 md:block', mobileDetail ? 'block' : 'hidden md:block')}>
        {selected ? (
          <MailDetail
            message={selected}
            onBack={() => setMobileDetail(false)}
            // Кнопка «Отметить непрочитанным» — только у не-супер-админа и только когда
            // письмо уже прочитано (условие `is_unread === false` проверяет сам MailDetail).
            onMarkUnread={readStateEnabled ? (id) => unmarkMutation.mutate(id) : undefined}
            markUnreadPending={unmarkMutation.isPending}
          />
        ) : (
          <CenteredState
            icon={<Mail className="h-9 w-9 text-text-tertiary" aria-hidden="true" />}
            title={
              isEmpty
                ? unreadOnly && readStateEnabled
                  ? 'Непрочитанных писем нет'
                  : 'Писем пока нет'
                : 'Выберите письмо'
            }
          />
        )}
      </div>
    </div>,
  );
}
