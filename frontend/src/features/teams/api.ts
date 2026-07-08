import { apiRequest } from '@/lib/api';
import type {
  TeamCreateRequest,
  TeamListItem,
  TeamListResponse,
  TeamUpdateRequest,
} from '@/types/api';

// --- Teams (04-api.md «Teams», гейт require("teams", <action>)) ---

export function listTeams(signal?: AbortSignal): Promise<TeamListResponse> {
  return apiRequest<TeamListResponse>('/teams', { signal });
}

/** POST /api/teams → 201 с созданным `TeamListItem` (лидер включён в members). */
export function createTeam(payload: TeamCreateRequest): Promise<TeamListItem> {
  return apiRequest<TeamListItem>('/teams', { method: 'POST', body: payload });
}

/** PATCH /api/teams/{id} → 200 с обновлённым `TeamListItem`. */
export function updateTeam(id: string, payload: TeamUpdateRequest): Promise<TeamListItem> {
  return apiRequest<TeamListItem>(`/teams/${id}`, { method: 'PATCH', body: payload });
}

/** DELETE /api/teams/{id} → 204 (каскад user_teams). */
export function deleteTeam(id: string): Promise<void> {
  return apiRequest<void>(`/teams/${id}`, { method: 'DELETE' });
}
