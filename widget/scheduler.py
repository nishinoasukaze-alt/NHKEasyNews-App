"""【已废弃】挂件进程内定时调度（APScheduler）+ 休眠/唤醒补偿。

⚠ 本模块已废弃，不再被 app.py 引用。定时改用 Windows 任务计划程序
（见 widget/task_scheduler.py），因为唯有任务计划的 WakeToRun 能把电脑从睡眠
唤醒执行；进程内定时在休眠时随进程冻结，跨过的时刻直接丢失（已实测）。

保留本文件仅因 tests/test_scheduler.py 仍测其纯函数 _missed_catchup_needed。
请勿在新代码中 import 本模块。

------------------------------------------------------------------------
原说明（历史）：
- 按 prefs.get_crawl_times() 注册每日 CronTrigger；
- 爬取在 APScheduler 后台线程执行（Playwright sync API 不能跑在 Qt 主线程），
  完成后发 Qt 信号让挂件在主线程 reload；
- 唤醒补偿：启动时与每 5 分钟检查，若今天某计划时刻已过且上次成功爬取早于它，补跑一次；
- 同一时刻只允许一个爬取在跑（threading.Lock）。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, time as dtime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from PySide6.QtCore import QObject, Signal

from nhk_tool import config, storage
from nhk_tool.logger import get_logger
import prefs

logger = get_logger()


def _run_crawl_once() -> int:
    """执行一次爬取，返回 run_crawl.main() 的退出码。"""
    # 延迟导入，避免循环依赖；run_crawl.main() 自带 open_session 包裹
    from nhk_tool import run_crawl
    return run_crawl.main()


def _missed_catchup_needed(times: list[str], last_run_at: str | None, now: datetime) -> bool:
    """判断是否错过了今天某个计划时刻（用于唤醒补偿）。

    纯函数便于测试：若今天有任一计划时刻 <= now，且 last_run_at 早于该时刻
    （或没有 last_run_at），则需补跑。
    """
    last_dt = None
    if last_run_at:
        try:
            last_dt = datetime.fromisoformat(last_run_at)
        except ValueError:
            last_dt = None

    for t in times:
        hh, mm = int(t[:2]), int(t[3:5])
        sched = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if sched <= now:  # 今天这个时刻已到/已过
            if last_dt is None or last_dt < sched:
                return True
    return False


class CrawlScheduler(QObject):
    """封装 APScheduler，对外发 crawl_finished(成功bool) 信号。"""

    crawl_finished = Signal(bool)
    crawl_started = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sched = BackgroundScheduler(daemon=True)
        self._busy = threading.Lock()  # 非阻塞获取：保证同一时刻只一个爬取

    # -- 生命周期 --------------------------------------------------------
    def start(self) -> None:
        self._reschedule_jobs()
        # 唤醒补偿心跳：每 5 分钟检查一次是否错过
        self._sched.add_job(
            self._catchup_check, CronTrigger(minute="*/5"),
            id="catchup", replace_existing=True,
        )
        self._sched.start()
        logger.info("定时调度已启动，爬取时间：%s", prefs.get_crawl_times())
        # 启动即检查一次补偿（覆盖“关机/睡眠期间错过”）
        self._catchup_check()

    def shutdown(self) -> None:
        try:
            self._sched.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass

    # -- 调度 ------------------------------------------------------------
    def reschedule(self) -> None:
        """爬取时间改变后重建每日 job。"""
        self._reschedule_jobs()
        logger.info("已按新时间重排定时：%s", prefs.get_crawl_times())

    def _reschedule_jobs(self) -> None:
        # 移除旧的每日 job（id 以 daily- 前缀）
        for job in self._sched.get_jobs():
            if job.id.startswith("daily-"):
                self._sched.remove_job(job.id)
        for t in prefs.get_crawl_times():
            hh, mm = int(t[:2]), int(t[3:5])
            self._sched.add_job(
                self._do_crawl, CronTrigger(hour=hh, minute=mm),
                id=f"daily-{t}", replace_existing=True,
            )

    # -- 爬取 ------------------------------------------------------------
    def trigger_now(self) -> None:
        """手动触发一次爬取（设置界面/托盘用）。"""
        self._sched.add_job(self._do_crawl, id="manual", replace_existing=True)

    def _catchup_check(self) -> None:
        times = prefs.get_crawl_times()
        last = storage.load_state().get("last_run_at")
        if _missed_catchup_needed(times, last, datetime.now()):
            logger.info("检测到错过的计划时刻，触发补偿爬取")
            self._do_crawl()

    def _do_crawl(self) -> None:
        """在子线程内执行爬取，主控施加总时限（超时记日志、不再等待）。

        Playwright sync 不能安全强杀线程，故超时采用"软超时"：超过时限即记日志、
        发失败信号、放弃等待；卡死的爬取线程自行了结后会释放 _busy 标志，
        其间新触发会因 _busy 被跳过，避免线程堆积。
        """
        if not self._busy.acquire(blocking=False):
            logger.info("已有爬取在进行，跳过本次触发")
            return

        self.crawl_started.emit()
        result = {"ok": False, "done": False}

        def _worker():
            try:
                rc = _run_crawl_once()
                result["ok"] = (rc == 0)
            except Exception as exc:  # noqa: BLE001  # 子线程内不可抛
                logger.error("定时爬取异常：%s", exc)
            finally:
                result["done"] = True
                self._busy.release()

        t = threading.Thread(target=_worker, daemon=True, name="crawl-worker")
        t.start()
        t.join(timeout=config.CRAWL_MAX_SECONDS)

        if not result["done"]:
            # 超时：爬取仍在跑（疑似卡死/网络挂起）。记日志、判失败、不阻塞调度。
            logger.error(
                "爬取超过最长时限 %d 秒，判为失败并放弃本次（后台线程将自行结束）",
                config.CRAWL_MAX_SECONDS,
            )
            self.crawl_finished.emit(False)
            return

        self.crawl_finished.emit(result["ok"])
