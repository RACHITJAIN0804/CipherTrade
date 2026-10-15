# CipherTrade — Architecture Document

> **Status:** Phase 1 scaffold. Updated as each phase is completed.

---

## 1. System Boundaries

```
┌──────────────────────────────────────────────────────────────┐
│                        Browser / Client                       │
│                                                              │
│  ┌─────────────────────┐   ┌──────────────────────────────┐ │
│  │   React Frontend    │   │  MetaMask / Ethers.js        │ │
│  │  (Vite + Tailwind)  │   │  (wallet & tx signing)       │ │
│  └────────┬────────────┘   └──────────────┬───────────────┘ │
│           │ HTTPS / WSS                    │ JSON-RPC         │
└───────────┼────────────────────────────────┼─────────────────┘
            │                                │
┌───────────▼────────────┐     ┌─────────────▼────────────────┐
│   FastAPI Backend       │     │   Ethereum Node / Anvil       │
│   (REST + WebSocket)    │     │   (local dev / testnet)       │
│                        │     │                               │
│  • Auth & sessions      │     │  ┌────────────────────────┐  │
│  • Listing management   │     │  │  CipherTradeEscrow.sol  │  │
│  • Offer negotiation    │     │  │  (on-chain only)        │  │
│  • Encrypted msg relay  │     │  └────────────────────────┘  │
│  • Report / block mgmt  │     └───────────────────────────────┘
│                        │
│  ┌─────────────────┐   │
│  │   PostgreSQL    │   │
│  │  (off-chain)    │   │
│  └─────────────────┘   │
└────────────────────────┘
```

### What stays off-chain

| Data | Storage | Rationale |
|---|---|---|
| User profiles & credentials | PostgreSQL | PII must remain private and deletable |
| Listing metadata & search index | PostgreSQL | Off-chain text search is practical and efficient |
| Encrypted message ciphertext | PostgreSQL | Plaintext is never available to server |
| Offers, negotiations, notifications | PostgreSQL | No need for immutability |
| Private encryption keys | Client only | E2EE guarantee requires server never sees keys |

### What lives on-chain

| Data | Rationale |
|---|---|
| Escrow deal ID, buyer, seller, amount | Trustless settlement; requires immutability |
| Deal state transitions (events) | Audit trail for dispute resolution |

---

## 2. Buyer Workflow

```
Register / Sign in
       │
Create Listing (title, description, category, budget, images, terms)
       │
Receive Seller Offers
       │
  ┌────┴──────────────────┐
  │ Negotiate via E2EE Chat│
  └────────────────────────┘
       │
Accept Offer → Connect Wallet → Fund Escrow (on-chain)
       │
Await Delivery
       │
Confirm Delivery ──→ Seller can withdraw payment (pull)
       │
   (Dispute?) ──→ Raise on-chain dispute → Arbitration
```

---

## 3. Seller Workflow

```
Register / Sign in
       │
Search Listings (keyword / category / buyer name)
       │
View Buyer Profile & Active Listings
       │
Submit Offer (price, deadline, terms)
       │
  ┌────┴──────────────────┐
  │ Negotiate via E2EE Chat│
  └────────────────────────┘
       │
Offer Accepted → Monitor Escrow Funding (on-chain event)
       │
Deliver
       │
Buyer Confirms → Withdraw Payment (pull)
       │
   (Dispute?) ──→ Raise on-chain dispute → Arbitration
```

---

## 4. End-to-End Encryption — Threat Model

### Goals

1. **Confidentiality**: Only the two conversation participants can decrypt messages.
2. **Server blindness**: The server relays and stores only ciphertext; it possesses no decryption keys.
3. **Authenticity**: Each participant can verify that a message was authored by their counterpart.
4. **Forward secrecy**: Compromise of long-term keys does not expose past sessions.

### Non-goals (Phase 1)

- Break-glass access for law enforcement without participant consent.
- Multi-device message sync (deferred to Phase 3).

### Protocol design (Phase 3 implementation target)

The messaging system will be modelled on the **Signal Protocol**:

