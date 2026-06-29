import { Card } from '@/components/ui/Card';

function Shimmer({ className }: { className?: string }) {
  return <div className={`skeleton-shimmer animate-shimmer rounded-md ${className ?? ''}`} />;
}

/** Skeleton-карточка для состояния загрузки списка. */
export function ServerCardSkeleton() {
  return (
    <Card className="flex flex-col gap-5 p-5 sm:p-6" aria-hidden="true">
      <div className="flex items-center gap-3">
        <Shimmer className="h-11 w-11 rounded-chip" />
        <div className="flex flex-col gap-2">
          <Shimmer className="h-5 w-32" />
          <Shimmer className="h-3 w-20" />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="flex flex-col gap-3 rounded-sub border border-border-subtle bg-surface-2 p-4"
          >
            <Shimmer className="h-9 w-24" />
            <Shimmer className="mx-auto h-28 w-28 rounded-full" />
            <Shimmer className="h-4 w-full" />
          </div>
        ))}
      </div>
    </Card>
  );
}
