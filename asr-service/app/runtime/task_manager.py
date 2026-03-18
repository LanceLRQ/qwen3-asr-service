import queue
import threading
import uuid
import logging
import time
from datetime import datetime
from app.config import TASK_TIMEOUT

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, max_queue_size=100):
        self._queue = queue.Queue(maxsize=max_queue_size)
        self._tasks = {}  # task_id -> task_dict
        self._lock = threading.Lock()
        self._worker_thread = None
        self._process_fn = None

    def set_processor(self, fn):
        """注入任务处理函数: fn(task_dict) -> result"""
        self._process_fn = fn

    def start(self):
        """启动工作线程"""
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        logger.info("任务工作线程已启动")

    def submit(self, file_path: str, language: str | None = None) -> str:
        """提交任务，返回 task_id"""
        task_id = str(uuid.uuid4())
        task = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0.0,
            "file_path": file_path,
            "language": language,
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }

        with self._lock:
            self._tasks[task_id] = task

        self._queue.put(task_id)
        logger.info(f"任务已提交: {task_id}")
        return task_id

    def get_task(self, task_id: str) -> dict | None:
        """查询任务状态"""
        with self._lock:
            return self._tasks.get(task_id)

    def update_progress(self, task_id: str, progress: float):
        """更新任务进度"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["progress"] = progress

    def _worker(self):
        """工作线程：串行处理任务"""
        while True:
            task_id = self._queue.get()

            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    continue
                task["status"] = "processing"

            start_time = time.time()
            try:
                result = self._process_fn(task)
                elapsed = time.time() - start_time

                if elapsed > TASK_TIMEOUT:
                    with self._lock:
                        task["status"] = "failed"
                        task["error"] = "处理超时"
                    logger.error(f"任务超时: {task_id} ({elapsed:.0f}s)")
                else:
                    with self._lock:
                        task["status"] = "completed"
                        task["progress"] = 1.0
                        task["result"] = result
                    logger.info(f"任务完成: {task_id} ({elapsed:.1f}s)")
            except Exception as e:
                with self._lock:
                    task["status"] = "failed"
                    task["error"] = str(e)
                logger.error(f"任务失败: {task_id}, 错误: {e}")
            finally:
                self._queue.task_done()
