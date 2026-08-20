import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Plus, RefreshCw, Search, User as UserIcon } from 'lucide-react';
import { toast } from 'sonner';
import { AddUserModal } from '@/components/AddUserModal';
import { Modal } from '@/components/ui/Modal';
import { SummaryCell } from '@/components/SummaryCell';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Pill } from '@/components/ui/Pill';
import { Spinner } from '@/components/ui/Spinner';
import { formatNumber } from '@/lib/format';
import { useTeams } from '@/features/teams/hooks';
import { fullName, userSearchHaystack } from '@/features/users/fullName';
import { useResetUserPassword, useRoles, useUsers } from '@/features/users/hooks';
import type { UserListItem } from '@/types/api';

/**
 * Бейдж производного тристатуса пользователя (ADR-028, 08-design-system.md
 * «Страница Пользователи»): «Активен» (green) — только после первого входа;
 * «Ожидает входа» (yellow) — заведён, но ещё не входил; «Неактивен» (neutral).
 */
function StatusBadge({ status }: { status: UserListItem['status'] }) {
  if (status === 'active') return <Badge tone="green">Активен</Badge>;
  if (status === 'pending') return <Badge tone="yellow">Ожидает входа</Badge>;
  return <Badge tone="neutral">Неактивен</Badge>;
}

/**
 * Страница «Пользователи» (08-design-system.md «Страница Пользователи», ADR-079 §10,
 * supersedes ADR-065). Admin-only (гейтинг — AdminRoute). Раскладка — ТАБЛИЦА
 * (прежний плоский список карточек отменён), один пользователь = одна строка:
 * ФИО | Роли | Команды | Telegram | Статус | Бот | Действия. Над таблицей — сводные
 * плашки (считаются на клиенте, серверных счётчиков нет) и клиентский поиск по
 * ФИО + username + telegram. Сортировка — по ФИО (`localeCompare`, локаль 'ru').
 */
