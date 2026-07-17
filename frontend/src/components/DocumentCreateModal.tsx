import { useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useCreateDocument, useCreateFolder } from '@/features/documents/hooks';
import { folderOptions } from '@/features/documents/tree';
import type { DocumentNode } from '@/types/api';

interface DocumentCreateModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 'folder' — новая папка; 'document' — новый документ. */
  kind: 'folder' | 'document';
  /** Все узлы (для селектора родительской папки — пути). */
  nodes: DocumentNode[];
  /** Родитель по умолчанию (id выбранной папки или `null` = корень). */
  defaultParentId: string | null;
  /** Открыть созданный документ в редакторе (для kind='document'). */
  onCreated?: (node: DocumentNode) => void;
}

/**
 * Создание папки/документа (POST /folders | POST /documents). Родительская папка —
 * `Select` путей (`folderOptions`), по умолчанию — выбранная папка или корень. Имя 1–255.
 * Документ создаётся пустым; контент правится в редакторе (открывается после создания).
 */
export function DocumentCreateModal({
  open,
  onOpenChange,
  kind,
  nodes,
  defaultParentId,
  onCreated,
}: DocumentCreateModalProps) {
  // key-ремоунт (в родителе) гарантирует свежий стейт на каждое открытие.
  const [name, setName] = useState('');
  const [parentId, setParentId] = useState<string>(defaultParentId ?? '');
  const [nameError, setNameError] = useState<string | null>(null);
  const createFolder = useCreateFolder();
  const createDocument = useCreateDocument();

  const isFolder = kind === 'folder';
  const mutation = isFolder ? createFolder : createDocument;
  const isSubmitting = mutation.isPending;
  const parentOptions = folderOptions(nodes);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError('Укажите название');
      return;
    }
    if (trimmed.length > 255) {
      setNameError('Не более 255 символов');
      return;
    }
    const parent = parentId || null;
    const onError = (err: unknown) => {
      if (err instanceof ApiError && err.status === 404) {
        setNameError('Родительская папка недоступна');
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        toast.error('Недостаточно прав');
        return;
      }
      if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
        const detail = err.details?.find((d) => d.field === 'name')?.message;
        setNameError(detail ?? 'Проверьте название');
        return;
      }
      toast.error(err instanceof ApiError ? err.message : 'Не удалось создать');
    };

    if (isFolder) {
      createFolder.mutate(
        { parent_id: parent, name: trimmed },
        {
          onSuccess: () => {
            toast.success('Папка создана');
            onOpenChange(false);
          },
          onError,
        },
      );
    } else {
      createDocument.mutate(
        { parent_id: parent, name: trimmed, content_md: '' },
        {
          onSuccess: (node) => {
            toast.success('Документ создан');
            onOpenChange(false);
            onCreated?.(node);
          },
          onError,
        },
      );
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title={isFolder ? 'Новая папка' : 'Новый документ'}
      description={
        isFolder
          ? 'Папка группирует документы и вложенные папки.'
          : 'Документ создаётся пустым — содержимое можно добавить в редакторе.'
      }
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="doc-create-form" loading={isSubmitting}>
            Создать
          </Button>
        </>
      }
    >
      <form id="doc-create-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <Input
          label="Название"
          value={name}
          error={nameError}
          autoFocus
          maxLength={255}
          autoComplete="off"
          onChange={(e) => {
            setName(e.target.value);
            if (nameError) setNameError(null);
          }}
        />
        <Select
          label="Родительская папка"
          options={parentOptions}
          value={parentId}
          onChange={(e) => setParentId(e.target.value)}
        />
      </form>
    </Modal>
  );
}
