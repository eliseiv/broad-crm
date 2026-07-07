import { Plus } from 'lucide-react';

interface AddBackendCardProps {
  onClick: () => void;
}

/** Glass/blur карточка «+ Добавить» для бэков (зеркало AddProxyCard, 08-design-system.md). */
export function AddBackendCard({ onClick }: AddBackendCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex min-h-[200px] w-full flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong bg-surface-1/40 backdrop-blur-md transition-all duration-200 hover:-translate-y-0.5 hover:border-accent hover:bg-surface-1/60 hover:shadow-[0_0_0_1px_var(--accent),0_8px_24px_rgba(99,102,241,0.18)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-surface-3 text-accent transition-colors group-hover:bg-accent group-hover:text-white">
        <Plus className="h-7 w-7" aria-hidden="true" />
      </span>
      <span className="text-base font-semibold text-text-primary">Добавить</span>
      <span className="max-w-[220px] text-center text-[13px] text-text-secondary">
        Подключить новый бэк для мониторинга
      </span>
    </button>
  );
}
