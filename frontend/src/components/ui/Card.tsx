import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  interactive?: boolean;
}

/** Внешняя поверхность карточки сервера (--surface-1). */
export function Card({ className, interactive, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-card border border-border-subtle bg-surface-1 shadow-card',
        interactive &&
          'transition-all duration-200 hover:-translate-y-0.5 hover:border-border-strong',
        className,
      )}
      {...props}
    />
  );
}
