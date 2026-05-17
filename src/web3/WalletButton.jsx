/**
 * WalletButton — 紧凑型钱包连接按钮
 * =====================================
 * 封装 RainbowKit ConnectButton.Custom，
 * 样式与 AI-Echo UI 体系完全匹配（深色 + mono 字体）。
 *
 * 用法：
 *   <WalletButton />                   // 标准尺寸
 *   <WalletButton size="sm" />         // 小尺寸（顶栏用）
 *   <WalletButton showChain={false} /> // 隐藏链名
 */

import { ConnectButton } from '@rainbow-me/rainbowkit';
import { Wallet, AlertTriangle, ChevronDown } from 'lucide-react';

const truncate = (addr) =>
  addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : '';

export default function WalletButton({ size = 'md', showChain = true }) {
  const isSmall = size === 'sm';
  const px      = isSmall ? 'px-2.5 py-1.5' : 'px-4 py-2';
  const text    = isSmall ? 'text-[11px]' : 'text-xs';

  return (
    <ConnectButton.Custom>
      {({
        account,
        chain,
        openAccountModal,
        openChainModal,
        openConnectModal,
        mounted,
      }) => {
        if (!mounted) return null;

        // ── 未连接 ─────────────────────────────────────────────────
        if (!account) {
          return (
            <button
              onClick={openConnectModal}
              className={`flex items-center gap-2 ${px} ${text} font-bold font-mono rounded-xl
                border border-purple-500/40 bg-purple-900/20 text-purple-300
                hover:bg-purple-900/40 hover:border-purple-400/60
                transition-all shadow-sm shadow-purple-900/20`}
            >
              <Wallet className={isSmall ? 'w-3.5 h-3.5' : 'w-4 h-4'} />
              连接钱包
            </button>
          );
        }

        // ── 链不对 ─────────────────────────────────────────────────
        if (chain?.unsupported) {
          return (
            <button
              onClick={openChainModal}
              className={`flex items-center gap-2 ${px} ${text} font-bold font-mono rounded-xl
                border border-amber-500/40 bg-amber-900/20 text-amber-300
                hover:bg-amber-900/40 transition-all`}
            >
              <AlertTriangle className={isSmall ? 'w-3.5 h-3.5' : 'w-4 h-4'} />
              切换网络
            </button>
          );
        }

        // ── 已连接 ─────────────────────────────────────────────────
        return (
          <div className="flex items-center gap-1.5">
            {/* 链名 */}
            {showChain && chain && (
              <button
                onClick={openChainModal}
                className={`flex items-center gap-1.5 ${px} ${text} font-mono rounded-xl
                  border border-slate-700 bg-slate-900/60 text-slate-400
                  hover:border-slate-500 hover:text-slate-200 transition-all`}
              >
                {chain.hasIcon && chain.iconUrl && (
                  <img
                    src={chain.iconUrl}
                    alt={chain.name}
                    className={isSmall ? 'w-3 h-3 rounded-full' : 'w-3.5 h-3.5 rounded-full'}
                  />
                )}
                {chain.name}
                <ChevronDown className={isSmall ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
              </button>
            )}

            {/* 地址 */}
            <button
              onClick={openAccountModal}
              className={`flex items-center gap-1.5 ${px} ${text} font-bold font-mono rounded-xl
                border border-emerald-500/30 bg-emerald-900/15 text-emerald-300
                hover:bg-emerald-900/30 hover:border-emerald-400/50 transition-all`}
            >
              <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              {account.displayName || truncate(account.address)}
            </button>
          </div>
        );
      }}
    </ConnectButton.Custom>
  );
}
