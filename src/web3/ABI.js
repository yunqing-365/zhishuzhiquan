/**
 * AIEchoProtocol 合约 ABI（精简版）
 * ====================================
 * 只包含前端实际调用的函数和监听的事件。
 * 完整 ABI 在 Hardhat 编译后生成于 artifacts/contracts/AIEchoProtocol.sol/
 *
 * 函数说明:
 *   registerAsset()       — 创作者注册资产，绑定链上指纹
 *   purchaseAndCallData() — B端购买访问权，触发 AMM 计价 + 分账
 *   verifyAccess()        — 验证调用方是否有有效 AccessToken
 *   getDynamicPrice()     — 查询当前 AMM 实时报价
 *   revokeAccess()        — 创作者吊销访问凭证
 *   setPaywallActive()    — 开启/关闭 Paywall 保护
 *   getRegisteredCount()  — 查询注册资产总数
 */

export const AI_ECHO_ABI = [
  // ── 状态查询 ────────────────────────────────────────────────────
  {
    name: 'platformAdmin',
    type: 'function',
    stateMutability: 'view',
    inputs: [],
    outputs: [{ type: 'address' }],
  },
  {
    name: 'getRegisteredCount',
    type: 'function',
    stateMutability: 'view',
    inputs: [],
    outputs: [{ type: 'uint256', name: 'count' }],
  },
  {
    name: 'getDynamicPrice',
    type: 'function',
    stateMutability: 'view',
    inputs: [
      { name: '_domainKey', type: 'string' },
      { name: '_baseValue', type: 'uint256' },
    ],
    outputs: [{ type: 'uint256', name: 'price' }],
  },
  {
    name: 'getDomainAlpha',
    type: 'function',
    stateMutability: 'view',
    inputs: [{ name: '_domainKey', type: 'string' }],
    outputs: [{ type: 'uint256', name: 'alpha' }],
  },
  {
    name: 'assetRegistry',
    type: 'function',
    stateMutability: 'view',
    inputs: [{ name: 'assetHash', type: 'uint256' }],
    outputs: [
      { name: 'creator',       type: 'address'  },
      { name: 'assetHash',     type: 'uint256'  },
      { name: 'modality',      type: 'string'   },
      { name: 'domainKey',     type: 'string'   },
      { name: 'audioScene',    type: 'string'   },
      { name: 'baseValue',     type: 'uint256'  },
      { name: 'hashAlgo',      type: 'uint8'    },
      { name: 'timestamp',     type: 'uint256'  },
      { name: 'paywallActive', type: 'bool'     },
    ],
  },
  {
    name: 'getAccessToken',
    type: 'function',
    stateMutability: 'view',
    inputs: [
      { name: '_assetHash', type: 'uint256' },
      { name: '_caller',    type: 'address' },
    ],
    outputs: [
      { name: 'callerAddr',      type: 'address' },
      { name: 'expiresAt',       type: 'uint256' },
      { name: 'callsRemaining',  type: 'uint256' },
      { name: 'callsUsed',       type: 'uint256' },
      { name: 'revoked',         type: 'bool'    },
    ],
  },
  {
    name: 'getHammingDistance',
    type: 'function',
    stateMutability: 'pure',
    inputs: [
      { name: 'a', type: 'uint256' },
      { name: 'b', type: 'uint256' },
    ],
    outputs: [{ type: 'uint256', name: 'distance' }],
  },

  // ── 写入操作（需要钱包签名）────────────────────────────────────
  {
    name: 'registerAsset',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [
      { name: '_assetHash',  type: 'uint256' },
      { name: '_modality',   type: 'string'  },
      { name: '_domainKey',  type: 'string'  },
      { name: '_audioScene', type: 'string'  },
      { name: '_baseValue',  type: 'uint256' },
      { name: '_hashAlgo',   type: 'uint8'   },
    ],
    outputs: [],
  },
  {
    name: 'purchaseAndCallData',
    type: 'function',
    stateMutability: 'payable',
    inputs: [
      { name: '_assetHash', type: 'uint256' },
      { name: '_callQuota', type: 'uint256' },
      { name: '_ttlDays',   type: 'uint256' },
    ],
    outputs: [],
  },
  {
    name: 'verifyAccess',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [{ name: '_assetHash', type: 'uint256' }],
    outputs: [{ type: 'bool', name: 'allowed' }],
  },
  {
    name: 'revokeAccess',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [
      { name: '_assetHash', type: 'uint256' },
      { name: '_caller',    type: 'address' },
    ],
    outputs: [],
  },
  {
    name: 'setPaywallActive',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [
      { name: '_assetHash', type: 'uint256' },
      { name: '_active',    type: 'bool'    },
    ],
    outputs: [],
  },

  // ── 事件 ────────────────────────────────────────────────────────
  {
    name: 'AssetRegistered',
    type: 'event',
    inputs: [
      { name: 'assetHash',  type: 'uint256', indexed: true  },
      { name: 'modality',   type: 'string',  indexed: false },
      { name: 'domainKey',  type: 'string',  indexed: false },
      { name: 'audioScene', type: 'string',  indexed: false },
      { name: 'hashAlgo',   type: 'uint8',   indexed: false },
    ],
  },
  {
    name: 'AccessGranted',
    type: 'event',
    inputs: [
      { name: 'assetHash',  type: 'uint256', indexed: true  },
      { name: 'caller',     type: 'address', indexed: true  },
      { name: 'expiresAt',  type: 'uint256', indexed: false },
      { name: 'callQuota',  type: 'uint256', indexed: false },
    ],
  },
  {
    name: 'PaywallTriggered',
    type: 'event',
    inputs: [
      { name: 'assetHash',          type: 'uint256', indexed: true  },
      { name: 'unauthorizedCaller', type: 'address', indexed: true  },
      { name: 'modality',           type: 'string',  indexed: false },
      { name: 'fingerprintType',    type: 'string',  indexed: false },
      { name: 'timestamp',          type: 'uint256', indexed: false },
    ],
  },
  {
    name: 'PaymentSettled',
    type: 'event',
    inputs: [
      { name: 'assetHash',     type: 'uint256', indexed: true  },
      { name: 'totalPayment',  type: 'uint256', indexed: false },
      { name: 'creatorShare',  type: 'uint256', indexed: false },
      { name: 'platformShare', type: 'uint256', indexed: false },
      { name: 'fundShare',     type: 'uint256', indexed: false },
    ],
  },
  {
    name: 'AccessRevoked',
    type: 'event',
    inputs: [
      { name: 'assetHash', type: 'uint256', indexed: true },
      { name: 'caller',    type: 'address', indexed: true },
    ],
  },
  {
    name: 'DataConsumed',
    type: 'event',
    inputs: [
      { name: 'assetHash', type: 'uint256', indexed: true  },
      { name: 'caller',    type: 'address', indexed: true  },
      { name: 'callsUsed', type: 'uint256', indexed: false },
    ],
  },
] as const;

// HashAlgorithm enum（与合约完全对齐）
export const HashAlgorithm = {
  SIMHASH: 0,  // 文本
  PHASH:   1,  // 图像
  AFP:     2,  // 音频
  VIDHASH: 0,  // 视频暂用 SIMHASH（Stage A）
} as const;

// 模态 → HashAlgorithm 映射
export const modalityToHashAlgo = (modality) => {
  const m = { text: 0, image: 1, audio: 2, video: 0 };
  return m[modality] ?? 0;
};
