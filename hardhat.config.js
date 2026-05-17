require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

/**
 * Hardhat 配置 — AI-Echo Protocol
 * ==================================
 * 安装依赖：
 *   npm install --save-dev hardhat @nomicfoundation/hardhat-toolbox dotenv
 *
 * 常用命令：
 *   npx hardhat compile               # 编译合约，生成 ABI
 *   npx hardhat test                  # 运行测试
 *   npx hardhat node                  # 本地节点 (localhost:8545)
 *   npx hardhat run scripts/deploy.js --network sepolia
 *
 * .env 需要配置：
 *   DEPLOYER_PRIVATE_KEY — 部署钱包私钥（不要 0x 前缀）
 *   SEPOLIA_RPC_URL      — Alchemy/Infura Sepolia RPC
 *   ETHERSCAN_API_KEY    — 用于合约验证（可选）
 */

const DEPLOYER_KEY = process.env.DEPLOYER_PRIVATE_KEY;
const SEPOLIA_RPC  = process.env.SEPOLIA_RPC_URL || "https://rpc.sepolia.org";

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },

  networks: {
    // 本地开发节点
    localhost: {
      url: "http://127.0.0.1:8545",
    },
    hardhat: {
      chainId: 31337,
    },
    // Sepolia 测试网
    sepolia: {
      url:      SEPOLIA_RPC,
      accounts: DEPLOYER_KEY ? [`0x${DEPLOYER_KEY}`] : [],
      chainId:  11155111,
    },
  },

  etherscan: {
    apiKey: {
      sepolia: process.env.ETHERSCAN_API_KEY || "",
    },
  },

  paths: {
    sources:   "./contracts",
    tests:     "./test",
    cache:     "./hardhat_cache",
    artifacts: "./hardhat_artifacts",
  },
};
