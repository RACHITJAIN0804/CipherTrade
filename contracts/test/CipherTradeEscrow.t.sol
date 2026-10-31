// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {CipherTradeEscrow} from "../src/CipherTradeEscrow.sol";

/**
 * @title  CipherTradeEscrowTest
 * @notice Comprehensive Foundry test suite for CipherTradeEscrow.
 *
 * Test groups
 * ───────────
 * A. Deployment / constructor
 * B. fundDeal — valid and invalid inputs
 * C. confirmDelivery — happy path and access control
 * D. withdrawPayment — happy path, access control, double-spend
 * E. refundBuyer — happy path and access control
 * F. cancelExpired — deadline enforcement
 * G. raiseDispute — valid states and access control
 * H. resolveDispute — arbitrator routing and access control
 * I. State-transition matrix — invalid transitions
 * J. Reentrancy — malicious receiver contracts
 * K. Fuzz — fund and withdraw with arbitrary amounts
 */
contract CipherTradeEscrowTest is Test {

    // ─── Fixtures ──────────────────────────────────────────────────────────────

    CipherTradeEscrow internal escrow;

    address payable internal buyer      = payable(makeAddr("buyer"));
    address payable internal seller     = payable(makeAddr("seller"));
    address          internal arb       = makeAddr("arbitrator");
    address          internal rando     = makeAddr("rando");

    uint256 internal constant AMOUNT    = 1 ether;
    uint256 internal constant ONE_DAY   = 1 days;
    bytes32 internal constant AG_HASH   = keccak256("listing:1|offer:42|price:1eth");

    function setUp() public {
        escrow = new CipherTradeEscrow(arb);
        vm.deal(buyer,  100 ether);
        vm.deal(seller, 10 ether);
    }

    // ─── Helpers ───────────────────────────────────────────────────────────────

    /// @dev Create a funded deal with default fixtures; returns dealId.
    function _fund() internal returns (uint256 id) {
        vm.prank(buyer);
        id = escrow.fundDeal{value: AMOUNT}(seller, block.timestamp + ONE_DAY, AG_HASH);
    }

    /// @dev Create, fund, and confirm delivery; returns dealId.
    function _fundAndConfirm() internal returns (uint256 id) {
        id = _fund();
        vm.prank(buyer);
        escrow.confirmDelivery(id);
    }

    /// @dev Create and fund, then raise a dispute; returns dealId.
    function _fundAndDispute() internal returns (uint256 id) {
        id = _fund();
        vm.prank(buyer);
        escrow.raiseDispute(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // A. Deployment / constructor
    // ══════════════════════════════════════════════════════════════════════════

    function test_A1_arbitratorSet() public view {
        assertEq(escrow.arbitrator(), arb);
    }

    function test_A2_zeroArbitrator_reverts() public {
        vm.expectRevert("Zero arbitrator");
        new CipherTradeEscrow(address(0));
    }

    function test_A3_initialNextDealId_isZero() public view {
        assertEq(escrow.nextDealId(), 0);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // B. fundDeal
    // ══════════════════════════════════════════════════════════════════════════

    function test_B1_fundDeal_storesCorrectly() public {
        uint256 deadline = block.timestamp + ONE_DAY;

        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: AMOUNT}(seller, deadline, AG_HASH);

        CipherTradeEscrow.Deal memory d = escrow.getDeal(id);
        assertEq(d.buyer,         buyer);
        assertEq(d.seller,        seller);
        assertEq(d.amount,        AMOUNT);
        assertEq(d.deadline,      deadline);
        assertEq(d.agreementHash, AG_HASH);
        assertEq(uint8(d.state),  uint8(CipherTradeEscrow.State.FUNDED));
        assertFalse(d.fundsWithdrawn);
    }

    function test_B2_fundDeal_emitsEvent() public {
        uint256 deadline = block.timestamp + ONE_DAY;

        vm.prank(buyer);
        vm.expectEmit(true, true, true, true);
        emit CipherTradeEscrow.DealFunded(0, buyer, seller, AMOUNT, deadline, AG_HASH);
        escrow.fundDeal{value: AMOUNT}(seller, deadline, AG_HASH);
    }

    function test_B3_fundDeal_incrementsDealId() public {
        _fund();
        assertEq(escrow.nextDealId(), 1);
        _fund();
        assertEq(escrow.nextDealId(), 2);
    }

    function test_B4_fundDeal_zeroValue_reverts() public {
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.ZeroAmount.selector);
        escrow.fundDeal{value: 0}(seller, block.timestamp + ONE_DAY, AG_HASH);
    }

    function test_B5_fundDeal_selfDeal_reverts() public {
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.SelfDeal.selector);
        escrow.fundDeal{value: AMOUNT}(buyer, block.timestamp + ONE_DAY, AG_HASH);
    }

    function test_B6_fundDeal_deadlineInPast_reverts() public {
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.InvalidDeadline.selector);
        escrow.fundDeal{value: AMOUNT}(seller, block.timestamp, AG_HASH);
    }

    function test_B7_fundDeal_deadlineExactlyNow_reverts() public {
        vm.warp(1000);
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.InvalidDeadline.selector);
        escrow.fundDeal{value: AMOUNT}(seller, block.timestamp, AG_HASH);
    }

    function test_B8_fundDeal_contractBalanceIncreases() public {
        _fund();
        assertEq(address(escrow).balance, AMOUNT);
    }

    function test_B9_plainEthSend_reverts() public {
        vm.prank(buyer);
        (bool ok,) = address(escrow).call{value: AMOUNT}("");
        assertFalse(ok);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // C. confirmDelivery
    // ══════════════════════════════════════════════════════════════════════════

    function test_C1_confirmDelivery_changesState() public {
        uint256 id = _fund();
        vm.prank(buyer);
        escrow.confirmDelivery(id);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.CONFIRMED));
    }

    function test_C2_confirmDelivery_emitsEvent() public {
        uint256 id = _fund();
        vm.prank(buyer);
        vm.expectEmit(true, false, false, false);
        emit CipherTradeEscrow.DeliveryConfirmed(id);
        escrow.confirmDelivery(id);
    }

    function test_C3_confirmDelivery_notBuyer_reverts() public {
        uint256 id = _fund();
        vm.prank(seller);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.confirmDelivery(id);
    }

    function test_C4_confirmDelivery_rando_reverts() public {
        uint256 id = _fund();
        vm.prank(rando);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.confirmDelivery(id);
    }

    function test_C5_confirmDelivery_wrongState_reverts() public {
        uint256 id = _fundAndConfirm();
        vm.prank(buyer);
        // Deal is CONFIRMED; confirmDelivery requires FUNDED — so the error is
        // InvalidState(current=CONFIRMED, required=FUNDED).
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.CONFIRMED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.confirmDelivery(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // D. withdrawPayment
    // ══════════════════════════════════════════════════════════════════════════

    function test_D1_withdrawPayment_happyPath() public {
        uint256 id = _fundAndConfirm();
        uint256 before = seller.balance;

        vm.prank(seller);
        escrow.withdrawPayment(id);

        assertEq(seller.balance, before + AMOUNT);
        assertEq(address(escrow).balance, 0);
    }

    function test_D2_withdrawPayment_setsReleased() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        escrow.withdrawPayment(id);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.RELEASED));
    }

    function test_D3_withdrawPayment_emitsEvent() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        vm.expectEmit(true, true, false, true);
        emit CipherTradeEscrow.PaymentReleased(id, seller, AMOUNT);
        escrow.withdrawPayment(id);
    }

    function test_D4_withdrawPayment_notSeller_reverts() public {
        uint256 id = _fundAndConfirm();
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.withdrawPayment(id);
    }

    function test_D5_withdrawPayment_rando_reverts() public {
        uint256 id = _fundAndConfirm();
        vm.prank(rando);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.withdrawPayment(id);
    }

    function test_D6_withdrawPayment_fromFunded_reverts() public {
        uint256 id = _fund();
        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.FUNDED,
                CipherTradeEscrow.State.CONFIRMED
            )
        );
        escrow.withdrawPayment(id);
    }

    function test_D7_withdrawPayment_double_reverts() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        escrow.withdrawPayment(id);

        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.RELEASED,
                CipherTradeEscrow.State.CONFIRMED
            )
        );
        escrow.withdrawPayment(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // E. refundBuyer
    // ══════════════════════════════════════════════════════════════════════════

    function test_E1_refundBuyer_happyPath() public {
        uint256 id = _fund();
        uint256 before = buyer.balance;

        vm.prank(seller);
        escrow.refundBuyer(id);

        assertEq(buyer.balance, before + AMOUNT);
        assertEq(address(escrow).balance, 0);
    }

    function test_E2_refundBuyer_setsRefunded() public {
        uint256 id = _fund();
        vm.prank(seller);
        escrow.refundBuyer(id);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.REFUNDED));
    }

    function test_E3_refundBuyer_emitsEvent() public {
        uint256 id = _fund();
        vm.prank(seller);
        vm.expectEmit(true, true, false, true);
        emit CipherTradeEscrow.RefundIssued(id, buyer, AMOUNT);
        escrow.refundBuyer(id);
    }

    function test_E4_refundBuyer_notSeller_reverts() public {
        uint256 id = _fund();
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.refundBuyer(id);
    }

    function test_E5_refundBuyer_fromConfirmed_reverts() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.CONFIRMED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.refundBuyer(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // F. cancelExpired
    // ══════════════════════════════════════════════════════════════════════════

    function test_F1_cancelExpired_happyPath() public {
        uint256 id = _fund();
        vm.warp(block.timestamp + ONE_DAY + 1);

        uint256 before = buyer.balance;
        vm.prank(buyer);
        escrow.cancelExpired(id);

        assertEq(buyer.balance, before + AMOUNT);
        assertEq(address(escrow).balance, 0);
    }

    function test_F2_cancelExpired_setsCancelled() public {
        uint256 id = _fund();
        vm.warp(block.timestamp + ONE_DAY + 1);

        vm.prank(buyer);
        escrow.cancelExpired(id);

        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.CANCELLED));
    }

    function test_F3_cancelExpired_emitsEvent() public {
        uint256 id = _fund();
        vm.warp(block.timestamp + ONE_DAY + 1);

        vm.prank(buyer);
        vm.expectEmit(true, true, false, true);
        emit CipherTradeEscrow.DealCancelled(id, buyer, AMOUNT);
        escrow.cancelExpired(id);
    }

    function test_F4_cancelExpired_beforeDeadline_reverts() public {
        uint256 id = _fund();
        // Still within deadline
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.DeadlineNotReached.selector);
        escrow.cancelExpired(id);
    }

    function test_F5_cancelExpired_exactlyDeadline_reverts() public {
        uint256 start = block.timestamp;
        uint256 id = _fund();
        vm.warp(start + ONE_DAY); // exactly at deadline (not past it)
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.DeadlineNotReached.selector);
        escrow.cancelExpired(id);
    }

    function test_F6_cancelExpired_notBuyer_reverts() public {
        uint256 id = _fund();
        vm.warp(block.timestamp + ONE_DAY + 1);

        vm.prank(seller);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.cancelExpired(id);
    }

    function test_F7_cancelExpired_fromConfirmed_reverts() public {
        uint256 id = _fundAndConfirm();
        vm.warp(block.timestamp + ONE_DAY + 1);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.CONFIRMED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.cancelExpired(id);
    }

    function test_F8_sellerCanStillRefundAfterDeadline() public {
        uint256 id = _fund();
        vm.warp(block.timestamp + ONE_DAY + 1);

        // Seller can still voluntarily refund even after deadline (before buyer cancels)
        uint256 before = buyer.balance;
        vm.prank(seller);
        escrow.refundBuyer(id);
        assertEq(buyer.balance, before + AMOUNT);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // G. raiseDispute
    // ══════════════════════════════════════════════════════════════════════════

    function test_G1_raiseDispute_byBuyer_fromFunded() public {
        uint256 id = _fund();
        vm.prank(buyer);
        escrow.raiseDispute(id);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.DISPUTED));
    }

    function test_G2_raiseDispute_bySeller_fromFunded() public {
        uint256 id = _fund();
        vm.prank(seller);
        escrow.raiseDispute(id);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.DISPUTED));
    }

    function test_G3_raiseDispute_byBuyer_fromConfirmed() public {
        uint256 id = _fundAndConfirm();
        vm.prank(buyer);
        escrow.raiseDispute(id);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.DISPUTED));
    }

    function test_G4_raiseDispute_bySeller_fromConfirmed() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        escrow.raiseDispute(id);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.DISPUTED));
    }

    function test_G5_raiseDispute_emitsEvent() public {
        uint256 id = _fund();
        vm.prank(buyer);
        vm.expectEmit(true, true, false, false);
        emit CipherTradeEscrow.DisputeRaised(id, buyer);
        escrow.raiseDispute(id);
    }

    function test_G6_raiseDispute_rando_reverts() public {
        uint256 id = _fund();
        vm.prank(rando);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.raiseDispute(id);
    }

    function test_G7_raiseDispute_fromReleased_reverts() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        escrow.withdrawPayment(id);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.RELEASED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.raiseDispute(id);
    }

    function test_G8_raiseDispute_fromRefunded_reverts() public {
        uint256 id = _fund();
        vm.prank(seller);
        escrow.refundBuyer(id);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.REFUNDED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.raiseDispute(id);
    }

    function test_G9_raiseDispute_fromDisputed_reverts() public {
        uint256 id = _fundAndDispute();
        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.DISPUTED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.raiseDispute(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // H. resolveDispute
    // ══════════════════════════════════════════════════════════════════════════

    function test_H1_resolveDispute_toSeller() public {
        uint256 id = _fundAndDispute();
        uint256 before = seller.balance;

        vm.prank(arb);
        escrow.resolveDispute(id, true);

        assertEq(seller.balance, before + AMOUNT);
        assertEq(address(escrow).balance, 0);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.RESOLVED));
    }

    function test_H2_resolveDispute_toBuyer() public {
        uint256 id = _fundAndDispute();
        uint256 before = buyer.balance;

        vm.prank(arb);
        escrow.resolveDispute(id, false);

        assertEq(buyer.balance, before + AMOUNT);
        assertEq(address(escrow).balance, 0);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.RESOLVED));
    }

    function test_H3_resolveDispute_emitsEvent_toSeller() public {
        uint256 id = _fundAndDispute();
        vm.prank(arb);
        vm.expectEmit(true, true, false, true);
        emit CipherTradeEscrow.DisputeResolved(id, seller, AMOUNT);
        escrow.resolveDispute(id, true);
    }

    function test_H4_resolveDispute_emitsEvent_toBuyer() public {
        uint256 id = _fundAndDispute();
        vm.prank(arb);
        vm.expectEmit(true, true, false, true);
        emit CipherTradeEscrow.DisputeResolved(id, buyer, AMOUNT);
        escrow.resolveDispute(id, false);
    }

    function test_H5_resolveDispute_notArbitrator_reverts() public {
        uint256 id = _fundAndDispute();
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.resolveDispute(id, true);
    }

    function test_H6_resolveDispute_sellerCantArbitrate_reverts() public {
        uint256 id = _fundAndDispute();
        vm.prank(seller);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.resolveDispute(id, true);
    }

    function test_H7_resolveDispute_fromFunded_reverts() public {
        uint256 id = _fund();
        vm.prank(arb);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.FUNDED,
                CipherTradeEscrow.State.DISPUTED
            )
        );
        escrow.resolveDispute(id, true);
    }

    function test_H8_resolveDispute_double_reverts() public {
        uint256 id = _fundAndDispute();
        vm.prank(arb);
        escrow.resolveDispute(id, true);

        vm.prank(arb);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.RESOLVED,
                CipherTradeEscrow.State.DISPUTED
            )
        );
        escrow.resolveDispute(id, false);
    }

    function test_H9_arbitrator_cannotCallNonDispute_functions() public {
        uint256 id = _fund();
        // Arbitrator has no special power outside resolveDispute
        vm.prank(arb);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.confirmDelivery(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // I. State-transition matrix — blocked paths
    // ══════════════════════════════════════════════════════════════════════════

    function test_I1_cannotWithdrawFromFunded() public {
        uint256 id = _fund();
        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.FUNDED,
                CipherTradeEscrow.State.CONFIRMED
            )
        );
        escrow.withdrawPayment(id);
    }

    function test_I2_cannotConfirmAfterRelease() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        escrow.withdrawPayment(id);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.RELEASED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.confirmDelivery(id);
    }

    function test_I3_cannotRefundAfterRelease() public {
        uint256 id = _fundAndConfirm();
        vm.prank(seller);
        escrow.withdrawPayment(id);

        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.RELEASED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.refundBuyer(id);
    }

    function test_I4_cannotCancelAfterConfirm() public {
        uint256 id = _fundAndConfirm();
        vm.warp(block.timestamp + ONE_DAY + 1);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.CONFIRMED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.cancelExpired(id);
    }

    function test_I5_cannotWithdrawFromDisputed() public {
        uint256 id = _fundAndDispute();
        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.DISPUTED,
                CipherTradeEscrow.State.CONFIRMED
            )
        );
        escrow.withdrawPayment(id);
    }

    function test_I6_cannotRefundFromDisputed() public {
        uint256 id = _fundAndDispute();
        vm.prank(seller);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.DISPUTED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.refundBuyer(id);
    }

    function test_I7_cannotCancelAfterCancelled() public {
        uint256 id = _fund();
        vm.warp(block.timestamp + ONE_DAY + 1);

        vm.prank(buyer);
        escrow.cancelExpired(id);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrow.InvalidState.selector,
                CipherTradeEscrow.State.CANCELLED,
                CipherTradeEscrow.State.FUNDED
            )
        );
        escrow.cancelExpired(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // J. Reentrancy — malicious receiver
    // ══════════════════════════════════════════════════════════════════════════

    function test_J1_reentrancy_onWithdrawPayment() public {
        MaliciousSeller evil = new MaliciousSeller(escrow);
        vm.deal(buyer, 10 ether);

        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: AMOUNT}(
            payable(address(evil)),
            block.timestamp + ONE_DAY,
            AG_HASH
        );

        vm.prank(buyer);
        escrow.confirmDelivery(id);

        // The evil contract attempts re-entry in its receive(); must revert cleanly.
        evil.setDealId(id);
        vm.prank(address(evil));
        vm.expectRevert(CipherTradeEscrow.Reentrancy.selector);
        escrow.withdrawPayment(id);
    }

    function test_J2_reentrancy_onRefundBuyer() public {
        MaliciousBuyer evil = new MaliciousBuyer(escrow);
        vm.deal(address(evil), 10 ether);

        vm.prank(address(evil));
        uint256 id = escrow.fundDeal{value: AMOUNT}(seller, block.timestamp + ONE_DAY, AG_HASH);
        evil.setDealId(id);

        // Seller refunds the malicious buyer; re-entry in receive() must be rejected.
        vm.prank(seller);
        vm.expectRevert(CipherTradeEscrow.Reentrancy.selector);
        escrow.refundBuyer(id);
    }

    function test_J3_reentrancy_onCancelExpired() public {
        MaliciousBuyer evil = new MaliciousBuyer(escrow);
        vm.deal(address(evil), 10 ether);

        vm.prank(address(evil));
        uint256 id = escrow.fundDeal{value: AMOUNT}(seller, block.timestamp + ONE_DAY, AG_HASH);
        evil.setDealId(id);

        vm.warp(block.timestamp + ONE_DAY + 1);

        vm.prank(address(evil));
        vm.expectRevert(CipherTradeEscrow.Reentrancy.selector);
        escrow.cancelExpired(id);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // K. Fuzz tests
    // ══════════════════════════════════════════════════════════════════════════

    /// @dev Any non-zero amount a buyer funds must be exactly returned to the seller.
    function testFuzz_K1_fullAmountReleasedToSeller(uint96 amount) public {
        vm.assume(amount > 0);
        vm.deal(buyer, amount);

        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: amount}(seller, block.timestamp + ONE_DAY, AG_HASH);

        vm.prank(buyer);
        escrow.confirmDelivery(id);

        uint256 before = seller.balance;
        vm.prank(seller);
        escrow.withdrawPayment(id);

        assertEq(seller.balance, before + amount);
        assertEq(address(escrow).balance, 0);
    }

    /// @dev Any non-zero refund must return exactly the escrowed amount to the buyer.
    function testFuzz_K2_fullAmountRefundedToBuyer(uint96 amount) public {
        vm.assume(amount > 0);
        vm.deal(buyer, amount);

        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: amount}(seller, block.timestamp + ONE_DAY, AG_HASH);

        uint256 before = buyer.balance;
        vm.prank(seller);
        escrow.refundBuyer(id);

        assertEq(buyer.balance, before + amount);
        assertEq(address(escrow).balance, 0);
    }

    /// @dev Dispute resolution always sends 100% of funds to buyer or seller, never elsewhere.
    function testFuzz_K3_disputeResolutionSendsFullAmount(uint96 amount, bool toSeller) public {
        vm.assume(amount > 0);
        vm.deal(buyer, amount);

        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: amount}(seller, block.timestamp + ONE_DAY, AG_HASH);

        vm.prank(buyer);
        escrow.raiseDispute(id);

        uint256 sellerBefore = seller.balance;
        uint256 buyerBefore  = buyer.balance;

        vm.prank(arb);
        escrow.resolveDispute(id, toSeller);

        if (toSeller) {
            assertEq(seller.balance, sellerBefore + amount);
            assertEq(buyer.balance,  buyerBefore);
        } else {
            assertEq(buyer.balance,  buyerBefore + amount);
            assertEq(seller.balance, sellerBefore);
        }
        assertEq(address(escrow).balance, 0);
    }

    /// @dev Multiple concurrent deals must not bleed funds between each other.
    function testFuzz_K4_isolatedDeals(uint64 amountA, uint64 amountB) public {
        vm.assume(amountA > 0 && amountB > 0);
        vm.deal(buyer, uint256(amountA) + uint256(amountB));

        vm.prank(buyer);
        uint256 idA = escrow.fundDeal{value: amountA}(seller, block.timestamp + ONE_DAY, AG_HASH);

        vm.prank(buyer);
        uint256 idB = escrow.fundDeal{value: amountB}(seller, block.timestamp + ONE_DAY, AG_HASH);

        // Confirm and withdraw deal A; deal B's funds must remain
        vm.prank(buyer); escrow.confirmDelivery(idA);
        vm.prank(seller); escrow.withdrawPayment(idA);

        assertEq(address(escrow).balance, amountB, "Deal B funds must be unaffected");

        // Refund deal B
        vm.prank(seller); escrow.refundBuyer(idB);
        assertEq(address(escrow).balance, 0);
    }
}

// ─── Attacker contracts ────────────────────────────────────────────────────────

/// @dev A seller that tries to re-enter withdrawPayment during its receive().
contract MaliciousSeller {
    CipherTradeEscrow private immutable _escrow;
    uint256 private _dealId;

    constructor(CipherTradeEscrow e) { _escrow = e; }
    function setDealId(uint256 id) external { _dealId = id; }

    receive() external payable {
        // Attempt reentry
        _escrow.withdrawPayment(_dealId);
    }

    fallback() external payable {}
}

/// @dev A buyer that tries to re-enter refundBuyer or cancelExpired during its receive().
contract MaliciousBuyer {
    CipherTradeEscrow private immutable _escrow;
    uint256 private _dealId;

    constructor(CipherTradeEscrow e) { _escrow = e; }
    function setDealId(uint256 id) external { _dealId = id; }

    // Fund deals on behalf of this contract
    function fundDeal(address payable seller, uint256 deadline, bytes32 hash)
        external payable
        returns (uint256)
    {
        return _escrow.fundDeal{value: msg.value}(seller, deadline, hash);
    }

    receive() external payable {
        // Try to call cancelExpired (will fail due to reentrancy guard)
        _escrow.cancelExpired(_dealId);
    }

    fallback() external payable {}
}
