import { useState, useEffect } from 'react';
import {
  Coins,
  ShieldCheck,
  ShieldAlert,
  Shield,
  AlertTriangle,
  CheckCircle2,
  Copy,
  Check,
  Clock,
  ArrowRight,
  RefreshCw,
  Wallet,
  AlertCircle,
  Database,
} from 'lucide-react';

import { isAddress, getAddress, parseEther } from 'ethers';
import { useWallet } from '../../context/WalletContext';
import {
  fundEscrowDealWithConsent,
  formatContractError,
  type FundDealResult,
  type FundDealWithConsentResult,
} from '../../services/contractService';
import {
  createEscrowDeal,
  verifyFunding,
  getCanonicalAgreement,
  getCanonicalAgreementByOffer,
  getConsentStatus,
  requestConsentChallenge,
  verifyConsentSignature,
  validateConsentDomain,
  getContractFundingPayload,
  type ConsentStatusResponse,
} from '../../services/escrowService';
import { getUser } from '../../services/userService';

import {
  ESCROW_CONTRACT_ADDRESS,
  ESCROW_V2_CONTRACT_ADDRESS,
  ESCROW_CHAIN_ID,
  escrowV2Config,
} from '../../config/contracts';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Spinner } from '../ui/Spinner';
import type { EscrowDeal } from '../../types';

export interface FundEscrowModalProps {
  open: boolean;
  onClose: () => void;
  dealId?: string; // Optional backend deal UUID if already created
  offerId?: string;
  sellerId?: string;
  sellerAddress?: string;
  amountEth?: number | string;
  deliveryDeadline?: number; // Unix timestamp in seconds
  terms?: string;
  agreementHash?: string;
  onSuccess?: (result: { onChain: FundDealResult | FundDealWithConsentResult; deal: EscrowDeal }) => void;
}

type Step =
  | 'form'
  | 'awaiting_signature'
  | 'pending_tx'
  | 'verifying_backend'
  | 'verified'
  | 'verification_failed'
  | 'blockchain_error';

