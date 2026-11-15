import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  formatContractError,
  confirmDeliveryOnChain,
  cancelExpiredOnChain,
  withdrawPaymentOnChain,
  refundBuyerOnChain,
  raiseDisputeOnChain,
} from '../services/contractService.ts';
import {
  listBuyerDeals,
  listSellerDeals,
  syncDealState,
} from '../services/escrowService.ts';
import { apiClient } from '../services/api.ts';
import type { EscrowTransaction, PaginatedEscrowDeals } from '../types/index.ts';

describe('Phase 5C.4: Escrow Dashboard Service Endpoints and Formatting', () => {
  it('should call GET /escrow/deals/buyer with proper query params', async () => {
    let capturedUrl = '';
    let capturedConfig: unknown = null;

    const originalGet = apiClient.get;
    apiClient.get = (async (url: string, config?: unknown) => {
      capturedUrl = url;
      capturedConfig = config;
      return {
        data: {
          items: [
            {
              id: '11111111-1111-1111-1111-111111111111',
              offer_id: '22222222-2222-2222-2222-222222222222',
              buyer_id: '33333333-3333-3333-3333-333333333333',
              seller_id: '44444444-4444-4444-4444-444444444444',
              chain_deal_id: 1,
              fund_tx_hash: '0x' + '1'.repeat(64),
              amount_eth: 1.5,
              deadline_ts: 1800000000,
              agreement_hash: '0x' + '2'.repeat(64),
              on_chain_state: 'FUNDED',
              requirement_title: 'Test Service',
              requirement_category: 'development',
              offer_terms: 'Full delivery within agreed terms.',
              buyer_display_name: 'Buyer User',
              buyer_wallet_address: '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266',
              seller_display_name: 'Seller User',
              seller_wallet_address: '0x70997970C51812dc3A010C7d01b50e0d17dc79C8',
              created_at: '2026-11-15T12:00:00Z',
              updated_at: '2026-11-15T12:00:00Z',
            },
          ],
          total: 1,
          page: 1,
          page_size: 10,
        } as PaginatedEscrowDeals,
      };
    }) as any;

    try {
      const res = await listBuyerDeals({ page: 1, page_size: 10, deal_state: 'FUNDED' });
      assert.equal(capturedUrl, '/escrow/deals/buyer');
      assert.deepEqual((capturedConfig as any)?.params, {
        page: 1,
        page_size: 10,
        deal_state: 'FUNDED',
      });
      assert.equal(res.total, 1);
      assert.equal(res.items[0].requirement_title, 'Test Service');
      assert.equal(res.items[0].buyer_display_name, 'Buyer User');
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('should call GET /escrow/deals/seller with proper query params', async () => {
    let capturedUrl = '';
    const originalGet = apiClient.get;
    apiClient.get = (async (url: string) => {
      capturedUrl = url;
      return {
        data: {
          items: [],
          total: 0,
          page: 1,
          page_size: 10,
        } as PaginatedEscrowDeals,
      };
    }) as any;

    try {
      const res = await listSellerDeals({ page: 2, page_size: 5 });
      assert.equal(capturedUrl, '/escrow/deals/seller');
      assert.equal(res.total, 0);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('should call POST /escrow/deals/{dealId}/sync for on-chain state sync', async () => {
    let capturedUrl = '';
    const originalPost = apiClient.post;
    apiClient.post = (async (url: string) => {
      capturedUrl = url;
      return {
        data: {
          id: 'test-deal-id',
          on_chain_state: 'CONFIRMED',
        } as EscrowTransaction,
      };
    }) as any;

    try {
      const res = await syncDealState('test-deal-id');
      assert.equal(capturedUrl, '/escrow/deals/test-deal-id/sync');
      assert.equal(res.on_chain_state, 'CONFIRMED');
    } finally {
      apiClient.post = originalPost;
    }
  });
});

describe('Phase 5C.4: Smart Contract Action Signatures and Parameter Validation', () => {
  it('should reject invalid deal ID or null signer for confirmDeliveryOnChain', async () => {
    await assert.rejects(
      async () => {
        await confirmDeliveryOnChain(-1, null as any);
      },
      { message: 'Invalid chain deal ID.' }
    );

    await assert.rejects(
      async () => {
        await confirmDeliveryOnChain(0, null as any);
      },
      { message: 'A connected wallet signer is required.' }
    );
  });

  it('should reject invalid deal ID or null signer for cancelExpiredOnChain', async () => {
    await assert.rejects(
      async () => {
        await cancelExpiredOnChain(-5, null as any);
      },
      { message: 'Invalid chain deal ID.' }
    );

    await assert.rejects(
      async () => {
        await cancelExpiredOnChain(2, null as any);
      },
      { message: 'A connected wallet signer is required.' }
    );
  });

  it('should reject invalid deal ID or null signer for withdrawPaymentOnChain', async () => {
    await assert.rejects(
      async () => {
        await withdrawPaymentOnChain(-1, null as any);
      },
      { message: 'Invalid chain deal ID.' }
    );

    await assert.rejects(
      async () => {
        await withdrawPaymentOnChain(0, null as any);
      },
      { message: 'A connected wallet signer is required.' }
    );
  });

  it('should reject invalid deal ID or null signer for refundBuyerOnChain', async () => {
    await assert.rejects(
      async () => {
        await refundBuyerOnChain(-1, null as any);
      },
      { message: 'Invalid chain deal ID.' }
    );

    await assert.rejects(
      async () => {
        await refundBuyerOnChain(1, null as any);
      },
      { message: 'A connected wallet signer is required.' }
    );
  });

  it('should reject invalid deal ID or null signer for raiseDisputeOnChain', async () => {
    await assert.rejects(
      async () => {
        await raiseDisputeOnChain(-1, null as any);
      },
      { message: 'Invalid chain deal ID.' }
    );

    await assert.rejects(
      async () => {
        await raiseDisputeOnChain(1, null as any);
      },
      { message: 'A connected wallet signer is required.' }
    );
  });
});

describe('Phase 5C.4: Contract Error Mapping and Pull-Payment Semantics', () => {
  it('should translate contract revert errors accurately', () => {
    assert.equal(
      formatContractError({ message: 'execution reverted: DeadlineNotReached()' }),
      'The delivery deadline has not elapsed yet. Cancellation is not allowed before the deadline.'
    );

    assert.equal(
      formatContractError({ message: 'execution reverted: AlreadyWithdrawn()' }),
      'Funds for this escrow deal have already been withdrawn or settled.'
    );

    assert.equal(
      formatContractError({ message: 'execution reverted: InvalidState(0, 1)' }),
      'The escrow deal is not in an eligible state for this action.'
    );

    assert.equal(
      formatContractError({ message: 'execution reverted: Unauthorized()' }),
      'You are not authorized to perform this operation.'
    );

    assert.equal(
      formatContractError({ code: 4001, message: 'User rejected the request' }),
      'Transaction was rejected in MetaMask.'
    );

    assert.equal(
      formatContractError({ code: 'ACTION_REJECTED' }),
      'Transaction was rejected in MetaMask.'
    );

    assert.equal(
      formatContractError({ code: 'INSUFFICIENT_FUNDS' }),
      'Insufficient funds in your wallet to cover the escrow amount and gas fee.'
    );
  });
});
