import { describe, expect, it } from 'vitest';
import {
  ALL_TEAMS_VALUE,
  NO_TEAM_VALUE,
  channelOptionsCount,
  channelScopeFromMe,
  shouldRenderTeamFilter,
  teamFilterOptions,
  teamFilterParams,
  type ChannelTeamScope,
} from '@/features/auth/channelTeams';
import type { MeResponse } from '@/types/api';

/**
 * Единое правило фильтра «Команда» (ADR-055 §6.2/§6.3) — чистые хелперы, общие для ВСЕХ
 * ПЯТИ экранов (`/mail` «Сообщения», `/mail` «Почты», `/sms` «Сообщения», Mini App
 * `/tg/mail`, Mini App `/tg/sms`). Здесь покрыто САМО правило; рендер на каждом из пяти
 * экранов — отдельными компонентными кейсами (нормативно: по кейсу на экран).
 *
 *   options_count = me.<channel>_teams.length + (me.<channel>_includes_unassigned ? 1 : 0)
 *   render_filter = options_count >= 2      // «Все команды» в счёт НЕ идёт
 *
 * ⚠️ Ветки «`sees_all_<channel>_teams === true` → рендерить всегда» НЕТ (правка редакции 2):
 * при НУЛЕ команд в системе она дала бы контрол с единственной опцией «Без команды».
 */

function scope(
  teams: { id: string; name: string }[],
  includesUnassigned = false,
): ChannelTeamScope {
  return { teams, includesUnassigned };
}

const T1 = { id: 't1', name: 'Продажи' };
const T2 = { id: 't2', name: 'Поддержка' };

describe('channelOptionsCount — «Все команды» не считается (ADR-055 §6.2)', () => {
  it('пустой scope → 0', () => {
    expect(channelOptionsCount(scope([]))).toBe(0);
  });

  it('одна команда без «Без команды» → 1', () => {
    expect(channelOptionsCount(scope([T1]))).toBe(1);
  });

  it('одна команда + «Без команды» → 2', () => {
    expect(channelOptionsCount(scope([T1], true))).toBe(2);
  });

  it('две команды → 2', () => {
    expect(channelOptionsCount(scope([T1, T2]))).toBe(2);
  });
});

describe('shouldRenderTeamFilter — порог 2 (ADR-055 §6.2, уточнение владельца)', () => {
  it('0 вариантов → контрол НЕ рендерится (не пустой, не disabled — отсутствует)', () => {
    expect(shouldRenderTeamFilter(scope([]))).toBe(false);
  });

  it('1 вариант (одна команда) → НЕ рендерится: фильтровать нечего', () => {
    expect(shouldRenderTeamFilter(scope([T1]))).toBe(false);
  });

  it('1 вариант (только «Без команды») → НЕ рендерится', () => {
    expect(shouldRenderTeamFilter(scope([], true))).toBe(false);
  });

  it('2 варианта (две команды) → рендерится', () => {
    expect(shouldRenderTeamFilter(scope([T1, T2]))).toBe(true);
  });

  it('2 варианта (команда + «Без команды») → рендерится', () => {
    expect(shouldRenderTeamFilter(scope([T1], true))).toBe(true);
  });

  it('ОТМЕНЁННАЯ норма: у актора admin-уровня с ОДНИМ вариантом контрола НЕТ', () => {
    // Ветка «sees_all → рендерить всегда» упразднена (ADR-055 §6.2, редакция 2): при нуле
    // команд в системе она дала бы мусорный контрол с единственной опцией «Без команды».
    // Правило `options_count >= 2` покрывает admin-уровень БЕЗ ветвления — вход тот же.
    expect(shouldRenderTeamFilter(scope([], true))).toBe(false);
  });
});

describe('teamFilterOptions — порядок и гейт «Без команды» (ADR-055 §6.2)', () => {
  it('«Все команды» первой → команды канала → «Без команды» последней', () => {
    expect(teamFilterOptions(scope([T1, T2], true))).toEqual([
      { value: ALL_TEAMS_VALUE, label: 'Все команды' },
      { value: 't1', label: 'Продажи' },
      { value: 't2', label: 'Поддержка' },
      { value: NO_TEAM_VALUE, label: 'Без команды' },
    ]);
  });

  it('без `includesUnassigned` опции «Без команды» НЕТ (её нельзя выбрать → не показываем)', () => {
    const labels = teamFilterOptions(scope([T1, T2])).map((o) => o.label);
    expect(labels).toEqual(['Все команды', 'Продажи', 'Поддержка']);
    expect(labels).not.toContain('Без команды');
  });
});

describe('teamFilterParams — «Без команды» → no_team=true (ADR-055 §5.3)', () => {
  it('«Без команды» → `noTeam: true` и `teamId` НЕ отправляется (иначе 400 validation_error)', () => {
    const params = teamFilterParams(NO_TEAM_VALUE);
    expect(params).toEqual({ noTeam: true });
    expect(params.teamId).toBeUndefined();
  });

  it('выбранная команда → `teamId`, `noTeam` не задан', () => {
    expect(teamFilterParams('t1')).toEqual({ teamId: 't1' });
  });

  it('сброс («Все команды») → ни одного параметра', () => {
    expect(teamFilterParams(ALL_TEAMS_VALUE)).toEqual({});
  });
});

describe('channelScopeFromMe — scope канала из `/api/auth/me` (ADR-055 §5.1)', () => {
  const me: MeResponse = {
    username: 'ivan',
    role: 'Оператор',
    is_superadmin: false,
    sees_all_sms_teams: false,
    sees_all_mail_teams: false,
    mail_teams: [T1, T2],
    sms_teams: [T1],
    mail_includes_unassigned: true,
    sms_includes_unassigned: false,
    permissions: { mail: ['view'] },
  };

  it('каналы независимы: `mail` и `sms` берут СВОИ поля', () => {
    expect(channelScopeFromMe(me, 'mail')).toEqual({
      teams: [T1, T2],
      includesUnassigned: true,
    });
    expect(channelScopeFromMe(me, 'sms')).toEqual({ teams: [T1], includesUnassigned: false });
  });

  it('`/me` ещё не доехал → пустой scope (фильтр не рендерится, лента НЕ ломается)', () => {
    expect(channelScopeFromMe(undefined, 'mail')).toEqual({
      teams: [],
      includesUnassigned: false,
    });
    expect(shouldRenderTeamFilter(channelScopeFromMe(undefined, 'sms'))).toBe(false);
  });
});
