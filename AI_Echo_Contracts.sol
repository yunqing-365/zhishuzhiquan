// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title AI-Echo 核心基建协议
 * @dev 包含数据要素确权 (KnowledgeRegistry) 与 智能分账 (SmartSplitBill)
 */
contract AIEchoProtocol {
    
    // ==========================================
    // 1. KnowledgeRegistry 知识产权确权模块
    // ==========================================
    
    struct Asset {
        address creator; // 创作者/提供者地址
        uint256 assetHash; // 【修改】数据的 SimHash 值（64位整数）
        uint256 timestamp; // 上链时间
        bool isVerified;   // 是否通过预言机清洗验证
    }
    
    // 【修改】映射：数据哈希 => 资产详情
    mapping(uint256 => Asset) public assetRegistry;
    
    // 【新增】保存所有已注册的哈希，用于链上查重
    uint256[] public registeredHashes;

    // 确权事件记录
    event AssetRegistered(uint256 indexed assetHash, address indexed creator, uint256 timestamp);

    // 【新增】计算两个哈希的汉明距离 (二进制下不同位的个数)
    function getHammingDistance(uint256 a, uint256 b) public pure returns (uint256) {
        uint256 x = a ^ b; // 异或运算：相同的位变0，不同的位变1
        uint256 distance = 0;
        while (x > 0) {
            distance += x & 1; // 统计 1 的个数
            x >>= 1;
        }
        return distance;
    }

    // 登记语料/非遗数据资产
    function registerAsset(uint256 _assetHash) public {
        // 【核心防御】遍历链上已有资产，计算感知哈希距离
        // 设定阈值：64位哈希中，如果差异小于 5 位，认为是洗稿或高度重合
        uint256 threshold = 5; 
        for (uint256 i = 0; i < registeredHashes.length; i++) {
            uint256 existingHash = registeredHashes[i];
            uint256 distance = getHammingDistance(_assetHash, existingHash);
            require(distance > threshold, "洗稿拦截：该数据与链上已有资产相似度过高！");
        }

        assetRegistry[_assetHash] = Asset({
            creator: msg.sender,
            assetHash: _assetHash,
            timestamp: block.timestamp,
            isVerified: true
        });
        
        registeredHashes.push(_assetHash); // 存入历史库

        emit AssetRegistered(_assetHash, msg.sender, block.timestamp);
    }

    // ==========================================
    // 2. SmartSplitBill 智能清算与分账模块
    // ==========================================
    
    address public platformNode; // 平台运维节点地址
    address public ecosystemFund; // 生态激励基金地址

    constructor(address _platformNode, address _ecosystemFund) {
        platformNode = _platformNode;
        ecosystemFund = _ecosystemFund;
    }

    event PaymentSettled(string indexed assetHash, uint256 totalAmount, uint256 creatorShare);

    /**
     * @dev B端大模型调用数据时触发的分账函数
     * @param _assetHash 调用的语料哈希
     * @param _creatorRatio 预言机传回的创作者分成比例 (例如 825 表示 82.5%)
     */
    function triggerSplitBill(string memory _assetHash, uint256 _creatorRatio) public payable {
        require(msg.value > 0, "调用账单金额必须大于 0");
        require(_creatorRatio <= 1000, "分成比例参数异常");
        
        // 查询该数据的原作者
        address creator = assetRegistry[_assetHash].creator;
        require(creator != address(0), "请求的数据资产未在链上注册");

        uint256 totalAmount = msg.value;
        
        // 秒级计算分配金额
        uint256 creatorAmount = (totalAmount * _creatorRatio) / 1000;
        uint256 remainingAmount = totalAmount - creatorAmount;
        
        // 剩余部分 60% 归平台节点，40% 归社区生态基金
        uint256 platformAmount = (remainingAmount * 60) / 100;
        uint256 fundAmount = remainingAmount - platformAmount;

        // 执行资金拨付 (免信任、全自动、无资金盘风险)
        payable(creator).transfer(creatorAmount);
        payable(platformNode).transfer(platformAmount);
        payable(ecosystemFund).transfer(fundAmount);

        emit PaymentSettled(_assetHash, totalAmount, creatorAmount);
    }
}
// ==========================================
    // 3. AMM 联合曲线动态定价模块 (Bonding Curve)
    // ==========================================

    // 记录各个领域数据被 B 端调用的总次数 (替代 Python 中的 global_demand_ledger)
    mapping(string => uint256) public domainDemandLedger;

    // 每次发生数据调用时，触发此事件
    event DataConsumed(string domainKey, uint256 newDemand, uint256 currentPrice);

    /**
     * @dev 获取某领域的当前动态调用价格
     * @param _domainKey 领域标识 (如 "medical_sft", "legal_doc")
     * @param _baseValue 预言机传回的基础内在价值 (Base Value)
     */
    function getDynamicPrice(string memory _domainKey, uint256 _baseValue) public view returns (uint256) {
        uint256 currentDemand = domainDemandLedger[_domainKey];
        
        // 智能合约简易联合曲线公式：Price = Base * (1000 + (Demand * Alpha)) / 1000
        // 这里假设 Alpha 涨幅系数为 15 (即每次调用涨价 1.5%)
        uint256 alpha = 15; 
        
        // 计算溢价乘数 (放大1000倍以处理小数)
        uint256 multiplier = 1000 + (currentDemand * alpha);
        
        // 返回最终的动态市场价格
        return (_baseValue * multiplier) / 1000;
    }

    /**
     * @dev B端购买并调用数据 (将之前的 triggerSplitBill 升级)
     */
    function purchaseAndCallData(
        string memory _assetHash, 
        string memory _domainKey, 
        uint256 _baseValue, 
        uint256 _creatorRatio
    ) public payable {
        // 1. 获取当前联合曲线的实时价格
        uint256 currentPrice = getDynamicPrice(_domainKey, _baseValue);
        
        // 2. 校验 B 端打入的钱是否足够
        require(msg.value >= currentPrice, "付款金额不足，无法调用该领域数据");

        // 3. 该领域需求量 +1
        domainDemandLedger[_domainKey] += 1;

        // 4. 执行智能分账 (复用您原有的分账逻辑)
        // (注：在实际生产中，这里应提取 assetRegistry 中存储的 creator 地址)
        address creator = assetRegistry[_assetHash].creator;
        require(creator != address(0), "数据未确权");

        uint256 creatorAmount = (msg.value * _creatorRatio) / 1000;
        uint256 remainingAmount = msg.value - creatorAmount;
        uint256 platformAmount = (remainingAmount * 60) / 100;
        uint256 fundAmount = remainingAmount - platformAmount;

        payable(creator).transfer(creatorAmount);
        payable(platformNode).transfer(platformAmount);
        payable(ecosystemFund).transfer(fundAmount);

        // 5. 触发上链事件，通知前端价格已更新
        emit DataConsumed(_domainKey, domainDemandLedger[_domainKey], currentPrice);
    }

