import { useState } from 'react';
import { Eye, EyeOff, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { MultiSelect } from '@/components/ui/MultiSelect';
import type { MultiSelectOption } from '@/components/ui/MultiSelect';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { UserChannelTeamsBlock } from '@/components/UserChannelTeamsBlock';
import { ApiError } from '@/lib/api';
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
  /** Роли для Select (из GET /api/roles). */
  roles: RoleListItem[];
  /** CRM-команды для мультивыбора «Команды» (из GET /api/teams). */
  teams: TeamListItem[];
  /** 'add' — создание (по умолчанию); 'edit' — редактирование пользователя. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH/DELETE. */
  user?: UserListItem;
}

type UserField = 'username' | 'telegram' | 'password' | 'role_id';
type Errors = Partial<Record<UserField, string>>;

function roleOptions(roles: RoleListItem[]): SelectOption[] {
  return roles.map((r) => ({ value: r.id, label: r.name }));
}

function teamOptions(teams: TeamListItem[]): MultiSelectOption[] {
  return teams.map((t) => ({ value: t.id, label: t.name }));
}

/** username: required, 1–64 после trim (кириллица допускается — валидацию формата ведёт сервер). */
function validateUsername(username: string): string | undefined {
  const trimmed = username.trim();
  if (!trimmed) return 'Укажите логин';
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

/** Маппинг ошибок API в пофилдовые (04-api.md прецеденция ошибок Users). */
function mapApiError(err: unknown, setErrors: (u: (prev: Errors) => Errors) => void): void {
  if (err instanceof ApiError) {
    if (err.status === 409) {
      // 04-api.md: 409 username_taken / telegram_taken (различаем по code, ADR-025).
      if (err.code === 'telegram_taken') {
        setErrors((prev) => ({
          ...prev,
          telegram: 'Пользователь с таким Телеграмом уже существует',
        }));
      } else {
        setErrors((prev) => ({
          ...prev,
          username: 'Пользователь с таким логином уже существует',
        }));
      }
      return;
    }
    if (err.status === 422 || err.status === 400) {
      const mapped: Errors = {};
      for (const d of err.details ?? []) {
        if (
          d.field === 'username' ||
          d.field === 'telegram' ||
          d.field === 'password' ||
          d.field === 'role_id'
        ) {
          mapped[d.field] = d.message;
        }
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
  const [username, setUsername] = useState('');
  const [telegram, setTelegram] = useState('');
  const [password, setPassword] = useState('');
  const [roleId, setRoleId] = useState(roles[0]?.id ?? '');
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const nextErrors: Errors = {};
    const uErr = validateUsername(username);
    if (uErr) nextErrors.username = uErr;
    // Пароль опционален (ADR-025): валидируем 8–128 только если введён.
    const pErr = validatePassword(password, false);
    if (pErr) nextErrors.password = pErr;
    if (!roleId) nextErrors.role_id = 'Выберите роль';
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    const payload: UserCreateRequest = {
      username: username.trim(),
      role_id: roleId,
    };
    // Пароль опционален: пусто → не отправляем (беспарольный «открытый первый вход», ADR-025).
    if (password) payload.password = password;
    // Телеграм опционален: пусто → не отправляем (без телеграма, 04-api.md).
    const trimmedTelegram = telegram.trim();
    if (trimmedTelegram) payload.telegram = trimmedTelegram;
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
      description="Логин обязателен; пароль можно не задавать — пользователь задаст его при первом входе. Доступ определяется ролью."
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
          label="Логин"
          value={username}
          error={errors.username}
          autoFocus
          maxLength={64}
          autoComplete="off"
          onChange={(e) => {
            setUsername(e.target.value);
            if (errors.username) setErrors((p) => ({ ...p, username: undefined }));
          }}
        />
        <Input
          label="Телеграм"
          type="text"
          placeholder="@username (опционально)"
          value={telegram}
          error={errors.telegram}
          autoComplete="off"
          onChange={(e) => {
            setTelegram(e.target.value);
            if (errors.telegram) setErrors((p) => ({ ...p, telegram: undefined }));
          }}
        />
        <Input
          label="Пароль"
          type={showPassword ? 'text' : 'password'}
          placeholder="Не менее 8 символов (опционально)"
          value={password}
          error={errors.password}
          maxLength={128}
          autoComplete="new-password"
          onChange={(e) => {
            setPassword(e.target.value);
            if (errors.password) setErrors((p) => ({ ...p, password: undefined }));
          }}
          trailing={
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
              className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          }
        />
        <Select
          label="Роль"
          options={roleOptions(roles)}
          value={roleId}
          error={errors.role_id}
          disabled={noRoles}
          onChange={(e) => {
            setRoleId(e.target.value);
            if (errors.role_id) setErrors((p) => ({ ...p, role_id: undefined }));
          }}
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
  // ТОЛЬКО ДОБАВКА канала (`*_extra_teams` — строки `user_channel_teams`, без базовых `teams`).
  const initialSmsExtra = user.sms_extra_teams.map((t) => t.id);
  const initialMailExtra = user.mail_extra_teams.map((t) => t.id);
  const [telegram, setTelegram] = useState(user.telegram ?? '');
  const [roleId, setRoleId] = useState(user.role_id);
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const nextErrors: Errors = {};
    const pErr = validatePassword(password, false);
    if (pErr) nextErrors.password = pErr;
    if (!roleId) nextErrors.role_id = 'Выберите роль';
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    // Отправляем ТОЛЬКО изменённые поля (04-api.md: exclude_unset). username не редактируется.
    const payload: UserUpdateRequest = {};
    if (roleId !== user.role_id) payload.role_id = roleId;
    if (isActive !== user.is_active) payload.is_active = isActive;
    if (password) payload.password = password;
    // telegram: сравниваем с текущим; пусто → null (убрать телеграм), значение → установить.
    const trimmedTelegram = telegram.trim();
    const currentTelegram = user.telegram ?? '';
    if (trimmedTelegram !== currentTelegram)
      payload.telegram = trimmedTelegram === '' ? null : trimmedTelegram;
    // team_ids: если набор изменился — передаём полный новый набор (заменяет членство).
    const teamsChanged =
      teamIds.length !== initialTeamIds.length ||
      !teamIds.every((id) => initialTeamIds.includes(id));
    if (teamsChanged) payload.team_ids = teamIds;

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

  return (
    <>
      <Modal
        open={open}
        onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
        title="Изменить пользователя"
        description={`Логин «${user.username}» не редактируется.`}
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
          <div className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-text-secondary">Логин</span>
            <div className="flex h-10 items-center rounded-[10px] border border-border-subtle bg-surface-2 px-3">
              <span className="font-mono text-sm text-text-primary">{user.username}</span>
            </div>
          </div>
          <Input
            label="Телеграм"
            type="text"
            placeholder="@username (опционально)"
            value={telegram}
            error={errors.telegram}
            autoComplete="off"
            onChange={(e) => {
              setTelegram(e.target.value);
              if (errors.telegram) setErrors((p) => ({ ...p, telegram: undefined }));
            }}
          />
          <Select
            label="Роль"
            options={roleOptions(roles)}
            value={roleId}
            error={errors.role_id}
            onChange={(e) => {
              setRoleId(e.target.value);
              if (errors.role_id) setErrors((p) => ({ ...p, role_id: undefined }));
            }}
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
              if (errors.password) setErrors((p) => ({ ...p, password: undefined }));
            }}
            trailing={
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
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
        description={`Пользователь «${user.username}» будет удалён. Действие необратимо.`}
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
