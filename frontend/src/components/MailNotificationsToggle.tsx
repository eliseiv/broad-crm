import { Bell, BellOff } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { useIsSuperadmin } from '@/features/auth/hooks';
import { useMailSettings, useUpdateMailSettings } from '@/features/mail/hooks';

/**
 * Персональный переключатель Telegram-уведомлений почты (opt-out, ADR-044 §2, MAJOR-4):
 * `GET/PATCH /api/mail/me/settings`. Размещён в шапке вкладок `/mail` — виден на всех
 * вкладках и не зависит от фазы ленты. Доступен любому пользователю с `mail:view`.
 *
 * Супер-админу из `.env` контрол СКРЫТ (сервер отдаёт 403 на `/api/mail/me/settings`).
 * Основание — security (ADR-051 §1.6), а НЕ «нет БД-строки»: bootstrap-учётке запрещена
 * Telegram-привязка ⇒ уведомления ей не доставляются и настраивать нечего. Личная
 * прочитанность писем под этот запрет НЕ попадает и супер-админу доступна (ADR-051 §3).
 * Иконка отражает состояние: `Bell` — уведомления включены, `BellOff` — выключены;
 * `aria-pressed` = включено. Клик переключает; ошибка — toast без смены кэша.
 */
export function MailNotificationsToggle() {
  // Признак читается через единый хук `useIsSuperadmin` (features/auth), а не из стора
  // напрямую: это ЕДИНСТВЕННАЯ оставшаяся супер-админ-развилка в UI (ADR-051 §1.6/§3).
  const isSuperadmin = useIsSuperadmin();
  const settingsQuery = useMailSettings(!isSuperadmin);
  const updateMutation = useUpdateMailSettings();

  // Супер-админ (Telegram-привязка запрещена, сервер отдаёт 403 — ADR-051 §1.6) или иная
  // ошибка чтения → контрол не показываем.
  if (isSuperadmin || settingsQuery.isError) return null;

  const loading = settingsQuery.isLoading;
  const enabled = settingsQuery.data?.tg_notifications_enabled ?? true;

  const handleToggle = () => {
    updateMutation.mutate(!enabled, {
      onError: (err) => {
        const message = err instanceof ApiError ? err.message : 'Не удалось изменить настройку';
        toast.error(message);
      },
    });
  };

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={handleToggle}
      disabled={loading || updateMutation.isPending}
      aria-pressed={enabled}
      aria-busy={loading || updateMutation.isPending}
      title={
        enabled
          ? 'Telegram-уведомления о новых письмах включены. Нажмите, чтобы отключить.'
          : 'Telegram-уведомления о новых письмах отключены. Нажмите, чтобы включить.'
      }
      className="shrink-0 text-text-secondary hover:text-text-primary"
    >
      {enabled ? (
        <Bell className="h-4 w-4" aria-hidden="true" />
      ) : (
        <BellOff className="h-4 w-4" aria-hidden="true" />
      )}
      <span className="hidden sm:inline">Уведомления</span>
    </Button>
  );
}