export function UsersPage() {
  const usersQuery = useUsers();
  const rolesQuery = useRoles();
  // useTeams нужен модалке AddUserModal (мультивыбор команд), а не раскладке
  // таблицы — убирать нельзя (08-design-system.md).
  const teamsQuery = useTeams();

  const [modalOpen, setModalOpen] = useState(false);
  const [editUser, setEditUser] = useState<UserListItem | undefined>(undefined);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  // Сброс пароля необратим для пользователя (он теряет текущий пароль), поэтому
  // выполняется через подтверждение, а не по одному клику.
  const [resetTarget, setResetTarget] = useState<UserListItem | undefined>(undefined);
  const resetPassword = useResetUserPassword();

  // Debounce поиска — тот же интервал, что у таблицы «Юзеры бэков» (ввод не дёргает
  // пересборку списка на каждый символ).
  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 400);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const roles = rolesQuery.data?.items ?? [];
  const teams = teamsQuery.data?.items ?? [];
  const usersData = usersQuery.data?.items;

  // Сортировка по ФИО-строке (ADR-079 §10): localeCompare с локалью 'ru' — кириллица
  // по алфавиту, а не по code-unit.
  const users = useMemo(
    () => [...(usersData ?? [])].sort((a, b) => fullName(a).localeCompare(fullName(b), 'ru')),
    [usersData],
  );

  // Клиентский поиск: ФИО + username + telegram, подстрочно, регистронезависимо.
  const visibleUsers = useMemo(() => {
    const needle = search.toLocaleLowerCase('ru');
    if (!needle) return users;
    return users.filter((user) => userSearchHaystack(user).includes(needle));
  }, [users, search]);

  // Сводка считается по ПОЛНОМУ списку (не по результату поиска): плашки описывают
  // реестр целиком, иначе «Всего» менялось бы от строки поиска.
  const summary = useMemo(
    () => ({
      total: users.length,
      active: users.filter((u) => u.status === 'active').length,
      pending: users.filter((u) => u.status === 'pending').length,
      bot: users.filter((u) => u.bot_started).length,
    }),
    [users],
  );

  const openAdd = () => {
    setEditUser(undefined);
    setModalOpen(true);
  };
  const openEdit = (user: UserListItem) => {
    setEditUser(user);
    setModalOpen(true);
  };

  const isReady = !usersQuery.isLoading && !usersQuery.isError;

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="w-72">
          <Input
            // Нормативная строка (08-design-system.md): слово «логин» в подписи не
            // употребляется — поля «Логин» в UI нет (ADR-079 §9). Область поиска при
            // этом ШИРЕ подписи: `username` ищется как фолбэк того, что видно в колонке
            // «ФИО» у исторических учёток без ФИО.
            aria-label="Поиск по ФИО или Телеграму"
            placeholder="Поиск по ФИО или Телеграму"
            value={searchInput}
            trailing={<Search className="h-4 w-4 text-text-tertiary" aria-hidden="true" />}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <Button size="sm" onClick={openAdd} disabled={rolesQuery.isLoading}>
          <Plus className="h-4 w-4" />
          Добавить пользователя
        </Button>
      </div>

      {isReady && (
        <div className="mb-4 grid grid-cols-2 gap-px overflow-hidden rounded-card border border-border-subtle bg-border-subtle lg:grid-cols-4">
          <SummaryCell label="Всего" value={formatNumber(summary.total)} />
          <SummaryCell label="Активны" value={formatNumber(summary.active)} />
          <SummaryCell label="Ожидают входа" value={formatNumber(summary.pending)} />
          <SummaryCell label="Активны в боте" value={formatNumber(summary.bot)} />
        </div>
      )}

      {usersQuery.isLoading && (
        <div className="flex items-center justify-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-[13px] text-text-secondary">
          <Spinner className="text-text-secondary" />
          Загрузка…
        </div>
      )}

      {usersQuery.isError && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
          <AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">
              Не удалось загрузить пользователей
            </p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Проверьте соединение с сервером и попробуйте снова.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => void usersQuery.refetch()}
            loading={usersQuery.isFetching}
          >
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        </div>
      )}

      {isReady && users.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <UserIcon className="h-8 w-8 text-text-tertiary" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-text-primary">Пока нет пользователей</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Добавьте первого пользователя и назначьте ему роль.
            </p>
          </div>
        </div>
      )}

      {/* Поиск не дал совпадений — это НЕ «пользователей нет»: разводим состояния,
          иначе оператор прочитает пустой результат фильтра как пустой реестр. */}
      {isReady && users.length > 0 && visibleUsers.length === 0 && (
        <div className="rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
          <p className="text-sm font-medium text-text-primary">Ничего не найдено</p>
        </div>
      )}

      {isReady && visibleUsers.length > 0 && (
        <div className="overflow-x-auto rounded-card border border-border-subtle bg-surface-1">
          <table className="w-full min-w-[960px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle text-[12px] uppercase tracking-wide text-text-tertiary">
                <th className="px-4 py-3 font-medium">ФИО</th>
                <th className="px-4 py-3 font-medium">Роли</th>
                <th className="px-4 py-3 font-medium">Команды</th>
                <th className="px-4 py-3 font-medium">Telegram</th>
                <th className="px-4 py-3 font-medium">Статус</th>
                <th className="px-4 py-3 font-medium">Бот</th>
                <th className="px-4 py-3 font-medium">Действия</th>
              </tr>
            </thead>
            <tbody>
              {visibleUsers.map((user) => {
                const name = fullName(user);
                return (
                  <tr
                    key={user.id}
                    onClick={() => openEdit(user)}
                    className="cursor-pointer border-b border-border-subtle transition-colors last:border-b-0 hover:bg-surface-2"
                  >
                    <td className="px-4 py-3">
                      <span className="font-medium text-text-primary">{name}</span>
                      {/* Второй строкой — технический username, и ТОЛЬКО если он
                          отличается от ФИО-строки: дублировать одно значение дважды
                          нельзя (08-design-system.md). */}
                      {user.username !== name && (
                        <span className="mt-0.5 block font-mono text-[12px] text-text-secondary">
                          {user.username}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {user.roles.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {user.roles.map((role) => (
                            <Pill key={role.id} tone="accent" label={role.name} title={role.name} />
                          ))}
                        </div>
                      ) : (
                        // Возможен только при правке БД в обход API (минимум одна
                        // роль — инвариант сервиса).
                        <span className="text-[13px] text-text-secondary">Без роли</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {user.teams.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {user.teams.map((team) => (
                            <Pill
                              key={team.id}
                              tone="neutral"
                              label={team.name}
                              title={team.name}
                            />
                          ))}
                        </div>
                      ) : (
                        <span className="text-[13px] text-text-secondary">Без команды</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-[13px] text-text-primary">
                      {user.telegram ? `@${user.telegram}` : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge status={user.status} />
                        {/* Беспарольный пользователь ещё не завершил «открытый первый
                            вход» (ADR-025 §5) — единственный визуальный признак учётки. */}
                        {!user.has_password && <Badge tone="yellow">Без пароля</Badge>}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {user.bot_started ? (
                        <Badge tone="green">Бот</Badge>
                      ) : (
                        <Badge tone="red">Бот не запущен</Badge>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          // Видимая подпись одинакова во всех строках, поэтому
                          // accessible name дополнен именем пользователя.
                          aria-label={`Открыть ${name}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            openEdit(user);
                          }}
                        >
                          Открыть
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          aria-label={`Сбросить пароль — ${name}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setResetTarget(user);
                          }}
                        >
                          Сброс
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <AddUserModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        roles={roles}
        teams={teams}
        mode={editUser ? 'edit' : 'add'}
        user={editUser}
      />

      <Modal
        open={resetTarget !== undefined}
        onOpenChange={(open) => {
          if (!open) setResetTarget(undefined);
        }}
        title="Сбросить пароль?"
        description={
          resetTarget
            ? `Пароль пользователя ${fullName(resetTarget)} будет удалён. При следующем входе ` +
              'он задаст новый пароль сам — как при первом входе. Текущий пароль перестанет работать.'
            : undefined
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setResetTarget(undefined)}>
              Отмена
            </Button>
            <Button
              variant="danger"
              loading={resetPassword.isPending}
              onClick={() => {
                if (!resetTarget) return;
                const target = resetTarget;
                resetPassword.mutate(target.id, {
                  onSuccess: () => {
                    toast.success(`Пароль сброшен — ${fullName(target)}`);
                    setResetTarget(undefined);
                  },
                  onError: (err: unknown) => {
                    toast.error(err instanceof Error ? err.message : 'Не удалось сбросить пароль');
                  },
                });
              }}
            >
              Сбросить
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-text-secondary">
          Новый пароль не генерируется и никуда не отправляется — пользователь придумает его сам при
          следующем входе.
        </p>
      </Modal>
    </>
  );
}
