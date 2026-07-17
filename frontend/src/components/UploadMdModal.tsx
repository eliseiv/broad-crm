import { useRef, useState } from 'react';
import { FileText, UploadCloud } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useUploadMd } from '@/features/documents/hooks';
import { folderOptions } from '@/features/documents/tree';
import type { DocumentNode } from '@/types/api';

interface UploadMdModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  nodes: DocumentNode[];
  defaultParentId: string | null;
  onUploaded?: (node: DocumentNode) => void;
}

/**
 * Загрузка `.md`-файла как документа (POST /api/documents/upload, multipart). Валидация
 * расширения/размера/UTF-8 — на сервере (422 document_upload_invalid); клиент подсказывает
 * `.md` и обрабатывает ошибку тостом. Имя опц. (по умолчанию — имя файла без `.md`).
 */
export function UploadMdModal({
  open,
  onOpenChange,
  nodes,
  defaultParentId,
  onUploaded,
}: UploadMdModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [parentId, setParentId] = useState<string>(defaultParentId ?? '');
  const [fileError, setFileError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadMd();
  const isSubmitting = uploadMutation.isPending;
  const parentOptions = folderOptions(nodes);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0] ?? null;
    setFile(picked);
    setFileError(null);
    // Клиентская подсказка (не замена серверной валидации): не-.md сразу видно.
    if (picked && !picked.name.toLowerCase().endsWith('.md')) {
      setFileError('Ожидается файл с расширением .md');
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setFileError('Выберите .md-файл');
      return;
    }
    const form = new FormData();
    form.append('file', file);
    form.append('parent_id', parentId); // пусто = корень
    const trimmedName = name.trim();
    if (trimmedName) form.append('name', trimmedName);

    uploadMutation.mutate(form, {
      onSuccess: (node) => {
        toast.success('Файл загружен');
        onOpenChange(false);
        onUploaded?.(node);
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 422) {
          // document_upload_invalid: не-.md / размер / битый UTF-8.
          setFileError(err.message || 'Файл не прошёл проверку: только корректный .md');
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          toast.error('Родительская папка недоступна');
          return;
        }
        if (err instanceof ApiError && err.status === 403) {
          toast.error('Недостаточно прав');
          return;
        }
        toast.error(err instanceof ApiError ? err.message : 'Не удалось загрузить файл');
      },
    });
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title="Загрузить .md"
      description="Markdown-файл станет документом. Проверка формата и размера — на сервере."
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="doc-upload-form" loading={isSubmitting} disabled={!file}>
            Загрузить
          </Button>
        </>
      }
    >
      <form id="doc-upload-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-text-secondary">Файл</span>
          <input
            ref={inputRef}
            type="file"
            accept=".md,text/markdown"
            onChange={handleFileChange}
            className="sr-only"
            aria-invalid={Boolean(fileError)}
          />
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="flex items-center gap-3 rounded-[10px] border border-dashed border-border-strong bg-surface-2 px-3 py-4 text-left transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {file ? (
              <FileText className="h-5 w-5 shrink-0 text-accent" aria-hidden="true" />
            ) : (
              <UploadCloud className="h-5 w-5 shrink-0 text-text-tertiary" aria-hidden="true" />
            )}
            <span className="min-w-0 flex-1 truncate text-sm text-text-primary">
              {file ? file.name : 'Выберите .md-файл'}
            </span>
          </button>
          {fileError && (
            <p role="alert" className="text-[12px] text-status-red">
              {fileError}
            </p>
          )}
        </div>
        <Input
          label="Название (опционально)"
          value={name}
          maxLength={255}
          autoComplete="off"
          placeholder="По умолчанию — имя файла"
          onChange={(e) => setName(e.target.value)}
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
