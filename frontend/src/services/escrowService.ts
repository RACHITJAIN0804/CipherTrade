import { apiClient } from './api.ts';
import type { EscrowDeal, EscrowTransaction, PaginatedEscrowDeals, EscrowState } from '../types';

export interface EscrowDealCreate {
  offer_id: string;
  amount_eth?: number;
  deadline_ts?: number;
  agreement_hash?: string;
}

export interface CanonicalAgreementResponse {
  deal_id?: string | null;
  offer_id: string;
  agreement_hash: string;
  canonical_agreement: Record<string, unknown>;
  schema_version: string;
}

export interface VerifyFundingPayload {
  tx_hash: string;
}

export interface ListDealsParams {
  page?: number;
  page_size?: number;
  deal_state?: EscrowState;
}

/**
 * Create or retrieve an off-chain EscrowDeal linked to an accepted Offer.
 */
export async function createEscrowDeal(data: EscrowDealCreate): Promise<EscrowDeal> {
  const res = await apiClient.post<EscrowDeal>('/escrow/deals', data);
  return res.data;
}

/**
 * Fetch an EscrowDeal record with transaction context by its backend UUID.
 */
export async function getEscrowDeal(id: string): Promise<EscrowTransaction> {
  const res = await apiClient.get<EscrowTransaction>(`/escrow/${id}`);
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
 * Fetch paginated escrow deals where the authenticated user is the buyer.
 */
export async function listBuyerDeals(params?: ListDealsParams): Promise<PaginatedEscrowDeals> {
  const res = await apiClient.get<PaginatedEscrowDeals>('/escrow/deals/buyer', { params });
  return res.data;
}

/**
 * Fetch paginated escrow deals where the authenticated user is the seller.
 */
export async function listSellerDeals(params?: ListDealsParams): Promise<PaginatedEscrowDeals> {
  const res = await apiClient.get<PaginatedEscrowDeals>('/escrow/deals/seller', { params });
  return res.data;
}

/**
 * Trigger backend on-chain state sync for a deal.
 */
export async function syncDealState(dealId: string): Promise<EscrowTransaction> {
  const res = await apiClient.post<EscrowTransaction>(`/escrow/deals/${dealId}/sync`);
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

/**
 * Fetch authoritative canonical agreement and hash for an escrow deal.
 */
export async function getCanonicalAgreement(dealId: string): Promise<CanonicalAgreementResponse> {
  const res = await apiClient.get<CanonicalAgreementResponse>(`/escrow/deals/${dealId}/agreement`);
  return res.data;
}

/**
 * Fetch authoritative canonical agreement and hash for an accepted offer.
 */
export async function getCanonicalAgreementByOffer(offerId: string): Promise<CanonicalAgreementResponse> {
  const res = await apiClient.get<CanonicalAgreementResponse>(`/escrow/offers/${offerId}/agreement`);
  return res.data;
}
