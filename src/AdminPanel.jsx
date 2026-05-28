/**
 * AdminPanel.jsx — 平台管理员控制台
 * =====================================
 * 第 15 轮新增：合约 pause()/unpause()/transferAdmin()/acceptAdmin()
 * 自第 14 轮上线以来一直没有前端入口，管理员只能通过 Hardhat console
 * 或 Etherscan 操作。本面板补全这一缺口。
 *
 * 功能：
 *  - 显示合约当前 paused 状态（实时读取链上）
 *  - 一键暂停 / 恢复合约
 *  - 发起管理员权限转移（transferAdmin）
 *  - 接受权限转移（acceptAdmin，新管理员钱包连接后可见）
 *  - 显示待接收管理员地址（pendingAdmin）
 *  - 操作记录（当前会话内）
 */
import React, { useState, useCallback, useEffect } from 'react';
import {
  ShieldAlert, ShieldCheck, ArrowRightLeft, CheckCircle2,
  AlertTriangle, Loader2, ExternalLink, Copy, RefreshCw,
  Lock, Unlock, UserCheck, Clock
} from 'lucide-react';
import {
  useAccount, useChainId, useReadContract, useWriteContract,
  useWaitForTransactionReceipt,
} from 'wagmi';
import { isAddress } from 'viem';
import { AI_ECHO_ABI } from './web3/ABI';
import { CONTRACT_ADDRESS, targetChain, getTxUrl } from './web3/config';
import WalletButton from './web3/WalletButton';

