import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { getAddress, type ContractRunner, type JsonRpcSigner } from 'ethers';
import {
  ESCROW_CONTRACT_ADDRESS,
  ESCROW_V2_CONTRACT_ADDRESS,
} from '../config/contracts.ts';
import {
  resolveLifecycleContract,
  confirmDeliveryOnChain,
  cancelExpiredOnChain,
  withdrawPaymentOnChain,
  refundBuyerOnChain,
  raiseDisputeOnChain,
  resolveDisputeOnChain,
} from '../services/contractService.ts';

describe('Phase 5E.4.2: Frontend V1/V2 Lifecycle Contract Routing', () => {
  const V1_ADDR = getAddress(ESCROW_CONTRACT_ADDRESS);
  const V2_ADDR = getAddress(ESCROW_V2_CONTRACT_ADDRESS || '0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512');

  // Dummy mock runner for contract instantiation
  const mockRunner: ContractRunner = {
    provider: null,
  };

  function createMockSigner(onCall?: (targetAddress: string, method: string, args: unknown[]) => void): JsonRpcSigner {
    const signer = {
      provider: {
        getNetwork: async () => ({ chainId: 31337n, name: 'anvil' }),
        getTransactionReceipt: async (hash: string) => ({
          status: 1,
          hash,
          blockNumber: 10,
          blockHash: '0x' + '1'.repeat(64),
          index: 0,
          from: '0x70997970C51812dc3A010C7d01b50e0d17dc79C8',
          to: '0x5FbDB2315678afecb367f032d93F642f64180aa3',
          gasUsed: 21000n,
          cumulativeGasUsed: 21000n,
          gasPrice: 1000000000n,
          logsBloom: '0x',
          logs: [],
        }),
      },
      getAddress: async () => '0x70997970C51812dc3A010C7d01b50e0d17dc79C8',
      estimateGas: async () => 100000n,
      sendTransaction: async (txRequest: any) => {
        // Ethers Contract calls sendTransaction on the runner
        const targetAddress = txRequest.to ? getAddress(txRequest.to) : '';
        if (onCall) {
          onCall(targetAddress, 'sendTransaction', [txRequest]);
        }
        return {
          hash: '0x' + 'f'.repeat(64),
          wait: async () => ({
            status: 1,
            hash: '0x' + 'f'.repeat(64),
            blockNumber: 10,
          }),
        };
      },
      call: async () => '0x',
    } as unknown as JsonRpcSigner;
    return signer;
  }

  describe('1. Contract Identity Validation and Rejection Rules', () => {
    it('should reject missing contract identity (null or undefined)', () => {
      assert.throws(
        () => resolveLifecycleContract(mockRunner, null),
        /Missing contract identity for escrow deal/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, undefined),
        /Missing contract identity for escrow deal/
      );
    });

    it('should reject empty contract identity object or whitespace strings', () => {
      assert.throws(
        () => resolveLifecycleContract(mockRunner, {}),
        /Missing contract identity for escrow deal/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_address: null, contract_version: null }),
        /Missing contract identity for escrow deal/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, ''),
        /Missing contract identity for escrow deal/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, '   '),
        /Missing contract identity for escrow deal/
      );
    });

    it('should reject unsupported contract versions', () => {
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v3', contract_address: V2_ADDR }),
        /Unsupported contract version: "v3"/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v0', contract_address: V1_ADDR }),
        /Unsupported contract version: "v0"/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'unknown' }),
        /Unsupported contract version: "unknown"/
      );
    });

    it('should reject malformed contract addresses', () => {
      assert.throws(
        () => resolveLifecycleContract(mockRunner, 'not-an-address'),
        /Malformed contract address: "not-an-address"/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_address: '0x123', contract_version: 'v2' }),
        /Malformed contract address: "0x123"/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_address: '0x' + 'g'.repeat(40), contract_version: 'v1' }),
        /Malformed contract address/
      );
    });

    it('should reject V2 deal with missing contract_address', () => {
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v2' }),
        /Missing contract address for V2 deal/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v2', contract_address: null }),
        /Missing contract address for V2 deal/
      );
    });

    it('should reject V2 deal attempting to target V1 contract address (no silent fallback)', () => {
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v2', contract_address: V1_ADDR }),
        /Contract identity mismatch: V2 deal cannot target the V1 contract address/
      );
    });

    it('should reject V1 deal attempting to target V2 contract address', () => {
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v1', contract_address: V2_ADDR }),
        /Contract identity mismatch: V1 deal cannot target the V2 contract address/
      );
    });

    it('should reject unconfigured or mismatched contract addresses', () => {
      const foreignAddr = '0x1111111111111111111111111111111111111111';
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v2', contract_address: foreignAddr }),
        /V2 contract address mismatch/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, { contract_version: 'v1', contract_address: foreignAddr }),
        /V1 contract address mismatch/
      );
      assert.throws(
        () => resolveLifecycleContract(mockRunner, foreignAddr),
        /Unknown or unconfigured contract address/
      );
    });
  });

  describe('2. Contract Routing & ABI Verification', () => {
    it('should route V1 deal to V1 contract address with V1 ABI', () => {
      const resolved = resolveLifecycleContract(mockRunner, {
        contract_version: 'v1',
        contract_address: V1_ADDR,
      });

      assert.equal(resolved.version, 'v1');
      assert.equal(resolved.address.toLowerCase(), V1_ADDR.toLowerCase());
      assert.equal(resolved.contract.target.toString().toLowerCase(), V1_ADDR.toLowerCase());

      // Check V1 specific function exists in interface
      assert.ok(resolved.contract.interface.hasFunction('fundDeal'));
      assert.ok(resolved.contract.interface.hasFunction('confirmDelivery'));
      assert.ok(resolved.contract.interface.hasFunction('withdrawPayment'));
      assert.ok(resolved.contract.interface.hasFunction('cancelExpired'));
      assert.ok(resolved.contract.interface.hasFunction('raiseDispute'));

      // Check V2-only function does NOT exist in V1 ABI
      assert.equal(resolved.contract.interface.hasFunction('fundDealWithConsent'), false);
    });

    it('should route V2 deal to V2 contract address with V2 ABI', () => {
      const resolved = resolveLifecycleContract(mockRunner, {
        contract_version: 'v2',
        contract_address: V2_ADDR,
      });

      assert.equal(resolved.version, 'v2');
      assert.equal(resolved.address.toLowerCase(), V2_ADDR.toLowerCase());
      assert.equal(resolved.contract.target.toString().toLowerCase(), V2_ADDR.toLowerCase());

      // Check V2 specific function exists in interface
      assert.ok(resolved.contract.interface.hasFunction('fundDealWithConsent'));
      assert.ok(resolved.contract.interface.hasFunction('confirmDelivery'));
      assert.ok(resolved.contract.interface.hasFunction('withdrawPayment'));
      assert.ok(resolved.contract.interface.hasFunction('cancelExpired'));
      assert.ok(resolved.contract.interface.hasFunction('raiseDispute'));
      assert.ok(resolved.contract.interface.hasFunction('chainDealIdByUuidHash'));

      // Check V1-only legacy fundDeal does NOT exist in V2 ABI
      assert.equal(resolved.contract.interface.hasFunction('fundDeal'), false);
    });

    it('should support legacy V1 deal specifying only V1 address', () => {
      const resolved = resolveLifecycleContract(mockRunner, V1_ADDR);
      assert.equal(resolved.version, 'v1');
      assert.equal(resolved.address.toLowerCase(), V1_ADDR.toLowerCase());
      assert.ok(resolved.contract.interface.hasFunction('fundDeal'));
    });

    it('should support V2 deal specifying V2 address as string', () => {
      const resolved = resolveLifecycleContract(mockRunner, V2_ADDR);
      assert.equal(resolved.version, 'v2');
      assert.equal(resolved.address.toLowerCase(), V2_ADDR.toLowerCase());
      assert.ok(resolved.contract.interface.hasFunction('fundDealWithConsent'));
    });
  });

  describe('3. Lifecycle Operations Routing & Execution', () => {
    it('confirmDeliveryOnChain routes to V1 and V2 contracts correctly', async () => {
      let calledAddress = '';
      const signer = createMockSigner((addr) => {
        calledAddress = addr;
      });

      // V1 Call
      const txV1 = await confirmDeliveryOnChain(1, signer, {
        contract_version: 'v1',
        contract_address: V1_ADDR,
      });
      assert.ok(txV1.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V1_ADDR.toLowerCase());

      // V2 Call
      const txV2 = await confirmDeliveryOnChain(2, signer, {
        contract_version: 'v2',
        contract_address: V2_ADDR,
      });
      assert.ok(txV2.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V2_ADDR.toLowerCase());
    });

    it('withdrawPaymentOnChain routes to V1 and V2 contracts correctly', async () => {
      let calledAddress = '';
      const signer = createMockSigner((addr) => {
        calledAddress = addr;
      });

      // V1 Call
      const txV1 = await withdrawPaymentOnChain(1, signer, {
        contract_version: 'v1',
        contract_address: V1_ADDR,
      });
      assert.ok(txV1.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V1_ADDR.toLowerCase());

      // V2 Call
      const txV2 = await withdrawPaymentOnChain(2, signer, {
        contract_version: 'v2',
        contract_address: V2_ADDR,
      });
      assert.ok(txV2.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V2_ADDR.toLowerCase());
    });

    it('refundBuyerOnChain routes to V1 and V2 contracts correctly', async () => {
      let calledAddress = '';
      const signer = createMockSigner((addr) => {
        calledAddress = addr;
      });

      // V1 Call
      const txV1 = await refundBuyerOnChain(1, signer, {
        contract_version: 'v1',
        contract_address: V1_ADDR,
      });
      assert.ok(txV1.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V1_ADDR.toLowerCase());

      // V2 Call
      const txV2 = await refundBuyerOnChain(2, signer, {
        contract_version: 'v2',
        contract_address: V2_ADDR,
      });
      assert.ok(txV2.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V2_ADDR.toLowerCase());
    });

    it('cancelExpiredOnChain routes to V1 and V2 contracts correctly', async () => {
      let calledAddress = '';
      const signer = createMockSigner((addr) => {
        calledAddress = addr;
      });

      // V1 Call
      const txV1 = await cancelExpiredOnChain(1, signer, {
        contract_version: 'v1',
        contract_address: V1_ADDR,
      });
      assert.ok(txV1.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V1_ADDR.toLowerCase());

      // V2 Call
      const txV2 = await cancelExpiredOnChain(2, signer, {
        contract_version: 'v2',
        contract_address: V2_ADDR,
      });
      assert.ok(txV2.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V2_ADDR.toLowerCase());
    });

    it('raiseDisputeOnChain routes to V1 and V2 contracts correctly', async () => {
      let calledAddress = '';
      const signer = createMockSigner((addr) => {
        calledAddress = addr;
      });

      // V1 Call
      const txV1 = await raiseDisputeOnChain(1, signer, {
        contract_version: 'v1',
        contract_address: V1_ADDR,
      });
      assert.ok(txV1.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V1_ADDR.toLowerCase());

      // V2 Call
      const txV2 = await raiseDisputeOnChain(2, signer, {
        contract_version: 'v2',
        contract_address: V2_ADDR,
      });
      assert.ok(txV2.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V2_ADDR.toLowerCase());
    });

    it('resolveDisputeOnChain routes to V1 and V2 contracts correctly', async () => {
      let calledAddress = '';
      const signer = createMockSigner((addr) => {
        calledAddress = addr;
      });

      // V1 Call
      const txV1 = await resolveDisputeOnChain(1, true, signer, {
        contract_version: 'v1',
        contract_address: V1_ADDR,
      });
      assert.ok(txV1.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V1_ADDR.toLowerCase());

      // V2 Call
      const txV2 = await resolveDisputeOnChain(2, false, signer, {
        contract_version: 'v2',
        contract_address: V2_ADDR,
      });
      assert.ok(txV2.startsWith('0x'));
      assert.equal(calledAddress.toLowerCase(), V2_ADDR.toLowerCase());
    });

    it('lifecycle operations reject invalid contract identity before transaction submission', async () => {
      const signer = createMockSigner();

      // Missing contract identity
      await assert.rejects(
        () => confirmDeliveryOnChain(1, signer),
        /Missing contract identity for escrow deal/
      );

      // Unsupported version
      await assert.rejects(
        () => cancelExpiredOnChain(1, signer, { contract_version: 'v99' }),
        /Unsupported contract version/
      );

      // Malformed address
      await assert.rejects(
        () => withdrawPaymentOnChain(1, signer, { contract_address: 'invalid' }),
        /Malformed contract address/
      );

      // V2 attempting to target V1 contract address (no silent fallback)
      await assert.rejects(
        () => refundBuyerOnChain(1, signer, { contract_version: 'v2', contract_address: V1_ADDR }),
        /Contract identity mismatch: V2 deal cannot target the V1 contract address/
      );

      // V1 attempting to target V2 contract address
      await assert.rejects(
        () => raiseDisputeOnChain(1, signer, { contract_version: 'v1', contract_address: V2_ADDR }),
        /Contract identity mismatch: V1 deal cannot target the V2 contract address/
      );
    });
  });
});
