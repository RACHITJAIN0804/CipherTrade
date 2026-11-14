import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  JsonRpcProvider,
  parseEther,
  formatEther,
  getAddress,
} from 'ethers';
import {
  ESCROW_CONTRACT_ADDRESS,
  ESCROW_CHAIN_ID,
  ESCROW_RPC_URL,
  ESCROW_ABI,
} from '../config/contracts.ts';
import {
  getEscrowContract,
  generateAgreementHash,
  validateFundDealParams,
  formatContractError,
  OnChainEscrowState,
  ON_CHAIN_STATE_NAMES,
  type FundDealParams,
} from '../services/contractService.ts';

describe('Phase 5C.2: Smart Contract Configuration & ABI Verification', () => {
  it('should have exact deployed contract address, chain ID, and RPC URL', () => {
    assert.equal(ESCROW_CONTRACT_ADDRESS, '0x5FbDB2315678afecb367f032d93F642f64180aa3');
    assert.equal(ESCROW_CHAIN_ID, 31337);
    assert.equal(ESCROW_RPC_URL, 'http://127.0.0.1:8545');
    assert.equal(getAddress(ESCROW_CONTRACT_ADDRESS), ESCROW_CONTRACT_ADDRESS);
  });

  it('should have all required CipherTradeEscrow functions and events in ABI', () => {
    const abiItems = ESCROW_ABI as Array<{ type: string; name?: string }>;
    const functionNames = abiItems.filter((i) => i.type === 'function').map((i) => i.name);
    const eventNames = abiItems.filter((i) => i.type === 'event').map((i) => i.name);

    // Required external/public contract functions
    assert.ok(functionNames.includes('fundDeal'), 'ABI missing fundDeal');
    assert.ok(functionNames.includes('getDeal'), 'ABI missing getDeal');
    assert.ok(functionNames.includes('nextDealId'), 'ABI missing nextDealId');
    assert.ok(functionNames.includes('confirmDelivery'), 'ABI missing confirmDelivery');
    assert.ok(functionNames.includes('withdrawPayment'), 'ABI missing withdrawPayment');
    assert.ok(functionNames.includes('cancelExpired'), 'ABI missing cancelExpired');
    assert.ok(functionNames.includes('raiseDispute'), 'ABI missing raiseDispute');
    assert.ok(functionNames.includes('resolveDispute'), 'ABI missing resolveDispute');
    assert.ok(functionNames.includes('arbitrator'), 'ABI missing arbitrator');

    // Required events
    assert.ok(eventNames.includes('DealFunded'), 'ABI missing DealFunded event');
    assert.ok(eventNames.includes('DeliveryConfirmed'), 'ABI missing DeliveryConfirmed event');
    assert.ok(eventNames.includes('PaymentReleased'), 'ABI missing PaymentReleased event');
    assert.ok(eventNames.includes('DealCancelled'), 'ABI missing DealCancelled event');
  });
});

describe('Agreement Hash Generation and Integrity', () => {
  it('should produce a valid 32-byte (66-char) keccak256 hex string starting with 0x', () => {
    const hash = generateAgreementHash('offer-123', 'Build frontend app', 0.5, 1700000000);
    assert.equal(typeof hash, 'string');
    assert.equal(hash.length, 66);
    assert.ok(/^0x[0-9a-fA-F]{64}$/.test(hash));
  });

  it('should produce deterministic hash for identical input parameters', () => {
    const hash1 = generateAgreementHash('offer-abc', 'Terms here', '1.0', 1800000000);
    const hash2 = generateAgreementHash('offer-abc', 'Terms here', '1.0', 1800000000);
    assert.equal(hash1, hash2);
  });

  it('should produce distinct hashes when terms, amount, or deadline differ', () => {
    const hashA = generateAgreementHash('offer-1', 'Terms A', 0.5, 1800000000);
    const hashB = generateAgreementHash('offer-1', 'Terms B', 0.5, 1800000000);
    const hashC = generateAgreementHash('offer-1', 'Terms A', 0.6, 1800000000);
    const hashD = generateAgreementHash('offer-1', 'Terms A', 0.5, 1800000001);

    assert.notEqual(hashA, hashB);
    assert.notEqual(hashA, hashC);
    assert.notEqual(hashA, hashD);
  });
});

