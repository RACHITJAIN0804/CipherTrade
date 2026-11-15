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
import { ESCROW_CONTRACT_ADDRESS, ESCROW_ABI } from '../config/contracts.ts';

/**
 * On-chain lifecycle states defined in CipherTradeEscrow.sol:
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

/**
 * Instantiate an ethers.js Contract instance for CipherTradeEscrow.
 */
export function getEscrowContract(
  runner: ContractRunner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Contract {
  return new Contract(contractAddress, ESCROW_ABI, runner);
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
 * Fetch and format on-chain deal record by deal ID.
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
 * Execute fundDeal transaction on the CipherTradeEscrow contract.
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
        // DealFunded(dealId, buyer, seller, amount, deadline, agreementHash)
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

  // Contract custom errors
  const rawMsg = String(errorObj.reason || errorObj.shortMessage || errorObj.message || '');
  if (rawMsg.includes('SelfDeal')) {
    return 'Cannot fund a deal where buyer and seller are the same wallet address.';
  }
  if (rawMsg.includes('ZeroAmount')) {
    return 'Escrow amount must be greater than zero.';
  }
  if (rawMsg.includes('InvalidDeadline')) {
    return 'The delivery deadline must be set in the future.';
  }
  if (rawMsg.includes('Reentrancy')) {
    return 'Transaction rejected due to contract reentrancy guard.';
  }
  if (rawMsg.includes('Unauthorized')) {
    return 'You are not authorized to perform this operation.';
  }
  if (rawMsg.includes('DeadlineNotReached')) {
    return 'The delivery deadline has not elapsed yet. Cancellation is not allowed before the deadline.';
  }
  if (rawMsg.includes('AlreadyWithdrawn')) {
    return 'Funds for this escrow deal have already been withdrawn or settled.';
  }
  if (rawMsg.includes('InvalidState')) {
    return 'The escrow deal is not in an eligible state for this action.';
  }
  if (rawMsg.includes('TransferFailed')) {
    return 'ETH transfer failed on-chain.';
  }

  return rawMsg || 'Failed to complete transaction on the blockchain.';
}

/**
 * Buyer confirms delivery on-chain.
 * Solidity: confirmDelivery(uint256 dealId) [onlyBuyer, FUNDED]
 */
export async function confirmDeliveryOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const contract = getEscrowContract(signer, contractAddress);
  const tx = await contract.confirmDelivery(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('confirmDelivery transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Buyer cancels expired deal and reclaims escrowed funds.
 * Solidity: cancelExpired(uint256 dealId) [onlyBuyer, FUNDED, timestamp > deadline]
 */
export async function cancelExpiredOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const contract = getEscrowContract(signer, contractAddress);
  const tx = await contract.cancelExpired(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('cancelExpired transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Seller withdraws payment after buyer has confirmed delivery (pull payment pattern).
 * Solidity: withdrawPayment(uint256 dealId) [onlySeller, CONFIRMED]
 */
export async function withdrawPaymentOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const contract = getEscrowContract(signer, contractAddress);
  const tx = await contract.withdrawPayment(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('withdrawPayment transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Seller voluntarily refunds the buyer before delivery is confirmed.
 * Solidity: refundBuyer(uint256 dealId) [onlySeller, FUNDED]
 */
export async function refundBuyerOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const contract = getEscrowContract(signer, contractAddress);
  const tx = await contract.refundBuyer(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('refundBuyer transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}

/**
 * Either party (buyer or seller) raises a dispute, freezing funds for arbitrator.
 * Solidity: raiseDispute(uint256 dealId) [onlyParty, FUNDED or CONFIRMED]
 */
export async function raiseDisputeOnChain(
  chainDealId: number,
  signer: JsonRpcSigner,
  contractAddress: string = ESCROW_CONTRACT_ADDRESS
): Promise<string> {
  if (chainDealId < 0) throw new Error('Invalid chain deal ID.');
  if (!signer) throw new Error('A connected wallet signer is required.');
  const contract = getEscrowContract(signer, contractAddress);
  const tx = await contract.raiseDispute(BigInt(chainDealId));
  const receipt = await tx.wait(1);
  if (!receipt || receipt.status !== 1) {
    throw new Error('raiseDispute transaction failed or reverted on-chain.');
  }
  return receipt.hash;
}
