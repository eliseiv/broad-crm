import { useState } from 'react';
import { Pencil, Plus, Sparkles, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { MailTagChip } from '@/components/MailTagChip';
import { MailTagModal } from '@/components/MailTagModal';
import { matchModeLabel, ruleLabel, RULE_TYPE_OPTIONS } from '@/features/mail/tags';
import { ApiError } from '@/lib/api';
import {
  useApplyTag,
  useCreateTagRule,
  useDeleteTag,
  useDeleteTagRule,
} from '@/features/mail/hooks';
import type { MailTagFull, MailTagRuleType } from '@/types/api';

interface MailTagRowProps {
  tag: MailTagFull;
  /** Право `mail:tags` — управление каталогом (создание/правка/правила/удаление/apply). */
  canManage: boolean;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/**
 * Строка таблицы «Теги» (08-design-system.md «Вкладка Теги», ADR-038). Колонки: Имя тега
 * (цветной чип по палитре + признак «встроенный»), Правила (режим + человекочитаемые
 * строки правил, добавление/удаление под `mail:tags`), Действия (редактировать, применить
 * к существующим, удалить — встроенный тег удалить нельзя). Колонки «Тип» нет — тип входит
 * в строку правила.
 */
export function MailTagRow({ tag, canManage }: MailTagRowProps) {
  const [editOpen, setEditOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [newRuleType, setNewRuleType] = useState<MailTagRuleType>('subject_contains');
  const [newPattern, setNewPattern] = useState('');

  const createRule = useCreateTagRule();
  const deleteRule = useDeleteTagRule();
  const applyMutation = useApplyTag();
  const deleteTag = useDeleteTag();

  const handleAddRule = (e: React.FormEvent) => {
    e.preventDefault();
    const pattern = newPattern.trim();
    if (!pattern) return;
    createRule.mutate(
      { tagId: tag.id, payload: { type: newRuleType, pattern } },
      {
        onSuccess: () => {
          setNewPattern('');
          toast.success('Правило добавлено');
        },
        onError: (err) => toast.error(errorMessage(err, 'Не удалось добавить правило')),
      },
    );
  };

  const handleDeleteRule = (ruleId: string) => {
    deleteRule.mutate(
      { tagId: tag.id, ruleId },
      {
        onSuccess: () => toast.success('Правило удалено'),
        onError: (err) => toast.error(errorMessage(err, 'Не удалось удалить правило')),
      },
    );
  };

  const handleApply = () => {
    applyMutation.mutate(tag.id, {
      onSuccess: (res) => toast.success(`Тег применён к письмам: ${res.applied_count}`),
      onError: (err) => toast.error(errorMessage(err, 'Не удалось применить тег')),
    });
  };

  const handleDeleteTag = () => {
    deleteTag.mutate(tag.id, {
      onSuccess: () => {
        toast.success('Тег удалён');
        setConfirmOpen(false);
      },
      onError: (err) => toast.error(errorMessage(err, 'Не удалось удалить тег')),
    });
  };

  return (
    <tr className="border-t border-border-subtle align-top">
      <td className="px-3 py-3">
        <div className="flex flex-col gap-1">
          <MailTagChip name={tag.name} color={tag.color} dot wrap className="px-2.5 text-[12px]" />
          {tag.is_builtin && <span className="text-[11px] text-text-tertiary">встроенный</span>}
        </div>
      </td>

      <td className="px-3 py-3">
        <div className="flex flex-col gap-2">
          <span className="text-[12px] text-text-tertiary">
            Совпадение: {matchModeLabel(tag.match_mode)}
          </span>
          {tag.rules.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {tag.rules.map((rule) => (
                <li key={rule.id} className="flex items-start gap-1.5">
                  <span className="min-w-0 break-words text-[13px] text-text-primary">
                    {ruleLabel(rule)}
                  </span>
                  {canManage && (
                    <button
                      type="button"
                      onClick={() => handleDeleteRule(rule.id)}
                      disabled={deleteRule.isPending}
                      aria-label={`Удалить правило: ${ruleLabel(rule)}`}
                      className="mt-0.5 shrink-0 rounded p-0.5 text-text-tertiary transition-colors hover:bg-surface-3 hover:text-status-red disabled:opacity-60"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-[13px] text-text-secondary">Правил нет</span>
          )}

          {canManage && (
            <form onSubmit={handleAddRule} className="flex flex-wrap items-end gap-2 pt-1">
              <div className="w-52">
                <Select
                  aria-label="Тип правила"
                  options={RULE_TYPE_OPTIONS}
                  value={newRuleType}
                  onChange={(e) => setNewRuleType(e.target.value as MailTagRuleType)}
                />
              </div>
              <div className="w-44">
                <Input
                  aria-label="Паттерн правила"
                  placeholder="Паттерн"
                  value={newPattern}
                  maxLength={256}
                  onChange={(e) => setNewPattern(e.target.value)}
                />
              </div>
              <Button
                type="submit"
                variant="outline"
                size="sm"
                loading={createRule.isPending}
                disabled={!newPattern.trim()}
              >
                <Plus className="h-4 w-4" />
                Правило
              </Button>
            </form>
          )}
        </div>
      </td>

      <td className="px-3 py-3">
        {canManage ? (
          <div className="flex flex-wrap items-center justify-end gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleApply}
              loading={applyMutation.isPending}
            >
              <Sparkles className="h-4 w-4" />
              Применить к существующим
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditOpen(true)}
              aria-label={`Изменить тег ${tag.name}`}
              className="text-text-tertiary hover:text-text-primary"
            >
              <Pencil className="h-4 w-4" />
            </Button>
            <MailTagModal open={editOpen} onOpenChange={setEditOpen} mode="edit" tag={tag} />
            {tag.is_builtin ? (
              <span
                className="inline-flex h-8 w-8 items-center justify-center text-text-tertiary opacity-50"
                title="Встроенный тег нельзя удалить"
                aria-label="Встроенный тег нельзя удалить"
              >
                <Trash2 className="h-4 w-4" />
              </span>
            ) : (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmOpen(true)}
                  aria-label={`Удалить тег ${tag.name}`}
                  className="text-text-tertiary hover:text-status-red"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
                <Modal
                  open={confirmOpen}
                  onOpenChange={(next) => !deleteTag.isPending && setConfirmOpen(next)}
                  title="Удалить тег?"
                  description={`Тег «${tag.name}» будет удалён из каталога.`}
                  dismissible={!deleteTag.isPending}
                  footer={
                    <>
                      <Button
                        variant="ghost"
                        onClick={() => setConfirmOpen(false)}
                        disabled={deleteTag.isPending}
                      >
                        Отмена
                      </Button>
                      <Button
                        variant="danger"
                        loading={deleteTag.isPending}
                        onClick={handleDeleteTag}
                      >
                        Удалить
                      </Button>
                    </>
                  }
                >
                  <p className="text-sm text-text-secondary">
                    Тег будет снят со всех писем. Правила тега также удалятся.
                  </p>
                </Modal>
              </>
            )}
          </div>
        ) : (
          <span className="text-[13px] text-text-tertiary">—</span>
        )}
      </td>
    </tr>
  );
}
