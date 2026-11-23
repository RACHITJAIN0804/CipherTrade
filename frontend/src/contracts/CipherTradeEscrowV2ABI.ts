/**
 * CipherTradeEscrowV2 ABI for frontend contract interaction.
 *
 * Generated from: contracts/out/CipherTradeEscrowV2.sol/CipherTradeEscrowV2.json
 * Phase: 5E.3 — V2 Deployment & Frontend Integration
 *
 * Key function: fundDealWithConsent(CommercialAgreement, PartyConsentProof, PartyConsentProof)
 * Key event:    DealFunded(uint256 chainDealId, bytes32 dealUuidHash, address buyer,
 *                          address seller, uint256 amount, uint256 deliveryDeadline,
 *                          bytes32 agreementHash, string dealUuid, string offerId)
 */

const CipherTradeEscrowV2ABI = [
  {
    "type": "constructor",
    "inputs": [{ "name": "_arbitrator", "type": "address", "internalType": "address" }],
    "stateMutability": "nonpayable"
  },
  { "type": "fallback", "stateMutability": "payable" },
  { "type": "receive", "stateMutability": "payable" },
  {
    "type": "function",
    "name": "AGREEMENT_CONSENT_TYPEHASH",
    "inputs": [],
    "outputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "SECP256K1_N_DIV_2",
    "inputs": [],
    "outputs": [{ "name": "", "type": "uint256", "internalType": "uint256" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "arbitrator",
    "inputs": [],
    "outputs": [{ "name": "", "type": "address", "internalType": "address" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "cancelExpired",
    "inputs": [{ "name": "dealId", "type": "uint256", "internalType": "uint256" }],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
  {
    "type": "function",
    "name": "chainDealIdByUuidHash",
    "inputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "outputs": [{ "name": "", "type": "uint256", "internalType": "uint256" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "computeDigest",
    "inputs": [{ "name": "structHash", "type": "bytes32", "internalType": "bytes32" }],
    "outputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "confirmDelivery",
    "inputs": [{ "name": "dealId", "type": "uint256", "internalType": "uint256" }],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
  {
    "type": "function",
    "name": "domainSeparator",
    "inputs": [],
    "outputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "fundDealWithConsent",
    "inputs": [
      {
        "name": "agreement",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.CommercialAgreement",
        "components": [
          { "name": "agreementHash", "type": "bytes32", "internalType": "bytes32" },
          { "name": "offerId", "type": "string", "internalType": "string" },
          { "name": "dealId", "type": "string", "internalType": "string" },
          { "name": "buyer", "type": "address", "internalType": "address payable" },
          { "name": "seller", "type": "address", "internalType": "address payable" },
          { "name": "amount", "type": "uint256", "internalType": "uint256" },
          { "name": "deliveryDeadline", "type": "uint256", "internalType": "uint256" }
        ]
      },
      {
        "name": "buyerConsent",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.PartyConsentProof",
        "components": [
          { "name": "nonce", "type": "string", "internalType": "string" },
          { "name": "deadline", "type": "uint256", "internalType": "uint256" },
          { "name": "signature", "type": "bytes", "internalType": "bytes" }
        ]
      },
      {
        "name": "sellerConsent",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.PartyConsentProof",
        "components": [
          { "name": "nonce", "type": "string", "internalType": "string" },
          { "name": "deadline", "type": "uint256", "internalType": "uint256" },
          { "name": "signature", "type": "bytes", "internalType": "bytes" }
        ]
      }
    ],
    "outputs": [{ "name": "chainDealId", "type": "uint256", "internalType": "uint256" }],
    "stateMutability": "payable"
  },
  {
    "type": "function",
    "name": "getDeal",
    "inputs": [{ "name": "dealId", "type": "uint256", "internalType": "uint256" }],
    "outputs": [
      {
        "name": "",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.Deal",
        "components": [
          { "name": "buyer", "type": "address", "internalType": "address payable" },
          { "name": "seller", "type": "address", "internalType": "address payable" },
          { "name": "amount", "type": "uint256", "internalType": "uint256" },
          { "name": "deadline", "type": "uint256", "internalType": "uint256" },
          { "name": "agreementHash", "type": "bytes32", "internalType": "bytes32" },
          { "name": "dealUuidHash", "type": "bytes32", "internalType": "bytes32" },
          { "name": "state", "type": "uint8", "internalType": "enum CipherTradeEscrowV2.State" },
          { "name": "fundsWithdrawn", "type": "bool", "internalType": "bool" }
        ]
      }
    ],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "hashBuyerConsent",
    "inputs": [
      {
        "name": "agreement",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.CommercialAgreement",
        "components": [
          { "name": "agreementHash", "type": "bytes32", "internalType": "bytes32" },
          { "name": "offerId", "type": "string", "internalType": "string" },
          { "name": "dealId", "type": "string", "internalType": "string" },
          { "name": "buyer", "type": "address", "internalType": "address payable" },
          { "name": "seller", "type": "address", "internalType": "address payable" },
          { "name": "amount", "type": "uint256", "internalType": "uint256" },
          { "name": "deliveryDeadline", "type": "uint256", "internalType": "uint256" }
        ]
      },
      {
        "name": "proof",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.PartyConsentProof",
        "components": [
          { "name": "nonce", "type": "string", "internalType": "string" },
          { "name": "deadline", "type": "uint256", "internalType": "uint256" },
          { "name": "signature", "type": "bytes", "internalType": "bytes" }
        ]
      }
    ],
    "outputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "stateMutability": "pure"
  },
  {
    "type": "function",
    "name": "hashSellerConsent",
    "inputs": [
      {
        "name": "agreement",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.CommercialAgreement",
        "components": [
          { "name": "agreementHash", "type": "bytes32", "internalType": "bytes32" },
          { "name": "offerId", "type": "string", "internalType": "string" },
          { "name": "dealId", "type": "string", "internalType": "string" },
          { "name": "buyer", "type": "address", "internalType": "address payable" },
          { "name": "seller", "type": "address", "internalType": "address payable" },
          { "name": "amount", "type": "uint256", "internalType": "uint256" },
          { "name": "deliveryDeadline", "type": "uint256", "internalType": "uint256" }
        ]
      },
      {
        "name": "proof",
        "type": "tuple",
        "internalType": "struct CipherTradeEscrowV2.PartyConsentProof",
        "components": [
          { "name": "nonce", "type": "string", "internalType": "string" },
          { "name": "deadline", "type": "uint256", "internalType": "uint256" },
          { "name": "signature", "type": "bytes", "internalType": "bytes" }
        ]
      }
    ],
    "outputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "stateMutability": "pure"
  },
  {
    "type": "function",
    "name": "isConsentNonceUsed",
    "inputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "outputs": [{ "name": "", "type": "bool", "internalType": "bool" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "isDealUuidFunded",
    "inputs": [{ "name": "", "type": "bytes32", "internalType": "bytes32" }],
    "outputs": [{ "name": "", "type": "bool", "internalType": "bool" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "nextDealId",
    "inputs": [],
    "outputs": [{ "name": "", "type": "uint256", "internalType": "uint256" }],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "raiseDispute",
    "inputs": [{ "name": "dealId", "type": "uint256", "internalType": "uint256" }],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
  {
    "type": "function",
    "name": "recoverSigner",
    "inputs": [
      { "name": "digest", "type": "bytes32", "internalType": "bytes32" },
      { "name": "signature", "type": "bytes", "internalType": "bytes" }
    ],
    "outputs": [{ "name": "", "type": "address", "internalType": "address" }],
    "stateMutability": "pure"
  },
  {
    "type": "function",
    "name": "refundBuyer",
    "inputs": [{ "name": "dealId", "type": "uint256", "internalType": "uint256" }],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
  {
    "type": "function",
    "name": "resolveDispute",
    "inputs": [
      { "name": "dealId", "type": "uint256", "internalType": "uint256" },
      { "name": "toSeller", "type": "bool", "internalType": "bool" }
    ],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
  {
    "type": "function",
    "name": "withdrawPayment",
    "inputs": [{ "name": "dealId", "type": "uint256", "internalType": "uint256" }],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
  {
    "type": "event",
    "name": "DealFunded",
    "inputs": [
      { "name": "chainDealId", "type": "uint256", "indexed": true, "internalType": "uint256" },
      { "name": "dealUuidHash", "type": "bytes32", "indexed": true, "internalType": "bytes32" },
      { "name": "buyer", "type": "address", "indexed": true, "internalType": "address" },
      { "name": "seller", "type": "address", "indexed": false, "internalType": "address" },
      { "name": "amount", "type": "uint256", "indexed": false, "internalType": "uint256" },
      { "name": "deliveryDeadline", "type": "uint256", "indexed": false, "internalType": "uint256" },
      { "name": "agreementHash", "type": "bytes32", "indexed": false, "internalType": "bytes32" },
      { "name": "dealUuid", "type": "string", "indexed": false, "internalType": "string" },
      { "name": "offerId", "type": "string", "indexed": false, "internalType": "string" }
    ],
    "anonymous": false
  },
  {
    "type": "error",
    "name": "AmountMismatch",
    "inputs": [
      { "name": "expected", "type": "uint256", "internalType": "uint256" },
      { "name": "provided", "type": "uint256", "internalType": "uint256" }
    ]
  },
  { "type": "error", "name": "ConsentExpired", "inputs": [
    { "name": "role", "type": "string", "internalType": "string" },
    { "name": "deadline", "type": "uint256", "internalType": "uint256" },
    { "name": "timestamp", "type": "uint256", "internalType": "uint256" }
  ]},
  { "type": "error", "name": "DealAlreadyFunded", "inputs": [{ "name": "dealUuid", "type": "string", "internalType": "string" }] },
  { "type": "error", "name": "InvalidDeadline", "inputs": [] },
  { "type": "error", "name": "InvalidSignatureLength", "inputs": [] },
  { "type": "error", "name": "InvalidSignatureS", "inputs": [] },
  { "type": "error", "name": "InvalidSignatureV", "inputs": [] },
  { "type": "error", "name": "NonceReplayed", "inputs": [
    { "name": "signer", "type": "address", "internalType": "address" },
    { "name": "nonce", "type": "string", "internalType": "string" }
  ]},
  { "type": "error", "name": "Reentrancy", "inputs": [] },
  { "type": "error", "name": "SelfDeal", "inputs": [] },
  { "type": "error", "name": "SignerMismatch", "inputs": [
    { "name": "role", "type": "string", "internalType": "string" },
    { "name": "expected", "type": "address", "internalType": "address" },
    { "name": "recovered", "type": "address", "internalType": "address" }
  ]},
  { "type": "error", "name": "TransferFailed", "inputs": [] },
  { "type": "error", "name": "UnauthorizedCaller", "inputs": [
    { "name": "caller", "type": "address", "internalType": "address" },
    { "name": "expected", "type": "address", "internalType": "address" }
  ]},
  { "type": "error", "name": "ZeroAddress", "inputs": [] },
  { "type": "error", "name": "ZeroAmount", "inputs": [] }
] as const;

export type CipherTradeEscrowV2ABIType = typeof CipherTradeEscrowV2ABI;
export default CipherTradeEscrowV2ABI;
