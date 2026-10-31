// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title  CipherTradeEscrow
 * @author CipherTrade
 * @notice Peer-to-peer escrow contract for the CipherTrade marketplace.
 *
 * @dev State machine
 * ─────────────────────────────────────────────────────────────────────
 *
 *                    fundDeal()
 *  (none) ─────────────────────────► FUNDED
 *                                      │
 *            confirmDelivery()          │  cancelExpired() [after deadline]
 *                 (buyer)              │     (buyer)
 *                    │                ▼
 *                    │          CANCELLED ◄─────────────────────────
 *                    │                                              │
 *                    ▼           raiseDispute()                    │
 *               CONFIRMED ──────────────────► DISPUTED             │
 *                    │                           │                 │
 *      withdrawPayment()             resolveDispute() (arbitrator) │
 *          (seller)                       │                        │
 *                    │         ┌──────────┴──────────┐             │
 *                    │      TO_SELLER              TO_BUYER        │
 *                    │         │                      │            │
 *                    ▼         ▼                      ▼            │
 *               RELEASED  RELEASED               REFUNDED ────────┘
 *
 * ─────────────────────────────────────────────────────────────────────
 *
 * @dev Security invariants enforced
 *  1. Pull-payment: funds are transferred only when a party calls a
 *     withdrawal function; the contract never pushes ETH unsolicited.
 *  2. State is updated to a terminal value BEFORE any external `.call`.
 *     This eliminates reentrancy as a concern on those paths.
 *  3. A reentrancy guard (`_locked`) protects all state-changing
 *     public entry points as a defence-in-depth measure.
 *  4. No privileged administrator can unilaterally withdraw funds.
 *     Only buyer, seller, or an accepted arbitrator (for disputes) can
 *     move money, and only under explicitly defined conditions.
 *  5. `agreementHash` binds off-chain deal metadata to this on-chain
 *     record without exposing private information on-chain.
 *  6. Zero-value escrows are rejected at creation.
 *  7. Buyer/seller cannot be the same address.
 *
 * NOTE: This contract has NOT been independently audited.
 *       Do not deploy on mainnet without a formal security review.
 */
