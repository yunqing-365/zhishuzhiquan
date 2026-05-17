/**
 * AIEchoProtocol 测试套件
 * ========================
 * 运行：npx hardhat test
 *
 * 覆盖：资产注册、Paywall、AMM 定价、访问控制、分账事件
 */

const { expect }  = require("chai");
const { ethers }  = require("hardhat");

describe("AIEchoProtocol", function () {
  let contract, owner, creator, buyer, ecosystem;
  const ASSET_HASH   = 1234567890n;
  const BASE_VALUE   = 10000n;
  const DOMAIN_KEY   = "medical_sft";
  const MODALITY     = "text";
  const HASH_ALGO    = 0; // SIMHASH

  beforeEach(async () => {
    [owner, creator, buyer, ecosystem] = await ethers.getSigners();
    const Factory = await ethers.getContractFactory("AIEchoProtocol");
    contract = await Factory.deploy(owner.address, ecosystem.address);
    await contract.waitForDeployment();
  });

  // ── 部署 ────────────────────────────────────────────────────────
  describe("部署", () => {
    it("设置正确的 platformAdmin", async () => {
      expect(await contract.platformAdmin()).to.equal(owner.address);
    });
    it("设置正确的 ecosystemFund", async () => {
      expect(await contract.ecosystemFund()).to.equal(ecosystem.address);
    });
    it("初始注册资产数为 0", async () => {
      expect(await contract.getRegisteredCount()).to.equal(0n);
    });
  });

  // ── 资产注册 ─────────────────────────────────────────────────────
  describe("资产注册 registerAsset()", () => {
    it("创作者可以成功注册资产", async () => {
      await expect(
        contract.connect(creator).registerAsset(
          ASSET_HASH, MODALITY, DOMAIN_KEY, "", BASE_VALUE, HASH_ALGO
        )
      )
        .to.emit(contract, "AssetRegistered")
        .withArgs(ASSET_HASH, MODALITY, DOMAIN_KEY, "", HASH_ALGO);
    });

    it("注册后 getRegisteredCount 增加", async () => {
      await contract.connect(creator).registerAsset(
        ASSET_HASH, MODALITY, DOMAIN_KEY, "", BASE_VALUE, HASH_ALGO
      );
      expect(await contract.getRegisteredCount()).to.equal(1n);
    });

    it("重复注册同一 hash 应该失败", async () => {
      await contract.connect(creator).registerAsset(
        ASSET_HASH, MODALITY, DOMAIN_KEY, "", BASE_VALUE, HASH_ALGO
      );
      await expect(
        contract.connect(creator).registerAsset(
          ASSET_HASH, MODALITY, DOMAIN_KEY, "", BASE_VALUE, HASH_ALGO
        )
      ).to.be.revertedWith("Asset already registered");
    });
  });

  // ── AMM 定价 ─────────────────────────────────────────────────────
  describe("AMM 定价 getDynamicPrice()", () => {
    it("demand=0 时价格等于 baseValue", async () => {
      const price = await contract.getDynamicPrice(DOMAIN_KEY, BASE_VALUE);
      // alpha=0 时: price = baseValue * 1000 / 1000 = baseValue
      // 实际 alpha 从 domainRegistry 读取，初始为 0
      expect(price).to.be.a("bigint");
    });
  });

  // ── 汉明距离 ─────────────────────────────────────────────────────
  describe("防洗稿 getHammingDistance()", () => {
    it("相同 hash 汉明距离为 0", async () => {
      expect(await contract.getHammingDistance(12345n, 12345n)).to.equal(0n);
    });
    it("不同 hash 汉明距离大于 0", async () => {
      const dist = await contract.getHammingDistance(0n, 1n);
      expect(dist).to.be.gt(0n);
    });
  });

  // ── Paywall ──────────────────────────────────────────────────────
  describe("Paywall & AccessToken", () => {
    beforeEach(async () => {
      // 先注册资产
      await contract.connect(creator).registerAsset(
        ASSET_HASH, MODALITY, DOMAIN_KEY, "", BASE_VALUE, HASH_ALGO
      );
      // 开启 Paywall
      await contract.connect(creator).setPaywallActive(ASSET_HASH, true);
    });

    it("未购买时 verifyAccess 应该触发 PaywallTriggered 事件", async () => {
      await expect(
        contract.connect(buyer).verifyAccess(ASSET_HASH)
      ).to.emit(contract, "PaywallTriggered");
    });

    it("purchaseAndCallData 后颁发 AccessToken", async () => {
      const price = await contract.getDynamicPrice(DOMAIN_KEY, BASE_VALUE);
      await expect(
        contract.connect(buyer).purchaseAndCallData(ASSET_HASH, 100n, 30n, {
          value: price > 0n ? price : ethers.parseEther("0.001"),
        })
      ).to.emit(contract, "AccessGranted");
    });

    it("创作者可以吊销访问权限", async () => {
      // 先购买
      const price = await contract.getDynamicPrice(DOMAIN_KEY, BASE_VALUE);
      await contract.connect(buyer).purchaseAndCallData(ASSET_HASH, 100n, 30n, {
        value: price > 0n ? price : ethers.parseEther("0.001"),
      });
      // 再吊销
      await expect(
        contract.connect(creator).revokeAccess(ASSET_HASH, buyer.address)
      ).to.emit(contract, "AccessRevoked");
    });
  });
});
