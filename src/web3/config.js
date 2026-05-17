/**
 * Web3 配置 — AI-Echo Protocol
 * ================================
 * 技术栈: wagmi v2 + viem + RainbowKit v2
 *
 * 支持网络:
 *   - Sepolia 测试网 (主要开发测试)
 *   - Ethereum 主网  (生产，合约部署后启用)
 *
 * 环境变量 (在 .env 中配置):
 *   VITE_WALLETCONNECT_PROJECT_ID — WalletConnect Cloud 项目 ID
 *   VITE_CONTRACT_ADDRESS         — 已部署合约地址
 *   VITE_CHAIN_ID                 — 目标链 ID (默认 11155111 = Sepolia)
 */

import { getDefaultConfig } from '@rainbow-me/rainbowkit';
import { sepolia, mainnet, hardhat } from 'wagmi/chains';

// WalletConnect Cloud Project ID
// 免费申请：https://cloud.walletconnect.com
const WC_PROJECT_ID =
  import.meta.env.VITE_WALLETCONNECT_PROJECT_ID || 'demo_project_id_replace_me';

// 已部署合约地址（部署后从 .env 读取）
export const CONTRACT_ADDRESS =
  import.meta.env.VITE_CONTRACT_ADDRESS || '0x0000000000000000000000000000000000000000';

// 目标链（默认 Sepolia）
const TARGET_CHAIN_ID = Number(import.meta.env.VITE_CHAIN_ID || 11155111);

// 链列表（开发时包含本地 hardhat 节点）
const chains = import.meta.env.DEV
  ? [sepolia, hardhat, mainnet]
  : [sepolia, mainnet];

// 找到目标链对象
export const targetChain =
  chains.find((c) => c.id === TARGET_CHAIN_ID) ?? sepolia;

// wagmi + RainbowKit 统一配置
export const wagmiConfig = getDefaultConfig({
  appName:     'AI-Echo Protocol',
  appDescription: '多模态 AI 数据资产定价与产权保护协议',
  appUrl:      'https://ai-echo.io',
  appIcon:     '/favicon.ico',
  projectId:   WC_PROJECT_ID,
  chains,
  ssr: false,  // Vite SPA，不需要 SSR
});

// Sepolia 区块浏览器
export const BLOCK_EXPLORER = {
  11155111: 'https://sepolia.etherscan.io',
  1:        'https://etherscan.io',
  31337:    'http://localhost:8545',  // hardhat local
};

export const getTxUrl = (txHash) =>
  `${BLOCK_EXPLORER[targetChain.id] ?? 'https://sepolia.etherscan.io'}/tx/${txHash}`;

export const getAddressUrl = (address) =>
  `${BLOCK_EXPLORER[targetChain.id] ?? 'https://sepolia.etherscan.io'}/address/${address}`;
