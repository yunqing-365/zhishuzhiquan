# dataset/pipeline_monitor.py
"""
知数知圈 · 流水线监控 & 告警 v1

功能：
  - 实时记录各阶段耗时、成功率、质量分布
  - 阶段超时 / 质量崩溃 / 失败率过高 → 写入告警日志
  - 提供 /api/platform/monitor 端点所需的快照数据

不引入外部 observability 依赖，SQLite 即可，后续可接 Prometheus。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

_MONITOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "monitor.json",
)
_ALERT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alerts.json",
)
os.makedirs(os.path.dirname(_MONITOR_PATH), exist_ok=True)

# ── 告警阈值 ──────────────────────────────────────────────────────
THRESHOLDS = {
    "stage_timeout_s":      300,    # 单阶段超过 5 分钟 → 告警
    "quality_crash_score":  3.0,    # 平均质量低于 3 分 → 崩溃告警
    "fail_rate_pct":        40.0,   # 失败率超过 40% → 告警
    "pass_rate_min_pct":    30.0,   # 质检通过率低于 30% → 告警
}

SEVERITY = {
    "INFO":    "🟢",
    "WARNING": "🟡",
    "ERROR":   "🔴",
    "CRITICAL":"🚨",
}


# ════════════════════════════════════════════════════════════════════
# 数据类
# ════════════════════════════════════════════════════════════════════

@dataclass
class StageMetrics:
    stage:      str
    job_id:     str
    started_at: float = field(default_factory=time.time)
    ended_at:   Optional[float] = None
    input_count:  int = 0
    output_count: int = 0
    failed_count: int = 0
    avg_score:    float = 0.0
    pass_rate:    float = 0.0
    error_msg:    str = ""

    @property
    def duration_s(self) -> float:
        end = self.ended_at or time.time()
        return round(end - self.started_at, 2)

    @property
    def fail_rate_pct(self) -> float:
        total = self.input_count
        return round(self.failed_count / total * 100, 1) if total else 0.0


@dataclass
class Alert:
    alert_id:   str
    severity:   str        # INFO / WARNING / ERROR / CRITICAL
    job_id:     str
    stage:      str
    message:    str
    value:      float = 0.0
    threshold:  float = 0.0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    resolved:   bool = False


# ════════════════════════════════════════════════════════════════════
# 监控器（单例）
# ════════════════════════════════════════════════════════════════════

class PipelineMonitor:
    """
    线程安全的流水线监控器。

    典型用法（在 pipeline.py 各阶段内）：
        monitor = PipelineMonitor.instance()
        ctx = monitor.start_stage(job_id, "annotating", input_count=10)
        # ... 执行阶段逻辑 ...
        monitor.end_stage(ctx, output_count=8, avg_score=7.2, pass_rate=0.8)
    """

    _instance: Optional["PipelineMonitor"] = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "PipelineMonitor":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._metrics:  Dict[str, List[StageMetrics]] = {}   # job_id → [StageMetrics]
        self._alerts:   List[Alert] = []
        self._wlock     = threading.Lock()
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(_ALERT_PATH):
                raw = json.loads(open(_ALERT_PATH).read())
                self._alerts = [Alert(**a) for a in raw]
        except Exception:
            self._alerts = []

    def _save_alerts(self):
        try:
            with open(_ALERT_PATH, "w", encoding="utf-8") as f:
                json.dump([asdict(a) for a in self._alerts[-500:]], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  [monitor] 告警保存失败: {e}")

    def _save_snapshot(self):
        try:
            snapshot = {
                job_id: [
                    {
                        "stage":        m.stage,
                        "duration_s":   m.duration_s,
                        "input_count":  m.input_count,
                        "output_count": m.output_count,
                        "failed_count": m.failed_count,
                        "avg_score":    m.avg_score,
                        "pass_rate":    m.pass_rate,
                        "fail_rate_pct":m.fail_rate_pct,
                        "error_msg":    m.error_msg,
                    }
                    for m in stages
                ]
                for job_id, stages in list(self._metrics.items())[-50:]
            }
            with open(_MONITOR_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  [monitor] 快照保存失败: {e}")

    # ── 阶段生命周期 ────────────────────────────────────────────────

    def start_stage(self, job_id: str, stage: str, input_count: int = 0) -> StageMetrics:
        ctx = StageMetrics(stage=stage, job_id=job_id, input_count=input_count)
        with self._wlock:
            self._metrics.setdefault(job_id, []).append(ctx)
        print(f"📊 [monitor] {job_id[:8]} | {stage} 开始 (输入 {input_count} 条)")
        return ctx

    def end_stage(
        self,
        ctx: StageMetrics,
        output_count: int = 0,
        failed_count: int = 0,
        avg_score: float = 0.0,
        pass_rate: float = 1.0,
        error_msg: str = "",
    ):
        ctx.ended_at     = time.time()
        ctx.output_count = output_count
        ctx.failed_count = failed_count
        ctx.avg_score    = avg_score
        ctx.pass_rate    = pass_rate
        ctx.error_msg    = error_msg

        self._check_thresholds(ctx)

        print(
            f"📊 [monitor] {ctx.job_id[:8]} | {ctx.stage} 完成 "
            f"({ctx.duration_s:.1f}s, 输出 {output_count}, 均质 {avg_score:.2f}, 通过率 {pass_rate:.0%})"
        )
        self._save_snapshot()

    # ── 阈值告警 ────────────────────────────────────────────────────

    def _check_thresholds(self, ctx: StageMetrics):
        alerts_to_add: List[Alert] = []

        import uuid as _uuid

        # 1. 超时
        if ctx.duration_s > THRESHOLDS["stage_timeout_s"]:
            alerts_to_add.append(Alert(
                alert_id=str(_uuid.uuid4()),
                severity="WARNING",
                job_id=ctx.job_id,
                stage=ctx.stage,
                message=f"阶段 {ctx.stage} 耗时 {ctx.duration_s:.0f}s，超过阈值 {THRESHOLDS['stage_timeout_s']}s",
                value=ctx.duration_s,
                threshold=THRESHOLDS["stage_timeout_s"],
            ))

        # 2. 质量崩溃
        if ctx.avg_score > 0 and ctx.avg_score < THRESHOLDS["quality_crash_score"]:
            alerts_to_add.append(Alert(
                alert_id=str(_uuid.uuid4()),
                severity="CRITICAL",
                job_id=ctx.job_id,
                stage=ctx.stage,
                message=f"质量崩溃：均质 {ctx.avg_score:.2f} < {THRESHOLDS['quality_crash_score']}，建议中止任务",
                value=ctx.avg_score,
                threshold=THRESHOLDS["quality_crash_score"],
            ))

        # 3. 失败率过高
        if ctx.fail_rate_pct > THRESHOLDS["fail_rate_pct"]:
            alerts_to_add.append(Alert(
                alert_id=str(_uuid.uuid4()),
                severity="ERROR",
                job_id=ctx.job_id,
                stage=ctx.stage,
                message=f"失败率 {ctx.fail_rate_pct:.1f}% 超过阈值 {THRESHOLDS['fail_rate_pct']}%",
                value=ctx.fail_rate_pct,
                threshold=THRESHOLDS["fail_rate_pct"],
            ))

        # 4. 质检通过率过低
        if ctx.stage == "scoring" and ctx.pass_rate * 100 < THRESHOLDS["pass_rate_min_pct"]:
            alerts_to_add.append(Alert(
                alert_id=str(_uuid.uuid4()),
                severity="WARNING",
                job_id=ctx.job_id,
                stage=ctx.stage,
                message=f"质检通过率 {ctx.pass_rate:.0%} 偏低，素材质量可能不足",
                value=ctx.pass_rate * 100,
                threshold=THRESHOLDS["pass_rate_min_pct"],
            ))

        if alerts_to_add:
            with self._wlock:
                self._alerts.extend(alerts_to_add)
            self._save_alerts()
            for a in alerts_to_add:
                print(f"{SEVERITY.get(a.severity, '?')} [ALERT] {a.severity} | {a.message}")

    def fire_alert(self, severity: str, job_id: str, stage: str, message: str) -> Alert:
        """手动触发告警（用于 pipeline 捕获到异常时）"""
        import uuid as _uuid
        alert = Alert(
            alert_id=str(_uuid.uuid4()),
            severity=severity, job_id=job_id, stage=stage, message=message,
        )
        with self._wlock:
            self._alerts.append(alert)
        self._save_alerts()
        print(f"{SEVERITY.get(severity, '?')} [ALERT] {severity} | {message}")
        return alert

    # ── 查询接口（供 API 端点调用）──────────────────────────────────

    def get_snapshot(self) -> dict:
        """返回全局监控快照（最近 50 个 job 的指标 + 未解决告警）"""
        with self._wlock:
            unresolved = [asdict(a) for a in self._alerts if not a.resolved][-50:]
            job_summaries = []
            for job_id, stages in list(self._metrics.items())[-20:]:
                total_in  = sum(s.input_count  for s in stages)
                total_out = sum(s.output_count for s in stages)
                avg_q = (
                    sum(s.avg_score for s in stages if s.avg_score > 0) /
                    max(1, sum(1 for s in stages if s.avg_score > 0))
                )
                job_summaries.append({
                    "job_id":        job_id,
                    "stages_done":   len(stages),
                    "total_input":   total_in,
                    "total_output":  total_out,
                    "avg_quality":   round(avg_q, 2),
                    "total_time_s":  round(sum(s.duration_s for s in stages), 1),
                    "has_alert":     any(
                        a.job_id == job_id and not a.resolved
                        for a in self._alerts
                    ),
                })

        return {
            "jobs":            job_summaries,
            "unresolved_alerts": unresolved,
            "alert_count":     len(unresolved),
            "thresholds":      THRESHOLDS,
        }

    def resolve_alert(self, alert_id: str) -> bool:
        with self._wlock:
            for a in self._alerts:
                if a.alert_id == alert_id:
                    a.resolved = True
                    self._save_alerts()
                    return True
        return False

    def get_alerts(self, include_resolved: bool = False, limit: int = 100) -> list:
        with self._wlock:
            items = [
                asdict(a) for a in reversed(self._alerts)
                if include_resolved or not a.resolved
            ]
        return items[:limit]
