import type { MailTag } from '@/types/api';

/** Цветная пилюля тега по `tag.color` (HEX). Единый вид в списке и в детали письма. */
function TagPill({ tag }: { tag: MailTag }) {
  return (
    <span
      className="inline-flex max-w-full items-center truncate rounded-full border px-2 py-0.5 text-[11px] font-medium"
      style={{
        color: tag.color,
        borderColor: `${tag.color}66`,
        backgroundColor: `${tag.color}1f`,
      }}
      title={tag.name}
    >
      {tag.name}
    </span>
  );
}

interface MailTagsProps {
  tags: MailTag[];
  /** Ограничение числа видимых пилюль (остаток сворачивается в «+N»). Без лимита — перенос. */
  max?: number;
}

/**
 * Ряд тегов письма (08-design-system.md «Страница «Почты»»). В списке — компактно с
 * лимитом и «+N» при переполнении; в детали — без лимита (перенос строк).
 */
export function MailTags({ tags, max }: MailTagsProps) {
  if (tags.length === 0) return null;

  const visible = max !== undefined ? tags.slice(0, max) : tags;
  const hidden = tags.length - visible.length;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {visible.map((tag) => (
        <TagPill key={tag.id} tag={tag} />
      ))}
      {hidden > 0 && <span className="text-[11px] font-medium text-text-tertiary">+{hidden}</span>}
    </div>
  );
}
