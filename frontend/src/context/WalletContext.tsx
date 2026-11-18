import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { BrowserProvider, JsonRpcSigner, getAddress } from 'ethers';
import {
  ANVIL_CHAIN_ID,
  ANVIL_CHAIN_ID_HEX,
  ANVIL_RPC_URL,
  ANVIL_CHAIN_NAME,
  formatAddress,
} from '../utils/wallet';
import { useAuth } from './AuthContext';
import { requestWalletChallenge, verifyWalletSignature } from '../services/walletVerificationService';

export {
  ANVIL_CHAIN_ID,
  ANVIL_CHAIN_ID_HEX,
  ANVIL_RPC_URL,
  ANVIL_CHAIN_NAME,
  formatAddress,
};

const WALLET_CONNECTED_KEY = 'ciphertrade_wallet_connected';

export interface WalletContextValue {
  account: string | null;
  chainId: number | null;
  isConnected: boolean;
  isConnecting: boolean;
  isCorrectNetwork: boolean;
  walletError: string | null;
  isMetaMaskInstalled: boolean;
  provider: BrowserProvider | null;
  signer: JsonRpcSigner | null;
  connectWallet: () => Promise<void>;
  disconnectWallet: () => void;
  switchToAnvil: () => Promise<void>;
  clearWalletError: () => void;
  formatAddress: (addr?: string | null) => string;

  // Phase 5D.1: Cryptographic Wallet Ownership Verification (SIWE)
  isWalletVerified: boolean;
  isVerifyingWallet: boolean;
  walletVerificationError: string | null;
  verifyWalletOwnership: () => Promise<boolean>;
  clearWalletVerificationError: () => void;
}

const WalletContext = createContext<WalletContextValue | null>(null);


interface ProviderRpcError extends Error {
  code: number;
  data?: unknown;
}

