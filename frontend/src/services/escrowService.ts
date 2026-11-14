import { apiClient } from './api.ts';
import type { EscrowDeal } from '../types';

export interface EscrowDealCreate {
  offer_id: string;
  amount_eth: number;
  deadline_ts: number;
  agreement_hash?: string;
}

export interface VerifyFundingPayload {
  tx_hash: string;
}

/**
 * Create or retrieve an off-chain EscrowDeal linked to an accepted Offer.
 */
export async function createEscrowDeal(data: EscrowDealCreate): Promise<EscrowDeal> {
  const res = await apiClient.post<EscrowDeal>('/escrow/deals', data);
  return res.data;
}

/**
 * Fetch an EscrowDeal record by its backend UUID.
 */
export async function getEscrowDeal(id: string): Promise<EscrowDeal> {
  const res = await apiClient.get<EscrowDeal>(`/escrow/${id}`);
  return res.data;
}

/**
 * Fetch an EscrowDeal record linked to a specific Offer UUID.
 */
export async function getEscrowDealByOffer(offerId: string): Promise<EscrowDeal> {
  const res = await apiClient.get<EscrowDeal>(`/escrow/deals/by-offer/${offerId}`);
  return res.data;
}

/**
 * Submit an on-chain transaction hash to the backend verification endpoint.
 * POST /api/v1/escrow/deals/{deal_id}/verify-funding
 */
export async function verifyFunding(dealId: string, txHash: string): Promise<EscrowDeal> {
  const payload: VerifyFundingPayload = { tx_hash: txHash };
  const res = await apiClient.post<EscrowDeal>(`/escrow/deals/${dealId}/verify-funding`, payload);
  return res.data;
}

/**
 * Fetch deployed escrow contract info and ABI from the backend.
 */
export async function getContractInfo(): Promise<{ address: string; abi: unknown }> {
  const res = await apiClient.get('/escrow/contract-info');
  return res.data;
}
