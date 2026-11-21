// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title  CipherTradeEscrowV2
 * @author CipherTrade
 * @notice Peer-to-peer escrow contract for CipherTrade with independent on-chain
 *         EIP-712 mutual consent verification.
 *
 * @dev State machine:
 *  (none) ── fundDealWithConsent() ──► FUNDED
 *                                        │
 *              confirmDelivery()          │  cancelExpired() [after deliveryDeadline]
 *                   (buyer)              │     (buyer)
 *                      │                ▼
 *                      │          CANCELLED
 *                      ▼
 *                 CONFIRMED ──────► DISPUTED
 *                      │                 │
 *        withdrawPayment()               │ resolveDispute() (arbitrator)
 *            (seller)                    ▼
 *                      │          ┌──────┴──────┐
 *                      ▼        TO_SELLER     TO_BUYER
 *                  RELEASED     RELEASED      REFUNDED
 */
contract CipherTradeEscrowV2 {

    // ─── Structs ──────────────────────────────────────────────────────────────

    /**
     * @notice Authoritative commercial agreement parameters shared and cryptographically
     *         bound across both buyer and seller EIP-712 signatures.
     */
    struct CommercialAgreement {
        bytes32 agreementHash;    // Keccak-256 canonical agreement terms hash
        string offerId;           // Authoritative marketplace offer UUID string
        string dealId;            // Authoritative marketplace escrow deal UUID string
        address payable buyer;    // Authoritative buyer wallet address
        address payable seller;   // Authoritative seller wallet address
        uint256 amount;           // Exact escrow deposit amount in Wei
        uint256 deliveryDeadline; // Unix timestamp for seller delivery
    }

    /**
     * @notice Role-specific cryptographic consent proof.
     */
    struct PartyConsentProof {
        string nonce;             // Single-use challenge nonce (64-char hex string)
        uint256 deadline;         // Consent challenge expiration timestamp
        bytes signature;          // 65-byte ECDSA signature in (r, s, v) layout
    }

    /**
     * @notice Lifecycle states for an escrow deal.
     */
    enum State {
        FUNDED,     // Buyer has deposited funds; awaiting delivery
        CONFIRMED,  // Buyer has confirmed delivery; seller may withdraw
        RELEASED,   // Funds sent to seller — terminal
        REFUNDED,   // Funds returned to buyer — terminal
        CANCELLED,  // Buyer cancelled after deadline expired — terminal
        DISPUTED,   // A party raised a dispute; awaiting arbitration
        RESOLVED    // Arbitrator resolved the dispute — terminal
    }

    /**
     * @notice Core on-chain record for an escrow deal.
     */
    struct Deal {
        address payable buyer;
        address payable seller;
        uint256 amount;           // Wei locked in this deal
        uint256 deadline;         // Unix timestamp: buyer may cancel if seller misses
        bytes32 agreementHash;    // keccak256 of off-chain deal terms
        bytes32 dealUuidHash;     // keccak256 of off-chain deal UUID string
        State state;
        bool fundsWithdrawn;      // Prevents double-transfer
    }

    // ─── Constants & EIP-712 Hashes ───────────────────────────────────────────

    bytes32 public constant AGREEMENT_CONSENT_TYPEHASH = keccak256(
        "AgreementConsent(bytes32 agreementHash,string offerId,string dealId,address buyer,address seller,uint256 amount,uint256 deliveryDeadline,string partyRole,address signer,string nonce,uint256 deadline)"
    );

    bytes32 private constant _EIP712_DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );

    bytes32 private constant _HASHED_NAME = keccak256(bytes("CipherTrade"));
    bytes32 private constant _HASHED_VERSION = keccak256(bytes("1"));

    // secp256k1 curve order n / 2 for EIP-2 low-s malleability protection
    uint256 public constant SECP256K1_N_DIV_2 = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    // ─── Storage ──────────────────────────────────────────────────────────────

    address public immutable arbitrator;

    uint256 private _nextDealId;
    mapping(uint256 => Deal) private _deals;

    /// @notice Maps keccak256(abi.encodePacked(signer, nonce)) => bool to prevent signature replay
    mapping(bytes32 => bool) public isConsentNonceUsed;

    /// @notice Maps keccak256(bytes(dealUuid)) => bool to prevent duplicate deal UUID funding
    mapping(bytes32 => bool) public isDealUuidFunded;

    /// @notice Maps keccak256(bytes(dealUuid)) => on-chain incremental dealId
    mapping(bytes32 => uint256) public chainDealIdByUuidHash;

    bool private _locked;

    // ─── Events ───────────────────────────────────────────────────────────────

    /// @notice Emitted when a deal is funded with verified mutual EIP-712 consent.
    event DealFunded(
        uint256 indexed chainDealId,
        bytes32 indexed dealUuidHash,
        address indexed buyer,
        address seller,
        uint256 amount,
        uint256 deliveryDeadline,
        bytes32 agreementHash,
        string dealUuid,
        string offerId
    );

    /// @notice Emitted when the buyer confirms delivery.
    event DeliveryConfirmed(uint256 indexed dealId);

    /// @notice Emitted when the seller withdraws payment after confirmation.
    event PaymentReleased(uint256 indexed dealId, address indexed seller, uint256 amount);

    /// @notice Emitted when the seller voluntarily refunds the buyer.
    event RefundIssued(uint256 indexed dealId, address indexed buyer, uint256 amount);

    /// @notice Emitted when the buyer cancels after delivery deadline has elapsed.
    event DealCancelled(uint256 indexed dealId, address indexed buyer, uint256 amount);

    /// @notice Emitted when either party raises a dispute.
    event DisputeRaised(uint256 indexed dealId, address indexed raisedBy);

    /// @notice Emitted when the arbitrator resolves a dispute.
    event DisputeResolved(uint256 indexed dealId, address indexed recipient, uint256 amount);

    // ─── Custom Errors ────────────────────────────────────────────────────────

    error Unauthorized();
    error UnauthorizedCaller(address caller, address expectedBuyer);
    error InvalidState(State current, State required);
    error ZeroAmount();
    error AmountMismatch(uint256 expected, uint256 provided);
    error TransferFailed();
    error AlreadyWithdrawn();
    error SelfDeal();
    error ZeroAddress();
    error DeadlineNotReached();
    error DeadlineExpired();
    error InvalidDeadline();
    error Reentrancy();
    error EmptyField(string fieldName);
    error DealAlreadyFunded(string dealId);
    error ConsentExpired(string party, uint256 deadline, uint256 currentTimestamp);
    error NonceReplayed(address signer, string nonce);
    error InvalidSignatureLength();
    error InvalidSignatureS();
    error InvalidSignatureV();
    error InvalidSignature();
    error SignerMismatch(string party, address expected, address recovered);

    // ─── Modifiers ────────────────────────────────────────────────────────────

    modifier nonReentrant() {
        if (_locked) revert Reentrancy();
        _locked = true;
        _;
        _locked = false;
    }

    modifier onlyBuyer(uint256 dealId) {
        if (msg.sender != _deals[dealId].buyer) revert Unauthorized();
        _;
    }

    modifier onlySeller(uint256 dealId) {
        if (msg.sender != _deals[dealId].seller) revert Unauthorized();
        _;
    }

    modifier onlyParty(uint256 dealId) {
        Deal storage d = _deals[dealId];
        if (msg.sender != d.buyer && msg.sender != d.seller) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != arbitrator) revert Unauthorized();
        _;
    }

    modifier inState(uint256 dealId, State expected) {
        if (_deals[dealId].state != expected) revert InvalidState(_deals[dealId].state, expected);
        _;
    }

    // ─── Constructor ──────────────────────────────────────────────────────────

    constructor(address _arbitrator) {
        if (_arbitrator == address(0)) revert ZeroAddress();
        arbitrator = _arbitrator;
    }

    // ─── EIP-712 View Helpers ─────────────────────────────────────────────────

    /**
     * @notice Computes the dynamic EIP-712 domain separator for this contract.
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
     * @notice Computes the EIP-712 struct hash for buyer agreement consent.
     */
    function hashBuyerConsent(
        CommercialAgreement calldata agreement,
        PartyConsentProof calldata proof
    ) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                AGREEMENT_CONSENT_TYPEHASH,
                agreement.agreementHash,
                keccak256(bytes(agreement.offerId)),
                keccak256(bytes(agreement.dealId)),
                agreement.buyer,
                agreement.seller,
                agreement.amount,
                agreement.deliveryDeadline,
                keccak256(bytes("buyer")),
                agreement.buyer,
                keccak256(bytes(proof.nonce)),
                proof.deadline
            )
        );
    }

    /**
     * @notice Computes the EIP-712 struct hash for seller agreement consent.
     */
    function hashSellerConsent(
        CommercialAgreement calldata agreement,
        PartyConsentProof calldata proof
    ) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                AGREEMENT_CONSENT_TYPEHASH,
                agreement.agreementHash,
                keccak256(bytes(agreement.offerId)),
                keccak256(bytes(agreement.dealId)),
                agreement.buyer,
                agreement.seller,
                agreement.amount,
                agreement.deliveryDeadline,
                keccak256(bytes("seller")),
                agreement.seller,
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

    // ─── External: Funding with Consent ───────────────────────────────────────

    /**
     * @notice Create and fund an escrow deal, strictly verifying that both buyer and seller
     *         cryptographically consented to the identical commercial terms via EIP-712.
     *
     * @param  agreement     Shared commercial parameters (buyer, seller, amount, deadlines, hashes).
     * @param  buyerConsent  Buyer's unique challenge nonce, consent deadline, and signature.
     * @param  sellerConsent Seller's unique challenge nonce, consent deadline, and signature.
     * @return chainDealId   Newly allocated on-chain deal ID.
     */
    function fundDealWithConsent(
        CommercialAgreement calldata agreement,
        PartyConsentProof calldata buyerConsent,
        PartyConsentProof calldata sellerConsent
    )
        external
        payable
        nonReentrant
        returns (uint256 chainDealId)
    {
        _validateCommercialAgreement(agreement);

        bytes32 dealUuidHash = keccak256(bytes(agreement.dealId));
        if (isDealUuidFunded[dealUuidHash]) revert DealAlreadyFunded(agreement.dealId);

        bytes32 buyerNonceKey = _verifyBuyerConsent(agreement, buyerConsent);
        bytes32 sellerNonceKey = _verifySellerConsent(agreement, sellerConsent);

        chainDealId = _createDealRecord(agreement, dealUuidHash, buyerNonceKey, sellerNonceKey);
    }

    function _validateCommercialAgreement(CommercialAgreement calldata agreement) private view {
        if (msg.value == 0 || agreement.amount == 0) revert ZeroAmount();
        if (msg.value != agreement.amount) revert AmountMismatch(agreement.amount, msg.value);
        if (agreement.buyer == address(0) || agreement.seller == address(0)) revert ZeroAddress();
        if (agreement.buyer == agreement.seller) revert SelfDeal();
        if (msg.sender != agreement.buyer) revert UnauthorizedCaller(msg.sender, agreement.buyer);
        if (agreement.deliveryDeadline <= block.timestamp) revert InvalidDeadline();

        if (agreement.agreementHash == bytes32(0)) revert EmptyField("agreementHash");
        if (bytes(agreement.offerId).length == 0) revert EmptyField("offerId");
        if (bytes(agreement.dealId).length == 0) revert EmptyField("dealId");
    }

    function _createDealRecord(
        CommercialAgreement calldata agreement,
        bytes32 dealUuidHash,
        bytes32 buyerNonceKey,
        bytes32 sellerNonceKey
    ) private returns (uint256 chainDealId) {
        isConsentNonceUsed[buyerNonceKey] = true;
        isConsentNonceUsed[sellerNonceKey] = true;
        isDealUuidFunded[dealUuidHash] = true;

        chainDealId = _nextDealId++;
        chainDealIdByUuidHash[dealUuidHash] = chainDealId;

        _deals[chainDealId] = Deal({
            buyer: agreement.buyer,
            seller: agreement.seller,
            amount: msg.value,
            deadline: agreement.deliveryDeadline,
            agreementHash: agreement.agreementHash,
            dealUuidHash: dealUuidHash,
            state: State.FUNDED,
            fundsWithdrawn: false
        });

        emit DealFunded(
            chainDealId,
            dealUuidHash,
            agreement.buyer,
            agreement.seller,
            msg.value,
            agreement.deliveryDeadline,
            agreement.agreementHash,
            agreement.dealId,
            agreement.offerId
        );
    }

    function _verifyBuyerConsent(
        CommercialAgreement calldata agreement,
        PartyConsentProof calldata proof
    ) private view returns (bytes32 nonceKey) {
        if (bytes(proof.nonce).length == 0) revert EmptyField("buyerNonce");
        if (block.timestamp > proof.deadline) {
            revert ConsentExpired("buyer", proof.deadline, block.timestamp);
        }

        nonceKey = keccak256(abi.encodePacked(agreement.buyer, proof.nonce));
        if (isConsentNonceUsed[nonceKey]) {
            revert NonceReplayed(agreement.buyer, proof.nonce);
        }

        bytes32 digest = computeDigest(hashBuyerConsent(agreement, proof));
        address recovered = _recoverSigner(digest, proof.signature);
        if (recovered != agreement.buyer) {
            revert SignerMismatch("buyer", agreement.buyer, recovered);
        }
    }

    function _verifySellerConsent(
        CommercialAgreement calldata agreement,
        PartyConsentProof calldata proof
    ) private view returns (bytes32 nonceKey) {
        if (bytes(proof.nonce).length == 0) revert EmptyField("sellerNonce");
        if (block.timestamp > proof.deadline) {
            revert ConsentExpired("seller", proof.deadline, block.timestamp);
        }

        nonceKey = keccak256(abi.encodePacked(agreement.seller, proof.nonce));
        if (isConsentNonceUsed[nonceKey]) {
            revert NonceReplayed(agreement.seller, proof.nonce);
        }

        bytes32 digest = computeDigest(hashSellerConsent(agreement, proof));
        address recovered = _recoverSigner(digest, proof.signature);
        if (recovered != agreement.seller) {
            revert SignerMismatch("seller", agreement.seller, recovered);
        }
    }

    // ─── External: Escrow Lifecycle (CEI + Pull-Payment) ──────────────────────

    /**
     * @notice Buyer confirms satisfactory delivery, enabling seller withdrawal.
     */
    function confirmDelivery(uint256 dealId)
        external
        nonReentrant
        onlyBuyer(dealId)
        inState(dealId, State.FUNDED)
    {
        _deals[dealId].state = State.CONFIRMED;
        emit DeliveryConfirmed(dealId);
    }

    /**
     * @notice Buyer cancels deal and reclaims funds after delivery deadline has elapsed.
     */
    function cancelExpired(uint256 dealId)
        external
        nonReentrant
        onlyBuyer(dealId)
        inState(dealId, State.FUNDED)
    {
        Deal storage deal = _deals[dealId];
        if (block.timestamp <= deal.deadline) revert DeadlineNotReached();
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.CANCELLED;
        uint256 amount = deal.amount;

        emit DealCancelled(dealId, msg.sender, amount);

        _safeTransfer(deal.buyer, amount);
    }

    /**
     * @notice Seller withdraws payment after delivery is confirmed.
     */
    function withdrawPayment(uint256 dealId)
        external
        nonReentrant
        onlySeller(dealId)
        inState(dealId, State.CONFIRMED)
    {
        Deal storage deal = _deals[dealId];
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.RELEASED;
        uint256 amount = deal.amount;

        emit PaymentReleased(dealId, msg.sender, amount);

        _safeTransfer(deal.seller, amount);
    }

    /**
     * @notice Seller voluntarily refunds buyer before delivery confirmation.
     */
    function refundBuyer(uint256 dealId)
        external
        nonReentrant
        onlySeller(dealId)
        inState(dealId, State.FUNDED)
    {
        Deal storage deal = _deals[dealId];
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.REFUNDED;
        uint256 amount = deal.amount;

        emit RefundIssued(dealId, deal.buyer, amount);

        _safeTransfer(deal.buyer, amount);
    }

    /**
     * @notice Either party raises a dispute from FUNDED or CONFIRMED state.
     */
    function raiseDispute(uint256 dealId)
        external
        nonReentrant
        onlyParty(dealId)
    {
        Deal storage deal = _deals[dealId];
        if (deal.state != State.FUNDED && deal.state != State.CONFIRMED) {
            revert InvalidState(deal.state, State.FUNDED);
        }

        deal.state = State.DISPUTED;
        emit DisputeRaised(dealId, msg.sender);
    }

    /**
     * @notice Arbitrator resolves a dispute, routing 100% of funds to seller or buyer.
     */
    function resolveDispute(uint256 dealId, bool toSeller)
        external
        nonReentrant
        onlyArbitrator
        inState(dealId, State.DISPUTED)
    {
        Deal storage deal = _deals[dealId];
        if (deal.fundsWithdrawn) revert AlreadyWithdrawn();

        deal.fundsWithdrawn = true;
        deal.state = State.RESOLVED;
        uint256 amount = deal.amount;

        address payable recipient = toSeller ? deal.seller : deal.buyer;
        emit DisputeResolved(dealId, recipient, amount);

        _safeTransfer(recipient, amount);
    }

    // ─── External: View Functions ─────────────────────────────────────────────

    function getDeal(uint256 dealId) external view returns (Deal memory) {
        return _deals[dealId];
    }

    function nextDealId() external view returns (uint256) {
        return _nextDealId;
    }

    // ─── Fallback & Plain ETH Rejection ───────────────────────────────────────

    receive() external payable {
        revert("Use fundDealWithConsent");
    }

    fallback() external payable {
        revert("Use fundDealWithConsent");
    }

    // ─── External / Public Signature Recovery Helper ─────────────────────────

    /**
     * @notice Recovers signer from 32-byte digest and 65-byte signature with EIP-2 low-s guard.
     */
    function recoverSigner(
        bytes32 digest,
        bytes calldata signature
    ) external pure returns (address) {
        return _recoverSigner(digest, signature);
    }

    // ─── Internal Helpers ─────────────────────────────────────────────────────

    /**
     * @dev Recovers signer from 32-byte digest and 65-byte signature with EIP-2 low-s guard.
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

        // Low-s malleability protection (EIP-2 / Homestead)
        if (uint256(s) > SECP256K1_N_DIV_2) revert InvalidSignatureS();

        // Valid legacy v values
        if (v != 27 && v != 28) revert InvalidSignatureV();

        address signer = ecrecover(digest, v, r, s);
        if (signer == address(0)) revert InvalidSignature();

        return signer;
    }

    /**
     * @dev Low-level ETH transfer bubbling inner revert data if present.
     */
    function _safeTransfer(address payable recipient, uint256 amount) private {
        (bool ok, bytes memory data) = recipient.call{value: amount}("");
        if (!ok) {
            if (data.length > 0) {
                assembly {
                    revert(add(data, 0x20), mload(data))
                }
            }
            revert TransferFailed();
        }
    }
}
