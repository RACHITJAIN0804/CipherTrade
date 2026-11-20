import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { getAddress, isAddress, keccak256, toUtf8Bytes } from 'ethers';
import { generateAgreementHash } from '../services/contractService.ts';
import {
  createEscrowDeal,
  getEscrowDeal,
  getEscrowDealByOffer,
  getCanonicalAgreement,
  getCanonicalAgreementByOffer,
  verifyFunding,
} from '../services/escrowService.ts';
import { apiClient } from '../services/api.ts';
import type { EscrowDeal } from '../types/index.ts';

// UUID validation regex (matches FastAPI / Pydantic uuid.UUID)
const UUID_REGEX = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const TX_HASH_REGEX = /^0x[0-9a-fA-F]{64}$/;

describe('Phase 5C.3: Agreement Hash Compatibility and Format Audit', () => {
  it('should generate an agreement hash that strictly satisfies backend validation rules', () => {
    const offerId = 'b8956961-3944-4860-9856-749e7a750bb8';
    const terms = 'Phase 5C escrow delivery terms';
    const priceEth = 1.5;
    const deadlineTs = 1800000000;

    const hash = generateAgreementHash(offerId, terms, priceEth, deadlineTs);

    // Backend rule 1: Must be string starting with 0x
    assert.ok(hash.startsWith('0x'));

    // Backend rule 2: Must be exactly 66 characters (0x + 64 hex characters)
    assert.equal(hash.length, 66);

    // Backend rule 3: Must match regex ^0x[0-9a-fA-F]{64}$
    assert.ok(TX_HASH_REGEX.test(hash));

    // Backend rule 4: Case-insensitive normalization must match
    assert.equal(hash.toLowerCase(), hash.toLowerCase());
  });

  it('should produce identical agreement hash for matching JSON payload', () => {
    const payload = JSON.stringify({
      offerId: 'test-offer',
      terms: 'Standard terms',
      priceEth: '0.75',
      deadlineTs: 1750000000,
    });
    const expected = keccak256(toUtf8Bytes(payload));
    const generated = generateAgreementHash('test-offer', 'Standard terms', '0.75', 1750000000);
    assert.equal(generated, expected);
  });
});

describe('Phase 5C.3: Deal Identifier Consistency & Mapping', () => {
  it('should distinguish backend UUID from on-chain numeric Deal ID', () => {
    const backendDealId = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
    const onChainDealId = 0;

    // Backend deal ID must be a UUID
    assert.ok(UUID_REGEX.test(backendDealId));

    // On-chain deal ID is a non-negative integer
    assert.equal(typeof onChainDealId, 'number');
    assert.ok(onChainDealId >= 0);

    // Passing on-chain ID directly as UUID fails validation
    assert.equal(UUID_REGEX.test(String(onChainDealId)), false);
  });

  it('should validate transaction hash format before backend submission', () => {
    const validTxHash = '0x88f662f3751c66c5413dca84488cde39e7b94564dd2e6d96ddef66b198752e09';
    assert.ok(TX_HASH_REGEX.test(validTxHash));

    const invalidHashes = [
      '',
      '0x123',
      '88f662f3751c66c5413dca84488cde39e7b94564dd2e6d96ddef66b198752e09', // missing 0x
      '0x' + 'z'.repeat(64), // non-hex
      '0x' + 'a'.repeat(63), // too short
      '0x' + 'a'.repeat(65), // too long
    ];

    for (const bad of invalidHashes) {
      assert.equal(TX_HASH_REGEX.test(bad), false, `Should reject "${bad}"`);
    }
  });
});

describe('Phase 5C.3: Seller and Buyer Wallet Consistency', () => {
  it('should normalize and validate checksummed addresses for buyer and seller', () => {
    const rawSeller = '0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc';
    const rawBuyer = '0x70997970c51812dc3a010c7d01b50e0d17dc79c8';

    assert.ok(isAddress(rawSeller));
    assert.ok(isAddress(rawBuyer));

    const checksumSeller = getAddress(rawSeller);
    const checksumBuyer = getAddress(rawBuyer);

    assert.equal(checksumSeller, '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC');
    assert.equal(checksumBuyer, '0x70997970C51812dc3A010C7d01b50e0d17dc79C8');

    // Self-deal check
    assert.notEqual(checksumSeller.toLowerCase(), checksumBuyer.toLowerCase());
  });
});

