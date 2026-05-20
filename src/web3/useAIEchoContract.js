/**
 * useAIEchoContract — AI-Echo 合约交互 Hook
 * ============================================
 * 封装所有链上操作，SmartSplitScreen 直接调用，不接触 wagmi/viem 底层。
 *
 * 返回值:
 *   isConnected      — 钱包是否已连接
 *   address          — 当前钱包地址
 *   chainId          — 当前链 ID
 *   isCorrectChain   — 是否在正确的链上
 *   contractReady    — 合约地址已配置且链正确
 *   registerAsset()  — 注册资产上链
 *   purchaseAccess() — 购买访问权（payable）
 *   getAssetInfo()   — 查询资产信息
 *   getDynamicPrice()— 查询实时报价
 *   registeredCount  — 链上注册资产总数
 *   txHash / txStatus / txError — 交易状态
 */

import { useState, useCallback } from 'react';
import {
  useAccount,
  useChainId,
  useReadContract,
  useWriteContract,
  useSwitchChain,
  useWaitForTransactionReceipt,
} from 'wagmi';
import { parseEther, formatEther, keccak256, encodePacked } from 'viem';
import { AI_ECHO_ABI, modalityToHashAlgo } from './ABI';
import { CONTRACT_ADDRESS, targetChain, getTxUrl } from './config';

// ── 工具：把后端 asset_hash 字符串转为 uint256 ────────────────────
const hashToUint256 = (hashStr) => {
  if (!hashStr) return 0n;
  // 后端生成的 hash 可能是 '0xSH_ABCD1234' 这种格式
  // 取 hex 部分做 BigInt
  const hex = hashStr.replace(/^0x[A-Z]+_/i, '').replace(/[^0-9a-fA-F]/g, '').slice(0, 16);
  if (!hex) return BigInt('0x' + keccak256(encodePacked(['string'], [hashStr])).slice(2, 18));
  return BigInt('0x' + hex.padStart(16, '0'));
};

export const useAIEchoContract = () => {
  const { address, isConnected } = useAccount();
  const chainId = useChainId();
  const { switchChain } = useSwitchChain();

  const isCorrectChain = chainId === targetChain.id;
  const contractReady  =
    isConnected &&
    isCorrectChain &&
    CONTRACT_ADDRESS !== '0x0000000000000000000000000000000000000000';

  const [txStatus, setTxStatus] = useState('idle'); // idle | pending | confirming | success | error
  const [txHash,   setTxHash]   = useState(null);
  const [txError,  setTxError]  = useState(null);

  // ── 写合约 ──────────────────────────────────────────────────────
  const { writeContractAsync } = useWriteContract();

  // ── 查询注册资产总数 ─────────────────────────────────────────────
  const { data: registeredCount } = useReadContract({
    address:      CONTRACT_ADDRESS,
    abi:          AI_ECHO_ABI,
    functionName: 'getRegisteredCount',
    query: { enabled: contractReady },
  });

  // ── 切换到正确的链 ──────────────────────────────────────────────
  const switchToTargetChain = useCallback(async () => {
    try {
      await switchChain({ chainId: targetChain.id });
    } catch (e) {
      console.error('switchChain error:', e);
    }
  }, [switchChain]);

  // ── 注册资产上链 ─────────────────────────────────────────────────
  const registerAsset = useCallback(async ({
    assetHashStr,   // 后端返回的 asset_hash 字符串
    modality,       // 'text' | 'image' | 'audio' | 'video'
    domainKey,      // 'medical_sft' | 'illustration' | ...
    audioScene,     // 音频细粒度场景（非音频时传 ''）
    baseValue,      // 后端 final_valuation.base_value (整数 CRD)
    zkCommitment,   // ★ Stage 2: bytes32 ZK 承诺（null → bytes32(0)）
  }) => {
    if (!contractReady) throw new Error('合约未就绪或钱包未连接');
    setTxStatus('pending');
    setTxError(null);
    setTxHash(null);
    try {
      const assetHashUint = hashToUint256(assetHashStr);
      const hashAlgo = modalityToHashAlgo(modality);
      // ★ ZK 承诺：有则转 bytes32，无则传 0x0000…
      const zkBytes32 = zkCommitment && zkCommitment.startsWith('0x')
        ? zkCommitment.padEnd(66, '0')   // 确保 66 chars (0x + 64)
        : '0x' + '0'.repeat(64);
      const hash = await writeContractAsync({
        address:      CONTRACT_ADDRESS,
        abi:          AI_ECHO_ABI,
        functionName: 'registerAsset',
        args: [
          assetHashUint,
          modality,
          domainKey   || 'general',
          audioScene  || '',
          BigInt(Math.round(baseValue || 0)),
          hashAlgo,
          zkBytes32,   // ★ Stage 2
        ],
      });
      setTxHash(hash);
      setTxStatus('confirming');
      return hash;
    } catch (e) {
      setTxError(e?.shortMessage || e?.message || '交易失败');
      setTxStatus('error');
      throw e;
    }
  }, [contractReady, writeContractAsync]);

  // ── 购买访问权 ───────────────────────────────────────────────────
  const purchaseAccess = useCallback(async ({
    assetHashStr,
    callQuota = 100n,   // 购买多少次调用配额
    ttlDays   = 30n,    // 凭证有效期（天）
    paymentEth,         // 支付金额（ETH 字符串，如 '0.01'）
  }) => {
    if (!contractReady) throw new Error('合约未就绪或钱包未连接');
    setTxStatus('pending');
    setTxError(null);
    setTxHash(null);
    try {
      const assetHashUint = hashToUint256(assetHashStr);
      const hash = await writeContractAsync({
        address:      CONTRACT_ADDRESS,
        abi:          AI_ECHO_ABI,
        functionName: 'purchaseAndCallData',
        args:  [assetHashUint, BigInt(callQuota), BigInt(ttlDays)],
        value: parseEther(paymentEth || '0.001'),
      });
      setTxHash(hash);
      setTxStatus('confirming');
      return hash;
    } catch (e) {
      setTxError(e?.shortMessage || e?.message || '交易失败');
      setTxStatus('error');
      throw e;
    }
  }, [contractReady, writeContractAsync]);

  // ── 查询资产信息 ─────────────────────────────────────────────────
  const getAssetInfo = useCallback(async (assetHashStr) => {
    // 这里用 wagmi 的 readContract（不需要 Hook，直接调用）
    // 仅在需要时按需调用，不做实时轮询
    return null; // 调用方通过 useReadContract 自行实现
  }, []);

  // 交易确认监听
  const { isLoading: isConfirming, isSuccess: isConfirmed } =
    useWaitForTransactionReceipt({
      hash: txHash,
      query: { enabled: !!txHash },
    });

  // 当区块确认后更新状态
  if (txStatus === 'confirming' && isConfirmed) {
    setTxStatus('success');
  }

  return {
    // 钱包状态
    isConnected,
    address,
    chainId,
    isCorrectChain,
    contractReady,
    contractAddress: CONTRACT_ADDRESS,
    targetChain,

    // 合约数据
    registeredCount: registeredCount ? Number(registeredCount) : null,

    // 操作
    switchToTargetChain,
    registerAsset,
    purchaseAccess,

    // 交易状态
    txStatus,
    txHash,
    txError,
    isConfirming,
    isConfirmed,
    txUrl: txHash ? getTxUrl(txHash) : null,

    // 工具
    hashToUint256,
    formatEther,
  };
};
