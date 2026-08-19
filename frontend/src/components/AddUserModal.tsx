import { useState } from 'react';
import { Eye, EyeOff, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { MultiSelect } from '@/components/ui/MultiSelect';
import type { MultiSelectOption } from '@/components/ui/MultiSelect';
import { UserChannelTeamsBlock } from '@/components/UserChannelTeamsBlock';
import { ApiError } from '@/lib/api';
import { fullName } from '@/features/users/fullName';
import { useCreateUser, useDeleteUser, useUpdateUser } from '@/features/users/hooks';
import type {
  RoleListItem,
  TeamListItem,
  UserCreateRequest,
  UserListItem,
  UserUpdateRequest,
} from '@/types/api';

interface AddUserModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Роли для мультивыбора «Роли» (из GET /api/roles). */
  roles: RoleListItem[];
  /** CRM-команды для мультивыбора «Команды» (из GET /api/teams). */
  teams: TeamListItem[];
  /** 'add' — создание (по умолчанию); 'edit' — редактирование пользователя. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH/DELETE. */
  user?: UserListItem;
}

type UserField = 'last_name' | 'first_name' | 'middle_name' | 'telegram' | 'password' | 'role_ids';
type Errors = Partial<Record<UserField, string>>;

/** Поля, по которым сервер отдаёт `details[].field` (04-api.md Users). */
const API_FIELDS: readonly UserField[] = [
  'last_name',
  'first_name',
  'middle_name',
  'telegram',
  'password',
  'role_ids',
];

function roleOptions(roles: RoleListItem[]): MultiSelectOption[] {
  return roles.map((r) => ({ value: r.id, label: r.name }));
}

function teamOptions(teams: TeamListItem[]): MultiSelectOption[] {
  return teams.map((t) => ({ value: t.id, label: t.name }));
}

/**
 * Часть ФИО: 1–64 после trim (кириллица допускается — формат ведёт сервер, ADR-079 §7).
 * `required` управляется вызывающим: фамилия и имя обязательны, отчество — нет.
 */
function validateNamePart(
  value: string,
  required: boolean,
  emptyMessage: string,
): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return required ? emptyMessage : undefined;
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

/** password: 8–128. `required` управляется вызывающим (create — да, edit-reset — только если введён). */
function validatePassword(password: string, required: boolean): string | undefined {
  if (!password) return required ? 'Укажите пароль' : undefined;
  if (password.length < 8) return 'Не менее 8 символов';
  if (password.length > 128) return 'Не более 128 символов';
  return undefined;
}

/** Сравнение наборов id без учёта порядка (нужно, чтобы не слать неизменённую добавку). */
function sameIds(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((id) => b.includes(id));
}

/** Маппинг ошибок API в пофилдовые (04-api.md прецеденция ошибок Users, ADR-079 §9). */
function mapApiError(err: unknown, setErrors: (u: (prev: Errors) => Errors) => void): void {
  if (err instanceof ApiError) {
    // ОБА известных конфликта порождены одним введённым значением — телеграм-ником,
    // поэтому оба показываются на поле «Телеграм»: поля «Логин» в форме больше нет
    // (ADR-079 §9). Коды перечислены явно: неопознанный 409 уходит в общий фолбэк, а не
    // вешает на «Телеграм» сообщение о конфликте, которого этот код не описывает.
    if (err.status === 409 && err.code === 'username_taken') {
      setErrors((prev) => ({
        ...prev,
        telegram: 'Этот Телеграм уже занят как логин другого пользователя',
      }));
      return;
    }
    if (err.status === 409 && err.code === 'telegram_taken') {
      setErrors((prev) => ({
        ...prev,
        telegram: 'Пользователь с таким Телеграмом уже существует',
      }));
      return;
    }
    if (err.status === 422 || err.status === 400) {
      const mapped: Errors = {};
      for (const d of err.details ?? []) {
        const field = API_FIELDS.find((f) => f === d.field);
        if (field) mapped[field] = d.message;
      }
      if (Object.keys(mapped).length > 0) {
        setErrors((prev) => ({ ...prev, ...mapped }));
      } else {
        toast.error('Проверьте корректность полей');
      }
      return;
    }
    toast.error(err.message);
    return;
  }
  toast.error('Не удалось сохранить пользователя');
}

