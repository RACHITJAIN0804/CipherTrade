import { apiClient } from './api';
import type { WalletChallengeResponse, WalletVerifyResponse } from '../types';

/**
 * Request an EIP-4361 SIWE challenge from the backend for the candidate wallet address.
 */
export async function requestWalletChallenge(walletAddress: string): Promise<WalletChallengeResponse> {
  const res = await apiClient.post<WalletChallengeResponse>('/auth/wallet/challenge', {
    wallet_address: walletAddress,
  });
  return res.data;
}

/**
 * Submit signed EIP-4361 SIWE challenge to link wallet address to user account.
 */
export async function verifyWalletSignature(
  message: string,
  signature: string
): Promise<WalletVerifyResponse> {
  const res = await apiClient.post<WalletVerifyResponse>('/auth/wallet/verify', {
    message,
    signature,
  });
  return res.data;
}

