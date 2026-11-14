import type { Eip1193Provider } from 'ethers';

declare global {
  interface Window {
    ethereum?: Eip1193Provider & {
      isMetaMask?: boolean;
      selectedAddress?: string | null;
      chainId?: string;
      on?: (event: string, handler: (...args: any[]) => void) => void;
      removeListener?: (event: string, handler: (...args: any[]) => void) => void;
      request: <T = unknown>(args: { method: string; params?: unknown[] | Record<string, unknown> }) => Promise<T>;
    };
  }
}

export {};

