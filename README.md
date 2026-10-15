<div align="center">

# CipherTrade

**Blockchain-powered peer-to-peer marketplace with end-to-end encrypted messaging and smart-contract escrow.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Stack: React + FastAPI + Solidity](https://img.shields.io/badge/Stack-React%20%7C%20FastAPI%20%7C%20Solidity-0ea5e9)](docs/ARCHITECTURE.md)

</div>

---

## What is CipherTrade?

CipherTrade connects buyers and sellers in a trustless marketplace where:

- **Buyers** post requirements and receive offers from verified sellers.
- **Sellers** discover relevant listings, submit offers, and negotiate terms.
- **All negotiation** happens through genuinely end-to-end encrypted private messaging — the server never sees plaintext.
- **Payment** is held in a Solidity escrow contract and released only when the buyer confirms delivery, with on-chain dispute resolution.

No platform intermediary can unilaterally move users' funds.

---

## Architecture Overview

```
CipherTrade/
├── frontend/          React + TypeScript + Vite + Tailwind CSS
├── backend/           Python FastAPI  (REST + WebSocket)
├── contracts/         Solidity + Foundry (escrow logic only)
├── docs/              Architecture, threat model, ADRs
└── scripts/           Utility scripts (migrations, deploy helpers)
```

Full system boundaries and data-flow diagrams: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

---

## Planned Features

| Feature | Status |
|---|---|
| Buyer & Seller authentication | 🔲 Phase 2 |
| Listing creation & discovery | 🔲 Phase 2 |
| End-to-end encrypted messaging | 🔲 Phase 3 |
| Offer submission & negotiation | 🔲 Phase 3 |
| Wallet connection (MetaMask) | 🔲 Phase 4 |
| Smart-contract escrow | 🏗️ Scaffold (Phase 1) |
| Dispute resolution | 🔲 Phase 4 |
| Reporting & blocking | 🔲 Phase 5 |

---

## Prerequisites

| Tool | Minimum version | Purpose |
|---|---|---|
| Node.js | 20 | Frontend build |
| npm | 10 | Package management |
| Python | 3.11 | Backend runtime |
| PostgreSQL | 15 | Primary database |
| Git | 2.40 | Version control |
| Foundry (`forge`, `anvil`) | latest | Smart-contract dev & testing |
| Rust / Cargo | stable | Required by Foundry |

> **Foundry installation (Windows):**
> ```powershell
> # Install Rust first
> winget install Rustlang.Rustup
> # Then install Foundry
> cargo install --git https://github.com/foundry-rs/foundry --profile local --locked forge cast anvil chisel
> ```

---

## Local Development

### 1 — Clone and configure environment

```bash
git clone https://github.com/your-org/ciphertrade.git
cd ciphertrade

# Backend
cp backend/.env.example backend/.env
# Edit backend/.env — set DATABASE_URL and generate a SECRET_KEY

# Frontend
cp frontend/.env.example frontend/.env.local
```

### 2 — Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn asgi:app --reload --port 8000
```

Health check: http://localhost:8000/health

### 3 — Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open: http://localhost:5173

### 4 — Smart Contracts (requires Foundry)

```bash
cd contracts
forge install foundry-rs/forge-std --no-commit
forge build
forge test -vv
```

### 5 — Local Blockchain

```bash
anvil --chain-id 31337
```

---

## Running Tests

```powershell
# Backend
cd backend
.venv\Scripts\activate
pytest -v

# Frontend
cd frontend
npm run test   # (Vitest — Phase 2)

# Contracts
cd contracts
forge test -vv
```

---

## Security Notes

- Private encryption keys are **never** sent to or stored by the server.
- Escrow funds can only be released or refunded by the buyer and seller — there is no admin withdrawal path.
- All `.env` files are git-ignored; only `.env.example` files with placeholders are committed.
- Do not use real ETH or mainnet credentials during development.

---

## License

MIT — see [LICENSE](LICENSE).
