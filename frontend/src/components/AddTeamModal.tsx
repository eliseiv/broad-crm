import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { MultiSelect } from '@/components/ui/MultiSelect';
import type { MultiSelectOption } from '@/components/ui/MultiSelect';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useMailTeams } from '@/features/mail/hooks';
import { useCreateTeam, useDeleteTeam, useUpdateTeam } from '@/features/teams/hooks';
import type {
  MailTeam,
  TeamCreateRequest,
  TeamListItem,
  TeamUpdateRequest,
  UserListItem,
} from '@/types/api';

interface AddTeamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Пользователи для выбора лидера/участников (из GET /api/users). */
  users: UserListItem[];
  /** 'add' — создание (по умолчанию); 'edit' — редактирование команды. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH/DELETE. */
  team?: TeamListItem;
  /** Показывать кнопку «Удалить» в режиме edit (по `teams:delete`). */
  canDelete?: boolean;
}

type Errors = { name?: string; leader_id?: string; member_ids?: string; mail_group_id?: string };

function userOptions(users: UserListItem[]): (SelectOption & MultiSelectOption)[] {
  return users.map((u) => ({ value: u.id, label: u.username }));
}

/** Пустое значение лидера — команда без лидера (только пустой состав, ADR-026/ADR-029). */
const NO_LEADER = '';
/** Пустое значение почтовой группы — команда без привязки к почте (ADR-038). */
const NO_MAIL_GROUP = '';

/** Опции селектора «Почтовая группа»: «Без привязки» + группы mail-агрегатора (MailTeam). */
function mailGroupOptions(teams: MailTeam[]): SelectOption[] {
  return [
    { value: NO_MAIL_GROUP, label: 'Без привязки' },
    ...teams.map((t) => ({ value: String(t.id), label: t.name })),
  ];
}

