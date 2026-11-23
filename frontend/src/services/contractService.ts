import {
  Contract,
  type ContractRunner,
  type JsonRpcSigner,
  getAddress,
  isAddress,
  parseEther,
  formatEther,
  keccak256,
  toUtf8Bytes,
} from 'ethers';
import {
  ESCROW_CONTRACT_ADDRESS,
  ESCROW_ABI,
  ESCROW_V2_CONTRACT_ADDRESS,
  ESCROW_V2_ABI,
} from '../config/contracts.ts';
import type { DealContractIdentity, EscrowContractVersion } from '../types/index.ts';

/**
 * On-chain lifecycle states defined in CipherTradeEscrow.sol and CipherTradeEscrowV2.sol:
 * enum State { FUNDED, CONFIRMED, RELEASED, REFUNDED, CANCELLED, DISPUTED, RESOLVED }
 */
export const OnChainEscrowState = {
  FUNDED: 0,
  CONFIRMED: 1,
  RELEASED: 2,
  REFUNDED: 3,
  CANCELLED: 4,
  DISPUTED: 5,
  RESOLVED: 6,
} as const;

export type OnChainEscrowState = typeof OnChainEscrowState[keyof typeof OnChainEscrowState];

export const ON_CHAIN_STATE_NAMES: Record<number, string> = {
  0: 'FUNDED',
  1: 'CONFIRMED',
  2: 'RELEASED',
  3: 'REFUNDED',
  4: 'CANCELLED',
  5: 'DISPUTED',
  6: 'RESOLVED',
};

/**
 * Representation of the on-chain Deal struct returned by getDeal().
 */
export interface OnChainDeal {
  dealId: number;
  buyer: string;
  seller: string;
  amount: bigint;
  amountEth: string;
  deadline: bigint;
  deadlineDate: Date;
  agreementHash: string;
  state: OnChainEscrowState;
  stateName: string;
  fundsWithdrawn: boolean;
}

// ─── V1 (Legacy) Types ───────────────────────────────────────────────────────

export interface FundDealParams {
  sellerAddress: string;
  deadlineTs: number;
  agreementHash: string;
  amountEth: string | number;
  signer: JsonRpcSigner;
  onTxSubmitted?: (txHash: string) => void;
}

export interface FundDealResult {
  txHash: string;
  chainDealId: number;
  blockNumber: number;
  buyer: string;
  seller: string;
  amountEth: string;
  deadlineTs: number;
  agreementHash: string;
  gasUsed?: bigint;
}

// ─── V2 (Dual-Consent) Types ─────────────────────────────────────────────────

/**
 * ContractFundingPayload mirrors the backend ContractFundingPayloadResponse schema
 * from backend/app/schemas/consent.py.
 */
export interface CommercialAgreementPayload {
  agreementHash: string;  // 0x + 64 hex chars (bytes32)
  offerId: string;        // UUID string
  dealId: string;         // UUID string
  buyer: string;          // checksummed Ethereum address
  seller: string;         // checksummed Ethereum address
  amount: string;         // Wei as decimal string
  deliveryDeadline: number; // Unix timestamp
}

export interface PartyConsentProofPayload {
  nonce: string;      // 64-char hex string
  deadline: number;   // Unix timestamp
  signature: string;  // 0x + 130 hex chars (65 bytes ECDSA)
}

export interface ContractFundingPayload {
  deal_id: string;
  agreement: CommercialAgreementPayload;
  buyerConsent: PartyConsentProofPayload;
  sellerConsent: PartyConsentProofPayload;
  contract_address: string | null;
}

export interface FundDealWithConsentResult {
  txHash: string;
  chainDealId: number;
  blockNumber: number;
  buyer: string;
  seller: string;
  amountWei: bigint;
  amountEth: string;
  agreementHash: string;
  dealUuid: string;
  gasUsed?: bigint;
  contractAddress: string;
}

// ─── V1 Contract Helpers ─────────────────────────────────────────────────────

/**
 * Instantiate an ethers.js Contract instance for CipherTradeEscrow (V1).
 */
