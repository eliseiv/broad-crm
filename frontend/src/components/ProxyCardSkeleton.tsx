import { Card } from '@/components/ui/Card';

function Shimmer({ className }: { className?: string }) {
  return <div className={`skeleton-shimmer animate-shimmer rounded-md ${className ?? ''}`} />;
}

/** Skeleton-карточка прокси для состояния загрузки списка. */
export function ProxyCardSkeleton() {
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-5" aria-hidden="true">
      <div className="flex items-center gap-3">
        <Shimmer className="h-10 w-10 rounded-chip" />
        <div className="flex flex-col gap-2">
          <Shimmer className="h-5 w-32" />
          <Shimmer className="h-3 w-16" />
        </div>
      </div>
      <Shimmer className="h-10 w-full rounded-sub" />
      <Shimmer className="h-4 w-40" />
    </Card>
  );
}
