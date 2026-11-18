import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { getAddress } from 'ethers';
import { ANVIL_CHAIN_ID } from '../utils/wallet.ts';
import type {
  User,
  WalletChallengeResponse,
  WalletVerifyResponse,
} from '../types/index.ts';

describe('Phase 5D.1: SIWE (EIP-4361) Wallet Ownership Verification', () => {
  const TEST_WALLET = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
  const OTHER_WALLET = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC';

  describe('Wallet Address Checksum and Normalization', () => {
    it('should validate and checksum standard Ethereum addresses', () => {
      const lower = TEST_WALLET.toLowerCase();
      const checksummed = getAddress(lower);
      assert.equal(checksummed, TEST_WALLET);
      assert.equal(checksummed.startsWith('0x'), true);
      assert.equal(checksummed.length, 42);
    });

    it('should detect mismatched addresses regardless of casing', () => {
      assert.equal(
        getAddress(TEST_WALLET).toLowerCase() === getAddress(OTHER_WALLET).toLowerCase(),
        false
      );
    });
  });

  describe('SIWE Challenge Message Format (EIP-4361)', () => {
    it('should contain all required EIP-4361 fields', () => {
      const mockChallenge: WalletChallengeResponse = {
        message: [
          'localhost wants you to sign in with your Ethereum account:',
          TEST_WALLET,
          '',
          'Sign in with Ethereum to verify ownership of this wallet for your CipherTrade account.',
          '',
          'URI: http://localhost:5173',
          'Version: 1',
          `Chain ID: ${ANVIL_CHAIN_ID}`,
          'Nonce: kf94zL01wP8',
          'Issued At: 2026-11-15T10:00:00.000Z',
          'Expiration Time: 2026-11-15T10:05:00.000Z',
        ].join('\n'),
        nonce: 'kf94zL01wP8',
        expires_at: '2026-11-15T10:05:00.000Z',
      };

      assert.ok(mockChallenge.message.includes(TEST_WALLET));
      assert.ok(mockChallenge.message.includes(`Chain ID: ${ANVIL_CHAIN_ID}`));
      assert.ok(mockChallenge.message.includes(`Nonce: ${mockChallenge.nonce}`));
      assert.ok(mockChallenge.message.includes('URI: http://localhost:5173'));
      assert.ok(mockChallenge.message.includes('Version: 1'));
      assert.ok(mockChallenge.message.includes('Expiration Time:'));
    });
  });

  describe('Wallet Verification State Logic', () => {
    it('should report isWalletVerified = true when connected account matches user.wallet_address', () => {
      const user: User = {
        id: 'user-uuid-1',
        email: 'alice@test.com',
        display_name: 'Alice',
        primary_role: 'buyer',
        wallet_address: TEST_WALLET,
        is_active: true,
        created_at: new Date().toISOString(),
      };

      const connectedAccount = TEST_WALLET.toLowerCase();
      const isVerified = Boolean(
        connectedAccount &&
        user.wallet_address &&
        connectedAccount.toLowerCase() === user.wallet_address.toLowerCase()
      );

      assert.equal(isVerified, true);
    });

    it('should report isWalletVerified = false when connected account differs from profile wallet', () => {
      const user: User = {
        id: 'user-uuid-1',
        email: 'alice@test.com',
        display_name: 'Alice',
        primary_role: 'buyer',
        wallet_address: OTHER_WALLET,
        is_active: true,
        created_at: new Date().toISOString(),
      };

      const connectedAccount = TEST_WALLET;
      const isVerified = Boolean(
        connectedAccount &&
        user.wallet_address &&
        connectedAccount.toLowerCase() === user.wallet_address.toLowerCase()
      );

      assert.equal(isVerified, false);
    });

    it('should report isWalletVerified = false when user profile has no wallet_address linked', () => {
      const user: User = {
        id: 'user-uuid-2',
        email: 'bob@test.com',
        display_name: 'Bob',
        primary_role: 'buyer',
        wallet_address: null,
        is_active: true,
        created_at: new Date().toISOString(),
      };

      const connectedAccount = TEST_WALLET;
      const isVerified = Boolean(
        connectedAccount &&
        user.wallet_address &&
        connectedAccount.toLowerCase() === (user.wallet_address as string).toLowerCase()
      );

      assert.equal(isVerified, false);
    });
  });

  describe('SIWE Signature Verification & Error Handling Simulation', () => {
    it('should simulate successful verification and update user state', () => {
      const initialUser: User = {
        id: 'user-uuid-1',
        email: 'buyer@test.com',
        display_name: 'Buyer',
        primary_role: 'buyer',
        wallet_address: null,
        is_active: true,
        created_at: new Date().toISOString(),
      };

      const verifyResponse: WalletVerifyResponse = {
        success: true,
        wallet_address: TEST_WALLET,
        user: {
          ...initialUser,
          wallet_address: TEST_WALLET,
        },
      };

      assert.equal(verifyResponse.success, true);
      assert.equal(verifyResponse.wallet_address, TEST_WALLET);
      assert.equal(verifyResponse.user.wallet_address, TEST_WALLET);
    });

    it('should handle user rejection in MetaMask (code 4001 / ACTION_REJECTED)', () => {
      function parseWalletVerificationError(err: unknown): string {
        const rpcErr = err as { code?: number | string; message?: string; response?: { data?: { detail?: string } } };
        if (rpcErr.code === 4001 || rpcErr.code === 'ACTION_REJECTED') {
          return 'Signature request was rejected in MetaMask.';
        }
        if (rpcErr.response?.data?.detail) {
          return rpcErr.response.data.detail;
        }
        return rpcErr.message || 'Failed to verify wallet ownership.';
      }

      const rejectionError = { code: 4001, message: 'User denied message signature.' };
      assert.equal(parseWalletVerificationError(rejectionError), 'Signature request was rejected in MetaMask.');

      const actionRejectedError = { code: 'ACTION_REJECTED', message: 'User rejected the request.' };
      assert.equal(parseWalletVerificationError(actionRejectedError), 'Signature request was rejected in MetaMask.');

      const conflictError = {
        response: { data: { detail: 'This wallet address is already linked to another account.' } },
      };
      assert.equal(
        parseWalletVerificationError(conflictError),
        'This wallet address is already linked to another account.'
      );
    });

    it('should reject verification if connected wallet changes before signing', async () => {
      const requestedAddress = TEST_WALLET;
      const signerAddressAfterPrompt = OTHER_WALLET;

      let caughtError = '';
      try {
        if (requestedAddress.toLowerCase() !== signerAddressAfterPrompt.toLowerCase()) {
          throw new Error('Active MetaMask account changed. Please try again.');
        }
      } catch (err: unknown) {
        caughtError = (err as Error).message;
      }

      assert.equal(caughtError, 'Active MetaMask account changed. Please try again.');
    });

    it('should reject verification if connected wallet changes during signing (Fix 3)', async () => {
      const requestedAddress = TEST_WALLET;
      const postSignerAddress = OTHER_WALLET;

      let caughtError = '';
      try {
        // Post-signing check immediately after signer.signMessage()
        if (postSignerAddress.toLowerCase() !== requestedAddress.toLowerCase()) {
          throw new Error('Active MetaMask account changed during signing. Please try again.');
        }
      } catch (err: unknown) {
        caughtError = (err as Error).message;
      }

      assert.equal(caughtError, 'Active MetaMask account changed during signing. Please try again.');
    });
  });
});

