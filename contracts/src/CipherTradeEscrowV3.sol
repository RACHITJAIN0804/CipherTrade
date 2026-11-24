// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable2Step, Ownable} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

/**
 * @title  CipherTradeEscrowV3
 * @author CipherTrade
 * @notice Multi-token (Native ETH, USDC, USDT) peer-to-peer escrow contract with bilateral
 *         EIP-712 mutual consent verification, pre-funding agreement amendments, post-funding
 *         deadline extensions, and platform fee distribution.
 *
 * @dev State machine:
 *  Pre-funding:
 *    registerInitialAgreement() [v1] ──► registerAgreementAmendment() [v2..vN]
 *                                                      │
 *                                            fundRegisteredDeal()
 *                                                      │
 *  Direct funding:                                     ▼
 *    fundDealWithConsent() [v1 direct] ──────────►  FUNDED
 *                                                      │
 *              confirmDelivery()                       │  cancelExpired() [after deliveryDeadline]
 *                   (buyer)                           │     (buyer)
 *                      │                              ▼
 *                      │                        CANCELLED
 *                      ▼
 *                 CONFIRMED ────────────────────► DISPUTED
 *                      │                               │
 *        withdrawPayment()                             │ resolveDispute() (arbitrator)
 *            (seller)                                  ▼
 *                      │                        ┌──────┴──────┐
 *                      ▼                      TO_SELLER     TO_BUYER
 *                  RELEASED                   RELEASED      REFUNDED
 *
 *  Post-funding modification:
 *    extendDeliveryDeadline() [State.FUNDED only, bilateral consent]
 */
