import { apiClient } from './api';
import type { User, UserPublic } from '../types';

export async function getUser(id: string): Promise<UserPublic> {
  const res = await apiClient.get<UserPublic>(`/users/${id}`);
  return res.data;
}
export async function searchUsers(params?: { q?: string; role?: string }): Promise<UserPublic[]> {
  const res = await apiClient.get<UserPublic[]>('/users', { params });
  return res.data;
}
export async function updateMe(data: { display_name?: string; wallet_address?: string }): Promise<User> {
  const res = await apiClient.patch<User>('/users/me', data);
  return res.data;
}
