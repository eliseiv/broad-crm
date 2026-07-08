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
import { useCreateTeam, useDeleteTeam, useUpdateTeam } from '@/features/teams/hooks';
import type { TeamCreateRequest, TeamListItem, TeamUpdateRequest, UserListItem } from '@/types/api';

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

type Errors = { name?: string; leader_id?: string; member_ids?: string };

function userOptions(users: UserListItem[]): (SelectOption & MultiSelectOption)[] {
  return users.map((u) => ({ value: u.id, label: u.username }));
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

/** Общая форма (Название / Лидер / Участники). Лидер зафиксирован как участник. */
function TeamFormFields({
  name,
  setName,
  leaderId,
  setLeaderId,
  memberIds,
  setMemberIds,
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
  errors: Errors;
  setErrors: (u: (prev: Errors) => Errors) => void;
  users: UserListItem[];
}) {
  const options = userOptions(users);
  const noUsers = users.length === 0;
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
      <Select
        label="Лидер"
        options={options}
        value={leaderId}
        error={errors.leader_id}
        disabled={noUsers}
        onChange={(e) => {
          setLeaderId(e.target.value);
          // Лидер не может числиться в member_ids (он добавляется автоматически).
          setMemberIds(memberIds.filter((id) => id !== e.target.value));
          if (errors.leader_id) setErrors((p) => ({ ...p, leader_id: undefined }));
        }}
      />
      <MultiSelect
        label="Участники"
        value={memberIds}
        options={options}
        onChange={setMemberIds}
        lockedValues={leaderId ? [leaderId] : []}
        error={errors.member_ids}
        emptyHint="Нет пользователей"
        disabled={noUsers}
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
  const [leaderId, setLeaderId] = useState(users[0]?.id ?? '');
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [errors, setErrors] = useState<Errors>({});
  const createMutation = useCreateTeam();
  const noUsers = users.length === 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const next: Errors = {};
    const nErr = validateName(name);
    if (nErr) next.name = nErr;
    if (!leaderId) next.leader_id = 'Выберите лидера';
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    const payload: TeamCreateRequest = {
      name: name.trim(),
      leader_id: leaderId,
      member_ids: memberIds.filter((id) => id !== leaderId),
    };
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
      description="Лидер и участники команды. Лидер всегда входит в участники."
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
  const initialMembers = team.members.map((m) => m.id).filter((id) => id !== team.leader_id);
  const [name, setName] = useState(team.name);
  const [leaderId, setLeaderId] = useState(team.leader_id);
  const [memberIds, setMemberIds] = useState<string[]>(initialMembers);
  const [errors, setErrors] = useState<Errors>({});
  const [confirmOpen, setConfirmOpen] = useState(false);
  const updateMutation = useUpdateTeam(team.id);
  const deleteMutation = useDeleteTeam();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const next: Errors = {};
    const nErr = validateName(name);
    if (nErr) next.name = nErr;
    if (!leaderId) next.leader_id = 'Выберите лидера';
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    // Отправляем ТОЛЬКО изменённые поля (04-api.md: exclude_unset).
    const payload: TeamUpdateRequest = {};
    if (name.trim() !== team.name) payload.name = name.trim();
    if (leaderId !== team.leader_id) payload.leader_id = leaderId;
    const nextMembers = memberIds.filter((id) => id !== leaderId);
    const changedMembers =
      nextMembers.length !== initialMembers.length ||
      !nextMembers.every((id) => initialMembers.includes(id)) ||
      leaderId !== team.leader_id;
    if (changedMembers) payload.member_ids = nextMembers;

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
        description="Лидер всегда входит в участники."
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