/** Ремоунт по ключу mode+id+open → чистый сброс формы (паттерн AddProxyModal). */
export function AddUserModal({
  open,
  onOpenChange,
  roles,
  teams,
  mode = 'add',
  user,
}: AddUserModalProps) {
  const key = `${mode}-${user?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  if (mode === 'edit' && user) {
    return (
      <EditUserDialog
        key={key}
        open={open}
        onOpenChange={onOpenChange}
        roles={roles}
        teams={teams}
        user={user}
      />
    );
  }
  return (
    <AddUserDialog key={key} open={open} onOpenChange={onOpenChange} roles={roles} teams={teams} />
  );
}

/** Кнопка-глазок показа пароля (общая для обеих модалок). */
function PasswordToggle({ shown, onToggle }: { shown: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={shown ? 'Скрыть пароль' : 'Показать пароль'}
      className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {shown ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
    </button>
  );
}

function AddUserDialog({
  open,
  onOpenChange,
  roles,
  teams,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roles: RoleListItem[];
  teams: TeamListItem[];
}) {
  const [lastName, setLastName] = useState('');
  const [firstName, setFirstName] = useState('');
  const [middleName, setMiddleName] = useState('');
  const [telegram, setTelegram] = useState('');
  const [password, setPassword] = useState('');
  // Роли — мультивыбор, минимум одна (ADR-079 §1). Предвыбора нет: назначение прав
  // должно быть осознанным действием, а не умолчанием первой роли из списка.
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [teamIds, setTeamIds] = useState<string[]>([]);
  // Блоки каналов (ADR-055 §6.1): хранится и отправляется ТОЛЬКО ДОБАВКА сверх базового
  // членства (`team_ids`); флаг «Без команды» — отдельным полем на канал.
  const [smsExtraTeamIds, setSmsExtraTeamIds] = useState<string[]>([]);
  const [smsUnassigned, setSmsUnassigned] = useState(false);
  const [mailExtraTeamIds, setMailExtraTeamIds] = useState<string[]>([]);
  const [mailUnassigned, setMailUnassigned] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [showPassword, setShowPassword] = useState(false);
  const createMutation = useCreateUser();

  const noRoles = roles.length === 0;

  const clearError = (field: UserField) => {
    setErrors((prev) => (prev[field] ? { ...prev, [field]: undefined } : prev));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const nextErrors: Errors = {};
    const lastErr = validateNamePart(lastName, true, 'Укажите фамилию');
    if (lastErr) nextErrors.last_name = lastErr;
    const firstErr = validateNamePart(firstName, true, 'Укажите имя');
    if (firstErr) nextErrors.first_name = firstErr;
    const middleErr = validateNamePart(middleName, false, '');
    if (middleErr) nextErrors.middle_name = middleErr;
    // Телеграм обязателен (ADR-079 §8) — он единственный способ входа нового
    // пользователя, из него же сервис выводит скрытый username.
    if (!telegram.trim()) nextErrors.telegram = 'Укажите Телеграм';
    // Пароль опционален (ADR-025): валидируем 8–128 только если введён.
    const pErr = validatePassword(password, false);
    if (pErr) nextErrors.password = pErr;
    if (roleIds.length === 0) nextErrors.role_ids = 'Выберите хотя бы одну роль';
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    const payload: UserCreateRequest = {
      last_name: lastName.trim(),
      first_name: firstName.trim(),
      telegram: telegram.trim(),
      role_ids: roleIds,
    };
    // Отчество опционально: пусто → не отправляем.
    const trimmedMiddle = middleName.trim();
    if (trimmedMiddle) payload.middle_name = trimmedMiddle;
    // Пароль опционален: пусто → не отправляем (беспарольный «открытый первый вход», ADR-025).
    if (password) payload.password = password;
    if (teamIds.length > 0) payload.team_ids = teamIds;
    // Отправляется ТОЛЬКО добавка: базовые (disabled) чекбоксы блоков в `*_extra_team_ids`
    // не включаются (ADR-055 §6.1; сервер вычитает пересечение и сам — §2.3).
    const smsExtra = smsExtraTeamIds.filter((id) => !teamIds.includes(id));
    const mailExtra = mailExtraTeamIds.filter((id) => !teamIds.includes(id));
    if (smsExtra.length > 0) payload.sms_extra_team_ids = smsExtra;
    if (smsUnassigned) payload.sms_extra_includes_unassigned = true;
    if (mailExtra.length > 0) payload.mail_extra_team_ids = mailExtra;
    if (mailUnassigned) payload.mail_extra_includes_unassigned = true;

    createMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Пользователь создан');
        onOpenChange(false);
      },
      onError: (err) => mapApiError(err, setErrors),
    });
  };

  const isSubmitting = createMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title="Добавить пользователя"
      description="Фамилия, имя и Телеграм обязательны; пароль можно не задавать — пользователь задаст его при первом входе. Доступ определяется ролями."
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="add-user-form" loading={isSubmitting} disabled={noRoles}>
            Добавить
          </Button>
        </>
      }
    >
      <form id="add-user-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {noRoles && (
          <p className="rounded-sub border border-status-yellow/40 bg-status-yellow/5 px-3 py-2 text-[13px] text-text-secondary">
            Сначала создайте хотя бы одну роль в разделе «Роли».
          </p>
        )}
        <Input
          label="Фамилия"
          value={lastName}
          error={errors.last_name}
          autoFocus
          maxLength={64}
          autoComplete="off"
          onChange={(e) => {
            setLastName(e.target.value);
            clearError('last_name');
          }}
        />
        <Input
          label="Имя"
          value={firstName}
          error={errors.first_name}
          maxLength={64}
          autoComplete="off"
          onChange={(e) => {
            setFirstName(e.target.value);
            clearError('first_name');
          }}
        />
        <Input
          label="Отчество"
          value={middleName}
          error={errors.middle_name}
          maxLength={64}
          autoComplete="off"
          onChange={(e) => {
            setMiddleName(e.target.value);
            clearError('middle_name');
          }}
        />
        <Input
          label="Телеграм"
          type="text"
          placeholder="@username"
          value={telegram}
          error={errors.telegram}
          autoComplete="off"
          onChange={(e) => {
            setTelegram(e.target.value);
            clearError('telegram');
          }}
        />
        <Input
          label="Пароль"
          type={showPassword ? 'text' : 'password'}
          placeholder="Не менее 8 символов"
          hint="Оставьте пустым — пользователь задаст пароль при первом входе"
          value={password}
          error={errors.password}
          maxLength={128}
          autoComplete="new-password"
          onChange={(e) => {
            setPassword(e.target.value);
            clearError('password');
          }}
          trailing={
            <PasswordToggle shown={showPassword} onToggle={() => setShowPassword((v) => !v)} />
          }
        />
        <MultiSelect
          label="Роли"
          value={roleIds}
          options={roleOptions(roles)}
          error={errors.role_ids}
          disabled={noRoles}
          onChange={(next) => {
            setRoleIds(next);
            clearError('role_ids');
          }}
          emptyHint="Пока нет ролей"
        />
        <MultiSelect
          label="Команды"
          value={teamIds}
          options={teamOptions(teams)}
          onChange={setTeamIds}
          emptyHint="Пока нет команд"
        />
        {/* Блоки каналов — ВНИЗУ формы, после блока «Команды», в порядке «СМС» → «Почты»;
            оба свёрнуты по умолчанию (ADR-055 §6.1). */}
        <UserChannelTeamsBlock
          channel="sms"
          teams={teams}
          baseTeamIds={teamIds}
          extraTeamIds={smsExtraTeamIds}
          onExtraTeamIdsChange={setSmsExtraTeamIds}
          includesUnassigned={smsUnassigned}
          onIncludesUnassignedChange={setSmsUnassigned}
        />
        <UserChannelTeamsBlock
          channel="mail"
          teams={teams}
          baseTeamIds={teamIds}
          extraTeamIds={mailExtraTeamIds}
          onExtraTeamIdsChange={setMailExtraTeamIds}
          includesUnassigned={mailUnassigned}
          onIncludesUnassignedChange={setMailUnassigned}
        />
      </form>
    </Modal>
  );
}

function EditUserDialog({
  open,
  onOpenChange,
  roles,
  teams,
  user,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roles: RoleListItem[];
  teams: TeamListItem[];
  user: UserListItem;
}) {
  const initialTeamIds = user.teams.map((t) => t.id);
  const initialRoleIds = user.roles.map((r) => r.id);
  // ТОЛЬКО ДОБАВКА канала (`*_extra_teams` — строки `user_channel_teams`, без базовых `teams`).
  const initialSmsExtra = user.sms_extra_teams.map((t) => t.id);
  const initialMailExtra = user.mail_extra_teams.map((t) => t.id);
  const currentLastName = user.last_name ?? '';
  const currentFirstName = user.first_name ?? '';
  const currentMiddleName = user.middle_name ?? '';
  const currentTelegram = user.telegram ?? '';
  const [lastName, setLastName] = useState(currentLastName);
  const [firstName, setFirstName] = useState(currentFirstName);
  const [middleName, setMiddleName] = useState(currentMiddleName);
  const [telegram, setTelegram] = useState(currentTelegram);
  const [roleIds, setRoleIds] = useState<string[]>(initialRoleIds);
  const [isActive, setIsActive] = useState(user.is_active);
  const [password, setPassword] = useState('');
  const [teamIds, setTeamIds] = useState<string[]>(initialTeamIds);
  const [smsExtraTeamIds, setSmsExtraTeamIds] = useState<string[]>(initialSmsExtra);
  const [smsUnassigned, setSmsUnassigned] = useState(user.sms_extra_includes_unassigned);
  const [mailExtraTeamIds, setMailExtraTeamIds] = useState<string[]>(initialMailExtra);
  const [mailUnassigned, setMailUnassigned] = useState(user.mail_extra_includes_unassigned);
  const [errors, setErrors] = useState<Errors>({});
  const [showPassword, setShowPassword] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const updateMutation = useUpdateUser(user.id);
  const deleteMutation = useDeleteUser();

  const clearError = (field: UserField) => {
    setErrors((prev) => (prev[field] ? { ...prev, [field]: undefined } : prev));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const nextErrors: Errors = {};
    // Части ФИО, уже заполненные у пользователя, очистить нельзя (PATCH `""`/`null` → 422).
    // Историческая строка с пустой фамилией остаётся редактируемой: пустое поле там
    // означает «не менять», а не «очистить».
    const lastErr = validateNamePart(lastName, Boolean(currentLastName), 'Укажите фамилию');
    if (lastErr) nextErrors.last_name = lastErr;
    const firstErr = validateNamePart(firstName, Boolean(currentFirstName), 'Укажите имя');
    if (firstErr) nextErrors.first_name = firstErr;
    const middleErr = validateNamePart(middleName, false, '');
    if (middleErr) nextErrors.middle_name = middleErr;
    // Очистка телеграма запрещена (ADR-079 §8) — форма обязана блокировать сабмит и
    // объяснять причину. У исторической строки без телеграма пустое поле = «не менять».
    if (currentTelegram && !telegram.trim()) {
      nextErrors.telegram = 'Телеграм нельзя удалить — это единственный способ входа';
    }
    const pErr = validatePassword(password, false);
    if (pErr) nextErrors.password = pErr;
    if (roleIds.length === 0) nextErrors.role_ids = 'Выберите хотя бы одну роль';
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    // Отправляем ТОЛЬКО изменённые поля (04-api.md: exclude_unset). username не
    // редактируется и при смене telegram не пересчитывается (ADR-079 §9).
    const payload: UserUpdateRequest = {};
    const trimmedLast = lastName.trim();
    const trimmedFirst = firstName.trim();
    const trimmedMiddle = middleName.trim();
    if (trimmedLast && trimmedLast !== currentLastName) payload.last_name = trimmedLast;
    if (trimmedFirst && trimmedFirst !== currentFirstName) payload.first_name = trimmedFirst;
    // Отчество — единственная снимаемая часть ФИО: пусто → null (очистить).
    if (trimmedMiddle !== currentMiddleName)
      payload.middle_name = trimmedMiddle === '' ? null : trimmedMiddle;
    const trimmedTelegram = telegram.trim();
    if (trimmedTelegram && trimmedTelegram !== currentTelegram) payload.telegram = trimmedTelegram;
    if (!sameIds(roleIds, initialRoleIds)) payload.role_ids = roleIds;
    if (isActive !== user.is_active) payload.is_active = isActive;
    if (password) payload.password = password;
    // team_ids: если набор изменился — передаём полный новый набор (заменяет членство).
    if (!sameIds(teamIds, initialTeamIds)) payload.team_ids = teamIds;

    // Добавки каналов (ADR-055 §5.2): переданное поле ПОЛНОСТЬЮ заменяет набор добавок
    // (`[]` → снять все) ⇒ шлём только при изменении. Базовые команды в добавку не входят.
    const smsExtra = smsExtraTeamIds.filter((id) => !teamIds.includes(id));
    const mailExtra = mailExtraTeamIds.filter((id) => !teamIds.includes(id));
    if (!sameIds(smsExtra, initialSmsExtra)) payload.sms_extra_team_ids = smsExtra;
    if (!sameIds(mailExtra, initialMailExtra)) payload.mail_extra_team_ids = mailExtra;
    if (smsUnassigned !== user.sms_extra_includes_unassigned)
      payload.sms_extra_includes_unassigned = smsUnassigned;
    if (mailUnassigned !== user.mail_extra_includes_unassigned)
      payload.mail_extra_includes_unassigned = mailUnassigned;

    if (Object.keys(payload).length === 0) {
      onOpenChange(false);
      return;
    }

    updateMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Пользователь обновлён');
        onOpenChange(false);
      },
      onError: (err) => mapApiError(err, setErrors),
    });
  };

  const handleDelete = () => {
    deleteMutation.mutate(user.id, {
      onSuccess: () => {
        toast.success('Пользователь удалён');
        setConfirmOpen(false);
        onOpenChange(false);
      },
      onError: (err) => {
        // ADR-026: код user_is_team_leader упразднён — удаление лидера проходит с
        // авто-передачей лидерства; блокирующей ветки больше нет.
        const message = err instanceof ApiError ? err.message : 'Не удалось удалить пользователя';
        toast.error(message);
      },
    });
  };

  const isSubmitting = updateMutation.isPending;
  const displayName = fullName(user);

  return (
    <>
      <Modal
        open={open}
        onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
        title="Изменить пользователя"
        description="Телеграм можно сменить, но не удалить — это единственный способ входа."
        dismissible={!isSubmitting}
        footer={
          <div className="flex w-full items-center justify-between gap-2">
            <Button variant="danger" onClick={() => setConfirmOpen(true)} disabled={isSubmitting}>
              <Trash2 className="h-4 w-4" />
              Удалить
            </Button>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                Отмена
              </Button>
              <Button type="submit" form="edit-user-form" loading={isSubmitting}>
                Сохранить
              </Button>
            </div>
          </div>
        }
      >
        <form
          id="edit-user-form"
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <Input
            label="Фамилия"
            value={lastName}
            error={errors.last_name}
            maxLength={64}
            autoComplete="off"
            onChange={(e) => {
              setLastName(e.target.value);
              clearError('last_name');
            }}
          />
          <Input
            label="Имя"
            value={firstName}
            error={errors.first_name}
            maxLength={64}
            autoComplete="off"
            onChange={(e) => {
              setFirstName(e.target.value);
              clearError('first_name');
            }}
          />
          <Input
            label="Отчество"
            value={middleName}
            error={errors.middle_name}
            maxLength={64}
            autoComplete="off"
            onChange={(e) => {
              setMiddleName(e.target.value);
              clearError('middle_name');
            }}
          />
          <Input
            label="Телеграм"
            type="text"
            placeholder="@username"
            value={telegram}
            error={errors.telegram}
            autoComplete="off"
            onChange={(e) => {
              setTelegram(e.target.value);
              clearError('telegram');
            }}
          />
          <MultiSelect
            label="Роли"
            value={roleIds}
            options={roleOptions(roles)}
            error={errors.role_ids}
            onChange={(next) => {
              setRoleIds(next);
              clearError('role_ids');
            }}
            emptyHint="Пока нет ролей"
          />
          <MultiSelect
            label="Команды"
            value={teamIds}
            options={teamOptions(teams)}
            onChange={setTeamIds}
            emptyHint="Пока нет команд"
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-text-secondary">Статус</span>
            <Checkbox
              label="Активен"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
            />
          </div>
          <Input
            label="Новый пароль"
            type={showPassword ? 'text' : 'password'}
            placeholder="Оставьте пустым, чтобы не менять"
            value={password}
            error={errors.password}
            maxLength={128}
            autoComplete="new-password"
            onChange={(e) => {
              setPassword(e.target.value);
              clearError('password');
            }}
            trailing={
              <PasswordToggle shown={showPassword} onToggle={() => setShowPassword((v) => !v)} />
            }
          />
          {/* Блоки каналов — ВНИЗУ формы, перед кнопками, в порядке «СМС» → «Почты»; оба
              свёрнуты по умолчанию (ADR-055 §6.1). */}
          <UserChannelTeamsBlock
            channel="sms"
            teams={teams}
            baseTeamIds={teamIds}
            extraTeamIds={smsExtraTeamIds}
            onExtraTeamIdsChange={setSmsExtraTeamIds}
            includesUnassigned={smsUnassigned}
            onIncludesUnassignedChange={setSmsUnassigned}
          />
          <UserChannelTeamsBlock
            channel="mail"
            teams={teams}
            baseTeamIds={teamIds}
            extraTeamIds={mailExtraTeamIds}
            onExtraTeamIdsChange={setMailExtraTeamIds}
            includesUnassigned={mailUnassigned}
            onIncludesUnassignedChange={setMailUnassigned}
          />
        </form>
      </Modal>

      <Modal
        open={confirmOpen}
        onOpenChange={(next) => !deleteMutation.isPending && setConfirmOpen(next)}
        title="Удалить пользователя?"
        description={`Пользователь «${displayName}» будет удалён. Действие необратимо.`}
        dismissible={!deleteMutation.isPending}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteMutation.isPending}
            >
              Отмена
            </Button>
            <Button variant="danger" loading={deleteMutation.isPending} onClick={handleDelete}>
              Удалить
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-secondary">
          Действующая сессия пользователя будет аннулирована.
        </p>
      </Modal>
    </>
  );
}
