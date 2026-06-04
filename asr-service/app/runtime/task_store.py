"""离线任务持久化存储（data/tasks.db，SQLite stdlib）。

设计文档：docs/plan/features/task_persistence/task-persistence-design.md
- 只做"结果可查"，不做"断点续跑"：重启时悬挂任务收口为 failed（close_dangling）；
- 过期清理仅在服务启动时执行（cleanup_expired），retention_days=0 表示永不清理；
- 容错契约：所有方法内部捕获 sqlite3.Error → WARN 日志 + 返回空值，
  绝不向主链路抛错（与 punc/speaker 同一哲学——附属能力不拖垮任务执行）。

线程模型：写路径来自 TaskManager 工作线程（同步调用）；路由读路径经
asyncio.to_thread 下沉。单连接 + 模块内线程锁串行化所有访问。
"""
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 进度合并写节流间隔（秒）：高频 progress 更新合并落库，状态跃迁不受此限制
PROGRESS_WRITE_INTERVAL = 1.0

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id     TEXT PRIMARY KEY,
  status      TEXT NOT NULL,
  progress    REAL NOT NULL DEFAULT 0,
  language    TEXT,
  wav_name    TEXT,
  result      TEXT,
  error       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_finished ON tasks(finished_at);
"""

# list_history / get_task 共用的摘要列（result 仅 get_task 单独取，避免列表查询拖大体积）
_SUMMARY_COLS = "task_id, status, progress, language, wav_name, created_at, finished_at, error"


class TaskStore:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str, retention_days: int = 7):
        self.db_path = db_path
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._last_progress_write: dict[str, float] = {}  # task_id -> monotonic 时刻

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        # 连接被 TaskManager 工作线程与 to_thread 线程池共用，靠 self._lock 串行化
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # auto_vacuum 须在建表前设置才对新库生效；存量库该 PRAGMA 已持久化
        self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(self.SCHEMA_VERSION),),
        )
        self._conn.commit()
        logger.info(f"任务持久化已启用: {db_path}（retention={retention_days} 天）")

    # ─── 写路径（TaskManager 钩子）───

    def _commit_write(self, sql: str, params: tuple) -> int:
        """须在持有 self._lock 时调用；库异常自吞（容错契约：不拖垮任务执行）。

        返回受影响行数（异常时 -1），调用方据此发现"静默无效"的写入。
        """
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            logger.warning(f"任务持久化写入失败（任务继续执行）: {e}")
            return -1

    def insert_task(self, task: dict) -> None:
        """任务创建时写入 pending 记录；task_id 已存在时不覆盖既有记录。"""
        with self._lock:
            affected = self._commit_write(
                "INSERT OR IGNORE INTO tasks"
                "(task_id, status, progress, language, wav_name, created_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?)",
                (task["task_id"], task["status"], task.get("progress", 0.0),
                 task.get("language"), task.get("wav_name"),
                 task["created_at"], task["created_at"]),
            )
        if affected == 0:
            logger.warning(f"任务记录已存在，跳过插入（保留原记录）: {task['task_id']}")

    def update_status(self, task_id: str, status: str) -> None:
        """非终态状态跃迁（pending→processing）立即写，不受进度节流限制。"""
        with self._lock:
            affected = self._commit_write(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                (status, datetime.now().isoformat(), task_id),
            )
        if affected == 0:
            logger.warning(f"任务持久化状态更新无匹配记录: {task_id} → {status}")

    def save_progress(self, task_id: str, progress: float) -> None:
        """进度合并写：距上次落库不足 PROGRESS_WRITE_INTERVAL 则静默跳过。"""
        now = time.monotonic()
        with self._lock:
            last = self._last_progress_write.get(task_id, 0.0)
            if now - last < PROGRESS_WRITE_INTERVAL:
                return
            self._last_progress_write[task_id] = now
            self._commit_write(
                "UPDATE tasks SET progress=?, updated_at=? WHERE task_id=?",
                (progress, datetime.now().isoformat(), task_id),
            )

    def finalize_task(self, task: dict) -> None:
        """终态（completed/failed/cancelled）一次性写入 result/error + finished_at。"""
        result_json = None
        if task.get("result") is not None:
            try:
                result_json = json.dumps(task["result"], ensure_ascii=False)
            except (TypeError, ValueError) as e:
                logger.warning(f"任务结果序列化失败，仅持久化元数据: {e}")
        with self._lock:
            self._last_progress_write.pop(task["task_id"], None)
            affected = self._commit_write(
                "UPDATE tasks SET status=?, progress=?, result=?, error=?,"
                " updated_at=?, finished_at=? WHERE task_id=?",
                (task["status"], task.get("progress", 0.0), result_json,
                 task.get("error"), datetime.now().isoformat(),
                 task.get("finished_at"), task["task_id"]),
            )
        if affected == 0:
            logger.warning(f"任务终态持久化无匹配记录（结果未落库）: {task['task_id']}")

    # ─── 读路径（路由经 asyncio.to_thread 调用）───

    def get_task(self, task_id: str) -> dict | None:
        """单任务查询（含 result），未命中或库异常返回 None。"""
        with self._lock:
            try:
                row = self._conn.execute(
                    f"SELECT {_SUMMARY_COLS}, result FROM tasks WHERE task_id=?",
                    (task_id,),
                ).fetchone()
            except sqlite3.Error as e:
                logger.warning(f"任务持久化读取失败: {e}")
                return None
        if row is None:
            return None
        task = dict(row)
        if task.get("result"):
            try:
                task["result"] = json.loads(task["result"])
            except (ValueError, TypeError):
                logger.warning(f"任务结果反序列化失败: {task_id}")
                task["result"] = None
        return task

    def list_history(self, limit: int = 50, status: str | None = None) -> list[dict]:
        """终态任务摘要（不含 result），created_at 倒序；status 过滤下推 SQL，保证 limit 语义。"""
        sql = f"SELECT {_SUMMARY_COLS} FROM tasks WHERE finished_at IS NOT NULL"
        params: tuple = (limit,)
        if status:
            sql += " AND status=?"
            params = (status, limit)
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as e:
                logger.warning(f"任务持久化读取失败: {e}")
                return []
        return [dict(r) for r in rows]

    def delete_task(self, task_id: str) -> bool:
        """删除历史记录（DELETE /tasks/{id} 对终态任务的语义）。"""
        with self._lock:
            try:
                cur = self._conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
                self._conn.commit()
                return cur.rowcount > 0
            except sqlite3.Error as e:
                logger.warning(f"任务持久化删除失败: {e}")
                return False

    # ─── 启动时序（main 装配期调用）───

    def close_dangling(self) -> int:
        """悬挂任务收口：上次进程退出时未完成的任务标记失败（不做断点续跑）。"""
        now = datetime.now().isoformat()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "UPDATE tasks SET status='failed', error='service restarted',"
                    " updated_at=?, finished_at=?"
                    " WHERE status IN ('pending', 'processing')",
                    (now, now),
                )
                self._conn.commit()
                return cur.rowcount
            except sqlite3.Error as e:
                logger.warning(f"悬挂任务收口失败: {e}")
                return 0

    def cleanup_expired(self) -> int:
        """过期清理：finished_at 早于保留窗口的终态记录删除并回收空间；0=永不清理。"""
        if self.retention_days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM tasks WHERE finished_at IS NOT NULL AND finished_at < ?",
                    (cutoff,),
                )
                self._conn.commit()
                if cur.rowcount:
                    self._conn.execute("PRAGMA incremental_vacuum")
                return cur.rowcount
            except sqlite3.Error as e:
                logger.warning(f"过期任务清理失败: {e}")
                return 0

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
