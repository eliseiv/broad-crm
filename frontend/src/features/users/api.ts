import { apiRequest } from '@/lib/api';
import type {
  PermissionsCatalogResponse,
  RoleCreateRequest,
  RoleListItem,
  RoleListResponse,
  RoleUpdateRequest,
  UserCreateRequest,
  UserListItem,
  UserListResponse,
  UserUpdateRequest,
} from '@/types/api';

// --- Users (04-api.md «Users», require_admin) ---

export function listUsers(signal?: AbortSignal): Promise<UserListResponse> {
  return apiRequest<UserListResponse>('/users', { signal });
}

/** POST /api/users → 201 с созданным `UserListItem` (без пароля). */
export function createUser(payload: UserCreateRequest): Promise<UserListItem> {
  return apiRequest<UserListItem>('/users', { method: 'POST', body: payload });
}

/** PATCH /api/users/{id} → 200 с обновлённым `UserListItem`. */
export function updateUser(id: string, payload: UserUpdateRequest): Promise<UserListItem> {
  return apiRequest<UserListItem>(`/users/${id}`, { method: 'PATCH', body: payload });
}

/** DELETE /api/users/{id} → 204 (hard delete). */
export function deleteUser(id: string): Promise<void> {
  return apiRequest<void>(`/users/${id}`, { method: 'DELETE' });
}

// --- Roles (04-api.md «Roles», require_admin) ---

export function listRoles(signal?: AbortSignal): Promise<RoleListResponse> {
  return apiRequest<RoleListResponse>('/roles', { signal });
}

/** POST /api/roles → 201 с созданным `RoleListItem`. */
export function createRole(payload: RoleCreateRequest): Promise<RoleListItem> {
  return apiRequest<RoleListItem>('/roles', { method: 'POST', body: payload });
}

/** PATCH /api/roles/{id} → 200 с обновлённым `RoleListItem`. */
export function updateRole(id: string, payload: RoleUpdateRequest): Promise<RoleListItem> {
  return apiRequest<RoleListItem>(`/roles/${id}`, { method: 'PATCH', body: payload });
}

/** DELETE /api/roles/{id} → 204 (409 role_in_use если назначена пользователям). */
export function deleteRole(id: string): Promise<void> {
  return apiRequest<void>(`/roles/${id}`, { method: 'DELETE' });
}

// --- Permissions catalog (04-api.md «Permissions», require_admin) ---

export function getPermissionsCatalog(signal?: AbortSignal): Promise<PermissionsCatalogResponse> {
  return apiRequest<PermissionsCatalogResponse>('/permissions/catalog', { signal });
}