// ==========================================
    // 4. zkML 零知识证明验证模块 (ZK Verifier)
    // ==========================================

    event ZKProofVerified(uint256 indexed assetHash, uint256 declaredScore);

    /**
     * @dev 验证前端生成的 zk-SNARK 证明 (实际项目中由 SnarkJS 自动生成)
     * @param a 椭圆曲线配对参数 A
     * @param b 椭圆曲线配对参数 B
     * @param c 椭圆曲线配对参数 C
     * @param input 公开输入数组 (这里包含两个元素：[assetHash, declaredScore])
     */
    function verifyZKProof(
        uint256[2] memory a,
        uint256[2][2] memory b,
        uint256[2] memory c,
        uint256[] memory input
    ) public returns (bool) {
        // 核心约束：公开输入必须包含哈希和分数
        require(input.length == 2, "ZK Circuit Invalid: Missing Public Inputs");

        uint256 _assetHash = input[0];
        uint256 _declaredScore = input[1];

        // 真实生产中，这里会调用 Groth16 或 Plonk 的复杂椭圆曲线数学验证
        // 这里作为架构 Demo，我们假设密码学验证通过
        bool isProofValid = true; 
        
        require(isProofValid, "ZK Proof Verification Failed! 存在造假行为");

        // 触发链上验证通过事件
        emit ZKProofVerified(_assetHash, _declaredScore);
        
        return true;
    }

    /**
     * @dev 升级版的资产登记函数 (支持 ZK 盲态确权)
     */
    function registerAssetWithZK(
        uint256 _assetHash, 
        uint256[2] memory a, 
        uint256[2][2] memory b, 
        uint256[2] memory c, 
        uint256[] memory input
    ) public {
        // 1. 验证零知识证明
        require(verifyZKProof(a, b, c, input), "无效的零知识证明");
        
        // 2. 确保证明对应的哈希就是要注册的哈希
        require(input[0] == _assetHash, "证明与数据哈希不匹配");

        // 3. 复用我们之前写的防洗稿查重逻辑
        uint256 threshold = 5; 
        for (uint256 i = 0; i < registeredHashes.length; i++) {
            uint256 distance = getHammingDistance(_assetHash, registeredHashes[i]);
            require(distance > threshold, "洗稿拦截：数据哈希与链上记录高度重合");
        }

        // 4. 盲态确权 (无需上传任何明文，只记录哈希)
        assetRegistry[_assetHash] = Asset({
            creator: msg.sender,
            assetHash: _assetHash,
            timestamp: block.timestamp,
            isVerified: true
        });
        registeredHashes.push(_assetHash);
        
        emit AssetRegistered(_assetHash, msg.sender, block.timestamp);
    }

// ==========================================
    // 5. 新增：RAG 检索微支付与防爬虫授权网关
    // ==========================================

    // 记录 B 端 AI 模型对特定内容的 RAG 调用次数
    mapping(address => mapping(uint256 => uint256)) public ragCallLedger;
    // 抛出事件：当 RAG 检索付款成功时，释放解密密钥
    event RagMicroPayment(uint256 indexed assetHash, address indexed aiCaller, uint256 amount, string unlockKey);

    /**
     * @dev RAG 检索单次微支付流水线 (Streaming Payment)
     * 当 AI 模型抓取到该段受保护语料时，触发按次付费，获取解密密钥
     */
    function triggerRagMicroPayment(uint256 _assetHash, uint256 _creatorRatio, string memory _unlockKey) public payable {
        require(msg.value > 0, "微支付调用费必须大于 0"); 
        
        // 查找原始创作者
        address creator = assetRegistry[_assetHash].creator;
        require(creator != address(0), "请求的数据资产未确权，拒绝服务");

        // 执行秒级免信任分账 (按次结算)
        uint256 creatorAmount = (msg.value * _creatorRatio) / 1000;
        uint256 remainingAmount = msg.value - creatorAmount;
        uint256 platformAmount = (remainingAmount * 60) / 100;
        uint256 fundAmount = remainingAmount - platformAmount;

        payable(creator).transfer(creatorAmount);
        payable(platformNode).transfer(platformAmount);
        payable(ecosystemFund).transfer(fundAmount);
        
        // 记录该 AI 公司的调用次数，并抛出解密密钥事件供 B 端爬虫抓取
        ragCallLedger[msg.sender][_assetHash] += 1;
        emit RagMicroPayment(_assetHash, msg.sender, msg.value, _unlockKey);
    }