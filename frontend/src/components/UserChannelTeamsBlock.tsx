import { useEffect, useId, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { Checkbox } from '@/components/ui/Checkbox';
import { cn } from '@/lib/cn';
import type { Channel } from '@/features/auth/channelTeams';
import type { TeamListItem } from '@/types/api';

/**
 * Свёрнутый блок канала («СМС» / «Почты») в форме пользователя (нормативно — ADR-055 §6.1,
 * 08-design-system.md «Блоки «СМС» и «Почты» в форме пользователя»).
 *
 * Модель (ADR-055 §1): блок задаёт **ДОПОЛНИТЕЛЬНЫЕ** команды канала сверх базового членства.
 * Базовые команды (блок «Команды») входят в scope обоих каналов ВСЕГДА — их чекбоксы
 * отмечены и `disabled` (снять нельзя), и в запрос (`*_extra_team_ids`) они НЕ включаются
 * (хранится только добавка — §2.3). Снятие команды в блоке «Команды» немедленно делает её
 * чекбокс здесь обычным НЕотмеченным (авто-простановки галочки не происходит).
 *
 * Примитива `ui/Collapsible` в ДС нет (TD-057) → ручной паттерн: кнопка-триггер с
 * `aria-expanded`/`aria-controls`, `ChevronDown` с `rotate-180`, содержимое монтируется при
 * раскрытии. Новый примитив ДС не вводится.
 */

interface ChannelCopy {
  /** Нормативный заголовок блока. */
  title: string;
  /**
   * ОБЯЗАТЕЛЬНАЯ подсказка под чекбоксом «Без команды» — РАЗНАЯ по каналам (ADR-055 §3.1/§6.1):
   * флаг симметричен по форме, но не по объёму (бесхозные номера появляются массово и
   * автоматически из синхронизации Twilio, бесхозные ящики заводит вручную только админ).
   */
  unassignedHint: string;
}

const COPY: Record<Channel, ChannelCopy> = {
  sms: {
    title: 'СМС',
    unassignedHint:
      'Даст доступ ко ВСЕМ номерам без команды — это весь ещё не распределённый поток из синхронизации Twilio, включая правку, удаление и перенос номера.',
  },
  mail: {
    title: 'Почты',
    unassignedHint:
      'Даст доступ к ящикам без команды (их заводит только администратор), включая правку, синк и удаление.',
  },
};

/** Пояснение под заголовком раскрытого блока (нормативная строка). */
const BLOCK_HINT = 'Дополнительные команды: пользователь видит и обрабатывает их наравне со своей.';
/** Вторичная подпись у базовой (неснимаемой) команды (нормативная строка). */
const BASE_TEAM_CAPTION = 'из блока «Команды»';
const NO_TEAM_LABEL = 'Без команды';

interface UserChannelTeamsBlockProps {
  channel: Channel;
  /** Все CRM-команды (`GET /api/teams`; страница «Пользователи» — admin-only, право есть). */
  teams: TeamListItem[];
  /** Базовое членство из блока «Команды» — реактивно (снятие там сразу отражается здесь). */
  baseTeamIds: string[];
  /** ДОБАВКА канала (без базовых) — то, что уходит в `*_extra_team_ids`. */
  extraTeamIds: string[];
  onExtraTeamIdsChange: (next: string[]) => void;
  /** Флаг «Без команды» канала (`*_extra_includes_unassigned`). */
  includesUnassigned: boolean;
  onIncludesUnassignedChange: (next: boolean) => void;
}

/** Сводка в заголовке свёрнутого блока (нормативные строки ADR-055 §6.1). */
function summary(title: string, extraCount: number, includesUnassigned: boolean): string {
  const base = extraCount > 0 ? `доп. команд: ${extraCount}` : 'доп. команд нет';
  return `${title} · ${base}${includesUnassigned ? ' + Без команды' : ''}`;
}

export function UserChannelTeamsBlock({
  channel,
  teams,
  baseTeamIds,
  extraTeamIds,
  onExtraTeamIdsChange,
  includesUnassigned,
  onIncludesUnassignedChange,
}: UserChannelTeamsBlockProps) {
  // Оба блока СВЁРНУТЫ по умолчанию — и в `add`, и в `edit` (ADR-055 §6.1).
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const hintId = `${panelId}-unassigned-hint`;
  const copy = COPY[channel];

  // Инвариант нормализации (ADR-055 §2.3) на стороне формы: команда, ставшая БАЗОВОЙ, не
  // может одновременно быть добавкой — она немедленно вычищается из `*_extra_team_ids`
  // (сервер вычитает пересечение и сам, но отправлять заведомо лишнее незачем).
  useEffect(() => {
    const cleaned = extraTeamIds.filter((id) => !baseTeamIds.includes(id));
    if (cleaned.length !== extraTeamIds.length) onExtraTeamIdsChange(cleaned);
  }, [baseTeamIds, extraTeamIds, onExtraTeamIdsChange]);

  const extraCount = extraTeamIds.filter((id) => !baseTeamIds.includes(id)).length;

  const toggleTeam = (teamId: string, checked: boolean) => {
    if (checked) {
      if (!extraTeamIds.includes(teamId)) onExtraTeamIdsChange([...extraTeamIds, teamId]);
    } else {
      onExtraTeamIdsChange(extraTeamIds.filter((id) => id !== teamId));
    }
  };

  return (
    <section className="rounded-sub border border-border-subtle bg-surface-1">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-[13px] font-semibold text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <span className="min-w-0 break-words">
          {summary(copy.title, extraCount, includesUnassigned)}
        </span>
        <ChevronDown
          className={cn(
            'h-4 w-4 shrink-0 text-text-tertiary transition-transform',
            open && 'rotate-180',
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div id={panelId} className="flex flex-col gap-2 border-t border-border-subtle px-3 py-3">
          <p className="text-[12px] leading-relaxed text-text-secondary">{BLOCK_HINT}</p>

          {teams.length === 0 ? (
            <p className="text-[13px] text-text-secondary">Пока нет команд</p>
          ) : (
            <div className="flex flex-col gap-2">
              {teams.map((team) => {
                const isBase = baseTeamIds.includes(team.id);
                return (
                  <div key={team.id} className="flex flex-wrap items-center gap-2">
                    <Checkbox
                      label={team.name}
                      // Базовая команда: отмечена и `disabled` (входит в scope канала всегда).
                      checked={isBase || extraTeamIds.includes(team.id)}
                      disabled={isBase}
                      onChange={(e) => toggleTeam(team.id, e.target.checked)}
                    />
                    {isBase && (
                      <span className="text-[12px] text-text-secondary">{BASE_TEAM_CAPTION}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Последний пункт — «Без команды» (доступ к объектам с `team_id = null`) + ОБЯЗАТЕЛЬНАЯ
              подсказка объёма (разная по каналам, ADR-055 §3.1/§6.1). */}
          <div className="flex flex-col gap-1 border-t border-border-subtle pt-2">
            <Checkbox
              label={NO_TEAM_LABEL}
              checked={includesUnassigned}
              // Подсказка программно связана с контролом (a11y, 08-design-system.md
              // «Подсказка под полем формы связывается с контролом»).
              aria-describedby={hintId}
              onChange={(e) => onIncludesUnassignedChange(e.target.checked)}
            />
            <p id={hintId} className="text-[12px] leading-relaxed text-text-secondary">
              {copy.unassignedHint}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
