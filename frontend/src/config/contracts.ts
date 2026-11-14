import CipherTradeEscrowABI from '../contracts/CipherTradeEscrowABI.json' with { type: 'json' };

export const ESCROW_CONTRACT_ADDRESS = '0x5FbDB2315678afecb367f032d93F642f64180aa3' as const;
export const ESCROW_CHAIN_ID = 31337;
export const ESCROW_RPC_URL = 'http://127.0.0.1:8545';
export const ESCROW_ABI = CipherTradeEscrowABI;

export interface EscrowContractConfig {
  address: string;
  chainId: number;
  rpcUrl: string;
  abi: typeof CipherTradeEscrowABI;
}

export const escrowConfig: EscrowContractConfig = {
  address: ESCROW_CONTRACT_ADDRESS,
  chainId: ESCROW_CHAIN_ID,
  rpcUrl: ESCROW_RPC_URL,
  abi: ESCROW_ABI,
};
