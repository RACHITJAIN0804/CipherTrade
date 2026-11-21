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

export interface ConsentPartyDetail {
  user_id: string;
  wallet_address: string;
  signed_at: string;
  chain_id: number;
  agreement_hash: string;
}

export interface ConsentStatusResponse {
  offer_id: string;
  deal_id?: string | null;
  agreement_hash: string;
  buyer_consented: boolean;
  seller_consented: boolean;
  both_consented: boolean;
  can_fund: boolean;
  buyer_consent?: ConsentPartyDetail | null;
  seller_consent?: ConsentPartyDetail | null;
}

export interface ConsentChallengeResponse {
  domain: {
    name: string;
    version: string;
    chainId: number;
    verifyingContract: string;
  };
  types: Record<string, Array<{ name: string; type: string }>>;
  primary_type: string;
  message: {
    agreementHash: string;
    offerId: string;
    dealId: string;
    partyRole: string;
    signer: string;
    nonce: string;
    deadline: number;
  };
  nonce: string;
  deadline: number;
}

export interface ConsentVerifyRequest {
  nonce: string;
  signature: string;
}

export interface ConsentVerifyResponse {
  status: string;
  offer_id: string;
  deal_id?: string | null;
  party_role: string;
  signer_wallet: string;
  agreement_hash: string;
  both_consented: boolean;
  can_fund: boolean;
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

/**
 * Fetch cryptographic agreement consent status for an escrow deal or offer.
 */
export async function getConsentStatus(
  targetId: string,
  isOfferId = false
): Promise<ConsentStatusResponse> {
  const endpoint = isOfferId
    ? `/escrow/offers/${targetId}/consent`
    : `/escrow/deals/${targetId}/consent`;
  const res = await apiClient.get<ConsentStatusResponse>(endpoint);
  return res.data;
}

/**
 * Request an EIP-712 agreement consent challenge for an escrow deal or offer.
 */
export async function requestConsentChallenge(
  targetId: string,
  isOfferId = false
): Promise<ConsentChallengeResponse> {
  const endpoint = isOfferId
    ? `/escrow/offers/${targetId}/consent/challenge`
    : `/escrow/deals/${targetId}/consent/challenge`;
  const res = await apiClient.post<ConsentChallengeResponse>(endpoint);
  return res.data;
}

/**
 * Submit signed EIP-712 challenge to record agreement consent.
 */
export async function verifyConsentSignature(
  targetId: string,
  payload: ConsentVerifyRequest,
  isOfferId = false
): Promise<ConsentVerifyResponse> {
  const endpoint = isOfferId
    ? `/escrow/offers/${targetId}/consent/verify`
    : `/escrow/deals/${targetId}/consent/verify`;
  const res = await apiClient.post<ConsentVerifyResponse>(endpoint, payload);
  return res.data;
}
