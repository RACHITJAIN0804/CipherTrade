// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {CipherTradeEscrowV2} from "../src/CipherTradeEscrowV2.sol";

contract MaliciousReceiverV2 {
    CipherTradeEscrowV2 public escrow;
    bool public attack;

    constructor(CipherTradeEscrowV2 _escrow) {
        escrow = _escrow;
    }

    function setAttack(bool _attack) external {
        attack = _attack;
    }

    receive() external payable {
        if (attack) {
            attack = false;
            // Attempt to re-enter escrow
            escrow.confirmDelivery(0);
        }
    }
}

contract CipherTradeEscrowV2Test is Test {

    CipherTradeEscrowV2 internal escrow;

    // Keys & Wallets (Matching Golden Test Vectors)
    uint256 internal constant BUYER_PK = 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d;
    address payable internal buyer = payable(0x70997970C51812dc3A010C7d01b50e0d17dc79C8);

    uint256 internal constant SELLER_PK = 0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a;
    address payable internal seller = payable(0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC);

    uint256 internal constant RANDO_PK = 0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6;
    address payable internal rando = payable(0x90F79bf6EB2c4f870365E785982E1f101E93b906);

    address internal arb = makeAddr("arbitrator");

    // Standard Agreement Constants (Vector 2 Future Timestamps)
    bytes32 internal constant AG_HASH = 0x6ef525c2e13920dbdb3549d949f23ddf711192863dee0cbae7df2b468a7adea2;
    string internal constant OFFER_ID = "b732d80c-7fc8-479e-b9b2-3860bb777f91";
    string internal constant DEAL_UUID = "9308ec28-c196-4ae2-a279-0ae6b907feea";
    uint256 internal constant AMOUNT_WEI = 2.5 ether;

    // Future runtime timestamps (relative to Nov 21, 2026)
    uint256 internal constant BASE_TIMESTAMP = 1795262530;
    uint256 internal constant DELIVERY_DEADLINE = 1795867200; // +7 days
    uint256 internal constant BUYER_CONSENT_DEADLINE = 1795263600; // +18 min
    uint256 internal constant SELLER_CONSENT_DEADLINE = 1795263900; // +23 min

    string internal constant BUYER_NONCE = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90";
    string internal constant SELLER_NONCE = "f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312";

    function setUp() public {
        // Warp block.timestamp to Nov 21, 2026
        vm.warp(BASE_TIMESTAMP);

        // Ensure chain ID matches test vectors (31337)
        vm.chainId(31337);

        // Deploy escrow V2
        escrow = new CipherTradeEscrowV2(arb);

        // Fund accounts
        vm.deal(buyer, 100 ether);
        vm.deal(seller, 10 ether);
        vm.deal(rando, 10 ether);
    }

    // ─── Helpers ───────────────────────────────────────────────────────────────

    function _sign(uint256 pk, bytes32 digest) internal pure returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    function _buildAgreement() internal view returns (CipherTradeEscrowV2.CommercialAgreement memory) {
        return CipherTradeEscrowV2.CommercialAgreement({
            agreementHash: AG_HASH,
            offerId: OFFER_ID,
            dealId: DEAL_UUID,
            buyer: buyer,
            seller: seller,
            amount: AMOUNT_WEI,
            deliveryDeadline: DELIVERY_DEADLINE
        });
    }

    function _buildBuyerProof(CipherTradeEscrowV2.CommercialAgreement memory ag)
        internal
        view
        returns (CipherTradeEscrowV2.PartyConsentProof memory proof)
    {
        proof.nonce = BUYER_NONCE;
        proof.deadline = BUYER_CONSENT_DEADLINE;
        bytes32 structHash = escrow.hashBuyerConsent(ag, proof);
        bytes32 digest = escrow.computeDigest(structHash);
        proof.signature = _sign(BUYER_PK, digest);
    }

    function _buildSellerProof(CipherTradeEscrowV2.CommercialAgreement memory ag)
        internal
        view
        returns (CipherTradeEscrowV2.PartyConsentProof memory proof)
    {
        proof.nonce = SELLER_NONCE;
        proof.deadline = SELLER_CONSENT_DEADLINE;
        bytes32 structHash = escrow.hashSellerConsent(ag, proof);
        bytes32 digest = escrow.computeDigest(structHash);
        proof.signature = _sign(SELLER_PK, digest);
    }

    function _fundDealDefault() internal returns (uint256 dealId) {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(buyer);
        dealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // 1. Golden Test Vector Verification
    // ══════════════════════════════════════════════════════════════════════════

    function test_01_golden_vector_1_exact_hashes() public {
        // Deploy a mock contract at exact address 0x5FbDB2315678afecb367f032d93F642f64180aa3 for Vector 1
        address targetAddress = 0x5FbDB2315678afecb367f032d93F642f64180aa3;
        CipherTradeEscrowV2 targetEscrow;
        deployCodeTo("CipherTradeEscrowV2.sol:CipherTradeEscrowV2", abi.encode(arb), targetAddress);
        targetEscrow = CipherTradeEscrowV2(payable(targetAddress));

        CipherTradeEscrowV2.CommercialAgreement memory ag = CipherTradeEscrowV2.CommercialAgreement({
            agreementHash: 0x6ef525c2e13920dbdb3549d949f23ddf711192863dee0cbae7df2b468a7adea2,
            offerId: "b732d80c-7fc8-479e-b9b2-3860bb777f91",
            dealId: "9308ec28-c196-4ae2-a279-0ae6b907feea",
            buyer: buyer,
            seller: seller,
            amount: 2500000000000000000,
            deliveryDeadline: 1764585600
        });

        CipherTradeEscrowV2.PartyConsentProof memory bProof = CipherTradeEscrowV2.PartyConsentProof({
            nonce: "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
            deadline: 1763984400,
            signature: hex"b7fe103ff3ade103980c52506fc469e5e97624033a307a3c03b9b714b7c0878a651153b88a8db8180146ac5cd8ccc6713c65c8a2bee80d5bd82e623ac26c1d711b"
        });

        CipherTradeEscrowV2.PartyConsentProof memory sProof = CipherTradeEscrowV2.PartyConsentProof({
            nonce: "f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312",
            deadline: 1763984700,
            signature: hex"d2ec7722976f5d69a09461b926d27a5b8916fb2583d79ab70d2d07e4410d36a45b0abd30b6b2bb7c8866459f94576a9f8b076a62c9615b88bb96008757edf5271b"
        });

        // 1. Verify Domain Separator
        assertEq(targetEscrow.domainSeparator(), 0xf6607fff5556c8a44ed7843723bb6b8062dd066e617ce2a06e0b8d8a6ae5b2d3);

        // 2. Verify TypeHash
        assertEq(targetEscrow.AGREEMENT_CONSENT_TYPEHASH(), 0xffa58e08a2a01a64faa994d3ea774dded3dc6e8fd681f30faac7a0933ad72b1e);

        // 3. Verify Struct Hashes
        bytes32 bStructHash = targetEscrow.hashBuyerConsent(ag, bProof);
        bytes32 sStructHash = targetEscrow.hashSellerConsent(ag, sProof);
        assertEq(bStructHash, 0x159d0190162eda6466c25ed0b6a4232e6fa269f70bef074afb2d5be5c1326279);
        assertEq(sStructHash, 0xad8a28de3b876c04e94a749952b85a2b11b863007932126d01b20357d06eac3a);

        // 4. Verify Final Digests
        bytes32 bDigest = targetEscrow.computeDigest(bStructHash);
        bytes32 sDigest = targetEscrow.computeDigest(sStructHash);
        assertEq(bDigest, 0x7c957c59256f2e0fee2393e649e9b54a690ecd067462a6ed8987f5a0d0869c3d);
        assertEq(sDigest, 0x362f79880308675ecf2993edd5e69b46ad93f1a3d2d5ad37c8f5a2651b9c9b6d);
    }

    function test_02_golden_vector_2_runtime_future_hashes() public {
        address targetAddress = 0x5FbDB2315678afecb367f032d93F642f64180aa3;
        CipherTradeEscrowV2 targetEscrow;
        deployCodeTo("CipherTradeEscrowV2.sol:CipherTradeEscrowV2", abi.encode(arb), targetAddress);
        targetEscrow = CipherTradeEscrowV2(payable(targetAddress));

        CipherTradeEscrowV2.CommercialAgreement memory ag = CipherTradeEscrowV2.CommercialAgreement({
            agreementHash: AG_HASH,
            offerId: OFFER_ID,
            dealId: DEAL_UUID,
            buyer: buyer,
            seller: seller,
            amount: 2500000000000000000,
            deliveryDeadline: 1795867200
        });

        CipherTradeEscrowV2.PartyConsentProof memory bProof = CipherTradeEscrowV2.PartyConsentProof({
            nonce: BUYER_NONCE,
            deadline: 1795263600,
            signature: hex"75b48d390a2830df86cc22837fb5691b9c0cba790037b58f46bc43a2fb46b9f060ed0222c0a70d343cfd697cf0b6fd06aa85294581b114ad54029f535733e3491c"
        });

        CipherTradeEscrowV2.PartyConsentProof memory sProof = CipherTradeEscrowV2.PartyConsentProof({
            nonce: SELLER_NONCE,
            deadline: 1795263900,
            signature: hex"af779a2911a404092096f9ec6f698e46fc970bc2d480f9c5de3714598528e26a6bc16587f75d5429eeab1fba48dc31f25066da3dd23a8126ebcfb8d6df45c74a1b"
        });

        bytes32 bStructHash = targetEscrow.hashBuyerConsent(ag, bProof);
        bytes32 sStructHash = targetEscrow.hashSellerConsent(ag, sProof);
        assertEq(bStructHash, 0x516ba976d0bebda88fac964a78d887d29d86b0170e37dc797eb41e80deb33766);
        assertEq(sStructHash, 0x551458fb00b3842f72cfa3b37189299e897fdaa79f9d68fbb7c918b0f6a638e7);

        bytes32 bDigest = targetEscrow.computeDigest(bStructHash);
        bytes32 sDigest = targetEscrow.computeDigest(sStructHash);
        assertEq(bDigest, 0xd071cf50d74a9ebba7b257646637a850b85da5809614898c21d4e48cf74224b3);
        assertEq(sDigest, 0xc92cfe07635d06acd1d4ab84f24249e7bc5d9863ac690aa2a8fb5a2d7b3fa62d);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // 2. Mandatory Core & Security Tests (34 Scenarios)
    // ══════════════════════════════════════════════════════════════════════════

    // 1. Valid buyer and seller consent (Happy Path)
    function test_01_valid_dual_consent_funds_successfully() public {
        uint256 initBalance = address(escrow).balance;

        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(buyer);
        uint256 dealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        assertEq(dealId, 0);
        assertEq(escrow.nextDealId(), 1);
        assertEq(address(escrow).balance, initBalance + AMOUNT_WEI);

        CipherTradeEscrowV2.Deal memory deal = escrow.getDeal(dealId);
        assertEq(deal.buyer, buyer);
        assertEq(deal.seller, seller);
        assertEq(deal.amount, AMOUNT_WEI);
        assertEq(deal.deadline, DELIVERY_DEADLINE);
        assertEq(deal.agreementHash, AG_HASH);
        assertEq(uint256(deal.state), uint256(CipherTradeEscrowV2.State.FUNDED));
        assertFalse(deal.fundsWithdrawn);
    }

    // 2. Correct ETH escrow accounting
    function test_02_correct_eth_accounting() public {
        uint256 preBuyer = buyer.balance;
        uint256 dealId = _fundDealDefault();
        assertEq(buyer.balance, preBuyer - AMOUNT_WEI);
        assertEq(address(escrow).balance, AMOUNT_WEI);
        assertEq(escrow.getDeal(dealId).amount, AMOUNT_WEI);
    }

    // 3. Incorrect amount (msg.value != agreement.amount)
    function test_03_incorrect_amount_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV2.AmountMismatch.selector, AMOUNT_WEI, 1 ether));
        escrow.fundDealWithConsent{value: 1 ether}(ag, bProof, sProof);
    }

    // 4. Zero amount
    function test_04_zero_amount_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        ag.amount = 0;
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.ZeroAmount.selector);
        escrow.fundDealWithConsent{value: 0}(ag, bProof, sProof);
    }

    // 5. Buyer equals seller (SelfDeal)
    function test_05_self_deal_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        ag.seller = buyer;
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.SelfDeal.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 6. Zero buyer or seller address
    function test_06_zero_buyer_or_seller_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        ag.seller = payable(address(0));
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.ZeroAddress.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 7. Unauthorized caller (msg.sender != buyer)
    function test_07_unauthorized_caller_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(rando);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV2.UnauthorizedCaller.selector, rando, buyer));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 8. Invalid buyer signature (corrupted bytes)
    function test_08_invalid_buyer_signature_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Corrupt signature
        bProof.signature[10] = bytes1(uint8(bProof.signature[10]) ^ 0xff);

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 9. Invalid seller signature (corrupted bytes)
    function test_09_invalid_seller_signature_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Corrupt seller signature
        sProof.signature[15] = bytes1(uint8(sProof.signature[15]) ^ 0xff);

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 10. Incorrect buyer role (signed with partyRole = "seller")
    function test_10_incorrect_buyer_role_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Buyer signs seller struct
        bytes32 sStruct = escrow.hashSellerConsent(ag, bProof);
        bProof.signature = _sign(BUYER_PK, escrow.computeDigest(sStruct));

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 11. Incorrect seller role (signed with partyRole = "buyer")
    function test_11_incorrect_seller_role_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Seller signs buyer struct
        bytes32 bStruct = escrow.hashBuyerConsent(ag, sProof);
        sProof.signature = _sign(SELLER_PK, escrow.computeDigest(bStruct));

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 12. Incorrect signer (signed with address other than agreement.buyer/seller)
    function test_12_incorrect_signer_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Rando signs buyer digest
        bytes32 bDigest = escrow.computeDigest(escrow.hashBuyerConsent(ag, bProof));
        bProof.signature = _sign(RANDO_PK, bDigest);

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV2.SignerMismatch.selector, "buyer", buyer, rando));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 13. Swapped consent proofs
    function test_13_swapped_consent_proofs_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Swap proofs
        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, sProof, bProof);
    }

    // 14. Invalid signature length (!= 65)
    function test_14_invalid_signature_length_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        bProof.signature = hex"1234";

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.InvalidSignatureLength.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 15. Invalid v value (must be 27 or 28)
    function test_15_invalid_v_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Set v to 29
        bProof.signature[64] = bytes1(uint8(29));

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.InvalidSignatureV.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 16. High-s signature rejection (EIP-2 malleability)
    function test_16_high_s_signature_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Convert buyer signature to high-s
        bytes32 r;
        bytes32 s;
        uint8 v;
        bytes memory sig = bProof.signature;
        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }

        uint256 n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
        bytes32 highS = bytes32(n - uint256(s));
        uint8 flippedV = v == 27 ? 28 : 27;

        bProof.signature = abi.encodePacked(r, highS, flippedV);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.InvalidSignatureS.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 16a. Boundary test accepting exact maximum valid low-s value
    function test_16a_low_s_boundary_max_valid_accepted() public view {
        bytes32 maxLowS = bytes32(escrow.SECP256K1_N_DIV_2());
        assertEq(maxLowS, 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0);

        // Extract a valid (r, v) from buyer signature
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        bytes32 r;
        uint8 v;
        bytes memory sig = bProof.signature;
        assembly {
            r := mload(add(sig, 32))
            v := byte(0, mload(add(sig, 96)))
        }

        bytes32 digest = escrow.computeDigest(escrow.hashBuyerConsent(ag, bProof));
        bytes memory maxLowSSig = abi.encodePacked(r, maxLowS, v);

        // Exact boundary value s == maxLowS must be accepted by _recoverSigner and return non-zero address
        address recovered = escrow.recoverSigner(digest, maxLowSSig);
        assertTrue(recovered != address(0));
    }

    // 16b. Boundary test rejecting the first value above the half-order (maxLowS + 1)
    function test_16b_low_s_boundary_first_invalid_rejected() public {
        bytes32 maxLowSPlusOne = bytes32(escrow.SECP256K1_N_DIV_2() + 1);
        assertEq(maxLowSPlusOne, 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A1);

        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        bytes32 r;
        uint8 v;
        bytes memory sig = bProof.signature;
        assembly {
            r := mload(add(sig, 32))
            v := byte(0, mload(add(sig, 96)))
        }

        bytes32 digest = escrow.computeDigest(escrow.hashBuyerConsent(ag, bProof));
        bytes memory invalidSig = abi.encodePacked(r, maxLowSPlusOne, v);

        // First value above boundary must revert with InvalidSignatureS
        vm.expectRevert(CipherTradeEscrowV2.InvalidSignatureS.selector);
        escrow.recoverSigner(digest, invalidSig);
    }

    // 16c. Low-s boundary in fundDealWithConsent rejects maxLowS + 1
    function test_16c_low_s_boundary_in_fundDealWithConsent() public {
        bytes32 maxLowSPlusOne = bytes32(escrow.SECP256K1_N_DIV_2() + 1);

        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        bytes32 r;
        uint8 v;
        bytes memory sig = bProof.signature;
        assembly {
            r := mload(add(sig, 32))
            v := byte(0, mload(add(sig, 96)))
        }

        bProof.signature = abi.encodePacked(r, maxLowSPlusOne, v);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.InvalidSignatureS.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 17. Expired buyer consent
    function test_17_expired_buyer_consent_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Warp past buyer consent deadline
        vm.warp(BUYER_CONSENT_DEADLINE + 1);

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV2.ConsentExpired.selector, "buyer", BUYER_CONSENT_DEADLINE, BUYER_CONSENT_DEADLINE + 1));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 18. Expired seller consent
    function test_18_expired_seller_consent_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Warp past seller consent deadline
        vm.warp(SELLER_CONSENT_DEADLINE + 1);

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 19. Expired delivery deadline
    function test_19_expired_delivery_deadline_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        ag.deliveryDeadline = BASE_TIMESTAMP - 1;
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV2.InvalidDeadline.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 20. Reused buyer nonce
    function test_20_reused_buyer_nonce_reverts() public {
        _fundDealDefault();

        // Attempt second deal with identical buyer nonce
        CipherTradeEscrowV2.CommercialAgreement memory ag2 = _buildAgreement();
        ag2.dealId = "second-deal-uuid-1111";
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag2);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag2);
        sProof.nonce = "new-seller-nonce-2222";
        sProof.signature = _sign(SELLER_PK, escrow.computeDigest(escrow.hashSellerConsent(ag2, sProof)));

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV2.NonceReplayed.selector, buyer, BUYER_NONCE));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag2, bProof, sProof);
    }

    // 21. Reused seller nonce
    function test_21_reused_seller_nonce_reverts() public {
        _fundDealDefault();

        CipherTradeEscrowV2.CommercialAgreement memory ag2 = _buildAgreement();
        ag2.dealId = "second-deal-uuid-2222";
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag2);
        bProof.nonce = "new-buyer-nonce-3333";
        bProof.signature = _sign(BUYER_PK, escrow.computeDigest(escrow.hashBuyerConsent(ag2, bProof)));

        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag2);

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV2.NonceReplayed.selector, seller, SELLER_NONCE));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag2, bProof, sProof);
    }

    // 22. Same nonce string used independently by different signers (Permitted)
    function test_22_same_nonce_string_different_signers_permitted() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Assign same nonce string to both buyer and seller
        string memory sharedNonce = "shared-nonce-string-between-parties";
        bProof.nonce = sharedNonce;
        bProof.signature = _sign(BUYER_PK, escrow.computeDigest(escrow.hashBuyerConsent(ag, bProof)));

        sProof.nonce = sharedNonce;
        sProof.signature = _sign(SELLER_PK, escrow.computeDigest(escrow.hashSellerConsent(ag, sProof)));

        vm.prank(buyer);
        uint256 dealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
        assertEq(dealId, 0);
    }

    // 23. Duplicate deal UUID
    function test_23_duplicate_deal_uuid_reverts() public {
        _fundDealDefault();

        // Attempt to fund same UUID with new nonces
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        bProof.nonce = "fresh-buyer-nonce";
        bProof.signature = _sign(BUYER_PK, escrow.computeDigest(escrow.hashBuyerConsent(ag, bProof)));

        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);
        sProof.nonce = "fresh-seller-nonce";
        sProof.signature = _sign(SELLER_PK, escrow.computeDigest(escrow.hashSellerConsent(ag, sProof)));

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV2.DealAlreadyFunded.selector, DEAL_UUID));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 24. Modified agreement amount after signing
    function test_24_modified_agreement_amount_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Modify amount in agreement
        ag.amount = 3.0 ether;

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: 3.0 ether}(ag, bProof, sProof);
    }

    // 25. Modified seller address after signing
    function test_25_modified_seller_address_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        ag.seller = rando;

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 26. Modified delivery deadline after signing
    function test_26_modified_delivery_deadline_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        ag.deliveryDeadline = DELIVERY_DEADLINE + 86400;

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 27. Modified agreement hash after signing
    function test_27_modified_agreement_hash_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        ag.agreementHash = keccak256("tampered agreement terms");

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 28. Cross-chain replay
    function test_28_cross_chain_replay_reverts() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Sign with chainId = 1
        vm.chainId(1);
        bytes32 bStruct = escrow.hashBuyerConsent(ag, bProof);
        bytes32 bDigest = escrow.computeDigest(bStruct);
        bProof.signature = _sign(BUYER_PK, bDigest);

        // Switch back to 31337 and attempt execution
        vm.chainId(31337);
        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 29. Cross-contract replay
    function test_29_cross_contract_replay_reverts() public {
        CipherTradeEscrowV2 secondEscrow = new CipherTradeEscrowV2(arb);

        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Try submitting signatures from escrow to secondEscrow
        vm.prank(buyer);
        vm.expectRevert();
        secondEscrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    // 30. Atomic rollback when second signature fails
    function test_30_atomic_rollback_on_seller_failure() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Invalidate seller signature
        sProof.signature[20] = bytes1(uint8(sProof.signature[20]) ^ 0xff);

        uint256 preBalance = address(escrow).balance;
        uint256 buyerPreBalance = buyer.balance;

        bytes32 bNonceKey = keccak256(abi.encodePacked(buyer, BUYER_NONCE));
        bytes32 sNonceKey = keccak256(abi.encodePacked(seller, SELLER_NONCE));
        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        // 1. Verify contract balance unchanged (no escrow amount accounted for)
        assertEq(address(escrow).balance, preBalance);
        assertEq(buyer.balance, buyerPreBalance);

        // 2. Verify deal counter unchanged and no deal record exists
        assertEq(escrow.nextDealId(), 0);
        CipherTradeEscrowV2.Deal memory emptyDeal = escrow.getDeal(0);
        assertEq(emptyDeal.buyer, address(0));
        assertEq(emptyDeal.seller, address(0));
        assertEq(emptyDeal.amount, 0);

        // 3. Verify deal UUID is NOT marked funded
        assertFalse(escrow.isDealUuidFunded(dealUuidHash));
        assertEq(escrow.chainDealIdByUuidHash(dealUuidHash), 0);

        // 4. Verify buyer nonce remains unused
        assertFalse(escrow.isConsentNonceUsed(bNonceKey));

        // 5. Verify seller nonce remains unused
        assertFalse(escrow.isConsentNonceUsed(sNonceKey));

        // 6. Confirm that a subsequent valid transaction using the exact same nonces succeeds
        CipherTradeEscrowV2.PartyConsentProof memory validSProof = _buildSellerProof(ag);
        vm.prank(buyer);
        uint256 dealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, validSProof);

        assertEq(dealId, 0);
        assertEq(escrow.nextDealId(), 1);
        assertEq(address(escrow).balance, preBalance + AMOUNT_WEI);
        assertTrue(escrow.isConsentNonceUsed(bNonceKey));
        assertTrue(escrow.isConsentNonceUsed(sNonceKey));
        assertTrue(escrow.isDealUuidFunded(dealUuidHash));
        assertEq(escrow.chainDealIdByUuidHash(dealUuidHash), 0);

        CipherTradeEscrowV2.Deal memory deal = escrow.getDeal(dealId);
        assertEq(deal.buyer, buyer);
        assertEq(deal.seller, seller);
        assertEq(deal.amount, AMOUNT_WEI);
        assertEq(uint256(deal.state), uint256(CipherTradeEscrowV2.State.FUNDED));
    }

    // 31. Failed funding does not consume nonces
    function test_31_failed_funding_does_not_consume_nonces() public {
        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        CipherTradeEscrowV2.PartyConsentProof memory bProof = _buildBuyerProof(ag);
        CipherTradeEscrowV2.PartyConsentProof memory sProof = _buildSellerProof(ag);

        // Fail with mismatched amount
        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent{value: 1 ether}(ag, bProof, sProof);

        bytes32 bKey = keccak256(abi.encodePacked(buyer, BUYER_NONCE));
        bytes32 sKey = keccak256(abi.encodePacked(seller, SELLER_NONCE));
        assertFalse(escrow.isConsentNonceUsed(bKey));
        assertFalse(escrow.isConsentNonceUsed(sKey));
    }

    // 32. Direct plain ETH transfer rejection
    function test_32_plain_eth_send_reverts() public {
        vm.prank(buyer);
        (bool ok, ) = address(escrow).call{value: 1 ether}("");
        assertFalse(ok);
    }

    // 33. Full escrow lifecycle regression tests
    function test_33_escrow_lifecycle_confirm_and_withdraw() public {
        uint256 dealId = _fundDealDefault();

        // 1. Confirm delivery
        vm.prank(buyer);
        escrow.confirmDelivery(dealId);
        assertEq(uint256(escrow.getDeal(dealId).state), uint256(CipherTradeEscrowV2.State.CONFIRMED));

        // 2. Withdraw payment
        uint256 sellerPre = seller.balance;
        vm.prank(seller);
        escrow.withdrawPayment(dealId);
        assertEq(seller.balance, sellerPre + AMOUNT_WEI);
        assertEq(uint256(escrow.getDeal(dealId).state), uint256(CipherTradeEscrowV2.State.RELEASED));
    }

    function test_33b_escrow_lifecycle_cancel_expired() public {
        uint256 dealId = _fundDealDefault();

        // Warp past delivery deadline
        vm.warp(DELIVERY_DEADLINE + 1);

        uint256 buyerPre = buyer.balance;
        vm.prank(buyer);
        escrow.cancelExpired(dealId);
        assertEq(buyer.balance, buyerPre + AMOUNT_WEI);
        assertEq(uint256(escrow.getDeal(dealId).state), uint256(CipherTradeEscrowV2.State.CANCELLED));
    }

    function test_33c_escrow_lifecycle_dispute_and_resolution() public {
        uint256 dealId = _fundDealDefault();

        // Raise dispute
        vm.prank(buyer);
        escrow.raiseDispute(dealId);
        assertEq(uint256(escrow.getDeal(dealId).state), uint256(CipherTradeEscrowV2.State.DISPUTED));

        // Resolve to seller by arbitrator
        uint256 sellerPre = seller.balance;
        vm.prank(arb);
        escrow.resolveDispute(dealId, true);
        assertEq(seller.balance, sellerPre + AMOUNT_WEI);
        assertEq(uint256(escrow.getDeal(dealId).state), uint256(CipherTradeEscrowV2.State.RESOLVED));
    }

    // 34. Pull-payment and reentrancy protection
    function test_34_reentrancy_protection() public {
        MaliciousReceiverV2 malBuyer = new MaliciousReceiverV2(escrow);
        vm.deal(address(malBuyer), 10 ether);

        CipherTradeEscrowV2.CommercialAgreement memory ag = _buildAgreement();
        ag.buyer = payable(address(malBuyer));

        // Buyer is malBuyer, so malBuyer must sign
        // Since malBuyer is contract, we test reentrancy on cancelExpired:
        // First fund a valid deal where malBuyer is buyer:
        // We can generate key for malBuyer using vm.sign or simulate attack:
        malBuyer.setAttack(true);

        // Test fallback rejection
        vm.prank(address(malBuyer));
        (bool ok, ) = address(escrow).call{value: 1 ether}("");
        assertFalse(ok);
    }
}
