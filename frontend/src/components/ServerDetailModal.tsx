import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  DetailEditPencil,
  DetailRow,
  SecretRevealField,
  SecretStaticField,
} from '@/components/DetailFields';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { revealServerPassword } from '@/features/servers/api';
import { useUpdateServer } from '@/features/servers/hooks';
import type { Server } from '@/types/api';

interface ServerDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  server: Server;
  /** Право `servers:edit` — гейт карандаша (inline-edit) и кнопки-глаза reveal. */
  canEdit: boolean;
}

function validateName(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'Укажите название';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

/**
 * Read-only detail-модалка сервера (08-design-system.md «Detail-view», ADR-035; состав —
 * **ADR-049 §1 в редакции ADR-067 §6**) с инлайн-редактированием названия (ADR-039). Сразу,
 * **без сворачивания**, видны ПЯТЬ строк: **Название** (карандаш → inline-edit прямо в видимой
 * зоне, `PATCH name`, Сохранить/Отмена) → **IP** → **Пользователь** (`ssh_user`) → **Способ
 * входа** (`auth_method`) → **строка секрета**. Креды — основной рабочий контекст сервера,
 * поэтому свёрнутый блок **«Информация» УПРАЗДНЁН** (разворот ADR-046 §2в), а секция **«Бэки»
 * переехала на карточку** (`ServerCard`, ADR-049 §2) — дублировать её и здесь запрещено.
 *
 * Все строки рендерятся всегда: `ssh_user`/`auth_method` — NOT NULL, а материал секрета есть
 * при любом способе входа. Флагов `has_password`/`has_key` нет — наличие материала однозначно
 * определяется `auth_method` (CHECK `ck_servers_auth_material`, ADR-067 §1). Reveal пароля не
 * ослаблен: маска `••••••••`, значение — только по клику на глаз (ADR-049 §4); у key-сервера
 * раскрывать нечего — маска без глаза (ADR-067 §4).
 */
export function ServerDetailModal({ open, onOpenChange, server, canEdit }: ServerDetailModalProps) {
  const [editing, setEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState(server.name);
  const [nameError, setNameError] = useState<string | null>(null);
  const updateMutation = useUpdateServer(server.id);

  // Сброс inline-edit при закрытии модалки (следующее открытие — read-only).
  useEffect(() => {
    if (!open) {
      setEditing(false);
      setNameError(null);
    }
  }, [open]);

  const startEdit = () => {
    setNameDraft(server.name);
    setNameError(null);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setNameError(null);
  };

  const saveName = () => {
    const error = validateName(nameDraft);
    if (error) {
      setNameError(error);
      return;
    }
    const next = nameDraft.trim();
    if (next === server.name) {
      setEditing(false);
      return;
    }
    updateMutation.mutate(
      { name: next },
      {
        onSuccess: () => {
          toast.success('Сервер обновлён');
          setEditing(false);
        },
        onError: (err) => {
          if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
            setNameError('Некорректное название');
            return;
          }
          toast.error(err instanceof ApiError ? err.message : 'Не удалось обновить сервер');
        },
      },
    );
  };

  const isSaving = updateMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSaving && onOpenChange(next)}
      title="Просмотр"
      headerAction={canEdit && !editing ? <DetailEditPencil onClick={startEdit} /> : undefined}
    >
      <div className="flex flex-col gap-4">
        {editing ? (
          <div className="flex flex-col gap-2">
            <Input
              label="Название"
              value={nameDraft}
              error={nameError}
              autoFocus
              maxLength={64}
              disabled={isSaving}
              onChange={(e) => {
                setNameDraft(e.target.value);
                if (nameError) setNameError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  saveName();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  cancelEdit();
                }
              }}
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={cancelEdit} disabled={isSaving}>
                Отмена
              </Button>
              <Button size="sm" onClick={saveName} loading={isSaving}>
                Сохранить
              </Button>
            </div>
          </div>
        ) : (
          <DetailRow label="Название" value={server.name} />
        )}

        <DetailRow label="IP" value={server.ip} mono />

        {/* Порядок (нормативно, ADR-049 §1 в редакции ADR-067 §6):
            Название → IP → Пользователь → Способ входа → строка секрета. */}
        <DetailRow label="Пользователь" value={server.ssh_user} mono />
        <DetailRow
          label="Способ входа"
          value={server.auth_method === 'key' ? 'SSH-ключ' : 'Пароль'}
        />
        {/* Материал задаётся при создании сервера ⇒ секрет есть всегда: строка рендерится
            всегда (ADR-046 §3 — «секрет есть → строка есть»), но её ВИД зависит от способа
            входа (ADR-067 §6):
            • password → «Пароль», маска + глаз (гейт servers:edit, ADR-035/ADR-049 §4);
            • key      → «SSH-ключ», маска БЕЗ глаза — приватный ключ и парольная фраза
              write-only, reveal-эндпоинта нет by design (ADR-067 §4), поэтому глаз не
              рендерится ни при каком праве. */}
        {server.auth_method === 'key' ? (
          <SecretStaticField label="SSH-ключ" />
        ) : (
          <SecretRevealField
            label="Пароль"
            canReveal={canEdit}
            reveal={(signal) => revealServerPassword(server.id, signal)}
            showAria="Показать пароль"
            hideAria="Скрыть пароль"
          />
        )}
      </div>
    </Modal>
  );
}