export function getEscrowContract(
  runner: ContractRunner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Contract {
  return new Contract(contractAddress, ESCROW_ABI, runner);
}

/**
 * Instantiate an ethers.js Contract instance for CipherTradeEscrowV2.
 * Throws if V2 address is not configured.
 */
export function getEscrowV2Contract(
  runner: ContractRunner,
  contractAddress?: string
): Contract {
  const addr = contractAddress ?? ESCROW_V2_CONTRACT_ADDRESS;
  if (!addr || !isAddress(addr)) {
    throw new Error(
      'CipherTradeEscrowV2 contract address is not configured. ' +
      'Set VITE_ESCROW_V2_CONTRACT_ADDRESS in your .env file after deployment.'
    );
  }
  return new Contract(addr, ESCROW_V2_ABI, runner);
}

/**
 * Query nextDealId from the escrow contract.
 */
export async function getNextDealId(
  runner: ContractRunner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<bigint> {
  const contract = getEscrowContract(runner, contractAddress);
  const nextId = await contract.nextDealId();
  return BigInt(nextId);
}

/**
 * Fetch and format on-chain deal record by deal ID (V1 contract).
 */
export async function getOnChainDeal(
  dealId: number | bigint,
  runner: ContractRunner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<OnChainDeal> {
  const contract = getEscrowContract(runner, contractAddress);
  const raw = await contract.getDeal(dealId);

  // raw Deal struct: [buyer, seller, amount, deadline, agreementHash, state, fundsWithdrawn]
  const buyer = getAddress(raw[0] || raw.buyer);
  const seller = getAddress(raw[1] || raw.seller);
  const amount = BigInt(raw[2] || raw.amount);
  const deadline = BigInt(raw[3] || raw.deadline);
  const agreementHash = String(raw[4] || raw.agreementHash);
  const stateNum = Number(raw[5] !== undefined ? raw[5] : raw.state);
  const fundsWithdrawn = Boolean(raw[6] !== undefined ? raw[6] : raw.fundsWithdrawn);

  return {
    dealId: Number(dealId),
    buyer,
    seller,
    amount,
    amountEth: formatEther(amount),
    deadline,
    deadlineDate: new Date(Number(deadline) * 1000),
    agreementHash,
    state: stateNum as OnChainEscrowState,
    stateName: ON_CHAIN_STATE_NAMES[stateNum] ?? 'UNKNOWN',
    fundsWithdrawn,
  };
}

/**
 * Fetch and format on-chain deal record by deal ID (V2 contract).
 * V2 Deal struct: [buyer, seller, amount, deadline, agreementHash, dealUuidHash, state, fundsWithdrawn]
 */
export async function getOnChainDealV2(
  dealId: number | bigint,
  runner: ContractRunner,
  contractAddress?: string
): Promise<OnChainDeal> {
  const contract = getEscrowV2Contract(runner, contractAddress);
  const raw = await contract.getDeal(dealId);

  const buyer = getAddress(raw[0] || raw.buyer);
  const seller = getAddress(raw[1] || raw.seller);
  const amount = BigInt(raw[2] || raw.amount);
  const deadline = BigInt(raw[3] || raw.deadline);
  const agreementHash = String(raw[4] || raw.agreementHash);
  // raw[5] is dealUuidHash — skip
  const stateNum = Number(raw[6] !== undefined ? raw[6] : raw.state);
  const fundsWithdrawn = Boolean(raw[7] !== undefined ? raw[7] : raw.fundsWithdrawn);

  return {
    dealId: Number(dealId),
    buyer,
    seller,
    amount,
    amountEth: formatEther(amount),
    deadline,
    deadlineDate: new Date(Number(deadline) * 1000),
    agreementHash,
    state: stateNum as OnChainEscrowState,
    stateName: ON_CHAIN_STATE_NAMES[stateNum] ?? 'UNKNOWN',
    fundsWithdrawn,
  };
}

/**
 * Generate a standard keccak256 32-byte agreement hash from offer & terms metadata.
 */
export function generateAgreementHash(
  offerId: string,
  terms: string,
  priceEth: number | string,
  deadlineTs: number
): string {
  const payload = JSON.stringify({
    offerId,
    terms: terms.trim(),
    priceEth: String(priceEth),
    deadlineTs,
  });
  return keccak256(toUtf8Bytes(payload));
}

/**
 * Validate funding parameters before sending transaction.
 */
export function validateFundDealParams(params: FundDealParams, buyerAddress: string): void {
  if (!params.signer) {
    throw new Error('A valid connected wallet signer is required to fund an escrow deal.');
  }

  if (!params.sellerAddress || !isAddress(params.sellerAddress)) {
    throw new Error('Invalid seller wallet address. Must be a valid 20-byte Ethereum address.');
  }

  const normalizedSeller = getAddress(params.sellerAddress);
  const normalizedBuyer = getAddress(buyerAddress);
  if (normalizedSeller.toLowerCase() === normalizedBuyer.toLowerCase()) {
    throw new Error('Buyer and seller wallet addresses cannot be identical (SelfDeal not allowed).');
  }

  const currentTs = Math.floor(Date.now() / 1000);
  if (!params.deadlineTs || params.deadlineTs <= currentTs) {
    throw new Error(
      `Delivery deadline (${params.deadlineTs}) must be in the future (current time: ${currentTs}).`
    );
  }

  if (
    !params.agreementHash ||
    !/^0x[0-9a-fA-F]{64}$/.test(params.agreementHash)
  ) {
    throw new Error(
      'Invalid agreement hash. Must be a 32-byte hex string (0x followed by 64 hex characters).'
    );
  }

  const amountStr = String(params.amountEth).trim();
  if (!amountStr || isNaN(Number(amountStr)) || Number(amountStr) <= 0) {
    throw new Error('Escrow amount must be a positive number greater than 0.');
  }

  try {
    const wei = parseEther(amountStr);
    if (wei <= 0n) {
      throw new Error('Parsed escrow amount in Wei must be greater than zero.');
    }
  } catch (err: unknown) {
    throw new Error(`Failed to parse ETH amount "${amountStr}": ${(err as Error).message}`);
  }
}

/**
 * Execute fundDeal transaction on the CipherTradeEscrow contract (V1 — LEGACY).
 *
 * This function is preserved for existing funded deals only. New deals must use
 * fundEscrowDealWithConsent() against CipherTradeEscrowV2.
 *
 * 1. Validates inputs thoroughly.
 * 2. Converts amount to Wei safely via parseEther (no float math).
 * 3. Sends transaction with { value: amountWei }.
 * 4. Awaits confirmation receipt.
 * 5. Parses DealFunded event log to extract confirmed chainDealId.
 */
export async function fundEscrowDeal(
  params: FundDealParams,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<FundDealResult> {
  const buyerAddress = await params.signer.getAddress();
  validateFundDealParams(params, buyerAddress);

  const contract = getEscrowContract(params.signer, contractAddress);
  const sellerChecksum = getAddress(params.sellerAddress);
  const amountWei = parseEther(String(params.amountEth).trim());

  // Submit transaction to the blockchain
  const tx = await contract.fundDeal(
    sellerChecksum,
    BigInt(params.deadlineTs),
    params.agreementHash,
    { value: amountWei }
  );

  // Notify caller that transaction was broadcast
  if (params.onTxSubmitted && tx.hash) {
    params.onTxSubmitted(tx.hash);
  }

  // Wait for 1 confirmation
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error(
      `Transaction failed or reverted on-chain. Status: ${receipt?.status}, Hash: ${receipt?.hash}`
    );
  }

  // Parse logs to extract DealFunded event
  let chainDealId: number | null = null;
  for (const log of receipt.logs) {
    try {
      const parsed = contract.interface.parseLog({
        topics: log.topics as string[],
        data: log.data,
      });
      if (parsed && parsed.name === 'DealFunded') {
        // V1 DealFunded(dealId, buyer, seller, amount, deadline, agreementHash)
        chainDealId = Number(parsed.args.dealId ?? parsed.args[0]);
        break;
      }
    } catch {
      // Not a log from this contract or interface
    }
  }

  if (chainDealId === null || isNaN(chainDealId)) {
    throw new Error(
      `Transaction confirmed (${receipt.hash}) but DealFunded event could not be found in logs.`
    );
  }

  return {
    txHash: receipt.hash,
    chainDealId,
    blockNumber: receipt.blockNumber,
    buyer: buyerAddress,
    seller: sellerChecksum,
    amountEth: formatEther(amountWei),
    deadlineTs: params.deadlineTs,
    agreementHash: params.agreementHash,
    gasUsed: receipt.gasUsed,
  };
}

// ─── V2 (Dual-Consent) Funding ───────────────────────────────────────────────

/**
 * Validate the backend contract funding payload before submitting on-chain.
 */
export function validateContractFundingPayload(
  payload: ContractFundingPayload,
  buyerAddress: string,
  requiredChainId: number
): void {
  if (
    !payload.contract_address || !isAddress(payload.contract_address)
  ) {
    throw new Error(
      'Invalid or missing V2 contract address in backend payload. ' +
      'The backend returned a configuration error (503). ' +
      'Ensure ESCROW_V2_CONTRACT_ADDRESS is set in the backend environment.'
    );
  }

  if (
    payload.contract_address.toLowerCase() === ESCROW_CONTRACT_ADDRESS.toLowerCase()
  ) {
    throw new Error(
      'V2 funding cannot use V1 contract address. Potential contract address confusion detected.'
    );
  }

  if (
    !ESCROW_V2_CONTRACT_ADDRESS ||
    !isAddress(ESCROW_V2_CONTRACT_ADDRESS)
  ) {
    throw new Error(
      'CipherTradeEscrowV2 address is not configured in the frontend. ' +
      'Set VITE_ESCROW_V2_CONTRACT_ADDRESS in the frontend .env file.'
    );
  }

  if (
    getAddress(payload.contract_address).toLowerCase() !==
    getAddress(ESCROW_V2_CONTRACT_ADDRESS).toLowerCase()
  ) {
    throw new Error(
      `Backend contract address (${payload.contract_address}) does not match ` +
      `frontend configured V2 address (${ESCROW_V2_CONTRACT_ADDRESS}). ` +
      'Addresses must match exactly. Check deployment configuration.'
    );
  }

  const { agreement, buyerConsent, sellerConsent } = payload;

  if (!agreement.agreementHash || !/^0x[0-9a-fA-F]{64}$/.test(agreement.agreementHash)) {
    throw new Error('Invalid agreementHash in payload. Must be 0x + 64 hex chars.');
  }

  if (!agreement.buyer || !isAddress(agreement.buyer)) {
    throw new Error('Invalid buyer address in agreement payload.');
  }

  if (!agreement.seller || !isAddress(agreement.seller)) {
    throw new Error('Invalid seller address in agreement payload.');
  }

  if (
    getAddress(agreement.buyer).toLowerCase() !== getAddress(buyerAddress).toLowerCase()
  ) {
    throw new Error(
      `Connected wallet (${buyerAddress}) does not match the authorized buyer ` +
      `in the payload (${agreement.buyer}). Please switch to the buyer account in MetaMask.`
    );
  }

  if (!agreement.amount || !/^\d+$/.test(agreement.amount)) {
    throw new Error('Invalid amount in agreement payload: must be a decimal Wei string.');
  }

  if (!agreement.deliveryDeadline || agreement.deliveryDeadline <= 0) {
    throw new Error('Invalid deliveryDeadline in agreement payload.');
  }

  for (const [role, proof] of [['buyer', buyerConsent], ['seller', sellerConsent]] as const) {
    if (!proof.nonce || !/^[0-9a-fA-F]{64}$/.test(proof.nonce)) {
      throw new Error(`Invalid nonce in ${role} consent proof.`);
    }
    if (!proof.deadline || proof.deadline <= 0) {
      throw new Error(`Invalid deadline in ${role} consent proof.`);
    }
    const currentTs = Math.floor(Date.now() / 1000);
    if (proof.deadline < currentTs) {
      throw new Error(
        `${role.charAt(0).toUpperCase() + role.slice(1)} consent proof has expired ` +
        `(deadline: ${proof.deadline}, current: ${currentTs}). Please re-sign consent.`
      );
    }
    if (!proof.signature || !/^0x[0-9a-fA-F]{130}$/.test(proof.signature)) {
      throw new Error(`Invalid signature in ${role} consent proof. Must be 0x + 130 hex chars.`);
    }
  }

  void requiredChainId; // Chain ID enforced by wallet context upstream
}

/**
 * Execute fundDealWithConsent() on CipherTradeEscrowV2.
 *
 * Uses the backend-validated payload returned by GET /api/v1/deals/{deal_id}/contract-payload.
 * Does NOT reconstruct or modify the signed agreement or consent proofs.
 *
 * Security requirements enforced:
 *  1. Connected wallet must be the authorized buyer.
 *  2. Backend and frontend V2 addresses must match.
 *  3. All consent deadlines must still be valid.
 *  4. Amount is passed as BigInt from the backend Wei string.
 *  5. DealFunded event must be present in the confirmed receipt.
 *  6. chainDealId is extracted from the event (not from return value).
 */
export async function fundEscrowDealWithConsent(
  payload: ContractFundingPayload,
  signer: JsonRpcSigner,
  onTxSubmitted?: (txHash: string) => void
): Promise<FundDealWithConsentResult> {
  const buyerAddress = await signer.getAddress();
  validateContractFundingPayload(payload, buyerAddress, 31337);

  const { agreement, buyerConsent, sellerConsent } = payload;
  const contractAddress = getAddress(payload.contract_address!);

  const contract = getEscrowV2Contract(signer, contractAddress);

  // Construct Solidity CommercialAgreement struct exactly:
  // bytes32 agreementHash, string offerId, string dealId,
  // address buyer, address seller, uint256 amount, uint256 deliveryDeadline
  const agreementStruct = {
    agreementHash: agreement.agreementHash as `0x${string}`,
    offerId: agreement.offerId,
    dealId: agreement.dealId,
    buyer: getAddress(agreement.buyer),
    seller: getAddress(agreement.seller),
    amount: BigInt(agreement.amount),         // Wei as BigInt
    deliveryDeadline: BigInt(agreement.deliveryDeadline),
  };

  // Construct Solidity PartyConsentProof structs exactly:
  // string nonce, uint256 deadline, bytes signature
  const buyerConsentStruct = {
    nonce: buyerConsent.nonce,
    deadline: BigInt(buyerConsent.deadline),
    signature: buyerConsent.signature as `0x${string}`,
  };

  const sellerConsentStruct = {
    nonce: sellerConsent.nonce,
    deadline: BigInt(sellerConsent.deadline),
    signature: sellerConsent.signature as `0x${string}`,
  };

  // Amount to send with the transaction
  const amountWei = BigInt(agreement.amount);

  // Submit fundDealWithConsent transaction
  const tx = await contract.fundDealWithConsent(
    agreementStruct,
    buyerConsentStruct,
    sellerConsentStruct,
    { value: amountWei }
  );

  if (onTxSubmitted && tx.hash) {
    onTxSubmitted(tx.hash);
  }

  // Wait for 1 confirmation
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error(
      `fundDealWithConsent transaction failed or reverted on-chain. ` +
      `Status: ${receipt?.status}, Hash: ${receipt?.hash}`
    );
  }

  // Parse DealFunded event from V2 contract
  // V2 DealFunded(uint256 chainDealId, bytes32 dealUuidHash, address buyer,
  //               address seller, uint256 amount, uint256 deliveryDeadline,
  //               bytes32 agreementHash, string dealUuid, string offerId)
  let chainDealId: number | null = null;
  for (const log of receipt.logs) {
    try {
      const parsed = contract.interface.parseLog({
        topics: log.topics as string[],
        data: log.data,
      });
      if (parsed && parsed.name === 'DealFunded') {
        chainDealId = Number(parsed.args.chainDealId ?? parsed.args[0]);
        break;
      }
    } catch {
      // Not a log from this contract
    }
  }

  if (chainDealId === null || isNaN(chainDealId)) {
    throw new Error(
      `fundDealWithConsent confirmed (${receipt.hash}) but DealFunded event not found in logs.`
    );
  }

  return {
    txHash: receipt.hash,
    chainDealId,
    blockNumber: receipt.blockNumber,
    buyer: buyerAddress,
    seller: getAddress(agreement.seller),
    amountWei,
    amountEth: formatEther(amountWei),
    agreementHash: agreement.agreementHash,
    dealUuid: agreement.dealId,
    gasUsed: receipt.gasUsed,
    contractAddress,
  };
}

/**
 * Format user-friendly error messages from contract/provider errors.
 */
export function formatContractError(err: unknown): string {
  if (!err) return 'An unknown blockchain error occurred.';
  const errorObj = err as Record<string, unknown>;

  // User rejected in MetaMask
  if (
    errorObj.code === 4001 ||
    errorObj.code === 'ACTION_REJECTED' ||
    String(errorObj.message).toLowerCase().includes('user rejected')
  ) {
    return 'Transaction was rejected in MetaMask.';
  }

  // Insufficient funds
  if (
    errorObj.code === 'INSUFFICIENT_FUNDS' ||
    String(errorObj.message).toLowerCase().includes('insufficient funds')
  ) {
    return 'Insufficient funds in your wallet to cover the escrow amount and gas fee.';
  }

  // V2 custom errors
  const rawMsg = String(errorObj.reason || errorObj.shortMessage || errorObj.message || '');
  if (rawMsg.includes('SelfDeal')) return 'Cannot fund a deal where buyer and seller are the same wallet address.';
  if (rawMsg.includes('ZeroAmount')) return 'Escrow amount must be greater than zero.';
  if (rawMsg.includes('InvalidDeadline')) return 'The delivery deadline must be set in the future.';
  if (rawMsg.includes('ConsentExpired')) return 'One or both consent signatures have expired. Please refresh consent.';
  if (rawMsg.includes('NonceReplayed')) return 'A consent nonce has already been used. Please refresh consent.';
  if (rawMsg.includes('SignerMismatch')) return 'Signature signer does not match the authorized buyer or seller address.';
  if (rawMsg.includes('DealAlreadyFunded')) return 'This deal UUID has already been funded on-chain.';
  if (rawMsg.includes('AmountMismatch')) return 'Transaction value does not match the agreed escrow amount.';
  if (rawMsg.includes('InvalidSignatureLength')) return 'Consent signature length is invalid (must be 65 bytes).';
  if (rawMsg.includes('InvalidSignatureS')) return 'Consent signature has high-s value (EIP-2 malleability rejected).';
  if (rawMsg.includes('InvalidSignatureV')) return 'Consent signature has invalid v value (must be 27 or 28).';
  if (rawMsg.includes('UnauthorizedCaller')) return 'Only the buyer can call fundDealWithConsent.';
  if (rawMsg.includes('Unauthorized')) return 'You are not authorized to perform this operation.';
  if (rawMsg.includes('ZeroAddress')) return 'Buyer or seller address is the zero address.';
  if (rawMsg.includes('Reentrancy')) return 'Transaction rejected due to contract reentrancy guard.';
  if (rawMsg.includes('TransferFailed')) return 'ETH transfer failed on-chain.';

  // V1 fallback errors
  if (rawMsg.includes('DeadlineNotReached')) return 'The delivery deadline has not elapsed yet. Cancellation is not allowed before the deadline.';
  if (rawMsg.includes('AlreadyWithdrawn')) return 'Funds for this escrow deal have already been withdrawn or settled.';
  if (rawMsg.includes('InvalidState')) return 'The escrow deal is not in an eligible state for this action.';

  return rawMsg || 'Failed to complete transaction on the blockchain.';
}

/**
 * Resolved lifecycle contract information.
 */
export interface ResolvedLifecycleContract {
  contract: Contract;
  version: EscrowContractVersion;
  address: string;
}

/**
 * Resolve and validate the smart contract (V1 or V2) for an escrow lifecycle operation.
 *
 * Rules:
 *  1. Missing contract identity is rejected.
 *  2. Unsupported contract versions are rejected (only 'v1' and 'v2' allowed).
 *  3. Malformed contract addresses are rejected.
 *  4. V2 deals route through V2 ABI and must not point to V1 address.
 *  5. V1 deals route through V1 ABI and must not point to V2 address.
 *  6. V2 deals never fall back to V1 under any circumstances.
 */
export function resolveLifecycleContract(
  runner: ContractRunner,
  contractIdentity?: string | DealContractIdentity | null
): ResolvedLifecycleContract {
  if (!contractIdentity) {
    throw new Error(
      'Missing contract identity for escrow deal. Neither contract version nor contract address was provided.'
    );
  }

  let rawVersion: string | null = null;
  let rawAddress: string | null = null;

  if (typeof contractIdentity === 'string') {
    rawAddress = contractIdentity.trim();
  } else if (typeof contractIdentity === 'object') {
    if (contractIdentity.contract_version !== undefined && contractIdentity.contract_version !== null) {
      rawVersion = String(contractIdentity.contract_version).trim();
    }
    if (contractIdentity.contract_address !== undefined && contractIdentity.contract_address !== null) {
      rawAddress = String(contractIdentity.contract_address).trim();
    }
  } else {
    throw new Error('Invalid contract identity specification: expected a contract address string or identity object.');
  }

  if (!rawVersion && !rawAddress) {
    throw new Error(
      'Missing contract identity for escrow deal. Neither contract version nor contract address was provided.'
    );
  }

  // Validate version if provided
  let normalizedVersion: EscrowContractVersion | null = null;
  if (rawVersion) {
    const vLower = rawVersion.toLowerCase();
    if (vLower === 'v1' || vLower === 'v2') {
      normalizedVersion = vLower;
    } else {
      throw new Error(`Unsupported contract version: "${rawVersion}". Only "v1" and "v2" are supported.`);
    }
  }

  // Validate address if provided
  let checksumAddress: string | null = null;
  if (rawAddress) {
    if (!isAddress(rawAddress)) {
      throw new Error(`Malformed contract address: "${rawAddress}". Must be a valid 20-byte hex Ethereum address.`);
    }
    checksumAddress = getAddress(rawAddress);
  }

  const v1ConfigAddress = getAddress(ESCROW_CONTRACT_ADDRESS);
  const v2ConfigAddress =
    ESCROW_V2_CONTRACT_ADDRESS && isAddress(ESCROW_V2_CONTRACT_ADDRESS)
      ? getAddress(ESCROW_V2_CONTRACT_ADDRESS)
      : null;

  if (normalizedVersion === 'v2') {
    if (!checksumAddress) {
      throw new Error('Missing contract address for V2 deal. V2 deals must specify a verified contract_address.');
    }
    if (checksumAddress.toLowerCase() === v1ConfigAddress.toLowerCase()) {
      throw new Error(
        `Contract identity mismatch: V2 deal cannot target the V1 contract address (${v1ConfigAddress}). Potential contract confusion detected.`
      );
    }
    if (!v2ConfigAddress) {
      throw new Error(
        'CipherTradeEscrowV2 address is not configured in the frontend. ' +
        'Set VITE_ESCROW_V2_CONTRACT_ADDRESS in your .env file after deployment.'
      );
    }
    if (checksumAddress.toLowerCase() !== v2ConfigAddress.toLowerCase()) {
      throw new Error(
        `V2 contract address mismatch: deal contract address (${checksumAddress}) does not match configured V2 address (${v2ConfigAddress}).`
      );
    }
    const contract = getEscrowV2Contract(runner, checksumAddress);
    return { contract, version: 'v2', address: checksumAddress };
  }

  if (normalizedVersion === 'v1') {
    if (checksumAddress) {
      if (v2ConfigAddress && checksumAddress.toLowerCase() === v2ConfigAddress.toLowerCase()) {
        throw new Error(
          `Contract identity mismatch: V1 deal cannot target the V2 contract address (${checksumAddress}). Potential contract confusion detected.`
        );
      }
      if (checksumAddress.toLowerCase() !== v1ConfigAddress.toLowerCase()) {
        throw new Error(
          `V1 contract address mismatch: deal contract address (${checksumAddress}) does not match configured V1 address (${v1ConfigAddress}).`
        );
      }
    }
    const targetAddress = checksumAddress ?? v1ConfigAddress;
    const contract = getEscrowContract(runner, targetAddress);
    return { contract, version: 'v1', address: targetAddress };
  }

  // Version omitted: deduce safely from checksumAddress
  if (checksumAddress) {
    if (checksumAddress.toLowerCase() === v1ConfigAddress.toLowerCase()) {
      const contract = getEscrowContract(runner, checksumAddress);
      return { contract, version: 'v1', address: checksumAddress };
    }
    if (v2ConfigAddress && checksumAddress.toLowerCase() === v2ConfigAddress.toLowerCase()) {
      const contract = getEscrowV2Contract(runner, checksumAddress);
      return { contract, version: 'v2', address: checksumAddress };
    }
    throw new Error(
      `Unknown or unconfigured contract address: "${checksumAddress}". Does not match configured V1 (${v1ConfigAddress}) or V2 (${v2ConfigAddress || 'not configured'}).`
    );
  }

  throw new Error('Missing contract identity for escrow deal. Neither contract version nor contract address was provided.');
}

/**
 * Buyer confirms delivery on-chain.
 * Routes to V1 (CipherTradeEscrow) or V2 (CipherTradeEscrowV2) based on contract identity.
 */
export async function confirmDeliveryOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractIdentity?: string | DealContractIdentity
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const { contract } = resolveLifecycleContract(signer, contractIdentity);
  const tx = await contract.confirmDelivery(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('confirmDelivery transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Buyer cancels expired deal and reclaims escrowed funds.
 * Routes to V1 (CipherTradeEscrow) or V2 (CipherTradeEscrowV2) based on contract identity.
 */
export async function cancelExpiredOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractIdentity?: string | DealContractIdentity
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const { contract } = resolveLifecycleContract(signer, contractIdentity);
  const tx = await contract.cancelExpired(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('cancelExpired transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Seller withdraws payment after buyer has confirmed delivery (pull payment pattern).
 * Routes to V1 (CipherTradeEscrow) or V2 (CipherTradeEscrowV2) based on contract identity.
 */
export async function withdrawPaymentOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractIdentity?: string | DealContractIdentity
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const { contract } = resolveLifecycleContract(signer, contractIdentity);
  const tx = await contract.withdrawPayment(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('withdrawPayment transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Seller voluntarily refunds the buyer before delivery is confirmed.
 * Routes to V1 (CipherTradeEscrow) or V2 (CipherTradeEscrowV2) based on contract identity.
 */
export async function refundBuyerOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractIdentity?: string | DealContractIdentity
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const { contract } = resolveLifecycleContract(signer, contractIdentity);
  const tx = await contract.refundBuyer(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('refundBuyer transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Either party (buyer or seller) raises a dispute, freezing funds for arbitrator.
 * Routes to V1 (CipherTradeEscrow) or V2 (CipherTradeEscrowV2) based on contract identity.
 */
export async function raiseDisputeOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractIdentity?: string | DealContractIdentity
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const { contract } = resolveLifecycleContract(signer, contractIdentity);
  const tx = await contract.raiseDispute(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('raiseDispute transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Arbitrator resolves an open dispute.
 * Routes to V1 (CipherTradeEscrow) or V2 (CipherTradeEscrowV2) based on contract identity.
 */
export async function resolveDisputeOnChain(
  chainDealId: number,
  toSeller: boolean,
  signer: JsonRpcSigner,
  contractIdentity?: string | DealContractIdentity
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const { contract } = resolveLifecycleContract(signer, contractIdentity);
  const tx = await contract.resolveDispute(BigInt(chainDealId), toSeller);
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('resolveDispute transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}
