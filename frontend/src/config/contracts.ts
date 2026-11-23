import CipherTradeEscrowABI from '../contracts/CipherTradeEscrowABI.json' with { type: 'json' };
import CipherTradeEscrowV2ABI from '../contracts/CipherTradeEscrowV2ABI.ts';

// ─── V1 (Legacy) ─────────────────────────────────────────────────────────────
/** Deployed V1 contract address (DO NOT change — legacy deals still use this). */
export const ESCROW_CONTRACT_ADDRESS = '0x5FbDB2315678afecb367f032d93F642f64180aa3' as const;
export const ESCROW_ABI = CipherTradeEscrowABI;

// ─── V2 (Dual-Consent) ───────────────────────────────────────────────────────
/**
 * Deployed CipherTradeEscrowV2 address.
 * Set via VITE_ESCROW_V2_CONTRACT_ADDRESS environment variable.
 * Must be populated after local Anvil deployment using `forge script DeployV2.s.sol`.
 *
 * IMPORTANT: Never set this to the V1 address. Never fall back to V1.
 * If this is empty, the frontend will show a configuration error for V2 funding.
 */
export const ESCROW_V2_CONTRACT_ADDRESS = (
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_ESCROW_V2_CONTRACT_ADDRESS) ||
  (typeof process !== 'undefined' && process.env?.VITE_ESCROW_V2_CONTRACT_ADDRESS) ||
  ''
) as string;

export const ESCROW_V2_ABI = CipherTradeEscrowV2ABI;

// ─── Shared Network Config ───────────────────────────────────────────────────
export const ESCROW_CHAIN_ID = 31337;
export const ESCROW_RPC_URL = 'http://127.0.0.1:8545';

// ─── Config Interfaces ───────────────────────────────────────────────────────
export interface EscrowContractConfig {
  address: string;
  chainId: number;
  rpcUrl: string;
  abi: typeof CipherTradeEscrowABI;
}

export interface EscrowV2ContractConfig {
  address: string;
  chainId: number;
  rpcUrl: string;
  abi: typeof CipherTradeEscrowV2ABI;
  isConfigured: boolean;
}

/** Legacy V1 config — for existing funded deals only. */
export const escrowConfig: EscrowContractConfig = {
  address: ESCROW_CONTRACT_ADDRESS,
  chainId: ESCROW_CHAIN_ID,
  rpcUrl: ESCROW_RPC_URL,
  abi: ESCROW_ABI,
};

/** V2 dual-consent config — for all new deal funding. */
export const escrowV2Config: EscrowV2ContractConfig = {
  address: ESCROW_V2_CONTRACT_ADDRESS,
  chainId: ESCROW_CHAIN_ID,
  rpcUrl: ESCROW_RPC_URL,
  abi: ESCROW_V2_ABI,
  isConfigured: Boolean(
    ESCROW_V2_CONTRACT_ADDRESS &&
    ESCROW_V2_CONTRACT_ADDRESS.startsWith('0x') &&
    ESCROW_V2_CONTRACT_ADDRESS.length === 42 &&
    ESCROW_V2_CONTRACT_ADDRESS.toLowerCase() !== ESCROW_CONTRACT_ADDRESS.toLowerCase()
  ),
};
