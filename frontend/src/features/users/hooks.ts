import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createRole,
  createUser,
  deleteRole,
  deleteUser,
  getPermissionsCatalog,
  listRoles,
  listUsers,
  updateRole,
  updateUser,
} from '@/features/users/api';
import type {
  RoleCreateRequest,
  RoleUpdateRequest,
  UserCreateRequest,
  UserUpdateRequest,
} from '@/types/api';

export const usersKey = ['users'] as const;
export const rolesKey = ['roles'] as const;
export const permissionsCatalogKey = ['permissions-catalog'] as const;

// --- Users ---

export function useUsers() {
  return useQuery({
    queryKey: usersKey,
    queryFn: ({ signal }) => listUsers(signal),
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UserCreateRequest) => createUser(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: usersKey });
    },
  });
}

export function useUpdateUser(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UserUpdateRequest) => updateUser(id, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: usersKey });
    },
  });
}

export function useDeleteUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteUser(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: usersKey });
    },
  });
}

// --- Roles ---

export function useRoles() {
  return useQuery({
    queryKey: rolesKey,
    queryFn: ({ signal }) => listRoles(signal),
  });
}

export function useCreateRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: RoleCreateRequest) => createRole(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: rolesKey });
    },
  });
}

export function useUpdateRole(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: RoleUpdateRequest) => updateRole(id, payload),
    onSuccess: () => {
      // Смена прав роли может изменить доступ носителей — обновим и users, и roles.
      void queryClient.invalidateQueries({ queryKey: rolesKey });
      void queryClient.invalidateQueries({ queryKey: usersKey });
    },
  });
}

export function useDeleteRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteRole(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: rolesKey });
    },
  });
}

// --- Permissions catalog ---

/** Каталог прав для матрицы (страница×действия). Стабилен — большой staleTime. */
export function usePermissionsCatalog() {
  return useQuery({
    queryKey: permissionsCatalogKey,
    queryFn: ({ signal }) => getPermissionsCatalog(signal),
    staleTime: 5 * 60_000,
  });
}