export function WalletProvider({ children }: { children: React.ReactNode }) {
  const { user, setUser, refreshUser } = useAuth();

  const [account, setAccount] = useState<string | null>(null);
  const [chainId, setChainId] = useState<number | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [walletError, setWalletError] = useState<string | null>(null);
  const [provider, setProvider] = useState<BrowserProvider | null>(null);
  const [signer, setSigner] = useState<JsonRpcSigner | null>(null);

  // SIWE Verification state
  const [isVerifyingWallet, setIsVerifyingWallet] = useState(false);
  const [walletVerificationError, setWalletVerificationError] = useState<string | null>(null);

  const isMetaMaskInstalled = typeof window !== 'undefined' && !!window.ethereum;
  const isConnected = !!account;
  const isCorrectNetwork = chainId === ANVIL_CHAIN_ID;

  // True if connected MetaMask wallet matches the user's verified profile wallet_address
  const isWalletVerified = Boolean(
    account &&
    user?.wallet_address &&
    account.toLowerCase() === user.wallet_address.toLowerCase()
  );

  const clearWalletError = useCallback(() => {
    setWalletError(null);
  }, []);

  const clearWalletVerificationError = useCallback(() => {
    setWalletVerificationError(null);
  }, []);

  const disconnectWallet = useCallback(() => {
    setAccount(null);
    setChainId(null);
    setProvider(null);
    setSigner(null);
    setWalletError(null);
    setWalletVerificationError(null);
    setIsVerifyingWallet(false);
    try {
      localStorage.removeItem(WALLET_CONNECTED_KEY);
    } catch {
      // Ignore storage errors
    }
  }, []);


  const switchToAnvil = useCallback(async () => {
    if (!window.ethereum) {
      setWalletError('MetaMask is not installed. Please install MetaMask to switch networks.');
      return;
    }

    setWalletError(null);
    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: ANVIL_CHAIN_ID_HEX }],
      });
    } catch (switchError: unknown) {
      const err = switchError as ProviderRpcError;
      // 4902 indicates that the chain has not been added to MetaMask yet
      if (err.code === 4902 || err.message?.includes('Unrecognized chain ID')) {
        try {
          await window.ethereum.request({
            method: 'wallet_addEthereumChain',
            params: [
              {
                chainId: ANVIL_CHAIN_ID_HEX,
                chainName: ANVIL_CHAIN_NAME,
                rpcUrls: [ANVIL_RPC_URL],
                nativeCurrency: {
                  name: 'Ether',
                  symbol: 'ETH',
                  decimals: 18,
                },
              },
            ],
          });
        } catch (addError: unknown) {
          const addErr = addError as ProviderRpcError;
          if (addErr.code === 4001) {
            setWalletError('Network addition request was rejected in MetaMask.');
          } else {
            setWalletError(addErr.message || 'Failed to add Anvil Local network to MetaMask.');
          }
        }
      } else if (err.code === 4001) {
        setWalletError('Network switch request was rejected in MetaMask.');
      } else {
        setWalletError(err.message || 'Failed to switch to Anvil Local network.');
      }
    }
  }, []);

  const initProviderAndSigner = useCallback(async (ethereum: NonNullable<typeof window.ethereum>, targetAccount?: string) => {
    try {
      const browserProvider = new BrowserProvider(ethereum);
      const network = await browserProvider.getNetwork();
      const currentChainId = Number(network.chainId);
      setProvider(browserProvider);
      setChainId(currentChainId);

      let currentSigner: JsonRpcSigner | null = null;
      try {
        currentSigner = await browserProvider.getSigner();
        setSigner(currentSigner);
      } catch {
        setSigner(null);
      }

      if (targetAccount) {
        setAccount(getAddress(targetAccount));
      } else if (currentSigner) {
        const addr = await currentSigner.getAddress();
        setAccount(getAddress(addr));
      }
    } catch (err: unknown) {
      const msg = (err as Error)?.message || 'Failed to initialize wallet connection.';
      setWalletError(msg);
    }
  }, []);

  const connectWallet = useCallback(async () => {
    if (!window.ethereum) {
      setWalletError('MetaMask is not detected. Please install the MetaMask browser extension.');
      return;
    }

    setIsConnecting(true);
    setWalletError(null);

    try {
      const accounts = await window.ethereum.request<string[]>({
        method: 'eth_requestAccounts',
      });

      if (!accounts || accounts.length === 0) {
        throw new Error('No accounts authorized in MetaMask.');
      }

      await initProviderAndSigner(window.ethereum, accounts[0]);

      try {
        localStorage.setItem(WALLET_CONNECTED_KEY, 'true');
      } catch {
        // Ignore storage errors
      }
    } catch (err: unknown) {
      const rpcErr = err as ProviderRpcError;
      if (rpcErr.code === 4001) {
        setWalletError('Wallet connection rejected by user.');
      } else if (rpcErr.code === -32002) {
        setWalletError('MetaMask connection request already pending. Please open MetaMask.');
      } else {
        setWalletError(rpcErr.message || 'Failed to connect MetaMask wallet.');
      }
      disconnectWallet();
    } finally {
      setIsConnecting(false);
    }
  }, [disconnectWallet, initProviderAndSigner]);

  // Initial silent connection check on mount (without prompting user)
  useEffect(() => {
    let isMounted = true;

    async function checkExistingConnection() {
      if (!window.ethereum) return;

      const wasConnected = localStorage.getItem(WALLET_CONNECTED_KEY) === 'true';
      if (!wasConnected) return;

      try {
        const accounts = await window.ethereum.request<string[]>({
          method: 'eth_accounts',
        });

        if (isMounted && accounts && accounts.length > 0) {
          await initProviderAndSigner(window.ethereum, accounts[0]);
        } else if (isMounted) {
          disconnectWallet();
        }
      } catch {
        if (isMounted) disconnectWallet();
      }
    }

    checkExistingConnection();

    return () => {
      isMounted = false;
    };
  }, [disconnectWallet, initProviderAndSigner]);

  // Listen to MetaMask EIP-1193 events
  useEffect(() => {
    const ethereum = window.ethereum;
    if (!ethereum || !ethereum.on || !ethereum.removeListener) return;

    const handleAccountsChanged = (accounts: unknown) => {
      const accs = accounts as string[];
      if (!accs || accs.length === 0) {
        disconnectWallet();
      } else {
        initProviderAndSigner(ethereum, accs[0]);
      }
    };

    const handleChainChanged = (chainIdHex: unknown) => {
      const newChainId = parseInt(chainIdHex as string, 16);
      setChainId(newChainId);
      initProviderAndSigner(ethereum);
    };

    const handleDisconnect = () => {
      disconnectWallet();
    };

    ethereum.on('accountsChanged', handleAccountsChanged);
    ethereum.on('chainChanged', handleChainChanged);
    ethereum.on('disconnect', handleDisconnect);

    return () => {
      ethereum.removeListener?.('accountsChanged', handleAccountsChanged);
      ethereum.removeListener?.('chainChanged', handleChainChanged);
      ethereum.removeListener?.('disconnect', handleDisconnect);
    };
  }, [disconnectWallet, initProviderAndSigner]);

  const verifyWalletOwnership = useCallback(async (): Promise<boolean> => {
    if (!account || !signer) {
      setWalletVerificationError('Please connect your MetaMask wallet first.');
      return false;
    }
    if (chainId !== ANVIL_CHAIN_ID) {
      setWalletVerificationError('Please switch to the Anvil Local network before verifying.');
      return false;
    }

    setIsVerifyingWallet(true);
    setWalletVerificationError(null);

    try {
      // Step A: Request challenge from backend
      const challenge = await requestWalletChallenge(account);

      // Verify account hasn't changed before signing
      const currentSignerAddress = await signer.getAddress();
      if (currentSignerAddress.toLowerCase() !== account.toLowerCase()) {
        throw new Error('Active MetaMask account changed. Please try again.');
      }

      // Step B: Prompt user to sign EIP-4361 SIWE challenge in MetaMask
      const signature = await signer.signMessage(challenge.message);

      // Verify active account has not switched in MetaMask during user approval prompt (Fix 3)
      const postSignerAddress = await signer.getAddress();
      if (postSignerAddress.toLowerCase() !== account.toLowerCase()) {
        throw new Error('Active MetaMask account changed during signing. Please try again.');
      }

      // Step C: Submit signature to backend
      const result = await verifyWalletSignature(challenge.message, signature);

      // Step D: Update user profile in AuthContext
      if (result.user) {
        setUser(result.user);
      } else {
        await refreshUser();
      }

      return true;
    } catch (err: unknown) {
      const rpcErr = err as { code?: number | string; message?: string; response?: { data?: { detail?: string } } };
      let errorMsg = 'Failed to verify wallet ownership.';

      if (rpcErr.code === 4001 || rpcErr.code === 'ACTION_REJECTED') {
        errorMsg = 'Signature request was rejected in MetaMask.';
      } else if (rpcErr.response?.data?.detail) {
        errorMsg = rpcErr.response.data.detail;
      } else if (rpcErr.message) {
        errorMsg = rpcErr.message;
      }

      setWalletVerificationError(errorMsg);
      return false;
    } finally {
      setIsVerifyingWallet(false);
    }
  }, [account, chainId, signer, setUser, refreshUser]);

  return (
    <WalletContext.Provider
      value={{
        account,
        chainId,
        isConnected,
        isConnecting,
        isCorrectNetwork,
        walletError,
        isMetaMaskInstalled,
        provider,
        signer,
        connectWallet,
        disconnectWallet,
        switchToAnvil,
        clearWalletError,
        formatAddress,
        isWalletVerified,
        isVerifyingWallet,
        walletVerificationError,
        verifyWalletOwnership,
        clearWalletVerificationError,
      }}
    >
      {children}
    </WalletContext.Provider>
  );

}

export function useWallet(): WalletContextValue {
  const ctx = useContext(WalletContext);
  if (!ctx) {
    throw new Error('useWallet must be used inside a WalletProvider');
  }
  return ctx;
}
