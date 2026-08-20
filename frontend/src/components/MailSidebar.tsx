import { Inbox, Mail, Pencil, Send, Tag, Trash2 } from 'lucide-react';
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
  icon?: React.ReactNode;
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
      {icon && (
        <span className="shrink-0" aria-hidden="true">
          {icon}
        </span>
      )}
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
 * Gmail-like сайдбар страницы «Почты» (ADR-074, 08-design-system.md).
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

  const toggleTagged = () => {
    onAdminViewChange(null);
    if (adminView === null && navFolder === 'tagged') {
      onNavFolderChange('inbox');
      onTagIdChange(undefined);
    } else {
      onNavFolderChange('tagged');
      onTagIdChange(undefined);
    }
  };

  const selectTag = (id: string) => {
    onAdminViewChange(null);
    if (tagId === id) {
      onTagIdChange(undefined);
      onNavFolderChange('inbox');
      return;
    }
    onNavFolderChange('inbox');
    onTagIdChange(id);
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

      <nav
        aria-label="Папки почты"
        className="scrollbar-none flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2"
      >
        <NavItem
          active={adminView === null && navFolder === 'inbox' && !tagId}
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
          onClick={toggleTagged}
        />
        <NavItem
          active={adminView === 'mailboxes'}
          icon={<Mail className="h-4 w-4" />}
          label="Почты"
          onClick={() => onAdminViewChange('mailboxes')}
        />
        <NavItem
          active={adminView === 'tags'}
          icon={<Tag className="h-4 w-4" />}
          label="Теги"
          onClick={() => onAdminViewChange('tags')}
        />

        {tags.map((tag) => (
          <button
            key={tag.id}
            type="button"
            onClick={() => selectTag(tag.id)}
            className={cn(
              'flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              adminView === null && tagId === tag.id
                ? 'bg-surface-2 font-medium text-text-primary'
                : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
            )}
          >
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: tag.color }}
              aria-hidden="true"
            />
            <span className="min-w-0 truncate">{tag.name}</span>
          </button>
        ))}
      </nav>

      {showTeamFilter && (
        <div className="shrink-0 border-t border-border-subtle px-2 py-3">
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
    </aside>
  );
}