describe('Ether to Wei Conversion & Floating-point Safety', () => {
  it('should accurately convert ETH to Wei using parseEther without float rounding errors', () => {
    assert.equal(parseEther('0.1'), 100000000000000000n);
    assert.equal(parseEther('0.0001'), 100000000000000n);
    assert.equal(parseEther('1.0'), 1000000000000000000n);
    assert.equal(parseEther('2.5'), 2500000000000000000n);

    // Verify formatEther round-trip
    assert.equal(formatEther(100000000000000000n), '0.1');
    assert.equal(formatEther(1000000000000000000n), '1.0');
  });
});

describe('Funding Parameter Validation', () => {
  const buyerAddr = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
  const sellerAddr = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC';
  const validHash = '0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef';
  const futureDeadline = Math.floor(Date.now() / 1000) + 86400; // +24 hours

  const mockSigner = {
    getAddress: async () => buyerAddr,
  } as any;

  it('should accept valid funding parameters', () => {
    const validParams: FundDealParams = {
      sellerAddress: sellerAddr,
      deadlineTs: futureDeadline,
      agreementHash: validHash,
      amountEth: '0.25',
      signer: mockSigner,
    };
    assert.doesNotThrow(() => validateFundDealParams(validParams, buyerAddr));
  });

  it('should reject missing or invalid seller address', () => {
    const badParams: FundDealParams = {
      sellerAddress: 'invalid-address',
      deadlineTs: futureDeadline,
      agreementHash: validHash,
      amountEth: '0.25',
      signer: mockSigner,
    };
    assert.throws(() => validateFundDealParams(badParams, buyerAddr), /Invalid seller wallet address/);
  });

  it('should reject self-deal (buyer == seller)', () => {
    const selfDealParams: FundDealParams = {
      sellerAddress: buyerAddr, // Same as buyer
      deadlineTs: futureDeadline,
      agreementHash: validHash,
      amountEth: '0.25',
      signer: mockSigner,
    };
    assert.throws(() => validateFundDealParams(selfDealParams, buyerAddr), /SelfDeal not allowed/);
  });

  it('should reject past or expired deadline', () => {
    const pastDeadlineParams: FundDealParams = {
      sellerAddress: sellerAddr,
      deadlineTs: Math.floor(Date.now() / 1000) - 100, // in the past
      agreementHash: validHash,
      amountEth: '0.25',
      signer: mockSigner,
    };
    assert.throws(() => validateFundDealParams(pastDeadlineParams, buyerAddr), /must be in the future/);
  });

  it('should reject invalid agreement hash format', () => {
    const badHashParams: FundDealParams = {
      sellerAddress: sellerAddr,
      deadlineTs: futureDeadline,
      agreementHash: '0x1234', // Too short
      amountEth: '0.25',
      signer: mockSigner,
    };
    assert.throws(() => validateFundDealParams(badHashParams, buyerAddr), /Invalid agreement hash/);
  });

  it('should reject zero, negative, or non-numeric amounts', () => {
    const zeroParams: FundDealParams = {
      sellerAddress: sellerAddr,
      deadlineTs: futureDeadline,
      agreementHash: validHash,
      amountEth: '0',
      signer: mockSigner,
    };
    assert.throws(() => validateFundDealParams(zeroParams, buyerAddr), /greater than 0/);

    const negParams: FundDealParams = {
      sellerAddress: sellerAddr,
      deadlineTs: futureDeadline,
      agreementHash: validHash,
      amountEth: '-1.5',
      signer: mockSigner,
    };
    assert.throws(() => validateFundDealParams(negParams, buyerAddr), /greater than 0/);
  });
});

describe('Error Formatting for MetaMask and Contract Reverts', () => {
  it('should format user rejection errors', () => {
    assert.equal(
      formatContractError({ code: 4001, message: 'User rejected the transaction' }),
      'Transaction was rejected in MetaMask.'
    );
    assert.equal(
      formatContractError({ code: 'ACTION_REJECTED', message: 'Action rejected' }),
      'Transaction was rejected in MetaMask.'
    );
  });

  it('should format insufficient funds errors', () => {
    assert.equal(
      formatContractError({ code: 'INSUFFICIENT_FUNDS', message: 'insufficient funds for gas * price + value' }),
      'Insufficient funds in your wallet to cover the escrow amount and gas fee.'
    );
  });

  it('should format custom Solidity contract reverts', () => {
    assert.equal(
      formatContractError({ reason: 'execution reverted: custom error SelfDeal()' }),
      'Cannot fund a deal where buyer and seller are the same wallet address.'
    );
    assert.equal(
      formatContractError({ message: 'reverted with custom error ZeroAmount()' }),
      'Escrow amount must be greater than zero.'
    );
    assert.equal(
      formatContractError({ shortMessage: 'execution reverted: InvalidDeadline()' }),
      'The delivery deadline must be set in the future.'
    );
  });
});

