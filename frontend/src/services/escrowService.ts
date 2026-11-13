import { apiClient } from './api';
import type { EscrowDeal } from '../types';

export interface EscrowDealCreate { offer_id: string; amount_eth: number; deadline_ts: number; agreement_hash?: string; }

export async function createEscrowDeal(data: EscrowDealCreate): Promise<EscrowDeal> {
  const res = await apiClient.post<EscrowDeal>('/escrow/deals', data);
  return res.data;
}
export async function getEscrowDeal(id: string): Promise<EscrowDeal> {
  const res = await apiClient.get<EscrowDeal>(`/escrow/${id}`);
  return res.data;
}
export async function getContractInfo(): Promise<{ address: string; abi: unknown }> {
  const res = await apiClient.get('/escrow/contract-info');
  return res.data;
}
