// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {CipherTradeEscrow} from "../src/CipherTradeEscrow.sol";

/**
 * @notice Foundry deploy script for CipherTradeEscrow.
 *
 * Usage (local Anvil only — NEVER use real funds or private keys):
 *
 *   anvil --chain-id 31337
 *
 *   forge script script/Deploy.s.sol \
 *     --rpc-url http://127.0.0.1:8545 \
 *     --broadcast \
 *     -vvvv
 *
 * The ARBITRATOR env variable must be set to a valid address.
 * If unset it defaults to the first Anvil test account for local runs only.
 */
contract DeployScript is Script {
    function run() external returns (CipherTradeEscrow escrow) {
        address arbitrator = vm.envOr("ARBITRATOR", address(0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266));

        console2.log("Deploying CipherTradeEscrow");
        console2.log("  arbitrator :", arbitrator);
        console2.log("  chain id   :", block.chainid);

        vm.startBroadcast();
        escrow = new CipherTradeEscrow(arbitrator);
        vm.stopBroadcast();

        console2.log("  deployed at:", address(escrow));
    }
}