export function FundEscrowModal({
  open,
  onClose,
  dealId: initialDealId = '',
  offerId = '',
  sellerId = '',
  sellerAddress: initialSellerAddress = '',
  amountEth: initialAmountEth = '',
  deliveryDeadline: initialDeadline = 0,
  terms: _terms = '',
  agreementHash: initialAgreementHash = '',
  onSuccess,
}: FundEscrowModalProps) {
  const {
    account,
    chainId,
    isConnected,
    isCorrectNetwork,
    signer,
    connectWallet,
    switchToAnvil,
    formatAddress,
    isWalletVerified,
    verifyWalletOwnership,
  } = useWallet();

  // Form state
  const [sellerAddr, setSellerAddr] = useState('');

  const [registeredSellerWallet, setRegisteredSellerWallet] = useState<string | null>(null);
  const [checkingSellerWallet, setCheckingSellerWallet] = useState(false);
  const [amount, setAmount] = useState('');
  const [deadlineDate, setDeadlineDate] = useState('');
  const [authoritativeHash, setAuthoritativeHash] = useState<string>(initialAgreementHash || '');
  const [isLoadingAgreement, setIsLoadingAgreement] = useState(false);
  const [agreementError, setAgreementError] = useState<string | null>(null);

  // Backend Deal ID (UUID)
  const [activeDealId, setActiveDealId] = useState<string>(initialDealId);

  // Flow & transaction state
  const [step, setStep] = useState<Step>('form');
  const [pendingTxHash, setPendingTxHash] = useState<string | null>(null);
  const [confirmedResult, setConfirmedResult] = useState<FundDealResult | FundDealWithConsentResult | null>(null);
  const [isLoadingPayload, setIsLoadingPayload] = useState(false);
  const [verifiedDeal, setVerifiedDeal] = useState<EscrowDeal | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const [copiedHash, setCopiedHash] = useState(false);
  const [copiedTx, setCopiedTx] = useState(false);
  const [copiedDealId, setCopiedDealId] = useState(false);

  // Cryptographic Consent (Phase 5D.3)
  const [consentStatus, setConsentStatus] = useState<ConsentStatusResponse | null>(null);
  const [isLoadingConsent, setIsLoadingConsent] = useState(false);
  const [isSigningConsent, setIsSigningConsent] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);
  const [consentSuccess, setConsentSuccess] = useState<string | null>(null);

  // Initialize form state when modal opens
  useEffect(() => {
    if (open) {
      setStep('form');
      setPendingTxHash(null);
      setConfirmedResult(null);
      setVerifiedDeal(null);
      setErrorMessage(null);
      setVerificationError(null);
      setActiveDealId(initialDealId);

      setSellerAddr(initialSellerAddress);
      setAmount(initialAmountEth ? String(initialAmountEth) : '');

      if (initialDeadline && initialDeadline > 0) {
        const d = new Date(initialDeadline * 1000);
        setDeadlineDate(d.toISOString().slice(0, 16));
      } else {
        const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000);
        setDeadlineDate(tomorrow.toISOString().slice(0, 16));
      }

      // Fetch or initialize authoritative agreement hash from backend (Phase 5D.2)
      if (initialAgreementHash) {
        setAuthoritativeHash(initialAgreementHash);
      } else if (initialDealId) {
        setIsLoadingAgreement(true);
        setAgreementError(null);
        getCanonicalAgreement(initialDealId)
          .then((res) => {
            setAuthoritativeHash(res.agreement_hash);
          })
          .catch((err) => {
            setAgreementError((err as Error).message || 'Failed to fetch authoritative agreement hash.');
          })
          .finally(() => setIsLoadingAgreement(false));
      } else if (offerId) {
        setIsLoadingAgreement(true);
        setAgreementError(null);
        getCanonicalAgreementByOffer(offerId)
          .then((res) => {
            setAuthoritativeHash(res.agreement_hash);
            if (res.deal_id) {
              setActiveDealId(res.deal_id);
            }
          })
          .catch((err) => {
            setAgreementError((err as Error).message || 'Failed to fetch authoritative agreement hash.');
          })
          .finally(() => setIsLoadingAgreement(false));
      } else {
        setAuthoritativeHash('');
      }

      // Check seller registered wallet if sellerId is provided
      if (sellerId) {
        setCheckingSellerWallet(true);
        getUser(sellerId)
          .then((sellerInfo) => {
            if (sellerInfo.wallet_address) {
              setRegisteredSellerWallet(sellerInfo.wallet_address);
              setSellerAddr(sellerInfo.wallet_address);
            } else {
              setRegisteredSellerWallet(null);
            }
          })
          .catch(() => {
            setRegisteredSellerWallet(null);
          })
          .finally(() => {
            setCheckingSellerWallet(false);
          });
      }

      // Fetch initial cryptographic consent status (Phase 5D.3)
      setConsentStatus(null);
      setConsentError(null);
      setConsentSuccess(null);
      const targetConsentId = initialDealId || offerId;
      if (targetConsentId) {
        setIsLoadingConsent(true);
        getConsentStatus(targetConsentId, !initialDealId && Boolean(offerId))
          .then((res) => setConsentStatus(res))
          .catch(() => setConsentStatus(null))
          .finally(() => setIsLoadingConsent(false));
      }
    }
  }, [
    open,
    initialDealId,
    offerId,
    sellerId,
    initialSellerAddress,
    initialAmountEth,
    initialDeadline,
    initialAgreementHash,
  ]);

  const refreshConsentStatus = async (targetIdOverride?: string, isOfferOverride?: boolean) => {
    const targetId = targetIdOverride || activeDealId || offerId;
    if (!targetId) return;
    setIsLoadingConsent(true);
    try {
      const isOffer = isOfferOverride !== undefined ? isOfferOverride : (!activeDealId && Boolean(offerId));
      const res = await getConsentStatus(targetId, isOffer);
      setConsentStatus(res);
      if (res.deal_id && !activeDealId) {
        setActiveDealId(res.deal_id);
      }
    } catch (err: unknown) {
      console.warn('Could not fetch consent status:', err);
    } finally {
      setIsLoadingConsent(false);
    }
  };

  const handleSignConsent = async () => {
    if (!signer || !account) {
      setConsentError('Please connect your MetaMask wallet first.');
      return;
    }
    if (!isCorrectNetwork) {
      setConsentError('Please switch to Anvil Local network (Chain ID 31337).');
      return;
    }
    const targetId = activeDealId || offerId;
    if (!targetId) {
      setConsentError('No active deal or offer found to sign.');
      return;
    }

    setIsSigningConsent(true);
    setConsentError(null);
    setConsentSuccess(null);

    try {
      const isOffer = !activeDealId && Boolean(offerId);
      const challenge = await requestConsentChallenge(targetId, isOffer);

      // Validate EIP-712 challenge domain and connected chain before presenting signature prompt
      validateConsentDomain(challenge.domain, chainId, ESCROW_V2_CONTRACT_ADDRESS || undefined);

      // Verify connected wallet matches challenge signer
      if (getAddress(account).toLowerCase() !== getAddress(challenge.message.signer).toLowerCase()) {
        throw new Error(
          `Connected wallet (${formatAddress(account)}) does not match registered account wallet (${formatAddress(challenge.message.signer)}). Please switch accounts in MetaMask.`
        );
      }

      // Sign EIP-712 typed-data
      const typesToSign = {
        AgreementConsent: challenge.types.AgreementConsent,
      };

      const signature = await signer.signTypedData(
        challenge.domain,
        typesToSign,
        challenge.message
      );

      // Verify and record consent
      const verifyRes = await verifyConsentSignature(
        targetId,
        { nonce: challenge.nonce, signature },
        isOffer
      );

      setConsentSuccess(`Agreement cryptographically signed as ${verifyRes.party_role}!`);
      await refreshConsentStatus(targetId, isOffer);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to cryptographically sign agreement.';
      setConsentError(msg);
    } finally {
      setIsSigningConsent(false);
    }
  };

  // Active agreement hash (authoritative backend fingerprint)
  const computedDeadlineTs = deadlineDate ? Math.floor(new Date(deadlineDate).getTime() / 1000) : 0;
  const effectiveHash = authoritativeHash.trim();

  // Copy helpers
  const handleCopy = (text: string, type: 'hash' | 'tx' | 'dealId') => {
    navigator.clipboard.writeText(text);
    if (type === 'hash') {
      setCopiedHash(true);
      setTimeout(() => setCopiedHash(false), 2000);
    } else if (type === 'tx') {
      setCopiedTx(true);
      setTimeout(() => setCopiedTx(false), 2000);
    } else {
      setCopiedDealId(true);
      setTimeout(() => setCopiedDealId(false), 2000);
    }
  };

  // Self-deal check
  const isSelfDeal =
    account && sellerAddr && isAddress(sellerAddr)
      ? getAddress(account).toLowerCase() === getAddress(sellerAddr).toLowerCase()
      : false;

  // Validation logic
  const isAddressValid = Boolean(sellerAddr && isAddress(sellerAddr));
  const isAmountValid = Boolean(amount && !isNaN(Number(amount)) && Number(amount) > 0);
  const isDeadlineValid = Boolean(computedDeadlineTs > Math.floor(Date.now() / 1000));
  const isHashValid = Boolean(/^0x[0-9a-fA-F]{64}$/.test(effectiveHash));

  // Seller wallet mismatch warning
  const isSellerWalletMismatch = Boolean(
    registeredSellerWallet &&
      isAddressValid &&
      getAddress(sellerAddr).toLowerCase() !== getAddress(registeredSellerWallet).toLowerCase()
  );

  const bothConsented = Boolean(consentStatus?.both_consented);

  const canSubmit =
    isConnected &&
    isCorrectNetwork &&
    signer &&
    isAddressValid &&
    isAmountValid &&
    isDeadlineValid &&
    isHashValid &&
    !isLoadingAgreement &&
    !agreementError &&
    !isLoadingPayload &&
    !isSelfDeal &&
    bothConsented &&
    escrowV2Config.isConfigured &&
    step === 'form';

  /**
   * Helper to execute backend verification on an already confirmed transaction.
   */
  const executeBackendVerification = async (dealIdToVerify: string, txHashToVerify: string) => {
    setStep('verifying_backend');
    setVerificationError(null);
    try {
      const verified = await verifyFunding(dealIdToVerify, txHashToVerify);
      setVerifiedDeal(verified);
      setStep('verified');
      if (onSuccess && confirmedResult) {
        onSuccess({ onChain: confirmedResult, deal: verified });
      }
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Backend failed to verify on-chain funding transaction.';
      setVerificationError(msg);
      setStep('verification_failed');
    }
  };

  /**
   * Primary Fund & Verification workflow
   */
  const handleFund = async () => {
    if (!signer || !account) {
      setErrorMessage('Please connect your MetaMask wallet first.');
      return;
    }
    if (!isCorrectNetwork) {
      setErrorMessage('Please switch to the Anvil Local network (Chain ID 31337).');
      return;
    }
    if (isSelfDeal) {
      setErrorMessage('Buyer and seller wallet cannot be identical (SelfDeal).');
      return;
    }

    try {
      setErrorMessage(null);
      setVerificationError(null);

      // Pre-flight 0: Ensure mutual agreement consent exists (Phase 5D.3)
      if (!consentStatus?.both_consented) {
        setErrorMessage(
          'Both buyer and seller must cryptographically consent to the agreement terms before escrow funding can proceed.'
        );
        return;
      }

      // Pre-flight 1: Ensure buyer's wallet is cryptographically verified via EIP-4361 SIWE
      if (!isWalletVerified) {
        const verified = await verifyWalletOwnership();
        if (!verified) {
          setErrorMessage('Cryptographic wallet ownership verification (SIWE) is required before funding escrow.');
          return;
        }
      }


      // Pre-flight 2: Create or retrieve backend EscrowDeal record to obtain the authoritative deal UUID
      let targetDealId = activeDealId;

      if (!targetDealId && offerId) {
        try {
          const dealRecord = await createEscrowDeal({
            offer_id: offerId,
          });
          targetDealId = dealRecord.id;
          setActiveDealId(dealRecord.id);
        } catch (dealErr: unknown) {
          const msg =
            (dealErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
            'Failed to create backend escrow deal record.';
          setErrorMessage(msg);
          setStep('blockchain_error');
          return;
        }
      }

      if (!targetDealId) {
        setErrorMessage('Cannot proceed: valid deal ID is required for dual-consent escrow funding.');
        return;
      }

      // Pre-flight 3: Ensure V2 contract configuration exists
      if (!escrowV2Config.isConfigured) {
        setErrorMessage(
          'CipherTradeEscrowV2 contract is not configured on this frontend. Please set VITE_ESCROW_V2_CONTRACT_ADDRESS.'
        );
        return;
      }

      // Step: Fetch contract-ready dual-consent funding payload from backend
      setIsLoadingPayload(true);
      let fundingPayload;
      try {
        fundingPayload = await getContractFundingPayload(targetDealId);
      } catch (payloadErr: unknown) {
        const msg =
          (payloadErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          (payloadErr as Error)?.message ??
          'Failed to retrieve contract funding payload from backend.';
        setErrorMessage(msg);
        setStep('blockchain_error');
        return;
      } finally {
        setIsLoadingPayload(false);
      }

      // Step: Awaiting MetaMask signature
      setStep('awaiting_signature');

      // Dispatch V2 dual-consent smart contract transaction
      const onChainResult = await fundEscrowDealWithConsent(
        fundingPayload,
        signer,
        (txHash: string) => {
          setPendingTxHash(txHash);
          setStep('pending_tx');
        }
      );

      setConfirmedResult(onChainResult);

      // Step: Trigger backend independent verification
      await executeBackendVerification(targetDealId, onChainResult.txHash);
    } catch (err: unknown) {
      const formatted = formatContractError(err);
      setErrorMessage(formatted);
      setStep('blockchain_error');
    }
  };

  /**
   * Retry handler for backend verification (without broadcasting a new transaction)
   */
  const handleRetryVerification = async () => {
    if (!activeDealId || !confirmedResult) return;
    await executeBackendVerification(activeDealId, confirmedResult.txHash);
  };

  return (
    <Modal open={open} onClose={onClose} title="Fund Escrow & Verify On-Chain" size="lg">
      <div className="space-y-5 text-slate-200">
        {/* Network / Wallet Warning Banner */}
        {!isConnected && (
          <div className="p-4 rounded-lg bg-blue-900/20 border border-blue-500/30 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Wallet className="w-5 h-5 text-blue-400 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-white">MetaMask Wallet Required</p>
                <p className="text-slate-400">Connect your wallet to lock funds into smart contract escrow.</p>
              </div>
            </div>
            <Button size="sm" onClick={connectWallet} leftIcon={<Wallet className="w-4 h-4" />}>
              Connect
            </Button>
          </div>
        )}

        {isConnected && !isCorrectNetwork && (
          <div className="p-4 rounded-lg bg-amber-900/20 border border-amber-500/30 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-white">Wrong Network (Chain ID: {chainId})</p>
                <p className="text-slate-400">
                  Switch to Anvil Local (Chain ID {ESCROW_CHAIN_ID}) to interact with the deployed escrow contract.
                </p>
              </div>
            </div>
            <Button size="sm" variant="secondary" onClick={switchToAnvil}>
              Switch to Anvil
            </Button>
          </div>
        )}

        {isConnected && isCorrectNetwork && !isWalletVerified && (
          <div className="p-3.5 rounded-lg bg-blue-950/40 border border-blue-500/40 flex items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2.5 text-blue-200">
              <ShieldAlert className="w-4 h-4 text-blue-400 shrink-0" />
              <div>
                <p className="font-semibold text-white">Wallet Ownership Not Verified</p>
                <p className="text-slate-400">Sign an EIP-4361 challenge to link this wallet address to your profile.</p>
              </div>
            </div>
            <Button
              size="sm"
              variant="secondary"
              onClick={verifyWalletOwnership}
              leftIcon={<Shield className="w-3.5 h-3.5" />}
            >
              Verify Wallet
            </Button>
          </div>
        )}

        {/* STEP: Form */}

        {step === 'form' && (
          <div className="space-y-4">
            {/* Contract info bar */}
            <div className="px-3.5 py-2.5 rounded-lg bg-slate-900/60 border border-slate-800 text-xs text-slate-400 flex items-center justify-between">
              <span>Escrow Contract (V2 Dual-Consent):</span>
              <span className="font-mono text-cyan-400">
                {escrowV2Config.isConfigured
                  ? formatAddress(ESCROW_V2_CONTRACT_ADDRESS)
                  : 'Not Configured'}
              </span>
            </div>

            {/* Seller Address */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="block text-sm font-medium text-slate-300">
                  Seller Wallet Address (Recipient) *
                </label>
                {checkingSellerWallet ? (
                  <span className="text-xs text-slate-400 flex items-center gap-1">
                    <Spinner size="sm" /> Checking registered wallet...
                  </span>
                ) : registeredSellerWallet ? (
                  <span className="text-xs text-green-400 flex items-center gap-1">
                    <Check className="w-3.5 h-3.5" /> Registered profile wallet
                  </span>
                ) : sellerId ? (
                  <span className="text-xs text-amber-400 flex items-center gap-1">
                    <AlertTriangle className="w-3.5 h-3.5" /> No wallet registered in seller profile
                  </span>
                ) : null}
              </div>
              <Input
                placeholder="0x..."
                value={sellerAddr}
                onChange={(e) => setSellerAddr(e.target.value.trim())}
                error={
                  sellerAddr && !isAddressValid
                    ? 'Invalid Ethereum address format'
                    : isSelfDeal
                    ? 'Seller address cannot match your connected buyer address'
                    : isSellerWalletMismatch
                    ? `Warning: Address differs from seller's registered wallet (${formatAddress(registeredSellerWallet)})`
                    : undefined
                }
                hint={isAddressValid ? 'Valid 20-byte address' : 'Address of the counterparty seller'}
              />
            </div>

            {/* Amount and Deadline grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">
                  Escrow Amount (ETH) *
                </label>
                <Input
                  type="number"
                  step="0.0001"
                  min="0.0001"
                  placeholder="e.g. 0.5"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  leftElement={<Coins className="w-4 h-4 text-cyan-400" />}
                  error={amount && !isAmountValid ? 'Must be greater than 0' : undefined}
                  hint={
                    isAmountValid
                      ? `≈ ${(() => {
                          try {
                            return parseEther(amount).toString();
                          } catch {
                            return '0';
                          }
                        })()} Wei`
                      : 'Amount locked until delivery'
                  }
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">
                  Delivery Deadline *
                </label>
                <Input
                  type="datetime-local"
                  value={deadlineDate}
                  onChange={(e) => setDeadlineDate(e.target.value)}
                  leftElement={<Clock className="w-4 h-4 text-blue-400" />}
                  error={deadlineDate && !isDeadlineValid ? 'Deadline must be in the future' : undefined}
                  hint={
                    isDeadlineValid
                      ? `Unix: ${computedDeadlineTs}`
                      : 'Buyer can cancel if seller fails to deliver by deadline'
                  }
                />
              </div>
            </div>

            {/* Agreement Hash */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <label className="font-medium text-slate-300">Agreement Hash (Authoritative)</label>
                <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  Backend Canonical v1
                </span>
              </div>

              {isLoadingAgreement ? (
                <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 text-xs text-slate-400 flex items-center gap-2">
                  <div className="w-3.5 h-3.5 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
                  <span>Computing canonical agreement hash from accepted terms...</span>
                </div>
              ) : agreementError ? (
                <div className="p-3 rounded-lg bg-rose-950/30 border border-rose-800/50 text-xs text-rose-300">
                  {agreementError}
                </div>
              ) : (
                <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 flex items-center justify-between gap-2">
                  <div className="truncate font-mono text-xs text-emerald-300/90" title={effectiveHash}>
                    {effectiveHash || 'No agreement hash available'}
                  </div>
                  {effectiveHash && (
                    <button
                      type="button"
                      onClick={() => handleCopy(effectiveHash, 'hash')}
                      className="p-1.5 text-slate-400 hover:text-white rounded hover:bg-slate-800 shrink-0 transition-colors"
                      title="Copy canonical agreement hash"
                    >
                      {copiedHash ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                    </button>
                  )}
                </div>
              )}
              <p className="text-[11px] text-slate-500">
                Authoritative Keccak-256 fingerprint deterministically computed by backend from immutable terms.
              </p>
            </div>

            {/* Cryptographic Agreement Consent (EIP-712) */}
            <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-3">
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-cyan-400" />
                  <span className="font-medium text-slate-200">Cryptographic Agreement Consent</span>
                </div>
                <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                  EIP-712 Typed Signatures
                </span>
              </div>

              <p className="text-xs text-slate-400">
                Both parties must cryptographically sign and approve the exact same canonical agreement terms prior to escrow funding.
              </p>

              {/* Consent Badges for Buyer and Seller */}
              <div className="grid grid-cols-2 gap-3 pt-1">
                <div
                  className={`p-3 rounded-lg border text-xs flex items-center justify-between ${
                    consentStatus?.buyer_consented
                      ? 'bg-emerald-950/20 border-emerald-500/30 text-emerald-300'
                      : 'bg-slate-950/40 border-slate-800 text-slate-400'
                  }`}
                >
                  <div>
                    <p className="font-semibold text-slate-200">Buyer Consent</p>
                    <p className="text-[11px] text-slate-400 truncate max-w-[120px]">
                      {consentStatus?.buyer_consent?.wallet_address
                        ? formatAddress(consentStatus.buyer_consent.wallet_address)
                        : 'Pending signature'}
                    </p>
                  </div>
                  {consentStatus?.buyer_consented ? (
                    <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded">
                      <CheckCircle2 className="w-3.5 h-3.5" /> Signed
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-[11px] font-medium text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded">
                      <Clock className="w-3.5 h-3.5" /> Pending
                    </span>
                  )}
                </div>

                <div
                  className={`p-3 rounded-lg border text-xs flex items-center justify-between ${
                    consentStatus?.seller_consented
                      ? 'bg-emerald-950/20 border-emerald-500/30 text-emerald-300'
                      : 'bg-slate-950/40 border-slate-800 text-slate-400'
                  }`}
                >
                  <div>
                    <p className="font-semibold text-slate-200">Seller Consent</p>
                    <p className="text-[11px] text-slate-400 truncate max-w-[120px]">
                      {consentStatus?.seller_consent?.wallet_address
                        ? formatAddress(consentStatus.seller_consent.wallet_address)
                        : 'Pending signature'}
                    </p>
                  </div>
                  {consentStatus?.seller_consented ? (
                    <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded">
                      <CheckCircle2 className="w-3.5 h-3.5" /> Signed
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-[11px] font-medium text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded">
                      <Clock className="w-3.5 h-3.5" /> Pending
                    </span>
                  )}
                </div>
              </div>

              {/* Status alerts */}
              {consentError && (
                <div className="p-3 rounded-lg bg-rose-950/30 border border-rose-800/50 text-xs text-rose-300 flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
                  <span>{consentError}</span>
                </div>
              )}

              {consentSuccess && (
                <div className="p-3 rounded-lg bg-emerald-950/30 border border-emerald-800/50 text-xs text-emerald-300 flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>{consentSuccess}</span>
                </div>
              )}

              {/* User Signing Button if current party hasn't signed */}
              {(!consentStatus?.buyer_consented || !consentStatus?.seller_consented) && (
                <div className="pt-1">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="w-full justify-center"
                    onClick={handleSignConsent}
                    loading={isSigningConsent || isLoadingConsent}
                    leftIcon={<ShieldCheck className="w-4 h-4 text-cyan-400" />}
                  >
                    Review & Sign Agreement (EIP-712)
                  </Button>
                </div>
              )}
            </div>

            {/* Buyer Wallet & Summary Box */}
            <div className="p-4 rounded-xl bg-slate-900/40 border border-slate-800/80 space-y-2 text-xs">
              <div className="flex justify-between text-slate-400">
                <span>Buyer (Your Account):</span>
                <span className="font-mono text-slate-200">
                  {account ? formatAddress(account) : 'Not connected'}
                </span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Target Contract:</span>
                <span className="font-mono text-cyan-400">
                  {escrowV2Config.isConfigured
                    ? `${formatAddress(ESCROW_V2_CONTRACT_ADDRESS)} (V2)`
                    : formatAddress(ESCROW_CONTRACT_ADDRESS)}
                </span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Network:</span>
                <span className="text-slate-200">Anvil Local (31337)</span>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex justify-end gap-3 pt-2">
              <Button variant="secondary" onClick={onClose}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleFund}
                disabled={!canSubmit}
                leftIcon={<ShieldCheck className="w-4 h-4" />}
              >
                {!bothConsented ? 'Consent Required Before Funding' : 'Deposit & Fund Escrow'}
              </Button>
            </div>
          </div>
        )}

        {/* STEP: Awaiting Signature */}
        {step === 'awaiting_signature' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <div className="relative">
              <Spinner size="lg" />
              <Wallet className="w-5 h-5 text-blue-400 absolute inset-0 m-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white">Awaiting MetaMask Confirmation</h3>
              <p className="text-sm text-slate-400 max-w-sm mt-1">
                Please review and approve the <code className="text-cyan-400 font-mono">fundDealWithConsent</code> transaction in your MetaMask wallet popup.
              </p>
            </div>
            <div className="p-3 rounded-lg bg-blue-900/20 border border-blue-500/20 text-xs text-blue-300 max-w-md">
              Amount to lock: <span className="font-bold text-white">{amount} ETH</span> + standard gas fee
            </div>
          </div>
        )}

        {/* STEP: Pending Blockchain Transaction */}
        {step === 'pending_tx' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <div className="relative">
              <Spinner size="lg" />
              <RefreshCw className="w-5 h-5 text-cyan-400 animate-spin absolute inset-0 m-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white">Transaction Submitted</h3>
              <p className="text-sm text-slate-400 max-w-sm mt-1">
                Waiting for blockchain transaction confirmation on Anvil...
              </p>
            </div>
            {pendingTxHash && (
              <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 flex items-center justify-between gap-3 max-w-md w-full">
                <div className="truncate font-mono text-xs text-slate-300">
                  Tx: {pendingTxHash}
                </div>
                <button
                  type="button"
                  onClick={() => handleCopy(pendingTxHash, 'tx')}
                  className="p-1.5 text-slate-400 hover:text-white rounded hover:bg-slate-800 shrink-0"
                  title="Copy transaction hash"
                >
                  {copiedTx ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
            )}
          </div>
        )}

        {/* STEP: Verifying with Backend */}
        {step === 'verifying_backend' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <div className="relative">
              <Spinner size="lg" />
              <Database className="w-5 h-5 text-blue-400 animate-pulse absolute inset-0 m-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white">Verifying with Backend</h3>
              <p className="text-sm text-slate-400 max-w-sm mt-1">
                Transaction confirmed on-chain! Backend service is independently verifying the receipt and logs against the contract...
              </p>
            </div>
            {confirmedResult && (
              <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 text-xs text-slate-300 max-w-md w-full space-y-1 text-left font-mono">
                <div>On-Chain Deal ID: #{confirmedResult.chainDealId}</div>
                <div className="truncate">Tx Hash: {confirmedResult.txHash}</div>
              </div>
            )}
          </div>
        )}

        {/* STEP: Fully Verified */}
        {step === 'verified' && confirmedResult && (
          <div className="py-4 space-y-5">
            <div className="flex flex-col items-center text-center space-y-2">
              <div className="w-12 h-12 rounded-full bg-green-500/20 border border-green-500/40 flex items-center justify-center text-green-400">
                <CheckCircle2 className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-white">Escrow Funded & Verified!</h3>
              <p className="text-sm text-slate-400">
                On-chain deposit confirmed and independently verified by the backend.
              </p>
            </div>

            {/* Result details card */}
            <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2.5 text-sm">
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-slate-400">On-Chain Deal ID:</span>
                <span className="font-mono font-bold text-lg text-cyan-400">
                  #{confirmedResult.chainDealId}
                </span>
              </div>
              {verifiedDeal && (
                <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                  <span className="text-slate-400">Backend Escrow State:</span>
                  <span className="px-2 py-0.5 rounded text-xs font-bold bg-green-500/20 text-green-400 border border-green-500/30">
                    {verifiedDeal.on_chain_state || 'FUNDED'}
                  </span>
                </div>
              )}
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Locked Amount:</span>
                <span className="font-semibold text-white">{confirmedResult.amountEth} ETH</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Seller Recipient:</span>
                <span className="font-mono text-slate-300">{formatAddress(confirmedResult.seller)}</span>
              </div>
              {activeDealId && (
                <div className="pt-2 border-t border-slate-800 space-y-1">
                  <span className="text-xs text-slate-400 block">Backend Deal UUID:</span>
                  <div className="p-2 rounded bg-slate-950/80 border border-slate-800/80 flex items-center justify-between text-xs font-mono text-slate-300">
                    <span className="truncate">{activeDealId}</span>
                    <button
                      type="button"
                      onClick={() => handleCopy(activeDealId, 'dealId')}
                      className="ml-2 text-slate-400 hover:text-white shrink-0"
                    >
                      {copiedDealId ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>
              )}
              <div className="pt-2 border-t border-slate-800 space-y-1">
                <span className="text-xs text-slate-400 block">Transaction Hash:</span>
                <div className="p-2 rounded bg-slate-950/80 border border-slate-800/80 flex items-center justify-between text-xs font-mono text-slate-300">
                  <span className="truncate">{confirmedResult.txHash}</span>
                  <button
                    type="button"
                    onClick={() => handleCopy(confirmedResult.txHash, 'tx')}
                    className="ml-2 text-slate-400 hover:text-white shrink-0"
                  >
                    {copiedTx ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <Button variant="primary" onClick={onClose} leftIcon={<ArrowRight className="w-4 h-4" />}>
                Done
              </Button>
            </div>
          </div>
        )}

        {/* STEP: Scenario A / Failure — Blockchain Succeeded, Backend Verification Failed */}
        {step === 'verification_failed' && confirmedResult && (
          <div className="py-4 space-y-5">
            <div className="flex flex-col items-center text-center space-y-2">
              <div className="w-12 h-12 rounded-full bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400">
                <AlertTriangle className="w-7 h-7" />
              </div>
              <h3 className="text-lg font-bold text-white">Blockchain Confirmed — Verification Incomplete</h3>
              <p className="text-sm text-slate-400 max-w-md">
                Your ETH was successfully deposited on-chain, but the backend verification encountered an issue.
              </p>
            </div>

            {/* Error detail banner */}
            <div className="p-3 rounded-lg bg-amber-950/40 border border-amber-500/20 text-amber-200 text-xs">
              <p className="font-semibold mb-1">Verification Response:</p>
              <p className="font-mono text-amber-300/90">{verificationError || 'Verification call failed or timed out.'}</p>
            </div>

            {/* Confirmed details preserved */}
            <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2 text-xs">
              <div className="flex justify-between text-slate-400">
                <span>On-Chain Deal ID:</span>
                <span className="font-mono text-cyan-400 font-bold">#{confirmedResult.chainDealId}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Transaction Hash:</span>
                <span className="font-mono text-slate-300 truncate max-w-[240px]">{confirmedResult.txHash}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Backend Deal ID:</span>
                <span className="font-mono text-slate-300 truncate max-w-[240px]">{activeDealId || 'Not set'}</span>
              </div>
            </div>

            <p className="text-xs text-slate-400 text-center">
              Do not send another blockchain transaction. You can retry verification with the existing transaction hash.
            </p>

            <div className="flex justify-end gap-3 pt-2">
              <Button variant="secondary" onClick={onClose}>
                Close
              </Button>
              <Button
                variant="primary"
                onClick={handleRetryVerification}
                leftIcon={<RefreshCw className="w-4 h-4" />}
              >
                Retry Backend Verification
              </Button>
            </div>
          </div>
        )}

        {/* STEP: Blockchain Error */}
        {step === 'blockchain_error' && (
          <div className="py-6 space-y-5">
            <div className="flex flex-col items-center text-center space-y-2">
              <div className="w-12 h-12 rounded-full bg-red-500/20 border border-red-500/40 flex items-center justify-center text-red-400">
                <AlertCircle className="w-7 h-7" />
              </div>
              <h3 className="text-lg font-semibold text-white">Transaction Failed</h3>
              <p className="text-sm text-red-300/90 max-w-md bg-red-950/40 border border-red-500/20 rounded-lg p-3">
                {errorMessage || 'An error occurred while attempting to fund the escrow deal.'}
              </p>
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <Button variant="secondary" onClick={onClose}>
                Close
              </Button>
              <Button variant="primary" onClick={() => setStep('form')} leftIcon={<RefreshCw className="w-4 h-4" />}>
                Try Again
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
