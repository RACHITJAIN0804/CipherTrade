export type UserRole = 'buyer' | 'seller';
export type OfferStatus = 'pending' | 'accepted' | 'rejected' | 'withdrawn';
export type RequirementStatus = 'active' | 'closed' | 'cancelled';
export type EscrowState = 'FUNDED' | 'CONFIRMED' | 'RELEASED' | 'REFUNDED' | 'CANCELLED' | 'DISPUTED' | 'RESOLVED';

export interface User {
  id: string;
  email: string;
  display_name: string;
  primary_role: UserRole;
  wallet_address: string | null;
  is_active: boolean;
  created_at: string;
}
export interface UserPublic {
  id: string;
  display_name: string;
  primary_role: UserRole;
  wallet_address?: string | null;
  created_at: string;
}
export interface TokenResponse { access_token: string; token_type: string; }
export interface LoginRequest { email: string; password: string; }
export interface UserCreate { email: string; display_name: string; primary_role: UserRole; password: string; }

export interface Requirement {
  id: string;
  buyer_id: string;
  title: string;
  description: string;
  category: string;
  keywords: string | null;
  budget_min: number | null;
  budget_max: number | null;
  status: RequirementStatus;
  created_at: string;
  updated_at: string;
}
export interface RequirementCreate {
  title: string;
  description: string;
  category: string;
  keywords?: string;
  budget_min?: number;
  budget_max?: number;
}
export interface PaginatedRequirements { items: Requirement[]; total: number; page: number; page_size: number; }

export interface Offer {
  id: string;
  requirement_id: string;
  seller_id: string;
  price_eth: number;
  delivery_deadline: number;
  terms: string;
  status: OfferStatus;
  created_at: string;
  updated_at: string;
}
export interface OfferCreate { requirement_id: string; price_eth: number; delivery_deadline: number; terms: string; }
export interface PaginatedOffers { items: Offer[]; total: number; page: number; page_size: number; }

export interface Conversation {
  id: string;
  initiator_id: string;
  recipient_id: string;
  offer_id: string | null;
  created_at: string;
}

export interface EscrowDeal {
  id: string;
  offer_id: string;
  buyer_id: string;
  seller_id: string;
  chain_deal_id: number | null;
  fund_tx_hash: string | null;
  amount_eth: number;
  deadline_ts: number;
  agreement_hash: string | null;
  on_chain_state: EscrowState | null;
  created_at: string;
  updated_at: string;
}

export interface ApiError { detail: string; }
export interface PaginationParams { page?: number; page_size?: number; q?: string; category?: string; }
