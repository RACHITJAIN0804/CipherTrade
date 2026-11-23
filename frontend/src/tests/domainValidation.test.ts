import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet, verifyTypedData, getAddress } from 'ethers';
import { ESCROW_CHAIN_ID, ESCROW_CONTRACT_ADDRESS, ESCROW_V2_CONTRACT_ADDRESS } from '../config/contracts.ts';
import { validateConsentDomain } from '../services/escrowService.ts';
import { validateContractFundingPayload, type ContractFundingPayload } from '../services/contractService.ts';

describe('Phase 5E.3.1: EIP-712 V2 Domain Validation and Enforcement', () => {
  const V2_ADDRESS = ESCROW_V2_CONTRACT_ADDRESS || '0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512';
  const V1_ADDRESS = ESCROW_CONTRACT_ADDRESS;

  const BUYER_PK = '0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d';
  const BUYER_WALLET = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';

  const SELLER_PK = '0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a';
  const SELLER_WALLET = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC';

  const EIP712_V2_DOMAIN = {
    name: 'CipherTrade',
    version: '1',
    chainId: ESCROW_CHAIN_ID,
    verifyingContract: V2_ADDRESS,
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

  it('1. should accept correct V2 domain with matching verifyingContract and chainId', () => {
    assert.doesNotThrow(() => {
      validateConsentDomain(EIP712_V2_DOMAIN, ESCROW_CHAIN_ID, V2_ADDRESS);
    });
  });

  it('2. should reject incorrect V2 verifying contract address when expected contract is specified', () => {
    const wrongContractDomain = {
      ...EIP712_V2_DOMAIN,
      verifyingContract: '0x1111111111111111111111111111111111111111',
    };
    assert.throws(
      () => {
        validateConsentDomain(wrongContractDomain, ESCROW_CHAIN_ID, V2_ADDRESS);
      },
      /does not match expected contract/
    );
  });

  it('3. should reject incorrect chain ID in domain', () => {
    const wrongChainDomain = {
      ...EIP712_V2_DOMAIN,
      chainId: 1, // Mainnet instead of 31337
    };
    assert.throws(
      () => {
        validateConsentDomain(wrongChainDomain, ESCROW_CHAIN_ID, V2_ADDRESS);
      },
      /does not match expected escrow network/
    );
  });

  it('4. should reject incorrect domain name or version', () => {
    const wrongNameDomain = {
      ...EIP712_V2_DOMAIN,
      name: 'CipherTradeEscrow', // Schema alignment: name must be CipherTrade
    };
    assert.throws(
      () => {
        validateConsentDomain(wrongNameDomain, ESCROW_CHAIN_ID, V2_ADDRESS);
      },
      /Untrusted consent domain name "CipherTradeEscrow"/
    );

    const wrongVersionDomain = {
      ...EIP712_V2_DOMAIN,
      version: '2',
    };
    assert.throws(
      () => {
        validateConsentDomain(wrongVersionDomain, ESCROW_CHAIN_ID, V2_ADDRESS);
      },
      /Unsupported consent domain version "2"/
    );
  });

  it('5. should reject V1-domain contract address during V2 funding pre-flight', () => {
    // Attempting to fund V2 using a payload specifying the V1 contract address
    const mockV1Payload: ContractFundingPayload = {
      deal_id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
      contract_address: V1_ADDRESS,
      agreement: {
        agreementHash: '0x' + 'aa'.repeat(32),
        offerId: 'b1b2c3d4-e5f6-7890-abcd-ef1234567890',
        dealId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
        buyer: BUYER_WALLET,
        seller: SELLER_WALLET,
        amount: '1000000000000000000',
        deliveryDeadline: Math.floor(Date.now() / 1000) + 86400,
      },
      buyerConsent: {
        nonce: '01'.repeat(32),
        deadline: Math.floor(Date.now() / 1000) + 600,
        signature: '0x' + 'cc'.repeat(65),
      },
      sellerConsent: {
        nonce: '02'.repeat(32),
        deadline: Math.floor(Date.now() / 1000) + 600,
        signature: '0x' + 'dd'.repeat(65),
      },
    };

    assert.throws(
      () => {
        validateContractFundingPayload(mockV1Payload, BUYER_WALLET, ESCROW_CHAIN_ID);
      },
      /V2 funding cannot use V1 contract address/
    );
  });

  it('6. should sign and cryptographically recover valid V2 buyer and seller signatures', async () => {
    const agreementHash = '0x' + 'aa'.repeat(32);
    const offerId = 'b1b2c3d4-e5f6-7890-abcd-ef1234567890';
    const dealId = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';
    const deadline = Math.floor(Date.now() / 1000) + 600;

    const buyerSigner = new Wallet(BUYER_PK);
    const sellerSigner = new Wallet(SELLER_PK);

    const buyerMessage = {
      agreementHash,
      offerId,
      dealId,
      partyRole: 'buyer',
      signer: BUYER_WALLET,
      nonce: '01'.repeat(32),
      deadline,
    };

    const sellerMessage = {
      agreementHash,
      offerId,
      dealId,
      partyRole: 'seller',
      signer: SELLER_WALLET,
      nonce: '02'.repeat(32),
      deadline,
    };

    const buyerSig = await buyerSigner.signTypedData(EIP712_V2_DOMAIN, EIP712_TYPES, buyerMessage);
    const sellerSig = await sellerSigner.signTypedData(EIP712_V2_DOMAIN, EIP712_TYPES, sellerMessage);

    const recoveredBuyer = verifyTypedData(EIP712_V2_DOMAIN, EIP712_TYPES, buyerMessage, buyerSig);
    const recoveredSeller = verifyTypedData(EIP712_V2_DOMAIN, EIP712_TYPES, sellerMessage, sellerSig);

    assert.equal(getAddress(recoveredBuyer), getAddress(BUYER_WALLET));
    assert.equal(getAddress(recoveredSeller), getAddress(SELLER_WALLET));
  });
});
