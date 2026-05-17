/**
 * 部署脚本 — AI-Echo Protocol
 * ================================
 * 用法（Sepolia 测试网）:
 *   npx hardhat run scripts/deploy.js --network sepolia
 *
 * 部署完成后：
 *   1. 把控制台输出的合约地址填入 .env 的 VITE_CONTRACT_ADDRESS
 *   2. 把合约地址填入 .env 的 SEPOLIA_CONTRACT_ADDRESS
 *   3. （可选）验证合约：npx hardhat verify --network sepolia <合约地址> <platformAdmin> <ecosystemFund>
 */

const { ethers } = require("hardhat");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("\n═══════════════════════════════════════");
  console.log("  AI-Echo Protocol — 部署脚本 v1.0");
  console.log("═══════════════════════════════════════");
  console.log(`  部署账户: ${deployer.address}`);

  const balance = await ethers.provider.getBalance(deployer.address);
  console.log(`  账户余额: ${ethers.formatEther(balance)} ETH`);

  const network = await ethers.provider.getNetwork();
  console.log(`  目标网络: ${network.name} (chainId: ${network.chainId})`);

  if (balance < ethers.parseEther("0.01")) {
    console.warn("\n⚠ 余额不足 0.01 ETH，部署可能失败");
    console.warn("  Sepolia 水龙头: https://sepoliafaucet.com");
  }

  // ── 部署参数 ─────────────────────────────────────────────────────
  // platformAdmin: 收取平台手续费的地址（可以是多签钱包）
  const platformAdmin = process.env.PLATFORM_ADMIN_ADDRESS || deployer.address;
  // ecosystemFund: 收取生态基金份额的地址
  const ecosystemFund = process.env.ECOSYSTEM_FUND_ADDRESS || deployer.address;

  console.log(`\n  platformAdmin : ${platformAdmin}`);
  console.log(`  ecosystemFund : ${ecosystemFund}`);

  // ── 编译 & 部署 ──────────────────────────────────────────────────
  console.log("\n>> 编译合约...");
  const Factory = await ethers.getContractFactory("AIEchoProtocol");

  console.log(">> 发送部署交易...");
  const contract = await Factory.deploy(platformAdmin, ecosystemFund);
  await contract.waitForDeployment();

  const address = await contract.getAddress();
  const txHash  = contract.deploymentTransaction()?.hash;

  console.log("\n✅ 部署成功！");
  console.log(`\n  合约地址: ${address}`);
  if (txHash) {
    const explorerBase = network.chainId === 11155111n
      ? "https://sepolia.etherscan.io"
      : "https://etherscan.io";
    console.log(`  交易哈希: ${txHash}`);
    console.log(`  浏览器:   ${explorerBase}/address/${address}`);
  }

  // ── 初始化 AMM 场景配置 ──────────────────────────────────────────
  console.log("\n>> 初始化 AMM 场景配置（与 scoring.py 同步）...");
  const domainConfigs = [
    { key: "medical_sft",  alpha: 32 },
    { key: "legal_doc",    alpha: 28 },
    { key: "code_tech",    alpha: 22 },
    { key: "creative",     alpha: 18 },
    { key: "chat_qa",      alpha: 12 },
    { key: "illustration", alpha: 20 },
    { key: "photo",        alpha: 15 },
    { key: "diagram",      alpha: 10 },
    { key: "screenshot",   alpha: 5  },
    { key: "speech_medical", alpha: 38 },
    { key: "speech_legal",   alpha: 32 },
    { key: "speech_edu",     alpha: 20 },
    { key: "music_original", alpha: 22 },
    { key: "ambient_sfx",    alpha: 14 },
    { key: "noise",          alpha: 0  },
    { key: "general",        alpha: 15 },
  ];

  for (const { key, alpha } of domainConfigs) {
    try {
      const tx = await contract.initDomain(key, alpha);
      await tx.wait();
      process.stdout.write(`  ✓ ${key.padEnd(18)} alpha=${alpha}\n`);
    } catch (e) {
      // initDomain 可能不存在于当前合约版本，忽略
      if (e.message?.includes("not a function") || e.code === "CALL_EXCEPTION") {
        console.log("  (跳过场景初始化 — 合约无 initDomain 函数，需手动配置)");
        break;
      }
    }
  }

  // ── 输出 .env 配置片段 ───────────────────────────────────────────
  console.log("\n════════════════════════════════════════");
  console.log("  把以下内容更新到 .env 文件:");
  console.log("════════════════════════════════════════");
  console.log(`VITE_CONTRACT_ADDRESS=${address}`);
  console.log(`SEPOLIA_CONTRACT_ADDRESS=${address}`);
  console.log(`VITE_CHAIN_ID=${network.chainId}`);
  console.log("\n════════════════════════════════════════\n");
}

main().catch((error) => {
  console.error("\n✗ 部署失败:", error.message);
  process.exit(1);
});
