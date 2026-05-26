// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  AI-Echo 核心基建协议 (Multi-modal V5.0)
 * @author AI-Echo Protocol Team
 *
 * v3 → v5 产权保护升级：
 *
 *   [★ 新增] PaywallGuard — 链上指纹拦截模块
 *     • registerFingerprint(): 资产上链同时注册指纹，含模态类型与哈希算法标记
 *     • verifyAccess(): B端调用前必须先验证访问凭证，凭证由 purchaseAndCallData 颁发
 *     • revokeAccess(): 创作者随时吊销某调用方的访问权限
 *     • AccessToken 结构：记录调用方地址、过期时间、已用次数、剩余调用配额
 *
 *   [★ 新增] FingerprintRegistry — 多模态指纹注册表
 *     • 支持三种哈希算法标记：SIMHASH(文本) / PHASH(图像) / AFP(音频声学指纹)
 *     • getHammingDistance() 用于防洗稿检测（SimHash / pHash 相似度）
 *     • getAudioFingerprint() 专用于 AFP 声学指纹冲突检测
 *
 *   [★ 新增] 音频细粒度场景 AMM 配置（与 scoring.py AMM_SCENE_CONFIG 同步）
 *     • speech_medical  alpha=38  医疗ASR，极稀缺
 *     • speech_legal    alpha=32  庭审语音，司法结构化
 *     • speech_edu      alpha=20  教育TTS
 *     • music_original  alpha=22  原创音乐生成
 *     • ambient_sfx     alpha=14  游戏音效
 *     • general(audio)  alpha=25  兜底
 *
 *   [★ v6 新增] 视频细粒度场景 AMM 配置（与 scoring.py 完全对齐）
 *     • documentary    alpha=36  纪录片/采访/庭审，极高语义密度
 *     • lecture        alpha=30  教学/演讲，TTS & 知识蒸馏
 *     • cinematic      alpha=26  影视级制作，LoRA/T2V 训练
 *     • sports_action  alpha=18  运动/动作，时序建模
 *     • vlog           alpha=12  个人 vlog，质量参差
 *     • Asset.videoScene 字段存储细粒度标签，AMM 优先使用 videoScene
 *
 *   [★ v6 新增] 四模态 Asset.modality 支持 "text" / "image" / "audio" / "video"
 *     • Audio 资产额外存储 audioScene 字段（细粒度标签）
 *     • Video 资产额外存储 videoScene 字段（细粒度标签）
 *     • AMM effectiveDomain 优先级：videoScene > audioScene > domainKey
 *
 *   [保留] v3 防洗稿：getHammingDistance + 汉明距离阈值检测
 *   [保留] v3 AMM 联合曲线：getDynamicPrice / purchaseAndCallData
 *   [保留] v3 分账逻辑：创作者 / 平台 / 生态基金三路清算
 */
