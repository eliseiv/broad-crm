import type { SelectOption } from '@/components/ui/Select';
import type { MeResponse, TeamRef } from '@/types/api';

/**
 * ЕДИНЫЙ источник опций команд канала и ЕДИНОЕ правило рендера фильтра «Команда»
 * (ADR-055 §6.2/§6.3, 08-design-system.md «Фильтр «Команда» — единое правило на пяти
 * экранах»). Пять экранов: `/mail` «Сообщения» (серверный), `/mail` «Почты» (клиентский),
 * `/sms` «Сообщения» (серверный), Mini App `/tg/mail`, Mini App `/tg/sms` (серверные).
 *
 * Правило одно для ЛЮБОГО актора (ветвления «admin ↔ не-админ» в клиенте НЕТ):
 *   options_count = me.<channel>_teams.length + (me.<channel>_includes_unassigned ? 1 : 0)
 *   render_filter = options_count >= 2
 * «Все команды» в счёт НЕ идёт. Отдельной ветки «`sees_all_*` → рендерить всегда» НЕТ.
 *
 * Источник опций — ТОЛЬКО `GET /api/auth/me` (`mail_teams`/`sms_teams`; у admin-уровня там
 * ВСЕ команды системы). Ходить за `GET /api/teams` ради команд КАНАЛА запрещено: эндпоинт
 * гейтится `teams:view` (у mail/sms-оператора его нет ⇒ пустой список — прод-баг TD-050),
 * а из Mini App он не берётся вовсе. `GET /api/teams` остаётся только на «Пользователях»
 * и «Командах».
 *
 * Принцип (ADR-055 §6.3): **опция, которую пользователь не вправе выбрать, не показывается**
 * — «Без команды» рендерится только при `includes_unassigned` (а в форме ящика — только
 * admin-уровню, см. `MailboxFormModal`).
 */

/** Канал per-channel scope (ADR-055 §1). */
export type Channel = 'mail' | 'sms';

/** Эффективный scope команд канала текущего актора (из `GET /api/auth/me`, ADR-055 §5.1). */
export interface ChannelTeamScope {
  /** Команды канала, доступные актору (у admin-уровня — все команды системы). */
  teams: TeamRef[];
  /** Виден ли актору объект канала без команды (`team_id IS NULL`). */
  includesUnassigned: boolean;
}

/** Значение опции «Все команды» (сброс фильтра). */
export const ALL_TEAMS_VALUE = '';
/** Значение опции «Без команды» (объекты с `team_id = null`). */
export const NO_TEAM_VALUE = '__no_team__';

/** Нормативные лейблы опций фильтра (08-design-system.md). */
export const ALL_TEAMS_LABEL = 'Все команды';
export const NO_TEAM_LABEL = 'Без команды';

/** Значение фильтра «Команда»: `''` (все) · UUID команды · `__no_team__` (без команды). */
export type TeamFilterValue = string;

/** Число ВАРИАНТОВ ВЫБОРА канала («Все команды» не считается) — ADR-055 §6.2. */
export function channelOptionsCount(scope: ChannelTeamScope): number {
  return scope.teams.length + (scope.includesUnassigned ? 1 : 0);
}

/** Порог рендера фильтра «Команда»: ≥ 2 варианта канала (ADR-055 §6.2; порог владельца). */
export function shouldRenderTeamFilter(scope: ChannelTeamScope): boolean {
  return channelOptionsCount(scope) >= 2;
}

/**
 * Опции контрола: «Все команды» (первая) → команды канала (`value = team.id`, лейбл `name`)
 * → «Без команды» (последняя, ТОЛЬКО при `includesUnassigned`).
 */
export function teamFilterOptions(scope: ChannelTeamScope): SelectOption[] {
  const options: SelectOption[] = [
    { value: ALL_TEAMS_VALUE, label: ALL_TEAMS_LABEL },
    ...scope.teams.map((t) => ({ value: t.id, label: t.name })),
  ];
  if (scope.includesUnassigned) options.push({ value: NO_TEAM_VALUE, label: NO_TEAM_LABEL });
  return options;
}

/**
 * Значение фильтра → параметры СЕРВЕРНОЙ ленты (ADR-055 §5.3, 04-api.md): «Без команды» →
 * `no_team=true` (при этом `team_id` НЕ отправляется — параметры взаимоисключающи, оба →
 * `400 validation_error`); выбранная команда → `team_id=<uuid>`; сброс → ни одного.
 */
export function teamFilterParams(value: TeamFilterValue): {
  teamId?: string;
  noTeam?: boolean;
} {
  if (value === NO_TEAM_VALUE) return { noTeam: true };
  if (value === ALL_TEAMS_VALUE) return {};
  return { teamId: value };
}

/** Scope канала из ответа `/me` (Mini App: SSO-JWT, админ-стор не задействован). */
export function channelScopeFromMe(me: MeResponse | undefined, channel: Channel): ChannelTeamScope {
  if (!me) return { teams: [], includesUnassigned: false };
  return channel === 'mail'
    ? { teams: me.mail_teams, includesUnassigned: me.mail_includes_unassigned }
    : { teams: me.sms_teams, includesUnassigned: me.sms_includes_unassigned };
}
