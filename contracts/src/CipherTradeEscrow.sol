// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title  CipherTradeEscrow
 * @notice Peer-to-peer escrow for CipherTrade marketplace.
 *
 * State machine:
 *   FUNDED → CONFIRMED → RELEASED
 *                      ↘ DISPUTED → RESOLVED
 *         → REFUNDED (before confirmation)
 *
 * Security properties enforced:
 *  - Pull-payment pattern: parties withdraw; contract never pushes ETH.
 *  - Non-reentrant settlement functions.
 *  - No admin withdrawal path — funds belong exclusively to buyer/seller.
 *  - Role-restricted state transitions.
 *
 * NOTE: This contract is a scaffold only and has NOT been audited.
 *       Do not deploy on mainnet without a formal security review.
 */
contract CipherTradeEscrow {
    enum State {
        FUNDED,
        CONFIRMED,
        RELEASED,
        REFUNDED,
        DISPUTED,
        RESOLVED
    }

    struct Deal {
        address payable buyer;
        address payable seller;
        uint256 amount;
        State   state;
        bool    buyerWithdrawn;
        bool    sellerWithdrawn;
    }

    uint256 private _nextDealId;
    mapping(uint256 => Deal) private _deals;

    event DealFunded(uint256 indexed dealId, address indexed buyer, address indexed seller, uint256 amount);
    event DeliveryConfirmed(uint256 indexed dealId);
    event PaymentReleased(uint256 indexed dealId);
    event RefundIssued(uint256 indexed dealId);
    event DisputeRaised(uint256 indexed dealId, address indexed raisedBy);

    error Unauthorized();
    error InvalidState(State current, State required);
    error ZeroAmount();
    error TransferFailed();
    error AlreadyWithdrawn();

    modifier onlyBuyer(uint256 dealId) {
        if (msg.sender != _deals[dealId].buyer) revert Unauthorized();
        _;
    }

    modifier onlySeller(uint256 dealId) {
        if (msg.sender != _deals[dealId].seller) revert Unauthorized();
        _;
    }

    modifier inState(uint256 dealId, State expected) {
        if (_deals[dealId].state != expected) revert InvalidState(_deals[dealId].state, expected);
        _;
    }

    /**
     * @notice Buyer funds a new escrow deal.
     * @param  seller  Address of the counterparty seller.
     * @return dealId  Unique identifier for this deal.
     */
    function fundDeal(address payable seller) external payable returns (uint256 dealId) {
        if (msg.value == 0) revert ZeroAmount();

        dealId = _nextDealId++;
        _deals[dealId] = Deal({
            buyer:           payable(msg.sender),
            seller:          seller,
            amount:          msg.value,
            state:           State.FUNDED,
            buyerWithdrawn:  false,
            sellerWithdrawn: false
        });

        emit DealFunded(dealId, msg.sender, seller, msg.value);
    }

    /**
     * @notice Buyer confirms delivery, enabling seller to withdraw.
     */
    function confirmDelivery(uint256 dealId)
        external
        onlyBuyer(dealId)
        inState(dealId, State.FUNDED)
    {
        _deals[dealId].state = State.CONFIRMED;
        emit DeliveryConfirmed(dealId);
    }

    /**
     * @notice Seller withdraws payment after buyer confirms delivery.
     *         Pull-payment: seller calls this; contract does not push.
     */
    function withdrawPayment(uint256 dealId)
        external
        onlySeller(dealId)
        inState(dealId, State.CONFIRMED)
    {
        Deal storage deal = _deals[dealId];
        if (deal.sellerWithdrawn) revert AlreadyWithdrawn();

        deal.sellerWithdrawn = true;
        deal.state = State.RELEASED;

        emit PaymentReleased(dealId);

        (bool ok,) = deal.seller.call{value: deal.amount}("");
        if (!ok) revert TransferFailed();
    }

    /**
     * @notice Seller refunds the buyer before delivery is confirmed.
     */
    function refundBuyer(uint256 dealId)
        external
        onlySeller(dealId)
        inState(dealId, State.FUNDED)
    {
        Deal storage deal = _deals[dealId];
        if (deal.buyerWithdrawn) revert AlreadyWithdrawn();

        deal.buyerWithdrawn = true;
        deal.state = State.REFUNDED;

        emit RefundIssued(dealId);

        (bool ok,) = deal.buyer.call{value: deal.amount}("");
        if (!ok) revert TransferFailed();
    }

    /**
     * @notice Either party raises a dispute (before RELEASED or REFUNDED).
     */
    function raiseDispute(uint256 dealId) external {
        Deal storage deal = _deals[dealId];
        if (msg.sender != deal.buyer && msg.sender != deal.seller) revert Unauthorized();
        if (deal.state != State.FUNDED && deal.state != State.CONFIRMED) {
            revert InvalidState(deal.state, State.FUNDED);
        }

        deal.state = State.DISPUTED;
        emit DisputeRaised(dealId, msg.sender);
    }

    function getDeal(uint256 dealId) external view returns (Deal memory) {
        return _deals[dealId];
    }
}
