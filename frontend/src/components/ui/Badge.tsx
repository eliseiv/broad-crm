import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

type Tone = 'green' | 'red' | 'yellow' | 'neutral' | 'accent';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  /** Показывать статус-точку слева. */
  dot?: boolean;
}

const tones: Record<Tone, { text: string; dot: string }> = {
  green: { text: 'text-status-green', dot: 'bg-status-green' },
  red: { text: 'text-status-red', dot: 'bg-status-red' },
  yellow: { text: 'text-status-yellow', dot: 'bg-status-yellow' },
  accent: { text: 'text-accent', dot: 'bg-accent' },
  neutral: { text: 'text-text-secondary', dot: 'bg-text-tertiary' },
};

/** Текстовый статус с опциональной точкой. Текст дублирует цвет (a11y). */
export function Badge({ tone = 'neutral', dot = true, className, children, ...props }: BadgeProps) {
  const t = tones[tone];
  return (
    <span
      className={cn('inline-flex items-center gap-1.5 text-[13px] font-medium', t.text, className)}
      {...props}
    >
      {dot && <span className={cn('h-2 w-2 rounded-full', t.dot)} aria-hidden="true" />}
      {children}
    </span>
  );
}
