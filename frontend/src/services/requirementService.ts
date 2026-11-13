import { apiClient } from './api';
import type { Requirement, RequirementCreate, PaginatedRequirements, PaginationParams } from '../types';

export async function listRequirements(params?: PaginationParams & { buyer_id?: string }): Promise<PaginatedRequirements> {
  const res = await apiClient.get<PaginatedRequirements>('/requirements', { params });
  return res.data;
}
export async function getRequirement(id: string): Promise<Requirement> {
  const res = await apiClient.get<Requirement>(`/requirements/${id}`);
  return res.data;
}
export async function createRequirement(data: RequirementCreate): Promise<Requirement> {
  const res = await apiClient.post<Requirement>('/requirements', data);
  return res.data;
}
export async function updateRequirement(id: string, data: Partial<RequirementCreate & { status: string }>): Promise<Requirement> {
  const res = await apiClient.patch<Requirement>(`/requirements/${id}`, data);
  return res.data;
}
export async function closeRequirement(id: string): Promise<void> {
  await apiClient.delete(`/requirements/${id}`);
}
export async function listBuyerRequirements(buyerId: string, params?: PaginationParams): Promise<PaginatedRequirements> {
  const res = await apiClient.get<PaginatedRequirements>(`/requirements/buyer/${buyerId}`, { params });
  return res.data;
}