contract CipherTradeEscrow {

    // ─── Types ────────────────────────────────────────────────────────────────

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
     *         Only the minimum data needed for trustless settlement is stored.
     *         All listing details, negotiation history, and user PII remain off-chain.
     */
    struct Deal {
        address payable buyer;
        address payable seller;
        uint256 amount;           // Wei locked in this deal
        uint256 deadline;         // Unix timestamp: buyer may cancel if seller misses
        bytes32 agreementHash;    // keccak256 of off-chain deal terms (for integrity)
        State   state;
        bool    fundsWithdrawn;   // Prevents double-transfer after state is terminal
    }

    // ─── Storage ──────────────────────────────────────────────────────────────

    address public immutable arbitrator;

    uint256 private _nextDealId;
    mapping(uint256 => Deal) private _deals;

    /**
     * @dev Simple mutex to guard all state-changing entry points.
     *      Using a boolean rather than a counter is sufficient here because
     *      none of the guarded functions re-enter themselves.
     */
    bool private _locked;

    // ─── Events ───────────────────────────────────────────────────────────────

    /// @notice Emitted when a buyer creates and funds a new escrow deal.
    event DealFunded(
        uint256 indexed dealId,
        address indexed buyer,
        address indexed seller,
        uint256 amount,
        uint256 deadline,
        bytes32 agreementHash
    );

    /// @notice Emitted when the buyer confirms satisfactory delivery.
    event DeliveryConfirmed(uint256 indexed dealId);

    /// @notice Emitted when the seller withdraws payment after confirmation.
    event PaymentReleased(uint256 indexed dealId, address indexed seller, uint256 amount);

    /// @notice Emitted when the seller refunds the buyer voluntarily.
    event RefundIssued(uint256 indexed dealId, address indexed buyer, uint256 amount);

    /// @notice Emitted when the buyer cancels after the deadline has passed.
    event DealCancelled(uint256 indexed dealId, address indexed buyer, uint256 amount);

    /// @notice Emitted when either party raises a dispute.
    event DisputeRaised(uint256 indexed dealId, address indexed raisedBy);

    /// @notice Emitted when the arbitrator resolves a dispute.
    event DisputeResolved(uint256 indexed dealId, address indexed recipient, uint256 amount);

    // ─── Errors ───────────────────────────────────────────────────────────────

    /// @notice Caller is not authorised to perform this action on the deal.
    error Unauthorized();

    /// @notice The deal is not in the expected state for this operation.
    error InvalidState(State current, State required);

    /// @notice Escrow amount must be greater than zero.
    error ZeroAmount();

    /// @notice ETH transfer to recipient failed.
    error TransferFailed();

    /// @notice Funds for this deal have already been withdrawn.
    error AlreadyWithdrawn();

    /// @notice Buyer and seller cannot be the same address.
    error SelfDeal();

    /// @notice The deal deadline has not yet passed.
    error DeadlineNotReached();

    /// @notice The deal deadline has already passed; the operation is no longer valid.
    error DeadlineExpired();

    /// @notice Deadline must be in the future.
    error InvalidDeadline();

    /// @notice Reentrancy detected.
    error Reentrancy();

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

    /**
     * @param _arbitrator Address that may resolve disputes.
     *                    The arbitrator cannot initiate actions or withdraw funds
     *                    outside of a DISPUTED deal — there is no general admin power.
     */
    constructor(address _arbitrator) {
        require(_arbitrator != address(0), "Zero arbitrator");
        arbitrator = _arbitrator;
    }

    // ─── External: Buyer actions ──────────────────────────────────────────────

    /**
     * @notice Create and fund a new escrow deal.
     * @dev    The buyer sends ETH with this call; it is held by the contract.
     *         `agreementHash` should be `keccak256(abi.encode(listingId, offerId, price, ...))`,
     *         computed off-chain and included here for integrity verification.
     *         All deal details (title, description, etc.) remain off-chain.
     *
     * @param  seller        Address of the counterparty seller (must differ from buyer).
     * @param  deadline      Unix timestamp by which the seller should complete delivery.
     *                       The buyer may cancel for a full refund after this passes.
     * @param  agreementHash Off-chain deal fingerprint for integrity verification.
     * @return dealId        Unique, auto-incrementing identifier for this deal.
     */
    function fundDeal(
        address payable seller,
        uint256 deadline,
        bytes32 agreementHash
    )
        external
        payable
        nonReentrant
        returns (uint256 dealId)
    {
        if (msg.value == 0) revert ZeroAmount();
        if (seller == msg.sender) revert SelfDeal();
        if (deadline <= block.timestamp) revert InvalidDeadline();

        dealId = _nextDealId++;
        _deals[dealId] = Deal({
            buyer:          payable(msg.sender),
            seller:         seller,
            amount:         msg.value,
            deadline:       deadline,
            agreementHash:  agreementHash,
            state:          State.FUNDED,
            fundsWithdrawn: false
        });

        emit DealFunded(dealId, msg.sender, seller, msg.value, deadline, agreementHash);
    }

    /**
     * @notice Buyer confirms satisfactory delivery, enabling the seller to withdraw payment.
     * @dev    Must be called while deal is FUNDED and before deadline.
     *         After deadline the buyer should use `cancelExpired` instead.
     * @param  dealId Identifier of the deal to confirm.
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
     * @notice Buyer cancels the deal and reclaims funds after the seller's deadline has passed.
     * @dev    Only valid from FUNDED state and only after `deal.deadline` has elapsed.
     *         State is set to CANCELLED and funds transferred to buyer in a single operation
     *         (state first, then call) to prevent reentrancy double-spend.
     * @param  dealId Identifier of the deal to cancel.
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

    // ─── External: Seller actions ─────────────────────────────────────────────

    /**
     * @notice Seller withdraws payment after the buyer has confirmed delivery.
     * @dev    Pull-payment: the seller must call this; the contract does not push ETH.
     *         State is set to RELEASED before the external call (CEI pattern).
     * @param  dealId Identifier of the deal to settle.
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
     * @notice Seller voluntarily refunds the buyer before delivery is confirmed.
     * @dev    Only valid while the deal is FUNDED. After the deadline has passed,
     *         the buyer should use `cancelExpired`; the seller may still call this
     *         until the buyer does so.
     * @param  dealId Identifier of the deal to refund.
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

    // ─── External: Dispute ────────────────────────────────────────────────────

    /**
     * @notice Either party raises a dispute, freezing the deal until arbitration.
     * @dev    Callable from FUNDED or CONFIRMED states only.
     *         Once DISPUTED, funds cannot move until the arbitrator calls `resolveDispute`.
     * @param  dealId Identifier of the deal to dispute.
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
     * @notice Arbitrator resolves a dispute by routing funds to buyer or seller.
     * @dev    The arbitrator has NO ability to redirect funds to themselves or any
     *         third party — the `toSeller` flag routes 100% of the escrowed amount
     *         to either the original seller or the original buyer.
     *         State is set to RESOLVED before the external call (CEI pattern).
     *
     * @param  dealId    Identifier of the disputed deal.
     * @param  toSeller  If true, release funds to the seller. If false, refund the buyer.
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

    // ─── External: View ───────────────────────────────────────────────────────

    /**
     * @notice Returns the full on-chain record for a deal.
     * @param  dealId Identifier of the deal to query.
     * @return        The `Deal` struct for the given id.
     */
    function getDeal(uint256 dealId) external view returns (Deal memory) {
        return _deals[dealId];
    }

    /**
     * @notice Returns the next deal id that will be assigned.
     *         Useful for off-chain indexers to know which ids exist.
     */
    function nextDealId() external view returns (uint256) {
        return _nextDealId;
    }

    // ─── Fallback rejection ───────────────────────────────────────────────────

    /// @dev Reject accidental plain ETH sends; only `fundDeal` is a valid entry.
    receive() external payable {
        revert("Use fundDeal");
    }

    fallback() external payable {
        revert("Use fundDeal");
    }

    // ─── Internal helpers ─────────────────────────────────────────────────────

    /**
     * @dev Transfer `amount` wei to `recipient` and revert on failure.
     *
     *      When the low-level call fails AND the callee provided revert data
     *      (e.g. the reentrancy guard inside a re-entrant call), this function
     *      **bubbles the inner revert up verbatim** rather than masking it with
     *      `TransferFailed()`.  This preserves meaningful error information for
     *      callers (and tests) while keeping the reentrancy guard's `Reentrancy()`
     *      error visible.
     *
     *      If the call fails with *no* return data (plain ETH send to a contract
     *      that has no `receive()`, or ran out of gas) we revert with
     *      `TransferFailed()` as before.
     *
     * @param recipient Address to send ETH to.
     * @param amount    Amount of wei to transfer.
     */
    function _safeTransfer(address payable recipient, uint256 amount) private {
        (bool ok, bytes memory data) = recipient.call{value: amount}("");
        if (!ok) {
            if (data.length > 0) {
                // Bubble the inner revert up (preserves Reentrancy(), etc.).
                assembly {
                    revert(add(data, 0x20), mload(data))
                }
            }
            revert TransferFailed();
        }
    }
}
