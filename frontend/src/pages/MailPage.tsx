import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Inbox, Mail, Menu, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { MailboxesTab } from '@/components/MailboxesTab';
import { MailComposeModal } from '@/components/MailComposeModal';
import { MailDetail } from '@/components/MailDetail';
import { MailListItem } from '@/components/MailListItem';
import { MailListToolbar } from '@/components/MailListToolbar';
import { MailNotificationsToggle } from '@/components/MailNotificationsToggle';
import { MailSentDetail } from '@/components/MailSentDetail';
import { MailSentListItem } from '@/components/MailSentListItem';
import { MailSidebar, type MailAdminView, type MailNavFolder } from '@/components/MailSidebar';
import { TagsTab } from '@/components/TagsTab';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useCanViewPage, useChannelTeamScope } from '@/features/auth/hooks';
import {
  shouldRenderTeamFilter,
  teamFilterOptions,
  teamFilterParams,
} from '@/features/auth/channelTeams';
import {
  useBatchArchiveMail,
  useBatchDeleteMail,
  useBatchMarkMailRead,
  useBatchRestoreMail,
  useMailFeed,
  useMailMailboxes,
  useMailSentFeed,
  useMailTags,
  useMailUnreadCount,
  useMarkMailRead,
  useUnmarkMailRead,
} from '@/features/mail/hooks';
import { mailboxSearchKeywords } from '@/features/mail/mailboxSearch';
import type { ComboboxOption } from '@/components/ui/Combobox';

const SHELL_CLASS =
  'flex min-h-0 flex-1 flex-col overflow-hidden rounded-card border border-border-subtle bg-surface-1 shadow-card';

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-1 p-2">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="h-14 animate-pulse rounded-lg border border-border-subtle bg-surface-1"
        />
      ))}
    </div>
  );
}