// ── 工具 ──────────────────────────────────────────────────────────
function shortAddr(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

function copyToClipboard(text) {
  navigator.clipboard?.writeText(text).catch(() => {});
}

// ── 状态徽章 ─────────────────────────────────────────────────────
function PausedBadge({ paused, loading }) {
  if (loading) return (
    <span className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs bg-slate-800 text-slate-400 border border-slate-700">
      <Loader2 size={11} className="animate-spin" /> 读取中…
    </span>
  );
  if (paused) return (
    <span className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs bg-red-900/40 text-red-300 border border-red-700/50 font-medium">
      <Lock size={11} /> 已暂停
    </span>
  );
  return (
    <span className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs bg-emerald-900/40 text-emerald-300 border border-emerald-700/50 font-medium">
      <Unlock size={11} /> 运行中
    </span>
  );
}

// ── 操作日志条目 ─────────────────────────────────────────────────
function LogEntry({ entry }) {
  const typeStyle = {
    success: 'text-emerald-400',
    error:   'text-red-400',
    info:    'text-slate-400',
    pending: 'text-yellow-400',
  };
  return (
    <div className={`flex items-start gap-2 text-[11px] font-mono ${typeStyle[entry.type] || 'text-slate-400'}`}>
      <span className="text-slate-600 shrink-0 mt-px">{entry.time}</span>
      <span className="leading-relaxed">{entry.msg}</span>
      {entry.hash && (
        <a
          href={getTxUrl(entry.hash)}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 text-purple-400 hover:text-purple-300 flex items-center gap-0.5"
        >
          tx <ExternalLink size={9} />
        </a>
      )}
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────────────────
export default function AdminPanel() {
  const { address, isConnected } = useAccount();
  const chainId = useChainId();
  const isCorrectChain = chainId === targetChain.id;
  const contractReady  = isConnected && isCorrectChain &&
    CONTRACT_ADDRESS !== '0x0000000000000000000000000000000000000000';

  const [logs, setLogs] = useState([]);
  const [newAdminInput, setNewAdminInput] = useState('');
  const [addrError, setAddrError] = useState('');
  const [activeTx, setActiveTx] = useState(null); // hash 字符串

  function addLog(msg, type = 'info', hash = null) {
    const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    setLogs(prev => [{ time, msg, type, hash }, ...prev].slice(0, 50));
  }

  // ── 链上读取 ──────────────────────────────────────────────────
  const { data: isPaused, isLoading: pausedLoading, refetch: refetchPaused } = useReadContract({
    address: CONTRACT_ADDRESS,
    abi:     AI_ECHO_ABI,
    functionName: 'paused',
    query: { enabled: contractReady, refetchInterval: 8000 },
  });

  const { data: platformAdmin, refetch: refetchAdmin } = useReadContract({
    address: CONTRACT_ADDRESS,
    abi:     AI_ECHO_ABI,
    functionName: 'platformAdmin',
    query: { enabled: contractReady },
  });

  const { data: pendingAdmin, refetch: refetchPending } = useReadContract({
    address: CONTRACT_ADDRESS,
    abi:     AI_ECHO_ABI,
    functionName: 'pendingAdmin',
    query: { enabled: contractReady, refetchInterval: 8000 },
  });

  const isAdmin   = address && platformAdmin && address.toLowerCase() === platformAdmin.toLowerCase();
  const isPending = address && pendingAdmin  && address.toLowerCase() === pendingAdmin.toLowerCase();

  // ── 写合约 ────────────────────────────────────────────────────
  const { writeContractAsync } = useWriteContract();

  // 等待交易确认
  const { isLoading: txConfirming } = useWaitForTransactionReceipt({
    hash: activeTx || undefined,
    onSuccess(receipt) {
      addLog(`交易已确认，区块 #${receipt.blockNumber}`, 'success', activeTx);
      setActiveTx(null);
      refetchPaused(); refetchAdmin(); refetchPending();
    },
  });

  async function sendTx(fnName, args = [], label) {
    try {
      addLog(`发送 ${label}…`, 'pending');
      const hash = await writeContractAsync({
        address: CONTRACT_ADDRESS,
        abi:     AI_ECHO_ABI,
        functionName: fnName,
        args,
      });
      setActiveTx(hash);
      addLog(`${label} 已广播`, 'info', hash);
    } catch (e) {
      const msg = e?.shortMessage || e?.message || '未知错误';
      addLog(`${label} 失败：${msg}`, 'error');
    }
  }

  function handlePause()   { sendTx('pause',   [], '暂停合约'); }
  function handleUnpause() { sendTx('unpause', [], '恢复合约'); }
  function handleAccept()  { sendTx('acceptAdmin', [], '接受管理员权限'); }

  function handleTransfer() {
    if (!isAddress(newAdminInput)) {
      setAddrError('请输入合法的以太坊地址（0x…）');
      return;
    }
    setAddrError('');
    sendTx('transferAdmin', [newAdminInput], `发起管理员转移 → ${shortAddr(newAdminInput)}`);
    setNewAdminInput('');
  }

  function refetchAll() {
    refetchPaused(); refetchAdmin(); refetchPending();
    addLog('手动刷新链上状态', 'info');
  }

  // ── 未连接钱包 ───────────────────────────────────────────────
  if (!isConnected) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center gap-4 px-4">
        <ShieldAlert size={40} className="text-amber-400" />
        <h2 className="text-white font-bold text-lg">管理员控制台</h2>
        <p className="text-slate-400 text-sm text-center max-w-xs">
          请先连接钱包。只有合约 <code className="text-purple-300">platformAdmin</code> 地址可执行管理操作。
        </p>
        <WalletButton />
      </div>
    );
  }

  if (!isCorrectChain) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center gap-4 px-4">
        <AlertTriangle size={36} className="text-orange-400" />
        <p className="text-slate-300 text-sm">请切换到 <span className="text-orange-300 font-bold">{targetChain.name}</span> 网络</p>
        <WalletButton />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white px-4 py-8">
      <div className="max-w-2xl mx-auto space-y-6">

        {/* 头部 */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <ShieldAlert size={20} className="text-amber-400" />
              合约管理员控制台
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {CONTRACT_ADDRESS !== '0x0000000000000000000000000000000000000000'
                ? `合约：${shortAddr(CONTRACT_ADDRESS)}`
                : '⚠ 合约地址未配置'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refetchAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 text-xs transition-all"
            >
              <RefreshCw size={11} />
              刷新
            </button>
            <WalletButton />
          </div>
        </div>

        {/* 身份提示 */}
        {!isAdmin && !isPending && (
          <div className="bg-orange-900/20 border border-orange-700/40 rounded-xl px-4 py-3 flex items-center gap-2 text-orange-300 text-sm">
            <AlertTriangle size={14} />
            当前钱包（{shortAddr(address)}）不是合约管理员，只读模式。
          </div>
        )}
        {isPending && (
          <div className="bg-blue-900/20 border border-blue-700/40 rounded-xl px-4 py-3 flex items-center gap-2 text-blue-300 text-sm">
            <Clock size={14} />
            您是待接收管理员，点击下方「接受权限」完成交接。
          </div>
        )}

        {/* 合约状态卡 */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl divide-y divide-slate-800">
          <div className="flex items-center justify-between px-5 py-4">
            <span className="text-sm text-slate-400">合约状态</span>
            <PausedBadge paused={isPaused} loading={pausedLoading} />
          </div>
          <div className="flex items-center justify-between px-5 py-3">
            <span className="text-sm text-slate-400">当前管理员</span>
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-slate-300">{shortAddr(platformAdmin)}</span>
              {platformAdmin && (
                <button onClick={() => copyToClipboard(platformAdmin)} className="text-slate-600 hover:text-slate-400">
                  <Copy size={11} />
                </button>
              )}
            </div>
          </div>
          {pendingAdmin && pendingAdmin !== '0x0000000000000000000000000000000000000000' && (
            <div className="flex items-center justify-between px-5 py-3">
              <span className="text-sm text-amber-400 flex items-center gap-1.5">
                <Clock size={12} /> 待接收管理员
              </span>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-amber-300">{shortAddr(pendingAdmin)}</span>
                <button onClick={() => copyToClipboard(pendingAdmin)} className="text-slate-600 hover:text-slate-400">
                  <Copy size={11} />
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 操作区（仅管理员可用） */}
        <div className={`space-y-4 ${!isAdmin && !isPending ? 'opacity-40 pointer-events-none' : ''}`}>

          {/* 暂停 / 恢复 */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              {isPaused ? <Unlock size={14} className="text-emerald-400" /> : <Lock size={14} className="text-red-400" />}
              {isPaused ? '恢复合约运行' : '紧急暂停合约'}
            </h3>
            <p className="text-xs text-slate-500">
              {isPaused
                ? '合约当前已暂停，所有 registerAsset 和 purchaseAndCallData 调用均被阻止。点击恢复以重新开放。'
                : '暂停后，所有资产注册和购买交易将立即失败，只读查询不受影响。仅在发现安全漏洞时使用。'}
            </p>
            <div className="flex gap-2">
              {isPaused ? (
                <button
                  onClick={handleUnpause}
                  disabled={txConfirming}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/40 border border-emerald-700/40 text-emerald-300 text-sm font-medium transition-all disabled:opacity-50"
                >
                  {txConfirming ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
                  恢复运行
                </button>
              ) : (
                <button
                  onClick={handlePause}
                  disabled={txConfirming}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600/20 hover:bg-red-600/40 border border-red-700/40 text-red-300 text-sm font-medium transition-all disabled:opacity-50"
                >
                  {txConfirming ? <Loader2 size={13} className="animate-spin" /> : <ShieldAlert size={13} />}
                  暂停合约
                </button>
              )}
            </div>
          </div>

          {/* 管理员权限转移 */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <ArrowRightLeft size={14} className="text-purple-400" />
              转移管理员权限
            </h3>
            <p className="text-xs text-slate-500">
              两步交接防误操作：先由当前管理员发起，新管理员连接钱包后点击「接受权限」完成。
            </p>

            {/* 发起转移（当前管理员） */}
            {isAdmin && (
              <div className="flex gap-2">
                <div className="flex-1">
                  <input
                    type="text"
                    value={newAdminInput}
                    onChange={e => { setNewAdminInput(e.target.value); setAddrError(''); }}
                    placeholder="新管理员地址 0x…"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-600 transition-colors"
                  />
                  {addrError && (
                    <p className="text-xs text-red-400 mt-1 flex items-center gap-1">
                      <AlertTriangle size={10} /> {addrError}
                    </p>
                  )}
                </div>
                <button
                  onClick={handleTransfer}
                  disabled={txConfirming || !newAdminInput}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-purple-600/20 hover:bg-purple-600/40 border border-purple-700/40 text-purple-300 text-sm font-medium transition-all disabled:opacity-50 whitespace-nowrap"
                >
                  {txConfirming ? <Loader2 size={12} className="animate-spin" /> : <ArrowRightLeft size={12} />}
                  发起转移
                </button>
              </div>
            )}

            {/* 接受权限（待接收管理员） */}
            {isPending && (
              <button
                onClick={handleAccept}
                disabled={txConfirming}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600/20 hover:bg-blue-600/40 border border-blue-700/40 text-blue-300 text-sm font-medium transition-all disabled:opacity-50"
              >
                {txConfirming ? <Loader2 size={13} className="animate-spin" /> : <UserCheck size={13} />}
                接受管理员权限
              </button>
            )}
          </div>
        </div>

        {/* 操作日志 */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">操作日志（当前会话）</h3>
          {logs.length === 0 ? (
            <p className="text-slate-700 text-xs text-center py-4">暂无操作记录</p>
          ) : (
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {logs.map((l, i) => <LogEntry key={i} entry={l} />)}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