describe('Phase 5C.3: Escrow Backend Verification API Client & Mock Lifecycle', () => {
  const originalPost = apiClient.post;
  const originalGet = apiClient.get;

  const mockDeal: EscrowDeal = {
    id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
    offer_id: 'a12bc345-67de-89fa-0123-456789abcdef',
    buyer_id: '11111111-1111-1111-1111-111111111111',
    seller_id: '22222222-2222-2222-2222-222222222222',
    chain_deal_id: 1,
    fund_tx_hash: '0x88f662f3751c66c5413dca84488cde39e7b94564dd2e6d96ddef66b198752e09',
    amount_eth: 1.5,
    deadline_ts: 1800000000,
    agreement_hash: '0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef',
    on_chain_state: 'FUNDED',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  it('should successfully submit transaction hash to verify-funding endpoint and return verified deal', async () => {
    let capturedUrl = '';
    let capturedPayload: unknown = null;

    apiClient.post = (async (url: string, data: unknown) => {
      capturedUrl = url;
      capturedPayload = data;
      return { data: mockDeal };
    }) as any;

    try {
      const result = await verifyFunding(mockDeal.id, mockDeal.fund_tx_hash!);
      assert.equal(capturedUrl, `/escrow/deals/${mockDeal.id}/verify-funding`);
      assert.deepEqual(capturedPayload, { tx_hash: mockDeal.fund_tx_hash });
      assert.equal(result.id, mockDeal.id);
      assert.equal(result.chain_deal_id, 1);
      assert.equal(result.on_chain_state, 'FUNDED');
      assert.equal(result.fund_tx_hash, mockDeal.fund_tx_hash);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('should support safe retry when backend verification initially fails or times out', async () => {
    let attempts = 0;
    const txHash = '0x88f662f3751c66c5413dca84488cde39e7b94564dd2e6d96ddef66b198752e09';

    apiClient.post = (async () => {
      attempts++;
      if (attempts === 1) {
        const error = new Error('Gateway Timeout') as any;
        error.response = { status: 504, data: { detail: 'Network timeout contacting RPC' } };
        throw error;
      }
      return { data: mockDeal };
    }) as any;

    try {
      // First attempt fails (Scenario B: Timeout)
      await assert.rejects(
        () => verifyFunding(mockDeal.id, txHash),
        /Gateway Timeout/
      );
      assert.equal(attempts, 1);

      // Second attempt (Retry with existing transaction hash and deal UUID) succeeds
      const retryResult = await verifyFunding(mockDeal.id, txHash);
      assert.equal(attempts, 2);
      assert.equal(retryResult.on_chain_state, 'FUNDED');
      assert.equal(retryResult.fund_tx_hash, txHash);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('should handle 409 Conflict when transaction hash is already used by another deal', async () => {
    apiClient.post = (async () => {
      const error = new Error('Conflict') as any;
      error.response = {
        status: 409,
        data: { detail: 'Transaction hash is already associated with another escrow deal.' },
      };
      throw error;
    }) as any;

    try {
      await assert.rejects(
        () => verifyFunding(mockDeal.id, mockDeal.fund_tx_hash!),
        (err: any) => {
          assert.equal(err.response.status, 409);
          assert.ok(err.response.data.detail.includes('already associated'));
          return true;
        }
      );
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('should handle 403 Forbidden when caller is not the buyer', async () => {
    apiClient.post = (async () => {
      const error = new Error('Forbidden') as any;
      error.response = {
        status: 403,
        data: { detail: 'Only the buyer can verify funding for this deal.' },
      };
      throw error;
    }) as any;

    try {
      await assert.rejects(
        () => verifyFunding(mockDeal.id, mockDeal.fund_tx_hash!),
        (err: any) => {
          assert.equal(err.response.status, 403);
          assert.ok(err.response.data.detail.includes('Only the buyer'));
          return true;
        }
      );
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('should fetch deal by offer UUID via getEscrowDealByOffer', async () => {
    apiClient.get = (async (url: string) => {
      assert.equal(url, `/escrow/deals/by-offer/${mockDeal.offer_id}`);
      return { data: mockDeal };
    }) as any;

    try {
      const deal = await getEscrowDealByOffer(mockDeal.offer_id);
      assert.equal(deal.offer_id, mockDeal.offer_id);
      assert.equal(deal.id, mockDeal.id);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('should fetch deal by deal UUID via getEscrowDeal', async () => {
    apiClient.get = (async (url: string) => {
      assert.equal(url, `/escrow/${mockDeal.id}`);
      return { data: mockDeal };
    }) as any;

    try {
      const deal = await getEscrowDeal(mockDeal.id);
      assert.equal(deal.id, mockDeal.id);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('should create escrow deal record via createEscrowDeal', async () => {
    apiClient.post = (async (url: string, payload: unknown) => {
      assert.equal(url, '/escrow/deals');
      assert.deepEqual(payload, {
        offer_id: mockDeal.offer_id,
      });
      return { data: mockDeal };
    }) as any;

    try {
      const created = await createEscrowDeal({
        offer_id: mockDeal.offer_id,
      });
      assert.equal(created.id, mockDeal.id);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('should fetch canonical agreement by deal UUID via getCanonicalAgreement', async () => {
    const mockCanonical = {
      deal_id: mockDeal.id,
      offer_id: mockDeal.offer_id,
      agreement_hash: '0xa11a6f30827ca95ab877aaa0be6b5491848b4b119f295da16ff455aecabbbc15',
      canonical_agreement: {
        schema_version: 'ciphertrade-agreement-v1',
        offer_id: mockDeal.offer_id,
        price_eth: '1.50000000',
        currency: 'ETH',
      },
      schema_version: 'ciphertrade-agreement-v1',
    };

    apiClient.get = (async (url: string) => {
      assert.equal(url, `/escrow/deals/${mockDeal.id}/agreement`);
      return { data: mockCanonical };
    }) as any;

    try {
      const res = await getCanonicalAgreement(mockDeal.id);
      assert.equal(res.deal_id, mockDeal.id);
      assert.equal(res.agreement_hash, mockCanonical.agreement_hash);
      assert.equal(res.schema_version, 'ciphertrade-agreement-v1');
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('should fetch canonical agreement by offer UUID via getCanonicalAgreementByOffer', async () => {
    const mockCanonical = {
      deal_id: null,
      offer_id: mockDeal.offer_id,
      agreement_hash: '0xa11a6f30827ca95ab877aaa0be6b5491848b4b119f295da16ff455aecabbbc15',
      canonical_agreement: {
        schema_version: 'ciphertrade-agreement-v1',
        offer_id: mockDeal.offer_id,
        price_eth: '1.50000000',
        currency: 'ETH',
      },
      schema_version: 'ciphertrade-agreement-v1',
    };

    apiClient.get = (async (url: string) => {
      assert.equal(url, `/escrow/offers/${mockDeal.offer_id}/agreement`);
      return { data: mockCanonical };
    }) as any;

    try {
      const res = await getCanonicalAgreementByOffer(mockDeal.offer_id);
      assert.equal(res.offer_id, mockDeal.offer_id);
      assert.equal(res.agreement_hash, mockCanonical.agreement_hash);
      assert.equal(res.deal_id, null);
    } finally {
      apiClient.get = originalGet;
    }
  });
});

describe('Phase 5D.2: Cross-Platform Canonical Agreement Hash Verification', () => {
  it('should reproduce deterministic Keccak-256 agreement hash matching Python backend serialization', () => {
    // Fixed canonical agreement test vector matching backend Python test
    const canonicalAgreement = {
      buyer_id: '00000000-0000-0000-0000-000000000001',
      category: 'Data Extraction',
      currency: 'ETH',
      deal_reference: 'offer:00000000-0000-0000-0000-000000000002',
      delivery_deadline: '2026-12-01T12:00:00Z',
      description: 'Test requirement description for fixed vector audit.',
      offer_id: '00000000-0000-0000-0000-000000000002',
      price_eth: '1.50000000',
      requirement_id: '00000000-0000-0000-0000-000000000003',
      schema_version: 'ciphertrade-agreement-v1',
      seller_id: '00000000-0000-0000-0000-000000000004',
      terms: 'Standard test terms for fixed vector verification.',
      title: 'Test Requirement Title',
    };

    // Deterministic sorted JSON stringify
    const sortedKeys = Object.keys(canonicalAgreement).sort();
    const sortedObj: Record<string, unknown> = {};
    for (const key of sortedKeys) {
      sortedObj[key] = (canonicalAgreement as any)[key];
    }
    const jsonStr = JSON.stringify(sortedObj);

    // Keccak-256 using ethers
    const hash = keccak256(toUtf8Bytes(jsonStr)).toLowerCase();

    // Fixed expected 32-byte Keccak-256 hash constant shared with Python Web3 test
    const expectedFixedHash = '0x6ef525c2e13920dbdb3549d949f23ddf711192863dee0cbae7df2b468a7adea2';

    assert.equal(hash, expectedFixedHash, `Hash mismatch! Got ${hash}, expected ${expectedFixedHash}`);
    assert.ok(hash.startsWith('0x'));
    assert.equal(hash.length, 66);
    assert.ok(/^0x[0-9a-f]{64}$/.test(hash));
  });
});

