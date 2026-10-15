// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {CipherTradeEscrow} from "../src/CipherTradeEscrow.sol";

contract CipherTradeEscrowTest is Test {
    CipherTradeEscrow internal escrow;

    address payable internal buyer  = payable(makeAddr("buyer"));
    address payable internal seller = payable(makeAddr("seller"));

    function setUp() public {
        escrow = new CipherTradeEscrow();
        vm.deal(buyer, 10 ether);
    }

    function test_fundDeal_emits_event() public {
        vm.prank(buyer);
        vm.expectEmit(true, true, true, true);
        emit CipherTradeEscrow.DealFunded(0, buyer, seller, 1 ether);
        escrow.fundDeal{value: 1 ether}(seller);
    }

    function test_confirmAndWithdraw_happyPath() public {
        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: 1 ether}(seller);

        vm.prank(buyer);
        escrow.confirmDelivery(id);

        uint256 before = seller.balance;
        vm.prank(seller);
        escrow.withdrawPayment(id);

        assertEq(seller.balance, before + 1 ether);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.RELEASED));
    }

    function test_refundBuyer() public {
        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: 1 ether}(seller);

        uint256 before = buyer.balance;
        vm.prank(seller);
        escrow.refundBuyer(id);

        assertEq(buyer.balance, before + 1 ether);
        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.REFUNDED));
    }

    function test_raiseDispute() public {
        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: 1 ether}(seller);

        vm.prank(buyer);
        escrow.raiseDispute(id);

        assertEq(uint8(escrow.getDeal(id).state), uint8(CipherTradeEscrow.State.DISPUTED));
    }

    function test_unauthorizedWithdraw_reverts() public {
        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: 1 ether}(seller);

        vm.prank(buyer);
        escrow.confirmDelivery(id);

        address rando = makeAddr("rando");
        vm.prank(rando);
        vm.expectRevert(CipherTradeEscrow.Unauthorized.selector);
        escrow.withdrawPayment(id);
    }

    function test_doubleWithdraw_reverts() public {
        vm.prank(buyer);
        uint256 id = escrow.fundDeal{value: 1 ether}(seller);

        vm.prank(buyer);
        escrow.confirmDelivery(id);

        vm.prank(seller);
        escrow.withdrawPayment(id);

        vm.prank(seller);
        vm.expectRevert();
        escrow.withdrawPayment(id);
    }

    function test_zeroFund_reverts() public {
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrow.ZeroAmount.selector);
        escrow.fundDeal{value: 0}(seller);
    }
}