contract AIEchoProtocol {

    // ═══════════════════════════════════════════════════════════════
    // 基础角色
    // ═══════════════════════════════════════════════════════════════
    address public platformAdmin;
    address public ecosystemFund;

    // ═══════════════════════════════════════════════════════════════
    // 1. 多模态哈希算法标记
    // ═══════════════════════════════════════════════════════════════
    enum HashAlgorithm {
        SIMHASH,   // 0 — 文本 SimHash（抗同义替换）
        PHASH,     // 1 — 图像感知哈希 DCT-pHash（抗缩放/重绘）
        AFP        // 2 — 音频声学指纹 Audio Fingerprint（抗重录/变调）
    }

    // ═══════════════════════════════════════════════════════════════
    // 2. 多模态核心资产结构
    // ═══════════════════════════════════════════════════════════════
    struct Asset {
        address       creator;
        uint256       assetHash;
        string        modality;      // "text" | "image" | "audio" | "video"
        string        domainKey;     // TEV 场景 (medical_sft / illustration / general …)
        string        audioScene;    // 音频细粒度场景 (仅 audio 模态；其他为 "")
        string        videoScene;    // ★ v6: 视频细粒度场景 (仅 video 模态；其他为 "")
        uint256       baseValue;
        HashAlgorithm hashAlgo;
        uint256       timestamp;
        bool          paywallActive; // Paywall 是否激活
        bytes32       zkCommitment;  // ★ Stage 2: ZK 承诺（poseidon_commitment_v1 / 0x0 = 未使用）
    }

    mapping(uint256 => Asset) public assetRegistry;
    uint256[] public registeredHashes;

    // ═══════════════════════════════════════════════════════════════
    // 3. ★ 新增：Paywall 访问凭证结构
    // ═══════════════════════════════════════════════════════════════
    struct AccessToken {
        address callerAddr;     // 被授权的 B 端调用方地址
        uint256 expiresAt;      // 凭证过期时间 (unix timestamp)
        uint256 callsRemaining; // 剩余可调用次数（按购买 tier 决定）
        uint256 callsUsed;      // 已消耗次数
        bool    revoked;        // 是否被创作者吊销
    }

    // assetHash => callerAddr => AccessToken
    mapping(uint256 => mapping(address => AccessToken)) public accessTokens;

    // ═══════════════════════════════════════════════════════════════
    // 4. AMM 场景配置（与 scoring.py AMM_SCENE_CONFIG 完全对齐）
    // ═══════════════════════════════════════════════════════════════
    struct DomainConfig {
        uint256 alpha;
        bool    isActive;
    }

    mapping(string => DomainConfig) public domainRegistry;
    mapping(string => uint256)      public domainDemandLedger;

    // ═══════════════════════════════════════════════════════════════
    // 5. 事件日志
    // ═══════════════════════════════════════════════════════════════
    event AssetRegistered(
        uint256 indexed assetHash,
        string  modality,
        string  domainKey,
        string  audioScene,
        HashAlgorithm hashAlgo,
        bytes32 zkCommitment  // ★ Stage 2
    );
    event PaywallTriggered(
        uint256 indexed assetHash,
        address indexed unauthorizedCaller,
        string  modality,
        string  fingerprintType,
        uint256 timestamp
    );
    event AccessGranted(
        uint256 indexed assetHash,
        address indexed caller,
        uint256 expiresAt,
        uint256 callQuota
    );
    event AccessRevoked(
        uint256 indexed assetHash,
        address indexed caller
    );
    event DataConsumed(
        string  domainKey,
        uint256 newDemand,
        uint256 currentPrice
    );
    event PaymentSettled(
        uint256 indexed assetHash,
        uint256 totalAmount,
        uint256 creatorShare
    );

    // ═══════════════════════════════════════════════════════════════
    // 构造器：初始化所有场景 AMM 配置
    // ═══════════════════════════════════════════════════════════════
    constructor(address _platformNode, address _ecosystemFund) {
        platformAdmin = _platformNode;
        ecosystemFund = _ecosystemFund;

        // ── 文本场景 ──────────────────────────────────────────────
        domainRegistry["medical_sft"]  = DomainConfig({alpha: 32, isActive: true});
        domainRegistry["legal_doc"]    = DomainConfig({alpha: 28, isActive: true});
        domainRegistry["code_tech"]    = DomainConfig({alpha: 22, isActive: true});
        domainRegistry["creative"]     = DomainConfig({alpha: 18, isActive: true});
        domainRegistry["chat_qa"]      = DomainConfig({alpha: 15, isActive: true});

        // ── 图像场景 ──────────────────────────────────────────────
        domainRegistry["illustration"] = DomainConfig({alpha: 20, isActive: true});
        domainRegistry["photo"]        = DomainConfig({alpha: 14, isActive: true});
        domainRegistry["diagram"]      = DomainConfig({alpha: 10, isActive: true});
        domainRegistry["screenshot"]   = DomainConfig({alpha:  6, isActive: true});

        // ── 音频细粒度场景（★ v5 新增）────────────────────────────
        // alpha 与 scoring.py AMM_SCENE_CONFIG 完全对齐
        domainRegistry["speech_medical"] = DomainConfig({alpha: 38, isActive: true});
        domainRegistry["speech_legal"]   = DomainConfig({alpha: 32, isActive: true});
        domainRegistry["speech_edu"]     = DomainConfig({alpha: 20, isActive: true});
        domainRegistry["music_original"] = DomainConfig({alpha: 22, isActive: true});
        domainRegistry["ambient_sfx"]    = DomainConfig({alpha: 14, isActive: true});
        domainRegistry["general"]        = DomainConfig({alpha: 25, isActive: true});

        // noise 熔断：alpha=0，不上市场（isActive=false 防止误入）
        domainRegistry["noise"]          = DomainConfig({alpha:  0, isActive: false});

        // ── 视频场景（★ v6: 与 scoring.py AMM_SCENE_CONFIG 完全对齐）──
        // alpha 比同档音频略低（视频稀缺但需求方尚未充分教育）
        domainRegistry["documentary"]   = DomainConfig({alpha: 36, isActive: true});  // 纪录片/采访/庭审
        domainRegistry["lecture"]        = DomainConfig({alpha: 30, isActive: true});  // 教学/演讲
        domainRegistry["cinematic"]      = DomainConfig({alpha: 26, isActive: true});  // 影视级制作
        domainRegistry["sports_action"]  = DomainConfig({alpha: 18, isActive: true});  // 运动/动作
        domainRegistry["vlog"]           = DomainConfig({alpha: 12, isActive: true});  // 个人 vlog
    }

    // ═══════════════════════════════════════════════════════════════
    // 6. 防洗稿工具：汉明距离（SimHash/pHash 通用）
    // ═══════════════════════════════════════════════════════════════
    function getHammingDistance(uint256 a, uint256 b) public pure returns (uint256) {
        uint256 x        = a ^ b;
        uint256 distance = 0;
        while (x > 0) { distance += x & 1; x >>= 1; }
        return distance;
    }

    // AFP 声学指纹：存储为字符串（格式 "0xAFP_<hex12>"），直接字符串比对
    function getAudioFingerprint(uint256 _assetHash) public view returns (string memory) {
        return assetRegistry[_assetHash].modality;  // 占位：实际实现中存储 AFP 字符串
    }

    // ═══════════════════════════════════════════════════════════════
    // 7. 资产注册与确权（★ v6: 支持 video + videoScene）
    // ═══════════════════════════════════════════════════════════════
    function registerAsset(
        uint256       _assetHash,
        string memory _modality,
        string memory _domainKey,
        string memory _audioScene,   // 非音频传 ""
        string memory _videoScene,   // ★ v6: 非视频传 ""
        uint256       _baseValue,
        uint8         _hashAlgo,     // 0=SIMHASH 1=PHASH 2=AFP
        bytes32       _zkCommitment  // ★ Stage 2: ZK 承诺（可传 bytes32(0) 跳过）
    ) public {
        require(domainRegistry[_domainKey].isActive, "该垂直领域尚未开放或已熔断");
        require(_baseValue > 0,                       "低质量垃圾数据触发熔断，拒绝上链");
        require(_hashAlgo <= 2,                       "未知哈希算法类型");

        HashAlgorithm algo = HashAlgorithm(_hashAlgo);

        // ── 防洗稿检测（SimHash/pHash 用汉明距离；AFP 跳过，用链下 Python 检测）
        if (algo != HashAlgorithm.AFP) {
            uint256 threshold = 5;
            for (uint256 i = 0; i < registeredHashes.length; i++) {
                // 只在相同模态间做相似度检测，避免跨模态误判
                if (keccak256(bytes(assetRegistry[registeredHashes[i]].modality))
                        == keccak256(bytes(_modality))) {
                    require(
                        getHammingDistance(_assetHash, registeredHashes[i]) > threshold,
                        "防洗稿拦截：资产特征与链上存量高度重合！"
                    );
                }
            }
        }

        assetRegistry[_assetHash] = Asset({
            creator:       msg.sender,
            assetHash:     _assetHash,
            modality:      _modality,
            domainKey:     _domainKey,
            audioScene:    _audioScene,
            videoScene:    _videoScene,   // ★ v6
            baseValue:     _baseValue,
            hashAlgo:      algo,
            timestamp:     block.timestamp,
            paywallActive: true,
            zkCommitment:  _zkCommitment
        });
        registeredHashes.push(_assetHash);

        emit AssetRegistered(_assetHash, _modality, _domainKey, _audioScene, algo, _zkCommitment);
    }

    // ═══════════════════════════════════════════════════════════════
    // 8. ★ 新增：Paywall 指纹验证入口
    //    B端在调用数据前必须先调用此函数，链上验证访问凭证
    //    未授权调用触发 PaywallTriggered 事件，供前端实时监听展示
    // ═══════════════════════════════════════════════════════════════
    function verifyAccess(uint256 _assetHash) public returns (bool) {
        Asset memory asset = assetRegistry[_assetHash];
        require(asset.creator != address(0), "资产未注册");

        // Paywall 未激活：直接放行（公开数据集）
        if (!asset.paywallActive) return true;

        AccessToken storage token = accessTokens[_assetHash][msg.sender];

        // 判断是否持有有效凭证
        bool hasValidToken = (
            token.callerAddr == msg.sender   &&
            !token.revoked                   &&
            token.expiresAt > block.timestamp &&
            token.callsRemaining > 0
        );

        if (!hasValidToken) {
            // ── 无凭证：触发 Paywall 拦截事件
            string memory fpType = _fingerprintTypeStr(asset.hashAlgo);
            emit PaywallTriggered(
                _assetHash,
                msg.sender,
                asset.modality,
                fpType,
                block.timestamp
            );
            return false;
        }

        // ── 有效凭证：消耗一次调用额度
        token.callsRemaining -= 1;
        token.callsUsed      += 1;
        return true;
    }

    // ═══════════════════════════════════════════════════════════════
    // 9. AMM 动态定价
    // ═══════════════════════════════════════════════════════════════
    function getDynamicPrice(
        string memory _domainKey,
        uint256       _baseValue
    ) public view returns (uint256) {
        uint256 currentDemand = domainDemandLedger[_domainKey];
        uint256 alpha         = domainRegistry[_domainKey].alpha;
        uint256 multiplier    = 1000 + (currentDemand * alpha);
        return (_baseValue * multiplier) / 1000;
    }

    // ═══════════════════════════════════════════════════════════════
    // 10. B端合规采买 + Paywall 凭证颁发 + 分账清算
    // ═══════════════════════════════════════════════════════════════
    /**
     * @param _assetHash    目标资产哈希
     * @param _creatorRatio 创作者分成比例 (‰，例如 850 = 85%)
     * @param _callQuota    购买的调用次数配额 (例如 100 次)
     * @param _ttlDays      凭证有效天数 (例如 30)
     */
    function purchaseAndCallData(
        uint256 _assetHash,
        uint256 _creatorRatio,
        uint256 _callQuota,
        uint256 _ttlDays
    ) public payable {
        Asset memory targetAsset = assetRegistry[_assetHash];
        require(targetAsset.creator != address(0), "数据未确权");

        // 多模态细粒度 AMM 场景选取：
        //   视频资产 → videoScene（如 documentary / lecture / cinematic…）
        //   音频资产 → audioScene（如 speech_medical / music_original…）
        //   其他     → domainKey（如 medical_sft / illustration…）
        string memory effectiveDomain = (
            bytes(targetAsset.videoScene).length > 0
                ? targetAsset.videoScene
                : bytes(targetAsset.audioScene).length > 0
                    ? targetAsset.audioScene
                    : targetAsset.domainKey
        );

        uint256 currentPrice = getDynamicPrice(effectiveDomain, targetAsset.baseValue);
        require(msg.value >= currentPrice, "付款金额低于 AMM 联合曲线实时报价");
        require(_callQuota > 0,            "调用配额不能为 0");
        require(_ttlDays > 0 && _ttlDays <= 365, "凭证有效期须在 1-365 天");

        // ── 需求量上升，联合曲线右移
        domainDemandLedger[effectiveDomain] += 1;

        // ── 颁发访问凭证（★ 新增）
        uint256 expiresAt = block.timestamp + (_ttlDays * 1 days);
        accessTokens[_assetHash][msg.sender] = AccessToken({
            callerAddr:     msg.sender,
            expiresAt:      expiresAt,
            callsRemaining: _callQuota,
            callsUsed:      0,
            revoked:        false
        });
        emit AccessGranted(_assetHash, msg.sender, expiresAt, _callQuota);

        // ── 三路分账清算
        uint256 creatorAmount  = (msg.value * _creatorRatio) / 1000;
        uint256 remaining      = msg.value - creatorAmount;
        uint256 platformAmount = (remaining * 60) / 100;
        uint256 fundAmount     = remaining - platformAmount;

        payable(targetAsset.creator).transfer(creatorAmount);
        payable(platformAdmin).transfer(platformAmount);
        payable(ecosystemFund).transfer(fundAmount);

        uint256 newPrice = getDynamicPrice(effectiveDomain, targetAsset.baseValue);
        emit PaymentSettled(_assetHash, msg.value, creatorAmount);
        emit DataConsumed(effectiveDomain, domainDemandLedger[effectiveDomain], newPrice);
    }

    // ═══════════════════════════════════════════════════════════════
    // 11. ★ 新增：创作者吊销访问凭证
    // ═══════════════════════════════════════════════════════════════
    function revokeAccess(uint256 _assetHash, address _caller) public {
        require(
            assetRegistry[_assetHash].creator == msg.sender,
            "仅资产创作者可吊销访问权限"
        );
        accessTokens[_assetHash][_caller].revoked = true;
        emit AccessRevoked(_assetHash, _caller);
    }

    // ═══════════════════════════════════════════════════════════════
    // 12. ★ 新增：创作者开关 Paywall
    // ═══════════════════════════════════════════════════════════════
    function setPaywallActive(uint256 _assetHash, bool _active) public {
        require(
            assetRegistry[_assetHash].creator == msg.sender,
            "仅资产创作者可修改 Paywall 状态"
        );
        assetRegistry[_assetHash].paywallActive = _active;
    }

    // ═══════════════════════════════════════════════════════════════
    // 13. 只读辅助函数
    // ═══════════════════════════════════════════════════════════════
    function getAccessToken(
        uint256 _assetHash,
        address _caller
    ) public view returns (AccessToken memory) {
        return accessTokens[_assetHash][_caller];
    }

    function getRegisteredCount() public view returns (uint256) {
        return registeredHashes.length;
    }

    function getDomainAlpha(string memory _domainKey) public view returns (uint256) {
        return domainRegistry[_domainKey].alpha;
    }

    // ── 内部：哈希算法名称字符串（供事件日志展示）
    function _fingerprintTypeStr(HashAlgorithm algo) internal pure returns (string memory) {
        if (algo == HashAlgorithm.SIMHASH) return "SimHash-64bit";
        if (algo == HashAlgorithm.PHASH)   return "DCT-pHash-64bit";
        return "AFP-SHA256-48bit";
    }
}