1. **Key generation** — On account creation, each user generates an Identity Key pair (long-term Ed25519) and a set of signed pre-keys and one-time pre-keys (X25519).
2. **Public key publication** — Only public keys are uploaded to the server. Private keys never leave the client.
3. **Session establishment** — On first message, the sender performs an Extended Triple Diffie-Hellman (X3DH) handshake using the recipient's published pre-keys. This produces a shared secret without server involvement.
4. **Double Ratchet** — Subsequent messages use the Double Ratchet algorithm (combining a Diffie-Hellman ratchet and a symmetric-key ratchet) to provide forward secrecy and break-in recovery.
5. **Storage** — The server stores encrypted message blobs; the database schema holds no plaintext columns.
6. **Reporting consent** — If a user wants to report a conversation, they explicitly select messages, decrypt them locally, and attach the plaintext as evidence only to the private moderation report. The server never initiates decryption.

#### Library selection criteria (implementation phase)

- Must be actively maintained with published security audits.
- Must implement Signal Protocol or a protocol with equivalent published security proofs.
- Candidates: `libsignal-protocol-typescript` (Signal Foundation) or a peer-reviewed WebCrypto-based implementation.
- Custom cryptographic algorithms are **prohibited**.

### Key management considerations

| Concern | Approach |
|---|---|
| Pre-key exhaustion | Server tracks one-time pre-key count; client replenishes proactively |
| Device change | New device generates new keys; user triggers key rotation; old sessions must be re-established |
| Recovery | Account recovery must not allow server to impersonate the user; design TBD in Phase 3 |
| Key rotation | Signed pre-keys rotated periodically; Identity Key rotation is a breaking change requiring explicit re-verification |

---

## 5. Escrow Transaction Lifecycle

```
State machine for CipherTradeEscrow.sol

  fundDeal()         confirmDelivery()     withdrawPayment()
FUNDED ──────────────────▶ CONFIRMED ──────────────────▶ RELEASED
  │                                                        (terminal)
  │  refundBuyer()
  └──────────────────────────────────────────────────────▶ REFUNDED
  │                                                        (terminal)
  │  raiseDispute()
  └──────────────────────────────────────────────────────▶ DISPUTED
                                                            │
                                          [Phase 4 arbitration]
                                                            │
                                                         RESOLVED
                                                          (terminal)
```

### Security properties enforced

| Threat | Mitigation |
|---|---|
| Reentrancy | State updated before external `.call{}()` in pull-payment functions |
| Unauthorized withdrawal | `onlyBuyer` / `onlySeller` modifiers on all state-transition functions |
| Admin theft | No privileged admin role; no `owner` with fund-access exists in the contract |
| Double settlement | `buyerWithdrawn` / `sellerWithdrawn` boolean guards; state moved to terminal before transfer |
| Zero-value escrow | `ZeroAmount` revert on `fundDeal` when `msg.value == 0` |
| Unsafe ETH transfer | `.call{value: ...}("")` with explicit success check; `TransferFailed` error on failure |

### On-chain data minimisation

Only the following are stored on-chain:
- `buyer` address, `seller` address, `amount` (wei), `state` enum.
- No listing titles, offer terms, message references, or user PII.

---

## 6. Authentication Design (Phase 2 target)

- Standard username + password registration with bcrypt hashing (via `passlib`).
- JWT access tokens (short-lived) + HTTP-only refresh tokens (long-lived).
- Wallet-based identity: users optionally link an Ethereum wallet by signing a server-issued challenge (EIP-191). The signature proves key ownership without exposing the private key.
- Session invalidation on password change and wallet re-link.

---

## 7. Reporting & Blocking

### Blocking rules

1. If user A blocks user B, A cannot receive new messages from B.
2. B is not notified of the block.
3. Existing message history is not deleted; it is hidden in the UI.
4. Blocking does not affect on-chain escrow deals already in progress.

### Reporting rules

1. Reports are stored in a private, moderator-only table.
2. Message evidence is **opt-in**: the reporting user selects messages, decrypts them locally (client-side), and explicitly attaches the plaintext. The server does not decrypt on behalf of the reporter.
3. Moderators can access reports but have no API endpoint to request decryption of any conversation.

---

## 8. Technology Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Contract framework | Foundry | Fast Rust-native test runner; fuzz testing built-in |
| Backend async ORM | SQLAlchemy 2 async + asyncpg | Mature, well-typed async PostgreSQL driver |
| Frontend bundler | Vite | Fast HMR; first-class TypeScript support |
| CSS framework | Tailwind CSS v4 | Utility-first with @tailwindcss/vite; no separate config file |
| Wallet integration | MetaMask + Ethers.js v6 | Most widely supported; Ethers v6 is tree-shakeable |
| E2EE protocol | Signal Protocol (Phase 3) | Published security proofs; audited reference implementation |
