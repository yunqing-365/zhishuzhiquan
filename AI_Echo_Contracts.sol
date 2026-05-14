// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title AI-Echo 核心基建协议 (Multi-modal V3.0)
 * @dev 包含 Token 当量(TEV)确权、多模态资产册、跨领域 AMM 动态定价与智能分账
 */
contract AIEchoProtocol {
    
    address public platformAdmin;      // 平台运维节点
    address public ecosystemFund;      // 社区生态治理基金

    // ==========================================
    // 1. 多模态核心资产结构 (Asset Registry)
    // ==========================================
    struct Asset {
        address creator;       // 创作者地址
        uint256 assetHash;     // 资产底层哈希 (DCT / GraphRAG SimHash)
        string modality;       // 模态类型 ("image", "text")
        string domainKey;      // 垂直交易领域 ("visual_art", "medical_sft")
        uint256 baseValue;     // 预言机写入的初始基准价值 (Base Value)
        uint256 timestamp;     
    }
    
    mapping(uint256 => Asset) public assetRegistry;
    uint256[] public registeredHashes;

    // ==========================================
    // 2. 联合曲线 (AMM) 领域配置
    // ==========================================
    struct DomainConfig {
        uint256 alpha;         // AMM 涨幅系数 (例如 25 表示每次调用涨价 2.5%)
        bool isActive;         // 领域是否开放
    }
    
    mapping(string => DomainConfig) public domainRegistry;
    mapping(string => uint256) public domainDemandLedger; // 各领域的累计被调用次数

    // 事件日志 (供前端监听展现)
    event AssetRegistered(uint256 indexed assetHash, string modality, string domainKey);
    event DataConsumed(string domainKey, uint256 newDemand, uint256 currentPrice);
    event PaymentSettled(uint256 indexed assetHash, uint256 totalAmount, uint256 creatorShare);

    constructor(address _platformNode, address _ecosystemFund) {
        platformAdmin = _platformNode;
        ecosystemFund = _ecosystemFund;
        
        // 【核心商业逻辑】：为不同领域配置不同的涨价斜率
        // 医疗垂直语料：极度稀缺，调用涨价极快 (Alpha = 25)
        domainRegistry["medical_sft"] = DomainConfig({alpha: 25, isActive: true});
        // 商业视觉原画：中高稀缺度，稳步涨价 (Alpha = 15)
        domainRegistry["visual_art"] = DomainConfig({alpha: 15, isActive: true});
        // 通用废话/普通数据：不涨价或涨价极慢 (Alpha = 2)
        domainRegistry["general"] = DomainConfig({alpha: 2, isActive: true});
    }

    // ==========================================
    // 3. 资产入表与确权
    // ==========================================
    function getHammingDistance(uint256 a, uint256 b) public pure returns (uint256) {
        uint256 x = a ^ b;
        uint256 distance = 0;
        while (x > 0) { distance += x & 1; x >>= 1; }
        return distance;
    }

    /**
     * @dev 接收 Python 预言机(Oracle)传来的 6 维质量验证结果并上链
     */
    function registerAsset(
        uint256 _assetHash, 
        string memory _modality, 
        string memory _domainKey,
        uint256 _baseValue
    ) public {
        require(domainRegistry[_domainKey].isActive, "该垂直领域尚未开放");
        require(_baseValue > 0, "低质量垃圾数据触发熔断，拒绝上链");

        // 防洗稿机制
        uint256 threshold = 5;
        for (uint256 i = 0; i < registeredHashes.length; i++) {
            require(getHammingDistance(_assetHash, registeredHashes[i]) > threshold, "防洗稿拦截：数据特征与链上存量高度重合！");
        }

        assetRegistry[_assetHash] = Asset({
            creator: msg.sender,
            assetHash: _assetHash,
            modality: _modality,
            domainKey: _domainKey,
            baseValue: _baseValue,
            timestamp: block.timestamp
        });
        registeredHashes.push(_assetHash);

        emit AssetRegistered(_assetHash, _modality, _domainKey);
    }

    // ==========================================
    // 4. B端大厂自动采购与清算网关
    // ==========================================
    function getDynamicPrice(string memory _domainKey, uint256 _baseValue) public view returns (uint256) {
        uint256 currentDemand = domainDemandLedger[_domainKey];
        uint256 alpha = domainRegistry[_domainKey].alpha;
        
        // 联合曲线核心公式：Price = Base * (1000 + (Demand * Alpha)) / 1000
        uint256 multiplier = 1000 + (currentDemand * alpha);
        return (_baseValue * multiplier) / 1000;
    }

    /**
     * @dev AI 大厂调用数据，触发免信任秒级清算
     */
    function purchaseAndCallData(uint256 _assetHash, uint256 _creatorRatio) public payable {
        Asset memory targetAsset = assetRegistry[_assetHash];
        require(targetAsset.creator != address(0), "数据未确权");

        // 1. 获取该领域的实时 AMM 价格
        uint256 currentPrice = getDynamicPrice(targetAsset.domainKey, targetAsset.baseValue);
        require(msg.value >= currentPrice, "付款金额低于当前 AMM 联合曲线报价");

        // 2. 领域需求量推高，驱动联合曲线右移
        domainDemandLedger[targetAsset.domainKey] += 1;

        // 3. 智能合约底层秒级分账
        uint256 creatorAmount = (msg.value * _creatorRatio) / 1000;
        uint256 remainingAmount = msg.value - creatorAmount;
        uint256 platformAmount = (remainingAmount * 60) / 100;
        uint256 fundAmount = remainingAmount - platformAmount;

        payable(targetAsset.creator).transfer(creatorAmount);
        payable(platformAdmin).transfer(platformAmount);
        payable(ecosystemFund).transfer(fundAmount);

        // 4. 释放授权解密密钥（供大厂爬虫抓取使用）
        emit PaymentSettled(_assetHash, msg.value, creatorAmount);
        emit DataConsumed(targetAsset.domainKey, domainDemandLedger[targetAsset.domainKey], currentPrice);
    }
}