contract CipherTradeEscrowV3 is Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─── Structs ──────────────────────────────────────────────────────────────

    /**
     * @notice Full commercial agreement terms for canonical hashing and EIP-712 consent.
     */
    struct CommercialAgreementV3 {
        bytes32 agreementHash;         // Authoritative Keccak-256 agreement terms hash
        bytes32 previousAgreementHash; // Hash of previous agreement version (bytes32(0) for v1)
        uint256 agreementVersion;      // Version number (1 for initial, N+1 for amendments)
        string offerId;                // Authoritative marketplace offer UUID string
        string dealId;                 // Authoritative marketplace escrow deal UUID string
        address buyer;                 // Authoritative buyer wallet address
        address seller;                // Authoritative seller wallet address
        address token;                 // address(0) for Native ETH; ERC-20 contract address otherwise
        uint256 amount;                // Exact escrow deposit amount in base units
        uint256 feeBps;                // Snapshotted platform fee in basis points (100 = 1.00%)
        address treasury;              // Snapshotted treasury recipient for this specific deal
        uint256 deliveryDeadline;      // Unix timestamp for seller delivery
    }

    /**
     * @notice Role-specific cryptographic consent proof.
     */
    struct PartyConsentProofV3 {
        string nonce;                  // Single-use challenge nonce (64-char hex string)
        uint256 deadline;              // Consent challenge expiration timestamp
        bytes signature;               // 65-byte ECDSA signature in (r, s, v) layout
    }

    /**
     * @notice Lifecycle states for an escrow deal.
     */
    enum State {
        FUNDED,     // 0: Buyer deposited funds; awaiting delivery
        CONFIRMED,  // 1: Buyer confirmed delivery; seller may withdraw
        RELEASED,   // 2: Funds sent to seller (minus fee) — terminal
        REFUNDED,   // 3: Funds returned to buyer (100%, zero fee) — terminal
        CANCELLED,  // 4: Buyer cancelled after deadline expired (100%, zero fee) — terminal
        DISPUTED,   // 5: A party raised a dispute; awaiting arbitration
        RESOLVED    // 6: Arbitrator resolved the dispute — terminal
    }

    /**
     * @notice Core on-chain record for an escrow deal.
     */
    struct DealV3 {
        bytes32 dealUuidHash;          // keccak256 of off-chain deal UUID string
        address buyer;
        address seller;
        address token;                 // address(0) for Native ETH; ERC-20 contract address otherwise
        uint256 amount;                // Base units locked in this deal
        uint256 feeBps;                // Platform fee in basis points
        address treasury;              // Fee recipient address
        uint256 deadline;              // Delivery deadline (Unix timestamp)
        State state;
        bool fundsWithdrawn;           // Pull-payment protection against double-transfer
        bytes32 agreementHash;         // Authoritative agreement hash at funding
    }

    /**
     * @notice Authorized agreement terms anchored on-chain during registration / amendment.
     */
    struct AuthorizedAgreement {
        uint256 version;
        bytes32 agreementHash;
        bytes32 previousAgreementHash;
        string offerId;
        address buyer;
        address seller;
        address token;
        uint256 amount;
        uint256 feeBps;
        address treasury;
        uint256 deliveryDeadline;
    }

    // ─── Constants & EIP-712 Hashes ───────────────────────────────────────────

    bytes32 public constant AGREEMENT_TERMS_TYPEHASH = keccak256(
        abi.encodePacked(
            "AgreementTerms(string dealId,uint256 agreementVersion,bytes32 previousAgreementHash,string offerId,address buyer,address seller,address token,uint256 amount,uint256 feeBps,address treasury,uint256 deliveryDeadline)"
        )
    );

    bytes32 public constant AGREEMENT_CONSENT_TYPEHASH = keccak256(
        abi.encodePacked(
            "AgreementConsent(bytes32 agreementHash,bytes32 previousAgreementHash,uint256 agreementVersion,string offerId,string dealId,address buyer,address seller,address token,uint256 amount,uint256 feeBps,address treasury,uint256 deliveryDeadline,string partyRole,address signer,string nonce,uint256 deadline)"
        )
    );

    bytes32 public constant DEADLINE_EXTENSION_TYPEHASH = keccak256(
        abi.encodePacked(
            "DeadlineExtension(uint256 chainDealId,bytes32 dealUuidHash,bytes32 agreementHash,address buyer,address seller,uint256 currentDeadline,uint256 newDeadline,string partyRole,address signer,string nonce,uint256 deadline)"
        )
    );

    bytes32 private constant _EIP712_DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );

    bytes32 private constant _HASHED_NAME = keccak256(bytes("CipherTrade"));
    bytes32 private constant _HASHED_VERSION = keccak256(bytes("3"));

    // secp256k1 curve order n / 2 for EIP-2 low-s malleability protection
    uint256 public constant SECP256K1_N_DIV_2 = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    /// @notice Maximum protocol fee: 5.00% (500 basis points)
    uint256 public constant MAX_FEE_BPS = 500;

    // ─── Storage ──────────────────────────────────────────────────────────────

    address public immutable arbitrator;
    address public treasury;
    uint256 public feeBps;

    uint256 private _nextDealId;
    mapping(uint256 => DealV3) private _deals;

    /// @notice Maps keccak256(abi.encode(signer, nonce)) => bool to prevent signature replay
    mapping(bytes32 => bool) public isConsentNonceUsed;

    /// @notice Maps keccak256(bytes(dealUuid)) => bool to prevent duplicate deal UUID funding
    mapping(bytes32 => bool) public isDealUuidFunded;

    /// @notice Maps keccak256(bytes(dealUuid)) => on-chain incremental chainDealId
    mapping(bytes32 => uint256) public chainDealIdByUuidHash;

    /// @notice Maps keccak256(bytes(dealUuid)) => on-chain authorized agreement terms
    mapping(bytes32 => AuthorizedAgreement) public authorizedAgreement;

    /// @notice Allowlist registry for deposit tokens (address(0) = Native ETH)
    mapping(address => bool) public isTokenAllowed;

    // ─── Events ───────────────────────────────────────────────────────────────

    event DealFunded(
        uint256 indexed chainDealId,
        bytes32 indexed dealUuidHash,
        address indexed buyer,
        address seller,
        address token,
        uint256 amount,
        uint256 feeBps,
        address treasury,
        uint256 deliveryDeadline,
        bytes32 agreementHash,
        uint256 agreementVersion
    );

    event InitialAgreementRegistered(
        bytes32 indexed dealUuidHash,
        bytes32 indexed agreementHash,
        address indexed buyer,
        address seller,
        address token,
        uint256 amount,
        uint256 deliveryDeadline
    );

    event AgreementAmended(
        bytes32 indexed dealUuidHash,
        uint256 indexed version,
        bytes32 agreementHash,
        bytes32 previousAgreementHash,
        address token,
        uint256 amount,
        uint256 deliveryDeadline
    );

    event DeliveryDeadlineExtended(
        uint256 indexed chainDealId,
        uint256 oldDeadline,
        uint256 newDeadline
    );

    event DeliveryConfirmed(uint256 indexed chainDealId);
    event PaymentReleased(uint256 indexed chainDealId, address indexed seller, uint256 sellerAmount, uint256 feeAmount);
    event RefundIssued(uint256 indexed chainDealId, address indexed buyer, uint256 amount);
    event DealCancelled(uint256 indexed chainDealId, address indexed buyer, uint256 amount);
    event DisputeRaised(uint256 indexed chainDealId, address indexed raisedBy);
    event DisputeResolved(uint256 indexed chainDealId, address indexed recipient, uint256 recipientAmount, uint256 feeAmount);

    event TokenAllowedSet(address indexed token, bool allowed);
    event FeeBpsUpdated(uint256 oldFeeBps, uint256 newFeeBps);
    event TreasuryUpdated(address indexed oldTreasury, address indexed newTreasury);

    // ─── Custom Errors ────────────────────────────────────────────────────────

    error Unauthorized();
    error UnauthorizedCaller(address caller, address expected);
    error InvalidState(State current, State required);
    error ZeroAmount();
    error AmountMismatch(uint256 expected, uint256 provided);
    error NativeValueNotAllowed();
    error TransferAmountMismatch(uint256 expected, uint256 received);
    error TransferFailed();
    error AlreadyWithdrawn();
    error SelfDeal();
    error ZeroAddress();
    error DeadlineNotReached();
    error DeadlineExpired();
    error InvalidDeadline();
    error TokenNotAllowed(address token);
    error InvalidFeeBps(uint256 provided, uint256 max);
    error DealAlreadyFunded();
    error AgreementAlreadyRegistered();
    error InitialAgreementNotRegistered();
    error InvalidInitialAgreement();
    error InvalidAmendmentVersion(uint256 provided, uint256 expected);
    error PreviousAgreementHashMismatch(bytes32 provided, bytes32 expected);
    error CanonicalAgreementHashMismatch(bytes32 expected, bytes32 provided);
    error BuyerImmutable(address currentBuyer, address newBuyer);
    error SellerImmutable(address currentSeller, address newSeller);
    error OfferImmutable(string currentOfferId, string newOfferId);
    error ConsentExpired(string party, uint256 deadline, uint256 currentTimestamp);
    error NonceReplayed(address signer, string nonce);
    error InvalidSignatureLength();
    error InvalidSignatureS();
    error InvalidSignatureV();
    error InvalidSignature();
    error SignerMismatch(string party, address expected, address recovered);

    // ─── Modifiers ────────────────────────────────────────────────────────────

    modifier onlyBuyer(uint256 chainDealId) {
        if (msg.sender != _deals[chainDealId].buyer) revert Unauthorized();
        _;
    }

    modifier onlySeller(uint256 chainDealId) {
        if (msg.sender != _deals[chainDealId].seller) revert Unauthorized();
        _;
    }

    modifier onlyParty(uint256 chainDealId) {
        DealV3 storage d = _deals[chainDealId];
        if (msg.sender != d.buyer && msg.sender != d.seller) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != arbitrator) revert Unauthorized();
        _;
    }

    modifier inState(uint256 chainDealId, State expected) {
        if (_deals[chainDealId].state != expected) revert InvalidState(_deals[chainDealId].state, expected);
        _;
    }

    // ─── Constructor ──────────────────────────────────────────────────────────

    constructor(
        address initialOwner,
        address _arbitrator,
        address _treasury,
        uint256 _feeBps
    ) Ownable(initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        if (_arbitrator == address(0)) revert ZeroAddress();
        if (_treasury == address(0)) revert ZeroAddress();
        if (_feeBps > MAX_FEE_BPS) revert InvalidFeeBps(_feeBps, MAX_FEE_BPS);

        arbitrator = _arbitrator;
        treasury = _treasury;
        feeBps = _feeBps;
        _nextDealId = 1;

        // Native ETH is allowed by default
        isTokenAllowed[address(0)] = true;
        emit TokenAllowedSet(address(0), true);
    }

    // ─── Admin Configuration Functions ───────────────────────────────────────

    /**
     * @notice Enables or disables an asset token for new deal funding.
     * @dev Delisting does not affect existing funded deals.
     */
    function setTokenAllowed(address token, bool allowed) external onlyOwner {
        isTokenAllowed[token] = allowed;
        emit TokenAllowedSet(token, allowed);
    }

    /**
     * @notice Updates the default protocol fee schedule.
     */
    function setFeeBps(uint256 newFeeBps) external onlyOwner {
        if (newFeeBps > MAX_FEE_BPS) revert InvalidFeeBps(newFeeBps, MAX_FEE_BPS);
        uint256 oldFeeBps = feeBps;
        feeBps = newFeeBps;
        emit FeeBpsUpdated(oldFeeBps, newFeeBps);
    }

    /**
     * @notice Updates the default protocol treasury address.
     */
    function setTreasury(address newTreasury) external onlyOwner {
        if (newTreasury == address(0)) revert ZeroAddress();
        address oldTreasury = treasury;
        treasury = newTreasury;
        emit TreasuryUpdated(oldTreasury, newTreasury);
    }

    // ─── EIP-712 View Helpers ─────────────────────────────────────────────────

    /**
     * @notice Computes the dynamic EIP-712 domain separator for V3.
     */
    function domainSeparator() public view returns (bytes32) {
        return keccak256(
            abi.encode(
                _EIP712_DOMAIN_TYPEHASH,
                _HASHED_NAME,
                _HASHED_VERSION,
                block.chainid,
                address(this)
            )
        );
    }

    /**
     * @notice Authoritative canonical agreement hash computation across all 11 agreement terms.
     */
    function computeCanonicalAgreementHash(
        string memory dealId,
        uint256 agreementVersion,
        bytes32 previousAgreementHash,
        string memory offerId,
        address buyer,
        address seller,
        address token,
        uint256 amount,
        uint256 dealFeeBps,
        address dealTreasury,
        uint256 deliveryDeadline
    ) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                AGREEMENT_TERMS_TYPEHASH,
                keccak256(bytes(dealId)),
                agreementVersion,
                previousAgreementHash,
                keccak256(bytes(offerId)),
                buyer,
                seller,
                token,
                amount,
                dealFeeBps,
                dealTreasury,
                deliveryDeadline
            )
        );
    }

    /**
     * @notice Helper to compute canonical agreement hash directly from CommercialAgreementV3 struct.
     */
    function computeAgreementTermsHash(CommercialAgreementV3 calldata agreement) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                AGREEMENT_TERMS_TYPEHASH,
                keccak256(bytes(agreement.dealId)),
                agreement.agreementVersion,
                agreement.previousAgreementHash,
                keccak256(bytes(agreement.offerId)),
                agreement.buyer,
                agreement.seller,
                agreement.token,
                agreement.amount,
                agreement.feeBps,
                agreement.treasury,
                agreement.deliveryDeadline
            )
        );
    }

    /**
     * @notice Computes the EIP-712 struct hash for an agreement consent proof.
     */
    function hashAgreementConsent(
        CommercialAgreementV3 calldata agreement,
        PartyConsentProofV3 calldata proof,
        string memory partyRole,
        address signer
    ) public pure returns (bytes32) {
        bytes memory part1 = abi.encode(
            AGREEMENT_CONSENT_TYPEHASH,
            agreement.agreementHash,
            agreement.previousAgreementHash,
            agreement.agreementVersion,
            keccak256(bytes(agreement.offerId)),
            keccak256(bytes(agreement.dealId)),
            agreement.buyer,
            agreement.seller,
            agreement.token
        );
        bytes memory part2 = abi.encode(
            agreement.amount,
            agreement.feeBps,
            agreement.treasury,
            agreement.deliveryDeadline,
            keccak256(bytes(partyRole)),
            signer,
            keccak256(bytes(proof.nonce)),
            proof.deadline
        );
        return keccak256(bytes.concat(part1, part2));
    }

    /**
     * @notice Computes the EIP-712 struct hash for a deadline extension consent proof.
     */
    function hashDeadlineExtension(
        uint256 chainDealId,
        bytes32 dealUuidHash,
        bytes32 agreementHash,
        address buyer,
        address seller,
        uint256 currentDeadline,
        uint256 newDeadline,
        string memory partyRole,
        address signer,
        PartyConsentProofV3 calldata proof
    ) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                DEADLINE_EXTENSION_TYPEHASH,
                chainDealId,
                dealUuidHash,
                agreementHash,
                buyer,
                seller,
                currentDeadline,
                newDeadline,
                keccak256(bytes(partyRole)),
                signer,
                keccak256(bytes(proof.nonce)),
                proof.deadline
            )
        );
    }

    /**
     * @notice Computes the final EIP-712 digest given a struct hash.
     */
    function computeDigest(bytes32 structHash) public view returns (bytes32) {
        return keccak256(
            abi.encodePacked("\x19\x01", domainSeparator(), structHash)
        );
    }

    // ─── Pre-Funding Agreement Registration & Amendments ──────────────────────

    /**
     * @notice Pre-registers an initial commercial agreement (Version 1) on-chain with bilateral consent.
     */
    function registerInitialAgreement(
        CommercialAgreementV3 calldata agreement,
        PartyConsentProofV3 calldata buyerProof,
        PartyConsentProofV3 calldata sellerProof
    ) external nonReentrant {
        bytes32 dealUuidHash = keccak256(bytes(agreement.dealId));
        if (isDealUuidFunded[dealUuidHash]) revert DealAlreadyFunded();
        if (authorizedAgreement[dealUuidHash].version != 0) revert AgreementAlreadyRegistered();
        if (agreement.agreementVersion != 1 || agreement.previousAgreementHash != bytes32(0)) {
            revert InvalidInitialAgreement();
        }
        if (agreement.amount == 0) revert ZeroAmount();
        if (agreement.buyer == address(0) || agreement.seller == address(0)) revert ZeroAddress();
        if (agreement.buyer == agreement.seller) revert SelfDeal();
        if (agreement.deliveryDeadline <= block.timestamp) revert InvalidDeadline();
        if (agreement.feeBps > MAX_FEE_BPS) revert InvalidFeeBps(agreement.feeBps, MAX_FEE_BPS);
        if (agreement.treasury == address(0)) revert ZeroAddress();

        bytes32 expectedHash = computeAgreementTermsHash(agreement);
        if (agreement.agreementHash != expectedHash) {
            revert CanonicalAgreementHashMismatch(expectedHash, agreement.agreementHash);
        }

        _verifyAndConsumeConsent(agreement, buyerProof, "buyer", agreement.buyer);
        _verifyAndConsumeConsent(agreement, sellerProof, "seller", agreement.seller);

        authorizedAgreement[dealUuidHash] = AuthorizedAgreement({
            version: 1,
            agreementHash: agreement.agreementHash,
            previousAgreementHash: bytes32(0),
            offerId: agreement.offerId,
            buyer: agreement.buyer,
            seller: agreement.seller,
            token: agreement.token,
            amount: agreement.amount,
            feeBps: agreement.feeBps,
            treasury: agreement.treasury,
            deliveryDeadline: agreement.deliveryDeadline
        });

        emit InitialAgreementRegistered(
            dealUuidHash,
            agreement.agreementHash,
            agreement.buyer,
            agreement.seller,
            agreement.token,
            agreement.amount,
            agreement.deliveryDeadline
        );
    }

    /**
     * @notice Registers a sequential agreement amendment on-chain with bilateral consent prior to funding.
     */
    function registerAgreementAmendment(
        CommercialAgreementV3 calldata newAgreement,
        PartyConsentProofV3 calldata buyerProof,
        PartyConsentProofV3 calldata sellerProof
    ) external nonReentrant {
        bytes32 dealUuidHash = keccak256(bytes(newAgreement.dealId));
        if (isDealUuidFunded[dealUuidHash]) revert DealAlreadyFunded();

        AuthorizedAgreement storage current = authorizedAgreement[dealUuidHash];
        if (current.version == 0) revert InitialAgreementNotRegistered();

        // Enforce party and offer immutability
        if (newAgreement.buyer != current.buyer) {
            revert BuyerImmutable(current.buyer, newAgreement.buyer);
        }
        if (newAgreement.seller != current.seller) {
            revert SellerImmutable(current.seller, newAgreement.seller);
        }
        if (keccak256(bytes(newAgreement.offerId)) != keccak256(bytes(current.offerId))) {
            revert OfferImmutable(current.offerId, newAgreement.offerId);
        }

        // Sequential chaining invariants
        if (newAgreement.agreementVersion != current.version + 1) {
            revert InvalidAmendmentVersion(newAgreement.agreementVersion, current.version + 1);
        }
        if (newAgreement.previousAgreementHash != current.agreementHash) {
            revert PreviousAgreementHashMismatch(newAgreement.previousAgreementHash, current.agreementHash);
        }

        if (newAgreement.amount == 0) revert ZeroAmount();
        if (newAgreement.deliveryDeadline <= block.timestamp) revert InvalidDeadline();
        if (newAgreement.feeBps > MAX_FEE_BPS) revert InvalidFeeBps(newAgreement.feeBps, MAX_FEE_BPS);
        if (newAgreement.treasury == address(0)) revert ZeroAddress();

        bytes32 expectedHash = computeAgreementTermsHash(newAgreement);
        if (newAgreement.agreementHash != expectedHash) {
            revert CanonicalAgreementHashMismatch(expectedHash, newAgreement.agreementHash);
        }

        _verifyAndConsumeConsent(newAgreement, buyerProof, "buyer", newAgreement.buyer);
        _verifyAndConsumeConsent(newAgreement, sellerProof, "seller", newAgreement.seller);

        current.version = newAgreement.agreementVersion;
        current.previousAgreementHash = current.agreementHash;
        current.agreementHash = newAgreement.agreementHash;
        current.token = newAgreement.token;
        current.amount = newAgreement.amount;
        current.feeBps = newAgreement.feeBps;
        current.treasury = newAgreement.treasury;
        current.deliveryDeadline = newAgreement.deliveryDeadline;

        emit AgreementAmended(
            dealUuidHash,
            current.version,
            current.agreementHash,
            current.previousAgreementHash,
            current.token,
            current.amount,
            current.deliveryDeadline
        );
    }

    // ─── Funding Functions ────────────────────────────────────────────────────

    /**
     * @notice Path A: Direct atomic funding for un-amended Version 1 agreements.
     * @dev Validates bilateral signatures, consumes nonces, anchors agreement, and pulls asset.
     */
    function fundDealWithConsent(
        CommercialAgreementV3 calldata agreement,
        PartyConsentProofV3 calldata buyerConsent,
        PartyConsentProofV3 calldata sellerConsent
    ) external payable nonReentrant returns (uint256 chainDealId) {
        bytes32 dealUuidHash = keccak256(bytes(agreement.dealId));
        if (isDealUuidFunded[dealUuidHash]) revert DealAlreadyFunded();
        if (authorizedAgreement[dealUuidHash].version != 0) revert AgreementAlreadyRegistered();
        if (agreement.agreementVersion != 1 || agreement.previousAgreementHash != bytes32(0)) {
            revert InvalidInitialAgreement();
        }
        if (msg.sender != agreement.buyer) revert UnauthorizedCaller(msg.sender, agreement.buyer);
        if (agreement.amount == 0) revert ZeroAmount();
        if (agreement.buyer == address(0) || agreement.seller == address(0)) revert ZeroAddress();
        if (agreement.buyer == agreement.seller) revert SelfDeal();
        if (agreement.deliveryDeadline <= block.timestamp) revert InvalidDeadline();
        if (agreement.feeBps > MAX_FEE_BPS) revert InvalidFeeBps(agreement.feeBps, MAX_FEE_BPS);
        if (agreement.treasury == address(0)) revert ZeroAddress();
        if (!isTokenAllowed[agreement.token]) revert TokenNotAllowed(agreement.token);

        bytes32 expectedHash = computeAgreementTermsHash(agreement);
        if (agreement.agreementHash != expectedHash) {
            revert CanonicalAgreementHashMismatch(expectedHash, agreement.agreementHash);
        }

        _verifyAndConsumeConsent(agreement, buyerConsent, "buyer", agreement.buyer);
        _verifyAndConsumeConsent(agreement, sellerConsent, "seller", agreement.seller);

        _pullAsset(agreement.token, agreement.buyer, agreement.amount);

        authorizedAgreement[dealUuidHash] = AuthorizedAgreement({
            version: 1,
            agreementHash: agreement.agreementHash,
            previousAgreementHash: bytes32(0),
            offerId: agreement.offerId,
            buyer: agreement.buyer,
            seller: agreement.seller,
            token: agreement.token,
            amount: agreement.amount,
            feeBps: agreement.feeBps,
            treasury: agreement.treasury,
            deliveryDeadline: agreement.deliveryDeadline
        });

        chainDealId = _nextDealId++;
        isDealUuidFunded[dealUuidHash] = true;
        chainDealIdByUuidHash[dealUuidHash] = chainDealId;

        _deals[chainDealId] = DealV3({
            dealUuidHash: dealUuidHash,
            buyer: agreement.buyer,
            seller: agreement.seller,
            token: agreement.token,
            amount: agreement.amount,
            feeBps: agreement.feeBps,
            treasury: agreement.treasury,
            deadline: agreement.deliveryDeadline,
            state: State.FUNDED,
            fundsWithdrawn: false,
            agreementHash: agreement.agreementHash
        });

        emit DealFunded(
            chainDealId,
            dealUuidHash,
            agreement.buyer,
            agreement.seller,
            agreement.token,
            agreement.amount,
            agreement.feeBps,
            agreement.treasury,
            agreement.deliveryDeadline,
            agreement.agreementHash,
            1
        );
    }

    /**
     * @notice Path B: Funding for pre-registered / amended agreements.
     * @dev Validates registered terms and pulls funds without signature or nonce reuse.
     */
    function fundRegisteredDeal(bytes32 dealUuidHash) external payable nonReentrant returns (uint256 chainDealId) {
        if (isDealUuidFunded[dealUuidHash]) revert DealAlreadyFunded();

        AuthorizedAgreement storage current = authorizedAgreement[dealUuidHash];
        if (current.version == 0) revert InitialAgreementNotRegistered();
        if (msg.sender != current.buyer) revert UnauthorizedCaller(msg.sender, current.buyer);
        if (current.deliveryDeadline <= block.timestamp) revert DeadlineExpired();
        if (!isTokenAllowed[current.token]) revert TokenNotAllowed(current.token);

        _pullAsset(current.token, current.buyer, current.amount);

        chainDealId = _nextDealId++;
        isDealUuidFunded[dealUuidHash] = true;
        chainDealIdByUuidHash[dealUuidHash] = chainDealId;

        _deals[chainDealId] = DealV3({
            dealUuidHash: dealUuidHash,
            buyer: current.buyer,
            seller: current.seller,
            token: current.token,
            amount: current.amount,
            feeBps: current.feeBps,
            treasury: current.treasury,
            deadline: current.deliveryDeadline,
            state: State.FUNDED,
            fundsWithdrawn: false,
            agreementHash: current.agreementHash
        });

        emit DealFunded(
            chainDealId,
            dealUuidHash,
            current.buyer,
            current.seller,
            current.token,
            current.amount,
            current.feeBps,
            current.treasury,
            current.deliveryDeadline,
            current.agreementHash,
            current.version
        );
    }

    // ─── Post-Funding Deadline Extension ──────────────────────────────────────

    /**
     * @notice Mutually extends the delivery deadline of an active funded deal.
     * @dev Does not alter canonical agreementHash; financial terms remain locked.
     */
    function extendDeliveryDeadline(
        uint256 chainDealId,
        uint256 newDeadline,
        PartyConsentProofV3 calldata buyerProof,
        PartyConsentProofV3 calldata sellerProof
    ) external nonReentrant {
        DealV3 storage deal = _deals[chainDealId];
        if (deal.state != State.FUNDED) revert InvalidState(deal.state, State.FUNDED);
        if (newDeadline <= deal.deadline) revert InvalidDeadline();

        _verifyAndConsumeDeadlineExtension(deal, chainDealId, newDeadline, buyerProof, "buyer", deal.buyer);
        _verifyAndConsumeDeadlineExtension(deal, chainDealId, newDeadline, sellerProof, "seller", deal.seller);

        uint256 oldDeadline = deal.deadline;
        deal.deadline = newDeadline;

        emit DeliveryDeadlineExtended(chainDealId, oldDeadline, newDeadline);
    }

    // ─── Lifecycle Settlement Functions ───────────────────────────────────────

    /**
     * @notice Buyer confirms delivery. Unlocks seller payment withdrawal.
     */
    function confirmDelivery(uint256 chainDealId)
        external
        nonReentrant
        onlyBuyer(chainDealId)
        inState(chainDealId, State.FUNDED)
    {
        _deals[chainDealId].state = State.CONFIRMED;
        emit DeliveryConfirmed(chainDealId);
    }

    /**
     * @notice Seller withdraws payment after buyer confirmation.
     * @dev Protocol fee is transferred to the snapshotted treasury.
     */
    function withdrawPayment(uint256 chainDealId)
        external
        nonReentrant
        onlySeller(chainDealId)
        inState(chainDealId, State.CONFIRMED)
    {
        DealV3 storage deal = _deals[chainDealId];
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.RELEASED;

        uint256 fee = Math.mulDiv(deal.amount, deal.feeBps, 10000);
        uint256 sellerAmount = deal.amount - fee;

        emit PaymentReleased(chainDealId, deal.seller, sellerAmount, fee);

        _transferAsset(deal.token, deal.seller, sellerAmount);
        if (fee > 0) {
            _transferAsset(deal.token, deal.treasury, fee);
        }
    }

    /**
     * @notice Seller voluntarily refunds 100% of escrowed funds back to the buyer (zero fee).
     */
    function refundBuyer(uint256 chainDealId)
        external
        nonReentrant
        onlySeller(chainDealId)
        inState(chainDealId, State.FUNDED)
    {
        DealV3 storage deal = _deals[chainDealId];
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.REFUNDED;

        uint256 refundAmount = deal.amount;
        emit RefundIssued(chainDealId, deal.buyer, refundAmount);

        _transferAsset(deal.token, deal.buyer, refundAmount);
    }

    /**
     * @notice Buyer cancels deal after delivery deadline has elapsed (100% refund, zero fee).
     */
    function cancelExpired(uint256 chainDealId)
        external
        nonReentrant
        onlyBuyer(chainDealId)
        inState(chainDealId, State.FUNDED)
    {
        DealV3 storage deal = _deals[chainDealId];
        if (block.timestamp <= deal.deadline) revert DeadlineNotReached();
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.CANCELLED;

        uint256 refundAmount = deal.amount;
        emit DealCancelled(chainDealId, deal.buyer, refundAmount);

        _transferAsset(deal.token, deal.buyer, refundAmount);
    }

    /**
     * @notice Either buyer or seller raises a dispute on a FUNDED or CONFIRMED deal.
     */
    function raiseDispute(uint256 chainDealId)
        external
        nonReentrant
        onlyParty(chainDealId)
    {
        DealV3 storage deal = _deals[chainDealId];
        if (deal.state != State.FUNDED && deal.state != State.CONFIRMED) {
            revert InvalidState(deal.state, State.FUNDED);
        }

        deal.state = State.DISPUTED;
        emit DisputeRaised(chainDealId, msg.sender);
    }

    /**
     * @notice Arbitrator resolves a dispute.
     * @param toSeller If true, seller receives net amount and treasury receives fee.
     *                 If false, buyer receives 100% refund (zero fee).
     */
    function resolveDispute(uint256 chainDealId, bool toSeller)
        external
        nonReentrant
        onlyArbitrator
        inState(chainDealId, State.DISPUTED)
    {
        DealV3 storage deal = _deals[chainDealId];
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.RESOLVED;

        if (toSeller) {
            uint256 fee = Math.mulDiv(deal.amount, deal.feeBps, 10000);
            uint256 sellerAmount = deal.amount - fee;

            emit DisputeResolved(chainDealId, deal.seller, sellerAmount, fee);

            _transferAsset(deal.token, deal.seller, sellerAmount);
            if (fee > 0) {
                _transferAsset(deal.token, deal.treasury, fee);
            }
        } else {
            uint256 refundAmount = deal.amount;
            emit DisputeResolved(chainDealId, deal.buyer, refundAmount, 0);

            _transferAsset(deal.token, deal.buyer, refundAmount);
        }
    }

    // ─── Getters ──────────────────────────────────────────────────────────────

    function getDeal(uint256 chainDealId) external view returns (DealV3 memory) {
        return _deals[chainDealId];
    }

    function nextDealId() external view returns (uint256) {
        return _nextDealId;
    }

    function getAuthorizedAgreement(bytes32 dealUuidHash) external view returns (AuthorizedAgreement memory) {
        return authorizedAgreement[dealUuidHash];
    }

    // ─── Public Signature Recovery Helper ────────────────────────────────────

    /**
     * @notice Recovers signer from 32-byte digest and 65-byte signature with EIP-2 low-s guard.
     */
    function recoverSigner(
        bytes32 digest,
        bytes calldata signature
    ) external pure returns (address) {
        return _recoverSigner(digest, signature);
    }

    // ─── Internal Consent Verification Helpers ────────────────────────────────

    function _verifyAndConsumeConsent(
        CommercialAgreementV3 calldata agreement,
        PartyConsentProofV3 calldata proof,
        string memory partyRole,
        address signer
    ) internal {
        if (proof.deadline < block.timestamp) {
            revert ConsentExpired(partyRole, proof.deadline, block.timestamp);
        }

        bytes32 nonceKey = keccak256(abi.encode(signer, proof.nonce));
        if (isConsentNonceUsed[nonceKey]) {
            revert NonceReplayed(signer, proof.nonce);
        }

        bytes32 structHash = hashAgreementConsent(agreement, proof, partyRole, signer);
        bytes32 digest = computeDigest(structHash);
        address recovered = _recoverSigner(digest, proof.signature);
        if (recovered != signer) {
            revert SignerMismatch(partyRole, signer, recovered);
        }

        isConsentNonceUsed[nonceKey] = true;
    }

    function _verifyAndConsumeDeadlineExtension(
        DealV3 storage deal,
        uint256 chainDealId,
        uint256 newDeadline,
        PartyConsentProofV3 calldata proof,
        string memory partyRole,
        address signer
    ) internal {
        if (proof.deadline < block.timestamp) {
            revert ConsentExpired(partyRole, proof.deadline, block.timestamp);
        }

        bytes32 nonceKey = keccak256(abi.encode(signer, proof.nonce));
        if (isConsentNonceUsed[nonceKey]) {
            revert NonceReplayed(signer, proof.nonce);
        }

        bytes32 structHash = hashDeadlineExtension(
            chainDealId,
            deal.dealUuidHash,
            deal.agreementHash,
            deal.buyer,
            deal.seller,
            deal.deadline,
            newDeadline,
            partyRole,
            signer,
            proof
        );
        bytes32 digest = computeDigest(structHash);
        address recovered = _recoverSigner(digest, proof.signature);
        if (recovered != signer) {
            revert SignerMismatch(partyRole, signer, recovered);
        }

        isConsentNonceUsed[nonceKey] = true;
    }

    /**
     * @dev Low-level signature recovery enforcing 65-byte length, low-s (EIP-2), and valid v.
     */
    function _recoverSigner(
        bytes32 digest,
        bytes calldata signature
    ) internal pure returns (address) {
        if (signature.length != 65) revert InvalidSignatureLength();

        bytes32 r;
        bytes32 s;
        uint8 v;

        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 0x20))
            v := byte(0, calldataload(add(signature.offset, 0x40)))
        }

        if (uint256(s) > SECP256K1_N_DIV_2) revert InvalidSignatureS();
        if (v != 27 && v != 28) revert InvalidSignatureV();

        address signer = ecrecover(digest, v, r, s);
        if (signer == address(0)) revert InvalidSignature();

        return signer;
    }

    // ─── Internal Asset Transfer Helpers ──────────────────────────────────────

    function _pullAsset(address token, address payer, uint256 amount) internal {
        if (token == address(0)) {
            if (msg.value != amount) revert AmountMismatch(amount, msg.value);
        } else {
            if (msg.value != 0) revert NativeValueNotAllowed();
            uint256 balanceBefore = IERC20(token).balanceOf(address(this));
            IERC20(token).safeTransferFrom(payer, address(this), amount);
            uint256 balanceAfter = IERC20(token).balanceOf(address(this));
            if (balanceAfter - balanceBefore != amount) {
                revert TransferAmountMismatch(amount, balanceAfter - balanceBefore);
            }
        }
    }

    function _transferAsset(address token, address recipient, uint256 amount) internal {
        if (amount == 0) return;
        if (token == address(0)) {
            (bool ok, bytes memory data) = payable(recipient).call{value: amount}("");
            if (!ok) {
                if (data.length > 0) {
                    assembly {
                        revert(add(data, 0x20), mload(data))
                    }
                }
                revert TransferFailed();
            }
        } else {
            IERC20(token).safeTransfer(recipient, amount);
        }
    }

    // ─── Fallback & Plain ETH Rejection ───────────────────────────────────────

    receive() external payable {
        revert("Direct ETH deposits not accepted");
    }

    fallback() external payable {
        revert("Function does not exist");
    }
}
