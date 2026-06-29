import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';

interface SpinnerProps {
  className?: string;
  label?: string;
}

export function Spinner({ className, label }: SpinnerProps) {
  return (
    <Loader2
      className={cn('h-4 w-4 animate-spin text-current', className)}
      aria-label={label}
      role={label ? 'status' : undefined}
      aria-hidden={label ? undefined : true}
    />
  );
}
