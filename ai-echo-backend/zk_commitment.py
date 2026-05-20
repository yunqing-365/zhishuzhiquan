"""
zk_commitment.py — ZK 承诺引擎 v1
===================================
阶段 2: Hash-based Poseidon-compatible Commitment（无需 SNARK 电路）

承诺方案:
  salt         = os.urandom(24).hex()                    # 私有随机盐
  secret       = SHA3(asset_hash || value_floor || salt) # 私有
  nullifier    = SHA3(secret || creator || epoch_hour)   # 私有防重放
  nullifier_h  = SHA3(nullifier)                         # 公开（防重放锚点）
  scene_h      = SHA3(scene)                             # 公开
  commitment   = SHA3(nullifier_h || scene_h[:18] || value_floor)  # 公开上链

  原始数据永不暴露；commitment 可被合约 bytes32 字段直接存储。

升级路径:
  Stage 2 (当前) — Pure hash commitment，本文件
  Stage 3        — snarkjs Groth16，以 commitment 作为公共输入，无需改动上层接口
"""

import hashlib
import os
import time
import math
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any


# ─── 工具函数 ────────────────────────────────────────────────────────

def _sha3(data: str) -> str:
    """SHA3-256 → 0x-prefixed hex (64 chars after 0x)"""
    return "0x" + hashlib.sha3_256(data.encode("utf-8")).hexdigest()


def _bytes32(hex_str: str) -> str:
    """标准化为 Solidity bytes32 格式 (0x + 64 hex chars)"""
    raw = hex_str.replace("0x", "").lower()
    return "0x" + raw[:64].ljust(64, "0")


# ─── 数据类 ──────────────────────────────────────────────────────────

@dataclass
class ZkProof:
    """
    ZK 承诺凭证。

    公开字段（可写入 API 响应 + 合约）:
        proof_type      — 算法标识
        commitment      — bytes32，绑定资产/定价/场景的公开承诺，可上链
        nullifier_hash  — bytes32，防重放检查的公开锚点
        public_signals  — 合约验证所需的公开参数

    私有字段（仅后端计算，不进响应）:
        salt / secret / nullifier — 由构造流程内部生成，未存储于此对象
    """
    proof_type:      str
    commitment:      str             # bytes32 hex
    nullifier_hash:  str             # bytes32 hex
    public_signals:  Dict[str, Any]
    timestamp:       int
    is_real_proof:   bool            # False = commitment; True = Groth16 SNARK

    def to_dict(self) -> dict:
        return asdict(self)

    def to_bytes32(self) -> str:
        """返回 Solidity bytes32 格式的 commitment，供合约 registerAsset 直接使用"""
        return _bytes32(self.commitment)


# ─── 核心生成函数 ────────────────────────────────────────────────────

def generate_zk_commitment(
    asset_hash:      str,
    base_value:      float,
    scene:           str,
    modality:        str,
    creator_address: Optional[str] = None,
) -> ZkProof:
    """
    为已估值资产生成 ZK 承诺凭证。

    参数:
        asset_hash      — 资产感知哈希（适配器 generate_hash 返回值）
        base_value      — 估值结果（USDT 精度）
        scene           — TEV 场景标签（如 "medical_sft"）
        modality        — 模态标签（"text" / "image" / "audio" / "video"）
        creator_address — 创作者钱包地址（可选；增强 nullifier 唯一性）

    返回:
        ZkProof — commitment / nullifier_hash / public_signals 可序列化到响应
    """
    # ── Step 1: 随机盐（私有，不出函数外）──────────────────────────
    salt = os.urandom(24).hex()

    # ── Step 2: value_floor（百位取整；降低精度保护隐私）───────────
    value_floor = int(math.floor(base_value / 500) * 500)

    # ── Step 3: 私有 secret（asset 绑定）────────────────────────────
    secret = _sha3(f"{asset_hash}|{value_floor}|{salt}")

    # ── Step 4: 私有 nullifier（小时级防重放）───────────────────────
    epoch_hour   = str(int(time.time()) // 3600)
    creator_part = (creator_address or "anon").lower().strip()
    nullifier    = _sha3(f"{secret}|{creator_part}|{epoch_hour}")

    # ── Step 5: 公开 nullifier_hash（防重放检查锚点）────────────────
    nullifier_hash = _sha3(nullifier)

    # ── Step 6: 场景 fingerprint（场景标签的单向摘要）───────────────
    scene_hash = _sha3(scene)

    # ── Step 7: 公开 commitment（三元素绑定）────────────────────────
    commitment = _sha3(f"{nullifier_hash}|{scene_hash[:18]}|{value_floor}")

    # ── Step 8: 模态编码（与合约 HashAlgorithm enum 顺序对齐）───────
    modality_codes = {"text": 1, "image": 2, "audio": 3, "video": 4}

    return ZkProof(
        proof_type     = "poseidon_commitment_v1",
        commitment     = _bytes32(commitment),
        nullifier_hash = _bytes32(nullifier_hash),
        public_signals = {
            "value_floor":        value_floor,
            "scene_fingerprint":  scene_hash[:18],   # 前 8 字节，低熵保护
            "modality_code":      modality_codes.get(modality, 0),
            "asset_hash_prefix":  asset_hash[:10],   # 资产识别前缀（展示用）
            "schema_version":     1,
        },
        timestamp     = int(time.time()),
        is_real_proof = False,   # Stage 2 = hash commitment，非真 SNARK
    )
