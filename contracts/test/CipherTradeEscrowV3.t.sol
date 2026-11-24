// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {CipherTradeEscrowV3} from "../src/CipherTradeEscrowV3.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

// ─── Test Mocks ───────────────────────────────────────────────────────────────

contract MockERC20 is ERC20 {
    constructor(string memory name, string memory symbol) ERC20(name, symbol) {
        _mint(msg.sender, 1_000_000 * 1e18);
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract MockUSDC is ERC20 {
    constructor() ERC20("USD Coin", "USDC") {
        _mint(msg.sender, 1_000_000 * 1e6);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract MockUSDT {
    string public name = "Tether USD";
    string public symbol = "USDT";
    uint8 public decimals = 6;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor() {
        balanceOf[msg.sender] = 1_000_000 * 1e6;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function transfer(address to, uint256 amount) external {
        require(balanceOf[msg.sender] >= amount, "Insufficient balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
    }

    function transferFrom(address from, address to, uint256 amount) external {
        require(balanceOf[from] >= amount, "Insufficient balance");
        require(allowance[from][msg.sender] >= amount, "Insufficient allowance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external {
        require(amount == 0 || allowance[msg.sender][spender] == 0, "Reset allowance to 0 first");
        allowance[msg.sender][spender] = amount;
    }
}

contract FeeOnTransferToken is ERC20 {
    uint256 public feeBps = 500; // 5% fee on transfer

    constructor() ERC20("Fee Token", "FEE") {
        _mint(msg.sender, 1_000_000 * 1e18);
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        uint256 fee = (amount * feeBps) / 10000;
        uint256 net = amount - fee;
        _spendAllowance(from, msg.sender, amount);
        _transfer(from, address(0xdead), fee);
        _transfer(from, to, net);
        return true;
    }
}

contract FailingTransferToken is ERC20 {
    bool public shouldFailTransfer;

    constructor() ERC20("Failing Token", "FAIL") {
        _mint(msg.sender, 1_000_000 * 1e18);
    }

    function setFail(bool _fail) external {
        shouldFailTransfer = _fail;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        if (shouldFailTransfer) {
            return false;
        }
        return super.transfer(to, amount);
    }
}

contract MaliciousReceiverV3 {
    CipherTradeEscrowV3 public escrow;
    bool public attack;

    constructor(CipherTradeEscrowV3 _escrow) {
        escrow = _escrow;
    }

    function setAttack(bool _attack) external {
        attack = _attack;
    }

    receive() external payable {
        if (attack) {
            attack = false;
            // Attempt to re-enter escrow
            escrow.confirmDelivery(1);
        }
    }
}

// ─── Main Test Suite ──────────────────────────────────────────────────────────

contract CipherTradeEscrowV3Test is Test {
    CipherTradeEscrowV3 internal escrow;

    MockUSDC internal usdc;
    MockUSDT internal usdt;
    FeeOnTransferToken internal feeToken;

    // Keys & Wallets
    uint256 internal constant BUYER_PK = 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d;
    address internal buyer = 0x70997970C51812dc3A010C7d01b50e0d17dc79C8;

    uint256 internal constant SELLER_PK = 0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a;
    address internal seller = 0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC;

    uint256 internal constant RANDO_PK = 0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6;
    address internal rando = 0x90F79bf6EB2c4f870365E785982E1f101E93b906;

    address internal ownerAddr = makeAddr("owner");
    address internal arb = makeAddr("arbitrator");
    address internal treasuryAddr = makeAddr("treasury");

    // Standard Agreement Constants
    string internal constant OFFER_ID = "b732d80c-7fc8-479e-b9b2-3860bb777f91";
    string internal constant DEAL_UUID = "9308ec28-c196-4ae2-a279-0ae6b907feea";
    uint256 internal constant AMOUNT_WEI = 2.5 ether;
    uint256 internal constant AMOUNT_USDC = 2500 * 1e6; // 2,500 USDC
    uint256 internal constant FEE_BPS = 100; // 1.00%

    uint256 internal constant BASE_TIMESTAMP = 1795262530;
    uint256 internal constant DELIVERY_DEADLINE = 1795867200; // +7 days
    uint256 internal constant BUYER_CONSENT_DEADLINE = 1795263600; // +18 min
    uint256 internal constant SELLER_CONSENT_DEADLINE = 1795263900; // +23 min

    string internal constant BUYER_NONCE = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90";
    string internal constant SELLER_NONCE = "f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312f9e8d7c6b5a40312";

    function setUp() public {
        vm.warp(BASE_TIMESTAMP);
        vm.chainId(31337);

        escrow = new CipherTradeEscrowV3(ownerAddr, arb, treasuryAddr, FEE_BPS);

        usdc = new MockUSDC();
        usdt = new MockUSDT();
        feeToken = new FeeOnTransferToken();

        // Whitelist USDC and USDT from owner
        vm.startPrank(ownerAddr);
        escrow.setTokenAllowed(address(usdc), true);
        escrow.setTokenAllowed(address(usdt), true);
        vm.stopPrank();

        // Fund accounts
        vm.deal(buyer, 100 ether);
        vm.deal(seller, 10 ether);
        vm.deal(rando, 10 ether);

        usdc.mint(buyer, 100_000 * 1e6);
        usdt.mint(buyer, 100_000 * 1e6);
        feeToken.mint(buyer, 100_000 * 1e18);

        vm.startPrank(buyer);
        usdc.approve(address(escrow), type(uint256).max);
        usdt.approve(address(escrow), type(uint256).max);
        feeToken.approve(address(escrow), type(uint256).max);
        vm.stopPrank();
    }

    // ─── Signature Helpers ────────────────────────────────────────────────────

    function _sign(uint256 pk, bytes32 digest) internal pure returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    function _buildAgreement(
        address token,
        uint256 amount,
        uint256 version,
        bytes32 prevHash,
        uint256 deadline
    ) internal view returns (CipherTradeEscrowV3.CommercialAgreementV3 memory ag) {
        ag.dealId = DEAL_UUID;
        ag.agreementVersion = version;
        ag.previousAgreementHash = prevHash;
        ag.offerId = OFFER_ID;
        ag.buyer = buyer;
        ag.seller = seller;
        ag.token = token;
        ag.amount = amount;
        ag.feeBps = FEE_BPS;
        ag.treasury = treasuryAddr;
        ag.deliveryDeadline = deadline;
        ag.agreementHash = escrow.computeCanonicalAgreementHash(
            ag.dealId,
            ag.agreementVersion,
            ag.previousAgreementHash,
            ag.offerId,
            ag.buyer,
            ag.seller,
            ag.token,
            ag.amount,
            ag.feeBps,
            ag.treasury,
            ag.deliveryDeadline
        );
    }

    function _buildBuyerProof(CipherTradeEscrowV3.CommercialAgreementV3 memory ag, string memory nonce)
        internal
        view
        returns (CipherTradeEscrowV3.PartyConsentProofV3 memory proof)
    {
        proof.nonce = nonce;
        proof.deadline = BUYER_CONSENT_DEADLINE;
        bytes32 structHash = escrow.hashAgreementConsent(ag, proof, "buyer", buyer);
        bytes32 digest = escrow.computeDigest(structHash);
        proof.signature = _sign(BUYER_PK, digest);
    }

    function _buildSellerProof(CipherTradeEscrowV3.CommercialAgreementV3 memory ag, string memory nonce)
        internal
        view
        returns (CipherTradeEscrowV3.PartyConsentProofV3 memory proof)
    {
        proof.nonce = nonce;
        proof.deadline = SELLER_CONSENT_DEADLINE;
        bytes32 structHash = escrow.hashAgreementConsent(ag, proof, "seller", seller);
        bytes32 digest = escrow.computeDigest(structHash);
        proof.signature = _sign(SELLER_PK, digest);
    }

    function _buildDeadlineProof(
        uint256 chainDealId,
        bytes32 dealUuidHash,
        bytes32 agHash,
        uint256 currentDeadline,
        uint256 newDeadline,
        string memory role,
        address signer,
        uint256 pk,
        string memory nonce
    ) internal view returns (CipherTradeEscrowV3.PartyConsentProofV3 memory proof) {
        proof.nonce = nonce;
        proof.deadline = BUYER_CONSENT_DEADLINE;
        bytes32 structHash = escrow.hashDeadlineExtension(
            chainDealId,
            dealUuidHash,
            agHash,
            buyer,
            seller,
            currentDeadline,
            newDeadline,
            role,
            signer,
            proof
        );
        bytes32 digest = escrow.computeDigest(structHash);
        proof.signature = _sign(pk, digest);
    }

    // ─── Tests: EIP-712 Domain & Canonical Hashing ─────────────────────────────

    function test_01_domain_separator_and_version_3() public view {
        bytes32 domainSep = escrow.domainSeparator();
        bytes32 expectedDomain = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256(bytes("CipherTrade")),
                keccak256(bytes("3")),
                block.chainid,
                address(escrow)
            )
        );
        assertEq(domainSep, expectedDomain, "V3 domain separator mismatch");
    }

    function test_02_canonical_agreement_hash_determinism() public view {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        bytes32 computed = escrow.computeCanonicalAgreementHash(
            ag.dealId,
            ag.agreementVersion,
            ag.previousAgreementHash,
            ag.offerId,
            ag.buyer,
            ag.seller,
            ag.token,
            ag.amount,
            ag.feeBps,
            ag.treasury,
            ag.deliveryDeadline
        );
        assertEq(ag.agreementHash, computed, "Canonical agreement hash must be deterministic");

        // Changing any term alters the hash
        bytes32 alteredHash = escrow.computeCanonicalAgreementHash(
            ag.dealId,
            ag.agreementVersion,
            ag.previousAgreementHash,
            ag.offerId,
            ag.buyer,
            ag.seller,
            ag.token,
            ag.amount + 1, // altered amount
            ag.feeBps,
            ag.treasury,
            ag.deliveryDeadline
        );
        assertTrue(ag.agreementHash != alteredHash, "Altering amount must alter agreement hash");
    }

    // ─── Tests: Path A Direct Funding ─────────────────────────────────────────

    function test_03_direct_funding_eth_success() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        uint256 contractBalBefore = address(escrow).balance;
        uint256 buyerBalBefore = buyer.balance;

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        assertEq(chainDealId, 1, "First dealId must be 1");
        assertEq(address(escrow).balance, contractBalBefore + AMOUNT_WEI, "Escrow ETH balance mismatch");
        assertEq(buyer.balance, buyerBalBefore - AMOUNT_WEI, "Buyer ETH balance mismatch");

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(deal.buyer, buyer);
        assertEq(deal.seller, seller);
        assertEq(deal.token, address(0));
        assertEq(deal.amount, AMOUNT_WEI);
        assertEq(deal.feeBps, FEE_BPS);
        assertEq(deal.treasury, treasuryAddr);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.FUNDED));
        assertFalse(deal.fundsWithdrawn);
    }

    function test_04_direct_funding_usdc_6_decimals_success() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        uint256 escrowBefore = usdc.balanceOf(address(escrow));
        uint256 buyerBefore = usdc.balanceOf(buyer);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        assertEq(chainDealId, 1);
        assertEq(usdc.balanceOf(address(escrow)), escrowBefore + AMOUNT_USDC);
        assertEq(usdc.balanceOf(buyer), buyerBefore - AMOUNT_USDC);

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(deal.token, address(usdc));
        assertEq(deal.amount, AMOUNT_USDC);
    }

    function test_05_direct_funding_usdt_void_return_success() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdt),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        uint256 escrowBefore = usdt.balanceOf(address(escrow));
        uint256 buyerBefore = usdt.balanceOf(buyer);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        assertEq(chainDealId, 1);
        assertEq(usdt.balanceOf(address(escrow)), escrowBefore + AMOUNT_USDC);
        assertEq(usdt.balanceOf(buyer), buyerBefore - AMOUNT_USDC);
    }

    function test_06_fee_on_transfer_token_reverts() public {
        vm.prank(ownerAddr);
        escrow.setTokenAllowed(address(feeToken), true);

        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(feeToken),
            1000 * 1e18,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrowV3.TransferAmountMismatch.selector,
                1000 * 1e18,
                950 * 1e18
            )
        );
        escrow.fundDealWithConsent(ag, bProof, sProof);
    }

    function test_07_eth_amount_mismatch_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.AmountMismatch.selector, AMOUNT_WEI, 1 ether));
        escrow.fundDealWithConsent{value: 1 ether}(ag, bProof, sProof);
    }

    function test_08_erc20_with_native_value_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.NativeValueNotAllowed.selector);
        escrow.fundDealWithConsent{value: 0.1 ether}(ag, bProof, sProof);
    }

    function test_09_unapproved_token_reverts() public {
        MockERC20 unapproved = new MockERC20("Random Token", "RND");
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(unapproved),
            1000 * 1e18,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.TokenNotAllowed.selector, address(unapproved)));
        escrow.fundDealWithConsent(ag, bProof, sProof);
    }

    function test_10_unauthorized_payer_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(rando);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.UnauthorizedCaller.selector, rando, buyer));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    function test_11_signature_tampering_and_mismatches() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        // Signer tampering: sign with rando PK instead of buyer PK
        bytes32 structHash = escrow.hashAgreementConsent(ag, bProof, "buyer", buyer);
        bytes32 digest = escrow.computeDigest(structHash);
        bProof.signature = _sign(RANDO_PK, digest);

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.SignerMismatch.selector, "buyer", buyer, rando));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    function test_12_low_s_malleability_protection() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        // Craft high-s signature
        bytes32 structHash = escrow.hashAgreementConsent(ag, bProof, "buyer", buyer);
        bytes32 digest = escrow.computeDigest(structHash);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(BUYER_PK, digest);

        uint256 secp256k1N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
        bytes32 highS = bytes32(secp256k1N - uint256(s));
        uint8 flippedV = (v == 27) ? 28 : 27;
        bProof.signature = abi.encodePacked(r, highS, flippedV);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.InvalidSignatureS.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    function test_13_expired_consent_deadline_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        // Warp past consent deadline
        vm.warp(BUYER_CONSENT_DEADLINE + 1);

        vm.prank(buyer);
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrowV3.ConsentExpired.selector,
                "buyer",
                BUYER_CONSENT_DEADLINE,
                BUYER_CONSENT_DEADLINE + 1
            )
        );
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    function test_14_invalid_delivery_deadline_reverts() public {
        // Delivery deadline in past
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            BASE_TIMESTAMP - 1
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.InvalidDeadline.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    function test_15_self_deal_and_zero_address_reverts() public {
        // Self deal
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        ag.seller = buyer;
        ag.agreementHash = escrow.computeCanonicalAgreementHash(
            ag.dealId,
            ag.agreementVersion,
            ag.previousAgreementHash,
            ag.offerId,
            ag.buyer,
            ag.seller,
            ag.token,
            ag.amount,
            ag.feeBps,
            ag.treasury,
            ag.deliveryDeadline
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.SelfDeal.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);
    }

    function test_16_nonce_replay_protection() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        // Attempt funding another deal reusing buyer nonce
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag2 = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        ag2.dealId = "different-deal-uuid-12345";
        ag2.agreementHash = escrow.computeCanonicalAgreementHash(
            ag2.dealId,
            ag2.agreementVersion,
            ag2.previousAgreementHash,
            ag2.offerId,
            ag2.buyer,
            ag2.seller,
            ag2.token,
            ag2.amount,
            ag2.feeBps,
            ag2.treasury,
            ag2.deliveryDeadline
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof2 = _buildBuyerProof(ag2, BUYER_NONCE); // Reused nonce
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof2 = _buildSellerProof(ag2, "new-seller-nonce-9999");

        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.NonceReplayed.selector, buyer, BUYER_NONCE));
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag2, bProof2, sProof2);
    }

    function test_17_duplicate_deal_uuid_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        // Attempt funding again with same dealId
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof2 = _buildBuyerProof(ag, "another-buyer-nonce");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof2 = _buildSellerProof(ag, "another-seller-nonce");

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.DealAlreadyFunded.selector);
        escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof2, sProof2);
    }

    // ─── Tests: Path B Pre-Funding Registration & Amendments ──────────────────

    function test_18_path_b_registration_and_amendments() public {
        // 1. Initial agreement v1
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV1 = _buildAgreement(
            address(0),
            1 ether,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof1 = _buildBuyerProof(agV1, "nonce-b-1");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof1 = _buildSellerProof(agV1, "nonce-s-1");

        escrow.registerInitialAgreement(agV1, bProof1, sProof1);

        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));
        CipherTradeEscrowV3.AuthorizedAgreement memory reg1 = escrow.getAuthorizedAgreement(dealUuidHash);
        assertEq(reg1.version, 1);
        assertEq(reg1.agreementHash, agV1.agreementHash);
        assertEq(reg1.amount, 1 ether);

        // 2. Amendment v2 (change to USDC 1500)
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV2 = _buildAgreement(
            address(usdc),
            1500 * 1e6,
            2,
            agV1.agreementHash,
            DELIVERY_DEADLINE + 86400
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof2 = _buildBuyerProof(agV2, "nonce-b-2");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof2 = _buildSellerProof(agV2, "nonce-s-2");

        escrow.registerAgreementAmendment(agV2, bProof2, sProof2);

        CipherTradeEscrowV3.AuthorizedAgreement memory reg2 = escrow.getAuthorizedAgreement(dealUuidHash);
        assertEq(reg2.version, 2);
        assertEq(reg2.agreementHash, agV2.agreementHash);
        assertEq(reg2.previousAgreementHash, agV1.agreementHash);
        assertEq(reg2.token, address(usdc));
        assertEq(reg2.amount, 1500 * 1e6);
        assertEq(reg2.deliveryDeadline, DELIVERY_DEADLINE + 86400);

        // 3. Amendment v3 (change amount to USDC 2000)
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV3 = _buildAgreement(
            address(usdc),
            2000 * 1e6,
            3,
            agV2.agreementHash,
            DELIVERY_DEADLINE + 86400
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof3 = _buildBuyerProof(agV3, "nonce-b-3");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof3 = _buildSellerProof(agV3, "nonce-s-3");

        escrow.registerAgreementAmendment(agV3, bProof3, sProof3);

        CipherTradeEscrowV3.AuthorizedAgreement memory reg3 = escrow.getAuthorizedAgreement(dealUuidHash);
        assertEq(reg3.version, 3);
        assertEq(reg3.amount, 2000 * 1e6);

        // 4. Fund registered deal (Path B) - no signature reuse!
        uint256 escrowBefore = usdc.balanceOf(address(escrow));
        uint256 buyerBefore = usdc.balanceOf(buyer);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundRegisteredDeal(dealUuidHash);

        assertEq(chainDealId, 1);
        assertEq(usdc.balanceOf(address(escrow)), escrowBefore + 2000 * 1e6);
        assertEq(usdc.balanceOf(buyer), buyerBefore - 2000 * 1e6);

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(deal.amount, 2000 * 1e6);
        assertEq(deal.token, address(usdc));
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.FUNDED));
    }

    function test_19_path_b_invalid_amendment_chains_revert() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV1 = _buildAgreement(
            address(0),
            1 ether,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof1 = _buildBuyerProof(agV1, "nonce-b-1");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof1 = _buildSellerProof(agV1, "nonce-s-1");
        escrow.registerInitialAgreement(agV1, bProof1, sProof1);

        // Try skipping version 2 directly to 3
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV3 = _buildAgreement(
            address(0),
            1.5 ether,
            3, // Invalid: should be 2
            agV1.agreementHash,
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof3 = _buildBuyerProof(agV3, "nonce-b-3");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof3 = _buildSellerProof(agV3, "nonce-s-3");

        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.InvalidAmendmentVersion.selector, 3, 2));
        escrow.registerAgreementAmendment(agV3, bProof3, sProof3);

        // Try wrong previousAgreementHash
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV2WrongHash = _buildAgreement(
            address(0),
            1.5 ether,
            2,
            keccak256("wrong-prev-hash"),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof2W = _buildBuyerProof(agV2WrongHash, "nonce-b-2w");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof2W = _buildSellerProof(agV2WrongHash, "nonce-s-2w");

        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrowV3.PreviousAgreementHashMismatch.selector,
                keccak256("wrong-prev-hash"),
                agV1.agreementHash
            )
        );
        escrow.registerAgreementAmendment(agV2WrongHash, bProof2W, sProof2W);
    }

    function test_20_path_b_party_identity_immutability() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV1 = _buildAgreement(
            address(0),
            1 ether,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof1 = _buildBuyerProof(agV1, "nonce-b-1");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof1 = _buildSellerProof(agV1, "nonce-s-1");
        escrow.registerInitialAgreement(agV1, bProof1, sProof1);

        // Attempt altering buyer in amendment
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV2BadBuyer = _buildAgreement(
            address(0),
            1 ether,
            2,
            agV1.agreementHash,
            DELIVERY_DEADLINE
        );
        agV2BadBuyer.buyer = rando;
        agV2BadBuyer.agreementHash = escrow.computeCanonicalAgreementHash(
            agV2BadBuyer.dealId,
            agV2BadBuyer.agreementVersion,
            agV2BadBuyer.previousAgreementHash,
            agV2BadBuyer.offerId,
            agV2BadBuyer.buyer,
            agV2BadBuyer.seller,
            agV2BadBuyer.token,
            agV2BadBuyer.amount,
            agV2BadBuyer.feeBps,
            agV2BadBuyer.treasury,
            agV2BadBuyer.deliveryDeadline
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof2 = _buildBuyerProof(agV2BadBuyer, "nonce-b-2");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof2 = _buildSellerProof(agV2BadBuyer, "nonce-s-2");

        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.BuyerImmutable.selector, buyer, rando));
        escrow.registerAgreementAmendment(agV2BadBuyer, bProof2, sProof2);
    }

    function test_21_path_b_conflict_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV1 = _buildAgreement(
            address(0),
            1 ether,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof1 = _buildBuyerProof(agV1, "nonce-b-1");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof1 = _buildSellerProof(agV1, "nonce-s-1");
        escrow.registerInitialAgreement(agV1, bProof1, sProof1);

        // Calling fundDealWithConsent after registration reverts AgreementAlreadyRegistered
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof2 = _buildBuyerProof(agV1, "nonce-b-2");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof2 = _buildSellerProof(agV1, "nonce-s-2");

        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.AgreementAlreadyRegistered.selector);
        escrow.fundDealWithConsent{value: 1 ether}(agV1, bProof2, sProof2);

        // Fund registered deal
        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));
        vm.prank(buyer);
        escrow.fundRegisteredDeal{value: 1 ether}(dealUuidHash);

        // Registering amendment after funding reverts DealAlreadyFunded
        CipherTradeEscrowV3.CommercialAgreementV3 memory agV2 = _buildAgreement(
            address(0),
            2 ether,
            2,
            agV1.agreementHash,
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof3 = _buildBuyerProof(agV2, "nonce-b-3");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof3 = _buildSellerProof(agV2, "nonce-s-3");

        vm.expectRevert(CipherTradeEscrowV3.DealAlreadyFunded.selector);
        escrow.registerAgreementAmendment(agV2, bProof3, sProof3);
    }

    // ─── Tests: Post-Funding Deadline Extension ────────────────────────────────

    function test_22_post_funding_deadline_extension_success() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        uint256 newDeadline = DELIVERY_DEADLINE + 86400 * 3; // +3 days
        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));

        CipherTradeEscrowV3.PartyConsentProofV3 memory bExtProof = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            newDeadline,
            "buyer",
            buyer,
            BUYER_PK,
            "ext-nonce-buyer-1"
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory sExtProof = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            newDeadline,
            "seller",
            seller,
            SELLER_PK,
            "ext-nonce-seller-1"
        );

        escrow.extendDeliveryDeadline(chainDealId, newDeadline, bExtProof, sExtProof);

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(deal.deadline, newDeadline, "Delivery deadline must be updated");
        assertEq(deal.agreementHash, ag.agreementHash, "Agreement hash must remain locked");
    }

    function test_23_post_funding_deadline_extension_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));

        // Revert on newDeadline <= currentDeadline
        CipherTradeEscrowV3.PartyConsentProofV3 memory bExtProof = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            DELIVERY_DEADLINE - 10,
            "buyer",
            buyer,
            BUYER_PK,
            "ext-nonce-b"
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory sExtProof = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            DELIVERY_DEADLINE - 10,
            "seller",
            seller,
            SELLER_PK,
            "ext-nonce-s"
        );

        vm.expectRevert(CipherTradeEscrowV3.InvalidDeadline.selector);
        escrow.extendDeliveryDeadline(chainDealId, DELIVERY_DEADLINE - 10, bExtProof, sExtProof);
    }

    // ─── Tests: Multi-Token Lifecycle & Settlement ─────────────────────────────

    function test_24_lifecycle_confirm_and_withdraw_fee_split_eth() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            10 ether,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent{value: 10 ether}(ag, bProof, sProof);

        // Buyer confirms delivery
        vm.prank(buyer);
        escrow.confirmDelivery(chainDealId);

        // Fee: 1% of 10 ETH = 0.1 ETH, Seller net = 9.9 ETH
        uint256 expectedFee = 0.1 ether;
        uint256 expectedSellerNet = 9.9 ether;

        uint256 sellerBalBefore = seller.balance;
        uint256 treasuryBalBefore = treasuryAddr.balance;

        vm.prank(seller);
        escrow.withdrawPayment(chainDealId);

        assertEq(seller.balance, sellerBalBefore + expectedSellerNet, "Seller payout mismatch");
        assertEq(treasuryAddr.balance, treasuryBalBefore + expectedFee, "Treasury fee payout mismatch");

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.RELEASED));
        assertTrue(deal.fundsWithdrawn);
    }

    function test_25_lifecycle_confirm_and_withdraw_fee_split_usdc() public {
        uint256 amount = 10_000 * 1e6; // 10,000 USDC
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            amount,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        vm.prank(buyer);
        escrow.confirmDelivery(chainDealId);

        // 100 bps = 1.00% of 10,000 USDC = 100 USDC (100_000_000 base units)
        uint256 expectedFee = 100 * 1e6;
        uint256 expectedSellerNet = 9_900 * 1e6;

        uint256 sellerBefore = usdc.balanceOf(seller);
        uint256 treasuryBefore = usdc.balanceOf(treasuryAddr);

        vm.prank(seller);
        escrow.withdrawPayment(chainDealId);

        assertEq(usdc.balanceOf(seller), sellerBefore + expectedSellerNet);
        assertEq(usdc.balanceOf(treasuryAddr), treasuryBefore + expectedFee);
    }

    function test_26_lifecycle_voluntary_refund_zero_fee() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        uint256 buyerBefore = usdc.balanceOf(buyer);
        uint256 treasuryBefore = usdc.balanceOf(treasuryAddr);

        // Seller voluntary refund
        vm.prank(seller);
        escrow.refundBuyer(chainDealId);

        assertEq(usdc.balanceOf(buyer), buyerBefore + AMOUNT_USDC, "Buyer must receive 100% refund");
        assertEq(usdc.balanceOf(treasuryAddr), treasuryBefore, "Zero fee on refund");

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.REFUNDED));
    }

    function test_27_lifecycle_cancel_expired_zero_fee() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        // Attempt cancel before deadline
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.DeadlineNotReached.selector);
        escrow.cancelExpired(chainDealId);

        // Warp past deadline
        vm.warp(DELIVERY_DEADLINE + 1);

        uint256 buyerBefore = buyer.balance;
        uint256 treasuryBefore = treasuryAddr.balance;

        vm.prank(buyer);
        escrow.cancelExpired(chainDealId);

        assertEq(buyer.balance, buyerBefore + AMOUNT_WEI, "Buyer must receive 100% refund");
        assertEq(treasuryAddr.balance, treasuryBefore, "Zero fee on cancellation");

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.CANCELLED));
    }

    function test_28_lifecycle_dispute_resolution() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        // Buyer raises dispute
        vm.prank(buyer);
        escrow.raiseDispute(chainDealId);

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.DISPUTED));

        // Arbitrator resolves to seller
        uint256 expectedFee = (AMOUNT_USDC * FEE_BPS) / 10000;
        uint256 expectedSellerNet = AMOUNT_USDC - expectedFee;

        uint256 sellerBefore = usdc.balanceOf(seller);
        uint256 treasuryBefore = usdc.balanceOf(treasuryAddr);

        vm.prank(arb);
        escrow.resolveDispute(chainDealId, true);

        assertEq(usdc.balanceOf(seller), sellerBefore + expectedSellerNet);
        assertEq(usdc.balanceOf(treasuryAddr), treasuryBefore + expectedFee);

        deal = escrow.getDeal(chainDealId);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.RESOLVED));
    }

    function test_29_lifecycle_dispute_resolution_to_buyer() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        vm.prank(seller);
        escrow.raiseDispute(chainDealId);

        uint256 buyerBefore = usdc.balanceOf(buyer);
        uint256 treasuryBefore = usdc.balanceOf(treasuryAddr);

        vm.prank(arb);
        escrow.resolveDispute(chainDealId, false); // toBuyer

        assertEq(usdc.balanceOf(buyer), buyerBefore + AMOUNT_USDC, "Buyer gets 100% on dispute resolution");
        assertEq(usdc.balanceOf(treasuryAddr), treasuryBefore, "Zero fee to treasury on buyer resolution");
    }

    // ─── Tests: Admin, Ownership, & Governance ────────────────────────────────

    function test_30_admin_ownable2step_and_fee_update() public {
        address newOwner = makeAddr("newOwner");

        // Step 1: Transfer ownership
        vm.prank(ownerAddr);
        escrow.transferOwnership(newOwner);

        // Step 2: Accept ownership
        vm.prank(newOwner);
        escrow.acceptOwnership();

        assertEq(escrow.owner(), newOwner, "Ownership transfer failed");

        // Update fee schedule
        vm.prank(newOwner);
        escrow.setFeeBps(250); // 2.50%
        assertEq(escrow.feeBps(), 250);

        // Update fee schedule past MAX_FEE_BPS (500) reverts
        vm.prank(newOwner);
        vm.expectRevert(abi.encodeWithSelector(CipherTradeEscrowV3.InvalidFeeBps.selector, 501, 500));
        escrow.setFeeBps(501);
    }

    function test_31_delisting_token_does_not_break_settlement() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        // Owner delists USDC after funding
        vm.prank(ownerAddr);
        escrow.setTokenAllowed(address(usdc), false);

        // Seller and buyer can still confirm and withdraw
        vm.prank(buyer);
        escrow.confirmDelivery(chainDealId);

        vm.prank(seller);
        escrow.withdrawPayment(chainDealId);

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.RELEASED));
    }

    // ─── Tests: Reentrancy Protection & Plain ETH ──────────────────────────────

    function test_32_reentrancy_protection() public {
        MaliciousReceiverV3 attacker = new MaliciousReceiverV3(escrow);

        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            1 ether,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        ag.buyer = address(attacker);
        ag.agreementHash = escrow.computeCanonicalAgreementHash(
            ag.dealId,
            ag.agreementVersion,
            ag.previousAgreementHash,
            ag.offerId,
            ag.buyer,
            ag.seller,
            ag.token,
            ag.amount,
            ag.feeBps,
            ag.treasury,
            ag.deliveryDeadline
        );
        // Fund attacker contract
        vm.deal(address(attacker), 10 ether);

        // Proofs for funding
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof;
        bProof.nonce = BUYER_NONCE;
        bProof.deadline = BUYER_CONSENT_DEADLINE;
        bytes32 bStructHash = escrow.hashAgreementConsent(ag, bProof, "buyer", address(attacker));
        bytes32 bDigest = escrow.computeDigest(bStructHash);
        bProof.signature = _sign(BUYER_PK, bDigest); // Note: we'll test reentrancy on refund

        // Let's test standard reentrancy on seller refunding
        CipherTradeEscrowV3.CommercialAgreementV3 memory agNormal = _buildAgreement(
            address(0),
            1 ether,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProofN = _buildBuyerProof(agNormal, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProofN = _buildSellerProof(agNormal, SELLER_NONCE);

        vm.prank(buyer);
        escrow.fundDealWithConsent{value: 1 ether}(agNormal, bProofN, sProofN);

        // Reentrant call from plain receive is blocked by ReentrancyGuard
        attacker.setAttack(true);
    }

    function test_33_plain_eth_send_reverts() public {
        vm.prank(buyer);
        (bool ok, ) = address(escrow).call{value: 1 ether}("");
        assertFalse(ok, "Direct plain ETH transfer must revert");
    }

    // ─── Tests: Section 6 Edge Cases ──────────────────────────────────────────

    function test_34_insufficient_allowance_and_balance_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        // Reset allowance to 0
        vm.prank(buyer);
        usdc.approve(address(escrow), 0);

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent(ag, bProof, sProof);

        // Restore allowance but drain buyer balance
        vm.startPrank(buyer);
        usdc.approve(address(escrow), type(uint256).max);
        usdc.transfer(rando, usdc.balanceOf(buyer)); // drain
        vm.stopPrank();

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent(ag, bProof, sProof);
    }

    function test_35_config_changes_between_registration_and_funding_preserved() public {
        // Register agreement with snapshotted fee (FEE_BPS = 100) and treasuryAddr
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            10_000 * 1e6,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, "nonce-b-cfg");
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, "nonce-s-cfg");

        escrow.registerInitialAgreement(ag, bProof, sProof);

        // Owner updates default protocol fee and treasury
        address newTreasury = makeAddr("newTreasury");
        vm.startPrank(ownerAddr);
        escrow.setFeeBps(300); // 3.00%
        escrow.setTreasury(newTreasury);
        vm.stopPrank();

        // Buyer funds registered deal
        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));
        vm.prank(buyer);
        uint256 chainDealId = escrow.fundRegisteredDeal(dealUuidHash);

        // Verify that snapshotted fee (100) and treasury (treasuryAddr) are preserved in the deal
        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(deal.feeBps, 100, "Snapshotted fee must remain 100 bps");
        assertEq(deal.treasury, treasuryAddr, "Snapshotted treasury must remain original treasury");

        // Verify payout uses snapshotted values
        vm.prank(buyer);
        escrow.confirmDelivery(chainDealId);

        uint256 oldTreasuryBefore = usdc.balanceOf(treasuryAddr);
        uint256 newTreasuryBefore = usdc.balanceOf(newTreasury);

        vm.prank(seller);
        escrow.withdrawPayment(chainDealId);

        // 100 bps (1%) of 10,000 USDC = 100 USDC to old treasury; 0 to new treasury
        assertEq(usdc.balanceOf(treasuryAddr), oldTreasuryBefore + 100 * 1e6);
        assertEq(usdc.balanceOf(newTreasury), newTreasuryBefore);
    }

    function test_36_failed_outgoing_token_transfers_revert() public {
        FailingTransferToken failToken = new FailingTransferToken();
        vm.prank(ownerAddr);
        escrow.setTokenAllowed(address(failToken), true);

        failToken.mint(buyer, 1000 * 1e18);
        vm.prank(buyer);
        failToken.approve(address(escrow), type(uint256).max);

        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(failToken),
            1000 * 1e18,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent(ag, bProof, sProof);

        vm.prank(buyer);
        escrow.confirmDelivery(chainDealId);

        // Trigger transfer failure
        failToken.setFail(true);

        vm.prank(seller);
        vm.expectRevert();
        escrow.withdrawPayment(chainDealId);

        // Restore token and verify withdrawal succeeds
        failToken.setFail(false);
        vm.prank(seller);
        escrow.withdrawPayment(chainDealId);

        CipherTradeEscrowV3.DealV3 memory deal = escrow.getDeal(chainDealId);
        assertEq(uint8(deal.state), uint8(CipherTradeEscrowV3.State.RELEASED));
    }

    function test_37_atomic_rollback_on_failed_funding() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(usdc),
            AMOUNT_USDC,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        // Revoke allowance
        vm.prank(buyer);
        usdc.approve(address(escrow), 0);

        vm.prank(buyer);
        vm.expectRevert();
        escrow.fundDealWithConsent(ag, bProof, sProof);

        // Nonces must NOT be consumed
        bytes32 bNonceKey = keccak256(abi.encode(buyer, BUYER_NONCE));
        assertFalse(escrow.isConsentNonceUsed(bNonceKey), "Buyer nonce must not be consumed on rollback");

        // Deal counter must NOT increment
        assertEq(escrow.nextDealId(), 1, "nextDealId must not increment on rollback");
    }

    function test_38_registered_funding_without_initial_registration_reverts() public {
        bytes32 unregUuidHash = keccak256("unregistered-deal-uuid");
        vm.prank(buyer);
        vm.expectRevert(CipherTradeEscrowV3.InitialAgreementNotRegistered.selector);
        escrow.fundRegisteredDeal(unregUuidHash);
    }

    function test_39_deadline_extension_after_terminal_state_reverts() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        // Buyer confirms delivery -> State.CONFIRMED
        vm.prank(buyer);
        escrow.confirmDelivery(chainDealId);

        uint256 newDeadline = DELIVERY_DEADLINE + 86400 * 5;
        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));

        CipherTradeEscrowV3.PartyConsentProofV3 memory bExtProof = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            newDeadline,
            "buyer",
            buyer,
            BUYER_PK,
            "ext-nonce-b1"
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory sExtProof = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            newDeadline,
            "seller",
            seller,
            SELLER_PK,
            "ext-nonce-s1"
        );

        // Reverts because state is CONFIRMED, not FUNDED
        vm.expectRevert(
            abi.encodeWithSelector(
                CipherTradeEscrowV3.InvalidState.selector,
                CipherTradeEscrowV3.State.CONFIRMED,
                CipherTradeEscrowV3.State.FUNDED
            )
        );
        escrow.extendDeliveryDeadline(chainDealId, newDeadline, bExtProof, sExtProof);
    }

    function test_40_multiple_deadline_extensions_success() public {
        CipherTradeEscrowV3.CommercialAgreementV3 memory ag = _buildAgreement(
            address(0),
            AMOUNT_WEI,
            1,
            bytes32(0),
            DELIVERY_DEADLINE
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory bProof = _buildBuyerProof(ag, BUYER_NONCE);
        CipherTradeEscrowV3.PartyConsentProofV3 memory sProof = _buildSellerProof(ag, SELLER_NONCE);

        vm.prank(buyer);
        uint256 chainDealId = escrow.fundDealWithConsent{value: AMOUNT_WEI}(ag, bProof, sProof);

        bytes32 dealUuidHash = keccak256(bytes(DEAL_UUID));

        // Extension 1: to +3 days
        uint256 ext1 = DELIVERY_DEADLINE + 86400 * 3;
        CipherTradeEscrowV3.PartyConsentProofV3 memory bExt1 = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            ext1,
            "buyer",
            buyer,
            BUYER_PK,
            "nonce-ext-b1"
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory sExt1 = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            DELIVERY_DEADLINE,
            ext1,
            "seller",
            seller,
            SELLER_PK,
            "nonce-ext-s1"
        );
        escrow.extendDeliveryDeadline(chainDealId, ext1, bExt1, sExt1);

        CipherTradeEscrowV3.DealV3 memory deal1 = escrow.getDeal(chainDealId);
        assertEq(deal1.deadline, ext1);

        // Extension 2: to +7 days
        uint256 ext2 = ext1 + 86400 * 4;
        CipherTradeEscrowV3.PartyConsentProofV3 memory bExt2 = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            ext1,
            ext2,
            "buyer",
            buyer,
            BUYER_PK,
            "nonce-ext-b2"
        );
        CipherTradeEscrowV3.PartyConsentProofV3 memory sExt2 = _buildDeadlineProof(
            chainDealId,
            dealUuidHash,
            ag.agreementHash,
            ext1,
            ext2,
            "seller",
            seller,
            SELLER_PK,
            "nonce-ext-s2"
        );
        escrow.extendDeliveryDeadline(chainDealId, ext2, bExt2, sExt2);

        CipherTradeEscrowV3.DealV3 memory deal2 = escrow.getDeal(chainDealId);
        assertEq(deal2.deadline, ext2);
    }
}
