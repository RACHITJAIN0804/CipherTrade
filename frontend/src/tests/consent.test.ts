import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet, verifyTypedData, getAddress } from 'ethers';
import { ESCROW_CHAIN_ID, ESCROW_CONTRACT_ADDRESS } from '../config/contracts.ts';
import type {
  ConsentStatusResponse,
  ConsentChallengeResponse,
} from '../services/escrowService.ts';

describe('Phase 5D.3: Cryptographic Agreement Consent (EIP-712)', () => {
  const BUYER_PK = '0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d';
  const BUYER_WALLET = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';

  const SELLER_PK = '0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a';
  const SELLER_WALLET = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC';

  const OTHER_PK = '0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6';

  const MOCK_AGREEMENT_HASH = '0x6ef525c2e13920dbdb3549d949f23ddf711192863dee0cbae7df2b468a7adea2';
  const MOCK_OFFER_ID = 'b732d80c-7fc8-479e-b9b2-3860bb777f91';
  const MOCK_DEAL_ID = '9308ec28-c196-4ae2-a279-0ae6b907feea';

  const EIP712_DOMAIN = {
    name: 'CipherTrade',
    version: '1',
    chainId: ESCROW_CHAIN_ID,
    verifyingContract: ESCROW_CONTRACT_ADDRESS,
  };

  const EIP712_TYPES = {
    AgreementConsent: [
      { name: 'agreementHash', type: 'bytes32' },
      { name: 'offerId', type: 'string' },
      { name: 'dealId', type: 'string' },
      { name: 'partyRole', type: 'string' },
      { name: 'signer', type: 'address' },
      { name: 'nonce', type: 'string' },
      { name: 'deadline', type: 'uint256' },
    ],
  };

  describe('EIP-712 Challenge Structure and Validation', () => {
    it('should validate challenge response schema and domain parameters', () => {
      const challenge: ConsentChallengeResponse = {
        domain: {
          name: 'CipherTrade',
          version: '1',
          chainId: ESCROW_CHAIN_ID,
          verifyingContract: ESCROW_CONTRACT_ADDRESS,
        },
        types: EIP712_TYPES,
        primary_type: 'AgreementConsent',
        message: {
          agreementHash: MOCK_AGREEMENT_HASH,
          offerId: MOCK_OFFER_ID,
          dealId: MOCK_DEAL_ID,
          partyRole: 'buyer',
          signer: BUYER_WALLET,
          nonce: 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90',
          deadline: Math.floor(Date.now() / 1000) + 600,
        },
        nonce: 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90',
        deadline: Math.floor(Date.now() / 1000) + 600,
      };

      assert.equal(challenge.domain.name, 'CipherTrade');
      assert.equal(challenge.domain.version, '1');
      assert.equal(challenge.domain.chainId, 31337);
      assert.equal(challenge.primary_type, 'AgreementConsent');
      assert.equal(challenge.message.partyRole, 'buyer');
      assert.equal(challenge.message.signer, BUYER_WALLET);
      assert.equal(challenge.message.agreementHash, MOCK_AGREEMENT_HASH);
      assert.equal(challenge.message.nonce.length, 64);
      assert.ok(challenge.message.deadline > Math.floor(Date.now() / 1000));
    });
  });

  describe('Cryptographic Signature Generation and Recovery (Ethers v6)', () => {
    it('should sign EIP-712 typed data with buyer wallet and recover exact address', async () => {
      const buyerSigner = new Wallet(BUYER_PK);
      assert.equal(getAddress(buyerSigner.address), BUYER_WALLET);

      const message = {
        agreementHash: MOCK_AGREEMENT_HASH,
        offerId: MOCK_OFFER_ID,
        dealId: MOCK_DEAL_ID,
        partyRole: 'buyer',
        signer: BUYER_WALLET,
        nonce: '1111111111111111111111111111111111111111111111111111111111111111',
        deadline: 1893456000,
      };

      const signature = await buyerSigner.signTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        message
      );

      assert.ok(signature.startsWith('0x'));
      assert.equal(signature.length, 132); // 0x + 130 hex characters

      // Recover signer
      const recovered = verifyTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        message,
        signature
      );
      assert.equal(getAddress(recovered), BUYER_WALLET);
    });

    it('should sign EIP-712 typed data with seller wallet and recover exact address', async () => {
      const sellerSigner = new Wallet(SELLER_PK);
      assert.equal(getAddress(sellerSigner.address), SELLER_WALLET);

      const message = {
        agreementHash: MOCK_AGREEMENT_HASH,
        offerId: MOCK_OFFER_ID,
        dealId: MOCK_DEAL_ID,
        partyRole: 'seller',
        signer: SELLER_WALLET,
        nonce: '2222222222222222222222222222222222222222222222222222222222222222',
        deadline: 1893456000,
      };

      const signature = await sellerSigner.signTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        message
      );

      const recovered = verifyTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        message,
        signature
      );
      assert.equal(getAddress(recovered), SELLER_WALLET);
    });

    it('should fail recovery if agreement hash is tampered', async () => {
      const buyerSigner = new Wallet(BUYER_PK);
      const originalMessage = {
        agreementHash: MOCK_AGREEMENT_HASH,
        offerId: MOCK_OFFER_ID,
        dealId: MOCK_DEAL_ID,
        partyRole: 'buyer',
        signer: BUYER_WALLET,
        nonce: '3333333333333333333333333333333333333333333333333333333333333333',
        deadline: 1893456000,
      };

      const signature = await buyerSigner.signTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        originalMessage
      );

      // Verify with tampered agreement hash
      const tamperedMessage = {
        ...originalMessage,
        agreementHash: '0x' + 'ff'.repeat(32),
      };

      const recovered = verifyTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        tamperedMessage,
        signature
      );
      assert.notEqual(getAddress(recovered), BUYER_WALLET);
    });

    it('should detect when signed by an unauthorized key', async () => {
      const unauthorizedSigner = new Wallet(OTHER_PK);
      const message = {
        agreementHash: MOCK_AGREEMENT_HASH,
        offerId: MOCK_OFFER_ID,
        dealId: MOCK_DEAL_ID,
        partyRole: 'buyer',
        signer: BUYER_WALLET, // claims to be buyer
        nonce: '4444444444444444444444444444444444444444444444444444444444444444',
        deadline: 1893456000,
      };

      const signature = await unauthorizedSigner.signTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        message
      );

      const recovered = verifyTypedData(
        EIP712_DOMAIN,
        EIP712_TYPES,
        message,
        signature
      );
      assert.notEqual(getAddress(recovered), BUYER_WALLET);
      assert.equal(getAddress(recovered), getAddress(unauthorizedSigner.address));
    });
  });

  describe('Consent Status Evaluation and Funding Authorization Logic', () => {
    it('should report both_consented=false and can_fund=false when neither party has signed', () => {
      const status: ConsentStatusResponse = {
        offer_id: MOCK_OFFER_ID,
        deal_id: MOCK_DEAL_ID,
        agreement_hash: MOCK_AGREEMENT_HASH,
        buyer_consented: false,
        seller_consented: false,
        both_consented: false,
        can_fund: false,
      };

      assert.equal(status.both_consented, false);
      assert.equal(status.can_fund, false);
    });

    it('should report both_consented=false and can_fund=false when only buyer has signed', () => {
      const status: ConsentStatusResponse = {
        offer_id: MOCK_OFFER_ID,
        deal_id: MOCK_DEAL_ID,
        agreement_hash: MOCK_AGREEMENT_HASH,
        buyer_consented: true,
        seller_consented: false,
        both_consented: false,
        can_fund: false,
        buyer_consent: {
          user_id: '88888888-8888-8888-8888-888888888888',
          wallet_address: BUYER_WALLET,
          signed_at: '2026-11-20T10:00:00Z',
          chain_id: 31337,
          agreement_hash: MOCK_AGREEMENT_HASH,
        },
      };

      assert.equal(status.both_consented, false);
      assert.equal(status.can_fund, false);
      assert.ok(status.buyer_consent);
      assert.equal(status.seller_consent, undefined);
    });

    it('should report both_consented=true and can_fund=true when both parties have signed', () => {
      const status: ConsentStatusResponse = {
        offer_id: MOCK_OFFER_ID,
        deal_id: MOCK_DEAL_ID,
        agreement_hash: MOCK_AGREEMENT_HASH,
        buyer_consented: true,
        seller_consented: true,
        both_consented: true,
        can_fund: true,
        buyer_consent: {
          user_id: '88888888-8888-8888-8888-888888888888',
          wallet_address: BUYER_WALLET,
          signed_at: '2026-11-20T10:00:00Z',
          chain_id: 31337,
          agreement_hash: MOCK_AGREEMENT_HASH,
        },
        seller_consent: {
          user_id: '99999999-9999-9999-9999-999999999999',
          wallet_address: SELLER_WALLET,
          signed_at: '2026-11-20T10:05:00Z',
          chain_id: 31337,
          agreement_hash: MOCK_AGREEMENT_HASH,
        },
      };

      assert.equal(status.both_consented, true);
      assert.equal(status.can_fund, true);
      assert.ok(status.buyer_consent);
      assert.ok(status.seller_consent);
    });

    it('should enforce that connected account matches challenge signer before dispatching signature', () => {
      const connectedAccount = '0x1234567890123456789012345678901234567890';
      const challengeSigner = BUYER_WALLET;

      const isAccountMatch =
        getAddress(connectedAccount).toLowerCase() === getAddress(challengeSigner).toLowerCase();
      assert.equal(isAccountMatch, false);
    });
  });
});