function CenteredState({
  icon,
  title,
  hint,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 py-16 text-center">
      {icon}
      <div>
        <p className="text-base font-semibold text-text-primary">{title}</p>
        {hint && <p className="mt-1 text-[13px] text-text-secondary">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

export function MailPage() {
  const canView = useCanViewPage('mail');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <MailLayout />;
}

function MailLayout() {
  const [navFolder, setNavFolder] = useState<MailNavFolder>('inbox');
  const [teamFilter, setTeamFilter] = useState('');
  const [mailAccountId, setMailAccountId] = useState<number | undefined>(undefined);
  const [mailboxQuery, setMailboxQuery] = useState('Все почты');
  const [tagId, setTagId] = useState<string | undefined>(undefined);
  const [adminView, setAdminView] = useState<MailAdminView>(null);
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);

  const teamParams = teamFilterParams(teamFilter);
  const scopeFilter = useMemo(
    () => ({ ...teamParams, mailAccountId }),
    [teamParams, mailAccountId],
  );
  const mailScope = useChannelTeamScope('mail');
  const showTeamFilter = shouldRenderTeamFilter(mailScope);
  const teamOptions = useMemo(() => teamFilterOptions(mailScope), [mailScope]);
  const mailboxesQuery = useMailMailboxes();
  const mailboxOptions = useMemo<ComboboxOption[]>(() => {
    const mailboxes = mailboxesQuery.data?.mailboxes ?? [];
    return [
      { value: '', label: 'Все почты', pinned: true },
      ...mailboxes.map((mb) => ({
        value: String(mb.id),
        label: mb.display_name ? `${mb.display_name} ${mb.email}` : mb.email,
        keywords: mailboxSearchKeywords(mb),
      })),
    ];
  }, [mailboxesQuery.data]);
  const showMailboxFilter = (mailboxesQuery.data?.mailboxes?.length ?? 0) > 1;
  const tagsQuery = useMailTags();
  const tags = tagsQuery.data?.tags ?? [];

  const unreadQuery = useMailUnreadCount(scopeFilter);
  const unreadCount = unreadQuery.data?.count ?? 0;

  const feedFilter = useMemo(
    () => ({
      ...scopeFilter,
      folder:
        navFolder === 'deleted'
          ? ('deleted' as const)
          : navFolder === 'inbox' || navFolder === 'tagged'
            ? ('inbox' as const)
            : undefined,
      hasTags: navFolder === 'tagged' ? true : undefined,
      tagId,
    }),
    [scopeFilter, navFolder, tagId],
  );

  const inboxFeed = useMailFeed(feedFilter, navFolder !== 'sent');
  const sentFeed = useMailSentFeed(scopeFilter, navFolder === 'sent');

  const batchRead = useBatchMarkMailRead();
  const batchArchive = useBatchArchiveMail();
  const batchDelete = useBatchDeleteMail();
  const batchRestore = useBatchRestoreMail();

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectedInboxId, setSelectedInboxId] = useState<number | null>(null);
  const [selectedSentId, setSelectedSentId] = useState<string | null>(null);
  const [mobileDetail, setMobileDetail] = useState(false);

  const { mutate: markRead } = useMarkMailRead();
  const unmarkMutation = useUnmarkMailRead();

  const isSent = navFolder === 'sent';
  const activeFeed = isSent ? sentFeed : inboxFeed;
  const { phase, error, hasMore, isFetchingMore, isReloading, loadMore, reload } = activeFeed;

  const inboxMessages = inboxFeed.messages;
  const sentMessages = sentFeed.messages;

  const visibleInboxMessages = inboxMessages;

  useEffect(() => {
    setSelectedIds(new Set());
    setSelectedInboxId(null);
    setSelectedSentId(null);
    setMobileDetail(false);
  }, [navFolder, teamFilter, mailAccountId, tagId, adminView]);

  const showInboxDetail = selectedInboxId !== null;
  const showSentDetail = selectedSentId !== null;
  const showDetail = isSent ? showSentDetail : showInboxDetail;

  const closeDetail = () => {
    setMobileDetail(false);
    setSelectedInboxId(null);
    setSelectedSentId(null);
  };

  const selectedInbox = useMemo(
    () => visibleInboxMessages.find((m) => m.id === selectedInboxId) ?? null,
    [visibleInboxMessages, selectedInboxId],
  );

  const selectedSent = useMemo(
    () => sentMessages.find((m) => m.id === selectedSentId) ?? null,
    [sentMessages, selectedSentId],
  );

  useEffect(() => {
    if (isSent && selectedSentId !== null && !sentMessages.some((m) => m.id === selectedSentId)) {
      closeDetail();
    }
    if (
      !isSent &&
      selectedInboxId !== null &&
      !visibleInboxMessages.some((m) => m.id === selectedInboxId)
    ) {
      closeDetail();
    }
  }, [isSent, sentMessages, visibleInboxMessages, selectedSentId, selectedInboxId]);

  const lastMarkedIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (isSent || selectedInboxId === null) {
      lastMarkedIdRef.current = null;
      return;
    }
    if (lastMarkedIdRef.current === selectedInboxId) return;
    lastMarkedIdRef.current = selectedInboxId;
    markRead(selectedInboxId);
  }, [selectedInboxId, markRead, isSent]);

  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasMore || adminView) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore();
      },
      { rootMargin: '200px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, loadMore, adminView]);

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectedArray = useMemo(() => [...selectedIds], [selectedIds]);

  const handleBatchRead = () => {
    if (selectedArray.length === 0) return;
    batchRead.mutate(selectedArray, {
      onSuccess: () => setSelectedIds(new Set()),
    });
  };

  const handleBatchArchive = () => {
    if (selectedArray.length === 0) return;
    batchArchive.mutate(selectedArray, {
      onSuccess: () => setSelectedIds(new Set()),
    });
  };

  const handleBatchDelete = () => {
    if (selectedArray.length === 0) return;
    batchDelete.mutate(selectedArray, {
      onSuccess: () => setSelectedIds(new Set()),
    });
  };

  const handleBatchRestore = () => {
    if (selectedArray.length === 0) return;
    batchRestore.mutate(selectedArray, {
      onSuccess: () => setSelectedIds(new Set()),
    });
  };

  const shell = (children: React.ReactNode) => <div className={SHELL_CLASS}>{children}</div>;

  if (adminView === 'mailboxes') {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
          <Button variant="ghost" size="sm" onClick={() => setAdminView(null)}>
            ← Сообщения
          </Button>
          <MailNotificationsToggle />
        </div>
        <div className="scrollbar-none min-h-0 flex-1 overflow-y-auto p-4">
          <MailboxesTab />
        </div>
      </div>
    );
  }

  if (adminView === 'tags') {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
          <Button variant="ghost" size="sm" onClick={() => setAdminView(null)}>
            ← Сообщения
          </Button>
          <MailNotificationsToggle />
        </div>
        <div className="scrollbar-none min-h-0 flex-1 overflow-y-auto p-4">
          <TagsTab />
        </div>
      </div>
    );
  }

  const isAuthError = error instanceof ApiError && error.status === 401;

  if (phase === 'loading') {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden p-3">
        {shell(<ListSkeleton />)}
      </div>
    );
  }

  if (phase === 'not_configured') {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden p-3">
        {shell(
          <CenteredState
            icon={<Mail className="h-10 w-10 text-text-tertiary" aria-hidden="true" />}
            title="Сервис почт не настроен"
            hint="Обратитесь к администратору для настройки почтового сервиса."
          />,
        )}
      </div>
    );
  }

  if (phase === 'error') {
    if (isAuthError) {
      return (
        <div className="flex h-full min-h-0 flex-col overflow-hidden p-3">
          {shell(<ListSkeleton />)}
        </div>
      );
    }
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden p-3">
        {shell(
          <CenteredState
            icon={<AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />}
            title="Почтовый сервис временно недоступен"
            hint="Проверьте соединение и попробуйте снова."
            action={
              <Button variant="outline" onClick={reload} loading={isReloading}>
                Повторить
              </Button>
            }
          />,
        )}
      </div>
    );
  }

  const isEmpty = isSent ? sentMessages.length === 0 : inboxMessages.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-end gap-2 border-b border-border-subtle px-3 py-2 md:hidden">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setMobileSidebar(true)}
          aria-label="Открыть навигацию"
        >
          <Menu className="h-4 w-4" />
        </Button>
        <MailNotificationsToggle />
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3">
        {shell(
          <div className="flex min-h-0 flex-1 overflow-hidden">
            {mobileSidebar && (
              <button
                type="button"
                className="fixed inset-0 z-40 bg-black/40 md:hidden"
                aria-label="Закрыть навигацию"
                onClick={() => setMobileSidebar(false)}
              />
            )}
            <MailSidebar
              navFolder={navFolder}
              onNavFolderChange={setNavFolder}
              unreadCount={unreadCount}
              showTeamFilter={showTeamFilter}
              teamOptions={teamOptions}
              teamFilter={teamFilter}
              onTeamFilterChange={setTeamFilter}
              tags={tags}
              tagId={tagId}
              onTagIdChange={setTagId}
              adminView={adminView}
              onAdminViewChange={setAdminView}
              onComposeClick={() => {
                setMobileSidebar(false);
                setComposeOpen(true);
              }}
              className={cn(
                'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-50 max-md:shadow-lg',
                mobileSidebar ? 'max-md:flex' : 'max-md:hidden',
                'md:flex',
              )}
            />
            {mobileSidebar && (
              <Button
                variant="ghost"
                size="sm"
                className="fixed left-[188px] top-3 z-50 md:hidden"
                onClick={() => setMobileSidebar(false)}
                aria-label="Закрыть навигацию"
              >
                <X className="h-4 w-4" />
              </Button>
            )}

            <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden md:flex-row">
              <div
                className={cn(
                  'flex min-h-0 min-w-0 flex-col border-border-subtle',
                  showDetail && mobileDetail ? 'hidden md:flex' : 'flex',
                  showDetail ? 'md:w-[38%] md:flex-none md:border-r' : 'flex-1',
                )}
              >
                <MailListToolbar
                  navFolder={navFolder}
                  selectedCount={selectedIds.size}
                  onMarkRead={handleBatchRead}
                  onArchive={handleBatchArchive}
                  onDelete={handleBatchDelete}
                  onRestore={handleBatchRestore}
                  onRefresh={reload}
                  isRefreshing={isReloading}
                  markReadPending={batchRead.isPending}
                  archivePending={batchArchive.isPending}
                  deletePending={batchDelete.isPending}
                  restorePending={batchRestore.isPending}
                  showMailboxFilter={showMailboxFilter}
                  mailboxOptions={mailboxOptions}
                  mailboxValue={mailAccountId != null ? String(mailAccountId) : ''}
                  mailboxQuery={mailboxQuery}
                  onMailboxChange={(v) => {
                    const next = v ?? '';
                    setMailAccountId(next ? Number(next) : undefined);
                  }}
                  onMailboxQueryChange={setMailboxQuery}
                  mailboxesLoading={mailboxesQuery.isLoading}
                />

                <div className="scrollbar-none flex min-h-0 flex-1 flex-col overflow-y-auto">
                  {isEmpty ? (
                    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-10 text-center">
                      <Inbox className="h-9 w-9 text-text-tertiary" aria-hidden="true" />
                      <p className="text-sm font-semibold text-text-primary">
                        {isSent ? 'Отправленных писем нет' : 'Писем пока нет'}
                      </p>
                    </div>
                  ) : isSent ? (
                    <>
                      {sentMessages.map((message) => (
                        <MailSentListItem
                          key={message.id}
                          message={message}
                          isActive={message.id === selectedSentId}
                          onSelect={(id) => {
                            setSelectedSentId(id);
                            setMobileDetail(true);
                          }}
                        />
                      ))}
                    </>
                  ) : (
                    <>
                      {visibleInboxMessages.map((message) => (
                        <MailListItem
                          key={message.id}
                          message={message}
                          isActive={message.id === selectedInboxId}
                          onSelect={(id) => {
                            setSelectedInboxId(id);
                            setMobileDetail(true);
                          }}
                          showCheckbox
                          selected={selectedIds.has(message.id)}
                          onToggleSelect={toggleSelect}
                        />
                      ))}
                    </>
                  )}
                  <div ref={sentinelRef} aria-hidden="true" className="h-px shrink-0" />
                  {isFetchingMore && (
                    <div className="flex shrink-0 items-center justify-center gap-2 py-4 text-[12px] text-text-secondary">
                      <Spinner className="text-text-secondary" />
                      Загрузка…
                    </div>
                  )}
                </div>
              </div>

              {showDetail && (
                <div
                  className={cn(
                    'min-h-0 min-w-0 flex-1 flex-col overflow-hidden',
                    mobileDetail ? 'flex' : 'hidden md:flex',
                  )}
                >
                  <div className="hidden shrink-0 justify-end border-b border-border-subtle px-3 py-2 md:flex">
                    <MailNotificationsToggle />
                  </div>
                  {isSent
                    ? selectedSent && <MailSentDetail message={selectedSent} onBack={closeDetail} />
                    : selectedInbox && (
                        <MailDetail
                          message={selectedInbox}
                          onBack={closeDetail}
                          onMarkUnread={(id) => unmarkMutation.mutate(id)}
                          markUnreadPending={unmarkMutation.isPending}
                        />
                      )}
                </div>
              )}
            </div>
          </div>,
        )}
      </div>
      <MailComposeModal open={composeOpen} onOpenChange={setComposeOpen} />
    </div>
  );
}