/** name: required, 1–64 после trim (формат — на сервере). */
function validateName(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'Укажите название';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

/** Маппинг ошибок API команды (04-api.md прецеденция: 422 refs → 409 name). */
function mapApiError(err: unknown, setErrors: (u: (prev: Errors) => Errors) => void): void {
  if (err instanceof ApiError) {
    if (err.status === 409) {
      if (err.code === 'team_mail_group_taken') {
        setErrors((prev) => ({
          ...prev,
          mail_group_id: 'Эта почтовая группа уже привязана к другой команде',
        }));
        return;
      }
      setErrors((prev) => ({ ...prev, name: 'Команда с таким названием уже существует' }));
      return;
    }
    if (err.status === 422 || err.status === 400) {
      const mapped: Errors = {};
      for (const d of err.details ?? []) {
        if (d.field === 'name') mapped.name = d.message;
        else if (d.field === 'leader_id') mapped.leader_id = d.message;
        else if (d.field === 'member_ids') mapped.member_ids = d.message;
      }
      if (Object.keys(mapped).length > 0) setErrors((prev) => ({ ...prev, ...mapped }));
      else toast.error('Проверьте корректность полей');
      return;
    }
    if (err.status === 403) {
      toast.error('Недостаточно прав');
      return;
    }
    toast.error(err.message);
    return;
  }
  toast.error('Не удалось сохранить команду');
}

/** Ремоунт по ключу mode+id+open → чистый сброс формы (паттерн AddUserModal). */
export function AddTeamModal({
  open,
  onOpenChange,
  users,
  mode = 'add',
  team,
  canDelete = true,
}: AddTeamModalProps) {
  const key = `${mode}-${team?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  if (mode === 'edit' && team) {
    return (
      <EditTeamDialog
        key={key}
        open={open}
        onOpenChange={onOpenChange}
        users={users}
        team={team}
        canDelete={canDelete}
      />
    );
  }
  return <AddTeamDialog key={key} open={open} onOpenChange={onOpenChange} users={users} />;
}

/**
 * Общая форма (Название / Участники / Лидер, ADR-029). `memberIds` — полный состав
 * (включая лидера). Лидер выбирается ТОЛЬКО из выбранных участников; дефолт — первый
 * добавленный. При исключении текущего лидера из состава лидерство переходит первому
 * из оставшихся; пустой состав → без лидера (`leader_id=NULL`, единственный кейс).
 */
function TeamFormFields({
  name,
  setName,
  leaderId,
  setLeaderId,
  memberIds,
  setMemberIds,
  mailGroupId,
  setMailGroupId,
  mailTeams,
  errors,
  setErrors,
  users,
}: {
  name: string;
  setName: (v: string) => void;
  leaderId: string;
  setLeaderId: (v: string) => void;
  memberIds: string[];
  setMemberIds: (v: string[]) => void;
  mailGroupId: string;
  setMailGroupId: (v: string) => void;
  mailTeams: MailTeam[];
  errors: Errors;
  setErrors: (u: (prev: Errors) => Errors) => void;
  users: UserListItem[];
}) {
  const options = userOptions(users);
  const noUsers = users.length === 0;
  const noMembers = memberIds.length === 0;

  // Кандидаты в лидеры = только выбранные участники (в порядке добавления). Дефолт —
  // первый (memberIds[0]). При пустом составе — placeholder, выбор недоступен.
  const leaderCandidates: SelectOption[] = memberIds
    .map((id) => users.find((u) => u.id === id))
    .filter((u): u is UserListItem => Boolean(u))
    .map((u) => ({ value: u.id, label: u.username }));
  const leaderOptions: SelectOption[] = noMembers
    ? [{ value: NO_LEADER, label: 'Сначала выберите участников' }]
    : leaderCandidates;

  const handleMembersChange = (next: string[]) => {
    setMemberIds(next);
    // Инвариант «лидер ∈ участники»: если лидера нет в новом составе (снят / состав
    // опустел) — лидером становится первый из оставшихся (авто-передача), иначе — пусто.
    if (!next.includes(leaderId)) setLeaderId(next[0] ?? NO_LEADER);
    if (errors.member_ids) setErrors((p) => ({ ...p, member_ids: undefined }));
  };

  return (
    <>
      {noUsers && (
        <p className="rounded-sub border border-status-yellow/40 bg-status-yellow/5 px-3 py-2 text-[13px] text-text-secondary">
          Нет пользователей для назначения лидера и участников.
        </p>
      )}
      <Input
        label="Название"
        value={name}
        error={errors.name}
        autoFocus
        maxLength={64}
        autoComplete="off"
        onChange={(e) => {
          setName(e.target.value);
          if (errors.name) setErrors((p) => ({ ...p, name: undefined }));
        }}
      />
      <MultiSelect
        label="Участники"
        value={memberIds}
        options={options}
        onChange={handleMembersChange}
        error={errors.member_ids}
        emptyHint="Нет пользователей"
        disabled={noUsers}
      />
      <Select
        label="Лидер"
        options={leaderOptions}
        value={leaderId}
        error={errors.leader_id}
        disabled={noMembers}
        onChange={(e) => {
          setLeaderId(e.target.value);
          if (errors.leader_id) setErrors((p) => ({ ...p, leader_id: undefined }));
        }}
      />
      <Select
        label="Почтовая группа"
        options={mailGroupOptions(mailTeams)}
        value={mailGroupId}
        error={errors.mail_group_id}
        onChange={(e) => {
          setMailGroupId(e.target.value);
          if (errors.mail_group_id) setErrors((p) => ({ ...p, mail_group_id: undefined }));
        }}
      />
    </>
  );
}

function AddTeamDialog({
  open,
  onOpenChange,
  users,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  users: UserListItem[];
}) {
  const [name, setName] = useState('');
  // По умолчанию — без лидера (пустой состав, ADR-026/ADR-029). Первый добавленный
  // участник авто-становится лидером (handleMembersChange в TeamFormFields).
  const [leaderId, setLeaderId] = useState(NO_LEADER);
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [mailGroupId, setMailGroupId] = useState(NO_MAIL_GROUP);
  const [errors, setErrors] = useState<Errors>({});
  const createMutation = useCreateTeam();
  const mailTeamsQuery = useMailTeams(open);
  const mailTeams = mailTeamsQuery.data?.teams ?? [];
  const noUsers = users.length === 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const next: Errors = {};
    const nErr = validateName(name);
    if (nErr) next.name = nErr;
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    // member_ids — полный состав (включая лидера; backend дедуплицирует, 04-api.md).
    const payload: TeamCreateRequest = { name: name.trim(), member_ids: memberIds };
    if (leaderId) payload.leader_id = leaderId;
    if (mailGroupId) payload.mail_group_id = Number(mailGroupId);
    createMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Команда создана');
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
      title="Добавить команду"
      description="Название обязательно; лидер и участники опциональны — можно создать пустую команду."
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="add-team-form" loading={isSubmitting} disabled={noUsers}>
            Добавить
          </Button>
        </>
      }
    >
      <form id="add-team-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <TeamFormFields
          name={name}
          setName={setName}
          leaderId={leaderId}
          setLeaderId={setLeaderId}
          memberIds={memberIds}
          setMemberIds={setMemberIds}
          mailGroupId={mailGroupId}
          setMailGroupId={setMailGroupId}
          mailTeams={mailTeams}
          errors={errors}
          setErrors={setErrors}
          users={users}
        />
      </form>
    </Modal>
  );
}

function EditTeamDialog({
  open,
  onOpenChange,
  users,
  team,
  canDelete,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  users: UserListItem[];
  team: TeamListItem;
  canDelete: boolean;
}) {
  // Текущий лидер как строка ('' — без лидера, ADR-026: leader_id может быть null).
  const currentLeader = team.leader_id ?? NO_LEADER;
  const currentMailGroup = team.mail_group_id != null ? String(team.mail_group_id) : NO_MAIL_GROUP;
  // Полный состав (включая лидера, ADR-029) — источник кандидатов в лидеры.
  const initialMembers = team.members.map((m) => m.id);
  const [name, setName] = useState(team.name);
  const [leaderId, setLeaderId] = useState(currentLeader);
  const [memberIds, setMemberIds] = useState<string[]>(initialMembers);
  const [mailGroupId, setMailGroupId] = useState(currentMailGroup);
  const [errors, setErrors] = useState<Errors>({});
  const [confirmOpen, setConfirmOpen] = useState(false);
  const updateMutation = useUpdateTeam(team.id);
  const deleteMutation = useDeleteTeam();
  const mailTeamsQuery = useMailTeams(open);
  const mailTeams = mailTeamsQuery.data?.teams ?? [];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const next: Errors = {};
    const nErr = validateName(name);
    if (nErr) next.name = nErr;
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    // Отправляем ТОЛЬКО изменённые поля (04-api.md: exclude_unset).
    const payload: TeamUpdateRequest = {};
    if (name.trim() !== team.name) payload.name = name.trim();
    // member_ids — полный состав (включая лидера). Сравнение множествами (без учёта порядка).
    const membersChanged =
      memberIds.length !== initialMembers.length ||
      !memberIds.every((id) => initialMembers.includes(id));
    if (membersChanged) payload.member_ids = memberIds;
    // Снятие лидера → null; смена/назначение → id. Лидер ∈ участники (ADR-029).
    if (leaderId !== currentLeader) payload.leader_id = leaderId === NO_LEADER ? null : leaderId;
    // Почтовая группа (presence-семантика): изменилась → int / null (снять привязку).
    if (mailGroupId !== currentMailGroup)
      payload.mail_group_id = mailGroupId === NO_MAIL_GROUP ? null : Number(mailGroupId);

    if (Object.keys(payload).length === 0) {
      onOpenChange(false);
      return;
    }

    updateMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Команда обновлена');
        onOpenChange(false);
      },
      onError: (err) => mapApiError(err, setErrors),
    });
  };

  const handleDelete = () => {
    deleteMutation.mutate(team.id, {
      onSuccess: () => {
        toast.success('Команда удалена');
        setConfirmOpen(false);
        onOpenChange(false);
      },
      onError: (err) => {
        const message = err instanceof ApiError ? err.message : 'Не удалось удалить команду';
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
        title="Изменить команду"
        description="Лидер опционален; если задан — всегда входит в участники."
        dismissible={!isSubmitting}
        footer={
          <div className="flex w-full items-center justify-between gap-2">
            {canDelete ? (
              <Button variant="danger" onClick={() => setConfirmOpen(true)} disabled={isSubmitting}>
                <Trash2 className="h-4 w-4" />
                Удалить
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                Отмена
              </Button>
              <Button type="submit" form="edit-team-form" loading={isSubmitting}>
                Сохранить
              </Button>
            </div>
          </div>
        }
      >
        <form
          id="edit-team-form"
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <TeamFormFields
            name={name}
            setName={setName}
            leaderId={leaderId}
            setLeaderId={setLeaderId}
            memberIds={memberIds}
            setMemberIds={setMemberIds}
            mailGroupId={mailGroupId}
            setMailGroupId={setMailGroupId}
            mailTeams={mailTeams}
            errors={errors}
            setErrors={setErrors}
            users={users}
          />
        </form>
      </Modal>

      <Modal
        open={confirmOpen}
        onOpenChange={(next) => !deleteMutation.isPending && setConfirmOpen(next)}
        title="Удалить команду?"
        description={`Команда «${team.name}» будет удалена. Действие необратимо.`}
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
          Участники не удаляются — снимается только их членство в команде.
        </p>
      </Modal>
    </>
  );
}
