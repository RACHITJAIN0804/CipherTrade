import { apiClient } from './api';
import type { Offer, OfferCreate, PaginatedOffers } from '../types';

export async function createOffer(data: OfferCreate): Promise<Offer> {
  const res = await apiClient.post<Offer>('/offers', data);
  return res.data;
}
export async function listMyOffers(params?: { offer_status?: string; page?: number; page_size?: number }): Promise<PaginatedOffers> {
  const res = await apiClient.get<PaginatedOffers>('/offers/me', { params });
  return res.data;
}
export async function listOffersForRequirement(requirementId: string, params?: { page?: number; page_size?: number }): Promise<PaginatedOffers> {
  const res = await apiClient.get<PaginatedOffers>(`/offers/requirement/${requirementId}`, { params });
  return res.data;
}
export async function getOffer(id: string): Promise<Offer> {
  const res = await apiClient.get<Offer>(`/offers/${id}`);
  return res.data;
}
export async function acceptOffer(id: string): Promise<Offer> {
  const res = await apiClient.post<Offer>(`/offers/${id}/accept`);
  return res.data;
}
export async function rejectOffer(id: string): Promise<Offer> {
  const res = await apiClient.post<Offer>(`/offers/${id}/reject`);
  return res.data;
}
export async function withdrawOffer(id: string): Promise<Offer> {
  const res = await apiClient.post<Offer>(`/offers/${id}/withdraw`);
  return res.data;
}
export async function updateOffer(id: string, data: { price_eth?: number; delivery_deadline?: number; terms?: string }): Promise<Offer> {
  const res = await apiClient.patch<Offer>(`/offers/${id}`, data);
  return res.data;
}
