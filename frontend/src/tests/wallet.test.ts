import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  ANVIL_CHAIN_ID,
  ANVIL_CHAIN_ID_HEX,
  ANVIL_RPC_URL,
  ANVIL_CHAIN_NAME,
  formatAddress,
} from '../utils/wallet.ts';

describe('Wallet Utilities and Network Configuration', () => {
  it('should have exact Anvil network constants configured', () => {
    assert.equal(ANVIL_CHAIN_ID, 31337);
    assert.equal(ANVIL_CHAIN_ID_HEX, '0x7a69');
    assert.equal(parseInt(ANVIL_CHAIN_ID_HEX, 16), 31337);
    assert.equal(ANVIL_RPC_URL, 'http://127.0.0.1:8545');
    assert.equal(ANVIL_CHAIN_NAME, 'Anvil Local');
  });

  describe('formatAddress', () => {
    it('should format a valid checksummed address with 0x prefix and last 4 characters', () => {
      const addr = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
      assert.equal(formatAddress(addr), '0x7099...79C8');
    });

    it('should convert lowercase address to checksummed shortened address', () => {
      const addr = '0x70997970c51812dc3a010c7d01b50e0d17dc79c8';
      assert.equal(formatAddress(addr), '0x7099...79C8');
    });

    it('should return empty string for null, undefined, or empty values', () => {
      assert.equal(formatAddress(null), '');
      assert.equal(formatAddress(undefined), '');
      assert.equal(formatAddress(''), '');
    });

    it('should handle short strings without crashing', () => {
      assert.equal(formatAddress('0x123'), '0x123');
    });
  });

  describe('EIP-1193 Mock Provider and Event Handling', () => {
    it('should properly simulate accountsChanged and chainChanged event lifecycle', () => {
      type EventHandler = (...args: any[]) => void;
      const listeners: Record<string, EventHandler[]> = {};

      const mockProvider = {
        isMetaMask: true,
        on(event: string, handler: EventHandler) {
          if (!listeners[event]) listeners[event] = [];
          listeners[event].push(handler);
        },
        removeListener(event: string, handler: EventHandler) {
          if (listeners[event]) {
            listeners[event] = listeners[event].filter((h) => h !== handler);
          }
        },
        emit(event: string, ...args: any[]) {
          if (listeners[event]) {
            listeners[event].forEach((h) => h(...args));
          }
        },
        async request({ method, params }: { method: string; params?: any }) {
          if (method === 'eth_requestAccounts') {
            return ['0x70997970c51812dc3a010c7d01b50e0d17dc79c8'];
          }
          if (method === 'wallet_switchEthereumChain') {
            if (params?.[0]?.chainId !== ANVIL_CHAIN_ID_HEX) {
              const err = new Error('Unrecognized chain ID') as any;
              err.code = 4902;
              throw err;
            }
            return null;
          }
          if (method === 'wallet_addEthereumChain') {
            assert.equal(params?.[0]?.chainId, ANVIL_CHAIN_ID_HEX);
            assert.equal(params?.[0]?.chainName, ANVIL_CHAIN_NAME);
            assert.equal(params?.[0]?.rpcUrls?.[0], ANVIL_RPC_URL);
            return null;
          }
          return null;
        },
      };

      // Test event listener registration
      let currentAccount = '';
      let currentChain = 0;

      const onAccounts = (accs: string[]) => {
        currentAccount = accs[0] ?? '';
      };
      const onChain = (hex: string) => {
        currentChain = parseInt(hex, 16);
      };

      mockProvider.on('accountsChanged', onAccounts);
      mockProvider.on('chainChanged', onChain);

      assert.equal(listeners['accountsChanged'].length, 1);
      assert.equal(listeners['chainChanged'].length, 1);

      // Trigger accountsChanged
      mockProvider.emit('accountsChanged', ['0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC']);
      assert.equal(currentAccount, '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC');

      // Trigger chainChanged
      mockProvider.emit('chainChanged', '0x7a69');
      assert.equal(currentChain, 31337);

      // Trigger disconnect / empty accounts
      mockProvider.emit('accountsChanged', []);
      assert.equal(currentAccount, '');

      // Test cleanup
      mockProvider.removeListener('accountsChanged', onAccounts);
      mockProvider.removeListener('chainChanged', onChain);

      assert.equal(listeners['accountsChanged'].length, 0);
      assert.equal(listeners['chainChanged'].length, 0);
    });

    it('should simulate network switch to Anvil and handle 4902 fallback to wallet_addEthereumChain', async () => {
      let addChainCalled = false;

      const mockProvider = {
        async request({ method, params }: { method: string; params?: any }) {
          if (method === 'wallet_switchEthereumChain') {
            const err = new Error('Unrecognized chain ID') as any;
            err.code = 4902;
            throw err;
          }
          if (method === 'wallet_addEthereumChain') {
            addChainCalled = true;
            assert.equal(params?.[0]?.chainId, ANVIL_CHAIN_ID_HEX);
            assert.equal(params?.[0]?.rpcUrls?.[0], ANVIL_RPC_URL);
            return null;
          }
          return null;
        },
      };

      try {
        await mockProvider.request({
          method: 'wallet_switchEthereumChain',
          params: [{ chainId: ANVIL_CHAIN_ID_HEX }],
        });
      } catch (err: any) {
        if (err.code === 4902) {
          await mockProvider.request({
            method: 'wallet_addEthereumChain',
            params: [
              {
                chainId: ANVIL_CHAIN_ID_HEX,
                chainName: ANVIL_CHAIN_NAME,
                rpcUrls: [ANVIL_RPC_URL],
                nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
              },
            ],
          });
        }
      }

      assert.equal(addChainCalled, true);
    });
  });
});

