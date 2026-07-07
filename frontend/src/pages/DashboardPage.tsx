import type { KeyboardEvent, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { KeyRound, Mail, RefreshCw, Server } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { ApiError } from '@/lib/api';
import { useCanViewPage } from '@/features/auth/hooks';
import { useAiKeys } from '@/features/ai-keys/hooks';
import { useMailMailboxes } from '@/features/mail/hooks';
import { useServers } from '@/features/servers/hooks';

type Tone = 'green' | 'red' | 'neutral';

interface Counter {
  label: string;
  value: number;
  tone: Tone;
}

/**
 * Обзорная страница «Дашборд» (ADR-017, 08-design-system.md «Страница Дашборд»).
 * Клиентская агрегация: каждая карточка сама запрашивает свой list-эндпоинт и считает
 * счётчики на фронте (backend-агрегатора нет). Клик по карточке — навигация в раздел.
 * Состояния карточек независимы (ошибка/пустота одной не ломает остальные).
 */
export function DashboardPage() {
  // Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
  // прямой URL/навигация без `dashboard:view` → заглушка «Недостаточно прав»
  // (page-scoped), а не контент. Супер-админ/admin — всегда доступ.
  const canView = useCanViewPage('dashboard');
  if (!canView) {
    return <InsufficientPermissions />;
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Дашборд</h1>
        <p className="mt-1 text-[13px] text-text-secondary">Сводка по разделам</p>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
        <MailCard />
        <ServersCard />
        <AiKeysCard />
      </div>
    </>
  );
}

/** Скелетон счётчиков во время загрузки источника карточки. */
function CountersSkeleton() {
  return (
    <div className="flex gap-5">
      {[0, 1].map((i) => (
        <div key={i} className="h-5 w-24 animate-pulse rounded bg-surface-3" />
      ))}
    </div>
  );
}

/**
 * Кликабельная карточка раздела. Вся карточка — область навигации (role=button,
 * Enter/Space); кнопка «Повторить» в состоянии ошибки — stopPropagation, не навигирует.
 */
function SectionCard({
  title,
  to,
  icon,
  children,
}: {
  title: string;
  to: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  const go = () => navigate(to);
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      go();
    }
  };

  return (
    <Card
      interactive
      role="button"
      tabIndex={0}
      aria-label={`${title} — открыть раздел`}
      onClick={go}
      onKeyDown={onKeyDown}
      className="cursor-pointer p-5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <div className="flex items-center gap-3">
        <span
          className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-3 text-accent"
          aria-hidden="true"
        >
          {icon}
        </span>
        <h2 className="text-xl font-bold text-text-primary">{title}</h2>
      </div>
      <div className="mt-5 min-h-[2.5rem]">{children}</div>
    </Card>
  );
}

/**
 * Ряд счётчиков (Badge-тона + моночисло, текст дублирует цвет — a11y).
 * Нормативно (08-design-system.md «Статус-строка карточки»): строка центрируется
 * по горизонтали (justify-center); активный счётчик (`green`) виден всегда, включая `0`,
 * вторичные (red/neutral) при значении `0` не рендерятся и не смещают центрирование.
 */
function Counters({ counters }: { counters: Counter[] }) {
  const visible = counters.filter((c) => c.tone === 'green' || c.value > 0);
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
      {visible.map((c) => (
        <Badge key={c.label} tone={c.tone}>
          <span>{c.label}</span>
          <span className="font-mono text-lg font-bold text-text-primary">{c.value}</span>
        </Badge>
      ))}
    </div>
  );
}

/** Состояние ошибки внутри карточки: подпись + «Повторить» (не навигирует). */
function CardError({ onRetry, loading }: { onRetry: () => void; loading: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-[13px] text-text-secondary">Не удалось загрузить</span>
      <Button
        variant="outline"
        size="sm"
        loading={loading}
        onClick={(e) => {
          e.stopPropagation();
          onRetry();
        }}
      >
        <RefreshCw className="h-4 w-4" />
        Повторить
      </Button>
    </div>
  );
}

/** Карточка «Почты»: активные/неактивные ящики (is_active) по GET /api/mail/mailboxes. */
function MailCard() {
  const { data, isLoading, isError, error, refetch, isFetching } = useMailMailboxes();
  const notConfigured = error instanceof ApiError && error.status === 503;

  return (
    <SectionCard title="Почты" to="/mail" icon={<Mail className="h-5 w-5" />}>
      {isLoading ? (
        <CountersSkeleton />
      ) : notConfigured ? (
        <span className="text-[13px] text-text-secondary">Сервис почт не настроен</span>
      ) : isError ? (
        <CardError onRetry={() => void refetch()} loading={isFetching} />
      ) : (
        <Counters
          counters={[
            {
              label: 'Активные',
              value: (data?.mailboxes ?? []).filter((m) => m.is_active).length,
              tone: 'green',
            },
            {
              label: 'Неактивные',
              value: (data?.mailboxes ?? []).filter((m) => !m.is_active).length,
              tone: 'red',
            },
          ]}
        />
      )}
    </SectionCard>
  );
}

/** Карточка «Серверы»: online/offline по GET /api/servers (server.online). */
function ServersCard() {
  const { data, isLoading, isError, refetch, isFetching } = useServers();

  return (
    <SectionCard title="Серверы" to="/servers" icon={<Server className="h-5 w-5" />}>
      {isLoading ? (
        <CountersSkeleton />
      ) : isError ? (
        <CardError onRetry={() => void refetch()} loading={isFetching} />
      ) : (
        <Counters
          counters={[
            {
              label: 'В сети',
              value: (data?.items ?? []).filter((s) => s.online).length,
              tone: 'green',
            },
            {
              label: 'Не в сети',
              value: (data?.items ?? []).filter((s) => !s.online).length,
              tone: 'red',
            },
          ]}
        />
      )}
    </SectionCard>
  );
}

/**
 * Карточка «ИИ - ключи»: работает (check_status='working') / не работает ('error');
 * опц. «Проверяется» ('pending', нейтральный) — по GET /api/ai-keys.
 */
function AiKeysCard() {
  const { data, isLoading, isError, refetch, isFetching } = useAiKeys();
  const items = data?.items ?? [];

  // Вторичные (Не работает/Проверяется) скрываются при 0 внутри Counters (нормативно).
  const counters: Counter[] = [
    {
      label: 'Работает',
      value: items.filter((k) => k.check_status === 'working').length,
      tone: 'green',
    },
    {
      label: 'Не работает',
      value: items.filter((k) => k.check_status === 'error').length,
      tone: 'red',
    },
    {
      label: 'Проверяется',
      value: items.filter((k) => k.check_status === 'pending').length,
      tone: 'neutral',
    },
  ];

  return (
    <SectionCard title="ИИ - ключи" to="/ai-keys" icon={<KeyRound className="h-5 w-5" />}>
      {isLoading ? (
        <CountersSkeleton />
      ) : isError ? (
        <CardError onRetry={() => void refetch()} loading={isFetching} />
      ) : (
        <Counters counters={counters} />
      )}
    </SectionCard>
  );
}
