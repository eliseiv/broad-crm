import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Inbox, Mail, RefreshCw, Tag } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { MailDetail } from '@/components/MailDetail';
import { MailListItem } from '@/components/MailListItem';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useMailFeed } from '@/features/mail/hooks';

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
  const { messages, phase, error, hasMore, isFetchingMore, isReloading, loadMore, reload } =
    useMailFeed();

  const [selectedId, setSelectedId] = useState<number | null>(null);
  // Узкие вьюпорты: показываем деталь письма поверх списка (одна колонка).
  const [mobileDetail, setMobileDetail] = useState(false);
  // Клиентский фильтр «Только с тегами» (server-side фильтров нет — TD-024). Тумблер
  // фильтрует уже загруженный набор по непустому tags[]; догрузка старых по скроллу
  // и авто-выбор не ломаются (08-design-system.md «Фильтр Только с тегами»).
  const [onlyTagged, setOnlyTagged] = useState(false);

  // Авто-выбор самого свежего письма (первое в desc-ленте) при первой загрузке.
  useEffect(() => {
    if (messages.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    const stillExists = selectedId !== null && messages.some((m) => m.id === selectedId);
    if (!stillExists) setSelectedId(messages[0].id);
  }, [messages, selectedId]);

  const selected = useMemo(
    () => messages.find((m) => m.id === selectedId) ?? null,
    [messages, selectedId],
  );

  // Отфильтрованное представление поверх загруженного набора (выбор берётся из полного
  // messages, поэтому скрытие выбранного фильтром не сбрасывает правую панель).
  const visibleMessages = useMemo(
    () => (onlyTagged ? messages.filter((m) => m.tags.length > 0) : messages),
    [messages, onlyTagged],
  );

  const handleSelect = (id: number) => {
    setSelectedId(id);
    setMobileDetail(true);
  };

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

  // phase === 'ready'
  if (messages.length === 0) {
    return shell(
      <div className="flex h-full flex-col md:flex-row">
        <div className="flex flex-1 items-center justify-center border-border-subtle p-6 md:w-[32%] md:flex-none md:border-r">
          <div className="flex flex-col items-center gap-3 text-center">
            <Inbox className="h-9 w-9 text-text-tertiary" aria-hidden="true" />
            <p className="text-sm font-semibold text-text-primary">Писем пока нет</p>
          </div>
        </div>
        <div className="hidden flex-1 md:block" />
      </div>,
    );
  }

  return shell(
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      {/* Левая панель — список (~30%). На узких скрыта, когда открыта деталь. */}
      <div
        className={cn(
          'min-h-0 flex-col border-border-subtle md:flex md:w-[32%] md:flex-none md:border-r',
          mobileDetail ? 'hidden' : 'flex',
        )}
      >
        {/* Тулбар: тумблер клиентского фильтра «Только с тегами» (aria-pressed). */}
        <div className="shrink-0 border-b border-border-subtle px-3 py-2">
          <Button
            variant={onlyTagged ? 'primary' : 'ghost'}
            size="sm"
            aria-pressed={onlyTagged}
            onClick={() => setOnlyTagged((v) => !v)}
          >
            <Tag className="h-4 w-4" />
            Только с тегами
          </Button>
        </div>

        <div className="scrollbar-none flex min-h-0 flex-1 flex-col overflow-y-auto">
          {visibleMessages.map((message) => (
            <MailListItem
              key={message.id}
              message={message}
              isActive={message.id === selectedId}
              onSelect={handleSelect}
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
        </div>
      </div>

      {/* Правая панель — деталь (~70%). На узких скрыта, пока не выбрано письмо. */}
      <div className={cn('min-h-0 flex-1 md:block', mobileDetail ? 'block' : 'hidden md:block')}>
        {selected ? (
          <MailDetail message={selected} onBack={() => setMobileDetail(false)} />
        ) : (
          <CenteredState
            icon={<Mail className="h-9 w-9 text-text-tertiary" aria-hidden="true" />}
            title="Выберите письмо"
          />
        )}
      </div>
    </div>,
  );
}