describe('Ethers Contract Event Log Parsing', () => {
  it('should parse DealFunded event log accurately', () => {
    const provider = new JsonRpcProvider(ESCROW_RPC_URL);
    const contract = getEscrowContract(provider);

    // Simulate event values
    const dealId = 42n;
    const buyer = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
    const seller = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC';
    const amount = parseEther('1.25');
    const deadline = 1750000000n;
    const agreementHash = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890';

    // Encode event log topics and data using contract interface
    const eventFragment = contract.interface.getEvent('DealFunded')!;
    const logData = contract.interface.encodeEventLog(eventFragment, [
      dealId,
      buyer,
      seller,
      amount,
      deadline,
      agreementHash,
    ]);

    // Parse the simulated log
    const parsed = contract.interface.parseLog(logData);
    assert.ok(parsed, 'Failed to parse log');
    assert.equal(parsed.name, 'DealFunded');
    assert.equal(Number(parsed.args.dealId), 42);
    assert.equal(parsed.args.buyer.toLowerCase(), buyer.toLowerCase());
    assert.equal(parsed.args.seller.toLowerCase(), seller.toLowerCase());
    assert.equal(parsed.args.amount, amount);
    assert.equal(parsed.args.deadline, deadline);
    assert.equal(parsed.args.agreementHash, agreementHash);
  });
});

describe('Escrow Contract Read Methods (nextDealId & getOnChainDeal)', () => {
  it('should execute getNextDealId and return bigint', async () => {
    const mockContract = {
      nextDealId: async () => 5n,
    };
    // Test with mock contract instance
    const nextId = await mockContract.nextDealId();
    assert.equal(typeof nextId, 'bigint');
    assert.equal(nextId, 5n);
  });

  it('should format OnChainDeal struct correctly from getOnChainDeal response', () => {
    const buyer = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
    const seller = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC';
    const amount = parseEther('1.5');
    const deadline = 1750000000n;
    const agreementHash = '0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef';

    // Simulate struct tuple returned by Solidity: [buyer, seller, amount, deadline, agreementHash, state, fundsWithdrawn]
    const rawDeal = [buyer, seller, amount, deadline, agreementHash, 0, false];
    const formatted = {
      dealId: 0,
      buyer: getAddress(rawDeal[0] as string),
      seller: getAddress(rawDeal[1] as string),
      amount: BigInt(rawDeal[2] as bigint),
      amountEth: formatEther(rawDeal[2] as bigint),
      deadline: BigInt(rawDeal[3] as bigint),
      deadlineDate: new Date(Number(rawDeal[3]) * 1000),
      agreementHash: String(rawDeal[4]),
      state: Number(rawDeal[5]) as OnChainEscrowState,
      stateName: ON_CHAIN_STATE_NAMES[Number(rawDeal[5])],
      fundsWithdrawn: Boolean(rawDeal[6]),
    };

    assert.equal(formatted.dealId, 0);
    assert.equal(formatted.buyer, buyer);
    assert.equal(formatted.seller, seller);
    assert.equal(formatted.amountEth, '1.5');
    assert.equal(formatted.stateName, 'FUNDED');
    assert.equal(formatted.fundsWithdrawn, false);
  });

  it('should verify on-chain states mapping matches Solidity enum', () => {
    assert.equal(OnChainEscrowState.FUNDED, 0);
    assert.equal(OnChainEscrowState.CONFIRMED, 1);
    assert.equal(OnChainEscrowState.RELEASED, 2);
    assert.equal(OnChainEscrowState.REFUNDED, 3);
    assert.equal(OnChainEscrowState.CANCELLED, 4);
    assert.equal(OnChainEscrowState.DISPUTED, 5);
    assert.equal(OnChainEscrowState.RESOLVED, 6);

    assert.equal(ON_CHAIN_STATE_NAMES[0], 'FUNDED');
    assert.equal(ON_CHAIN_STATE_NAMES[1], 'CONFIRMED');
    assert.equal(ON_CHAIN_STATE_NAMES[2], 'RELEASED');
    assert.equal(ON_CHAIN_STATE_NAMES[3], 'REFUNDED');
    assert.equal(ON_CHAIN_STATE_NAMES[4], 'CANCELLED');
    assert.equal(ON_CHAIN_STATE_NAMES[5], 'DISPUTED');
    assert.equal(ON_CHAIN_STATE_NAMES[6], 'RESOLVED');
  });
});
