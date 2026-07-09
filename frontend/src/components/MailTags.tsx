import { MailTagChip } from '@/components/MailTagChip';
import type { MailTag } from '@/types/api';

interface MailTagsProps {
  tags: MailTag[];
  /** Ограничение числа видимых чипов (остаток сворачивается в «+N»). Без лимита — перенос. */
  max?: number;
}

/**
 * Ряд тегов письма (08-design-system.md §«Тег-чип»). Единый `MailTagChip` по `tag.color`.
 * В списке (`max` задан) — компактно с лимитом и «+N», имя усекается (`truncate` + title);
 * в детали (`max` не задан) — без лимита, длинное имя переносится (`break-words`).
 */
export function MailTags({ tags, max }: MailTagsProps) {
  if (tags.length === 0) return null;

  const visible = max !== undefined ? tags.slice(0, max) : tags;
  const hidden = tags.length - visible.length;
  const wrap = max === undefined;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {visible.map((tag) => (
        <MailTagChip key={tag.id} name={tag.name} color={tag.color} wrap={wrap} />
      ))}
      {hidden > 0 && <span className="text-[11px] font-medium text-text-tertiary">+{hidden}</span>}
    </div>
  );
}
