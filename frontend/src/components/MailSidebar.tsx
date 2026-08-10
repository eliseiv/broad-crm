import {
  Inbox,
  Mail,
  Pencil,
  Send,
  Tag,
  Trash2,
} from 'lucide-react';
import { MailTagChip } from '@/components/MailTagChip';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import type { MailTagFull } from '@/types/api';

export type MailNavFolder = 'inbox' | 'sent' | 'deleted' | 'tagged';
export type MailAdminView = 'mailboxes' | 'tags' | null;

interface TeamOption {
  value: string;
  label: string;
}

interface MailSidebarProps {
  navFolder: MailNavFolder;
  onNavFolderChange: (folder: MailNavFolder) => void;
  unreadCount: number;
  showTeamFilter: boolean;
  teamOptions: TeamOption[];
  teamFilter: string;
  onTeamFilterChange: (value: string) => void;
  tags: MailTagFull[];
  tagId: string | undefined;
  onTagIdChange: (tagId: string | undefined) => void;
  adminView: MailAdminView;
  onAdminViewChange: (view: MailAdminView) => void;
  onComposeClick?: () => void;
  className?: string;
}

function NavItem({
  active,
  icon,
  label,
  badge,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  badge?: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        active
          ? 'bg-surface-2 text-text-primary'
          : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
      )}
    >
      <span className="shrink-0" aria-hidden="true">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {badge !== undefined && badge > 0 && (
        <span className="shrink-0 rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-semibold text-accent">
          {badge}
        </span>
      )}
    </button>
  );
}

/**
 * Gmail-like сайдбар страницы «Почты» (ADR-071, 08-design-system.md).
 */
export function MailSidebar({
  navFolder,
  onNavFolderChange,
  unreadCount,
  showTeamFilter,
  teamOptions,
  teamFilter,
  onTeamFilterChange,
  tags,
  tagId,
  onTagIdChange,
  adminView,
  onAdminViewChange,
  onComposeClick,
  className,
}: MailSidebarProps) {
  const selectFolder = (folder: MailNavFolder) => {
    onAdminViewChange(null);
    onNavFolderChange(folder);
    if (folder !== 'tagged') {
      onTagIdChange(undefined);
    }
  };

  return (
    <aside
      className={cn(
        'flex h-full w-[220px] shrink-0 flex-col border-r border-border-subtle bg-surface-1',
        className,
      )}
    >
      <div className="shrink-0 p-3">
        <Button
          variant="primary"
          className="w-full"
          onClick={onComposeClick}
          aria-label="Написать новое письмо"
        >
          <Pencil className="h-4 w-4" aria-hidden="true" />
          Написать
        </Button>
      </div>

      <nav aria-label="Папки почты" className="flex flex-col gap-0.5 px-2">
        <NavItem
          active={adminView === null && navFolder === 'inbox'}
          icon={<Inbox className="h-4 w-4" />}
          label="Входящие"
          badge={unreadCount}
          onClick={() => selectFolder('inbox')}
        />
        <NavItem
          active={adminView === null && navFolder === 'sent'}
          icon={<Send className="h-4 w-4" />}
          label="Отправленные"
          onClick={() => selectFolder('sent')}
        />
        <NavItem
          active={adminView === null && navFolder === 'deleted'}
          icon={<Trash2 className="h-4 w-4" />}
          label="Удалённые"
          onClick={() => selectFolder('deleted')}
        />
        <NavItem
          active={adminView === null && navFolder === 'tagged'}
          icon={<Tag className="h-4 w-4" />}
          label="С тегами"
          onClick={() => {
            onAdminViewChange(null);
            onNavFolderChange('tagged');
            onTagIdChange(undefined);
          }}
        />
      </nav>

      {showTeamFilter && (
        <div className="mt-4 px-2">
          <p className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wide text-text-tertiary">
            Команды
          </p>
          <div className="flex flex-col gap-0.5">
            {teamOptions.map((opt) => (
              <button
                key={opt.value || '__all__'}
                type="button"
                onClick={() => onTeamFilterChange(opt.value)}
                className={cn(
                  'rounded-md px-3 py-1.5 text-left text-sm transition-colors',
                  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                  teamFilter === opt.value
                    ? 'bg-surface-2 font-medium text-text-primary'
                    : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto px-2">
        <p className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wide text-text-tertiary">
          Теги
        </p>
        <button
          type="button"
          onClick={() => onTagIdChange(undefined)}
          className={cn(
            'mb-1 w-full rounded-md px-3 py-1.5 text-left text-sm transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            !tagId
              ? 'bg-surface-2 font-medium text-text-primary'
              : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
          )}
        >
          Все теги
        </button>
        <div className="flex flex-col gap-1 px-1">
          {tags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              onClick={() => {
                onAdminViewChange(null);
                onNavFolderChange('inbox');
                onTagIdChange(tag.id);
              }}
              className={cn(
                'rounded-md px-2 py-1 transition-colors',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                tagId === tag.id ? 'bg-surface-2' : 'hover:bg-surface-3',
              )}
            >
              <MailTagChip name={tag.name} color={tag.color} dot />
            </button>
          ))}
        </div>
      </div>

      <div className="shrink-0 border-t border-border-subtle p-2">
        <button
          type="button"
          onClick={() => onAdminViewChange('mailboxes')}
          className={cn(
            'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            adminView === 'mailboxes'
              ? 'bg-surface-2 font-medium text-text-primary'
              : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
          )}
        >
          <Mail className="h-4 w-4 shrink-0" aria-hidden="true" />
          Почты
        </button>
        <button
          type="button"
          onClick={() => onAdminViewChange('tags')}
          className={cn(
            'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            adminView === 'tags'
              ? 'bg-surface-2 font-medium text-text-primary'
              : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
          )}
        >
          <Tag className="h-4 w-4 shrink-0" aria-hidden="true" />
          Теги
        </button>
      </div>
    </aside>
  );
}
