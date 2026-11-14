import { getAddress } from 'ethers';

export const ANVIL_CHAIN_ID = 31337;
export const ANVIL_CHAIN_ID_HEX = '0x7a69';
export const ANVIL_RPC_URL = 'http://127.0.0.1:8545';
export const ANVIL_CHAIN_NAME = 'Anvil Local';

export function formatAddress(address?: string | null): string {
  if (!address) return '';
  try {
    const formatted = getAddress(address);
    return `${formatted.slice(0, 6)}...${formatted.slice(-4)}`;
  } catch {
    if (address.length <= 10) return address;
    return `${address.slice(0, 6)}...${address.slice(-4)}`;
  }
}

