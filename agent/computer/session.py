"""管理电脑助手和自动回复的交接。"""

import time


class Session:
    """通过监听和 worker 接口驱动交接，结果由界面回调接收。"""

    def __init__(self, listener, worker, changed, completed, clock=time.monotonic):
        """接收两个 Runner、界面回调和计时函数。"""
        self.listener = listener
        self.worker = worker
        self.changed = changed
        self.completed = completed
        self.clock = clock
        self.state = 'idle'
        self.started = 0.0
        self.task_started = None
        self.wait_seconds = 0.0
        self.restore_args = None
        self.quit_callback = None
        worker.finished.connect(self.finish)

    @property
    def busy(self):
        """当前任务是否占用自动操作入口。"""
        return self.state != 'idle'

    @property
    def can_restore(self):
        """是否已有待恢复的监听参数，且本轮已收尾。"""
        return self.restore_args is not None and not self.busy and self.quit_callback is None

    def elapsed(self):
        """返回自提交任务以来的总秒数。"""
        return self.clock() - self.started

    def start(self, task):
        """必要时先停监听，然后启动给定任务。"""
        if self.busy or self.quit_callback is not None:
            return
        self.started = self.clock()
        self.task_started = None
        self.wait_seconds = 0.0
        if not self.listener.running():
            self._launch(task)
            return
        self.restore_args = list(self.listener.last_extra)
        self.state = 'waiting'
        self.changed()
        try:
            self.listener.stop(then=lambda: self._listener_stopped(task))
        except OSError as exc:
            self.restore_args = None
            self.finish('ERROR', f'未能请求停止监听（{type(exc).__name__}）')

    def _listener_stopped(self, task):
        """收到监听退出通知后启动任务，或完成待启动取消。"""
        self.wait_seconds = self.elapsed()
        if self.state == 'waiting':
            self._launch(task)
        elif self.state == 'cancelled':
            self.finish('STOPPED', '已取消启动')

    def _launch(self, task):
        """记录任务起点并提交 worker。"""
        self.task_started = self.clock()
        self.state = 'running'
        self.changed()
        self.worker.start(task)

    def stop(self):
        """取消待启动的任务，或请求停止运行中的 worker。"""
        if self.state == 'waiting':
            self.state = 'cancelled'
            self.changed()
        elif self.state == 'running':
            self.state = 'stopping'
            self.changed()
            self.worker.stop()

    def quit(self, then):
        """等待助手收尾，然后通知托盘进入监听退出流程。"""
        if self.quit_callback is not None:
            return
        self.quit_callback = then
        if self.busy:
            self.stop()
        else:
            self.changed()
            then()

    def finish(self, result, detail):
        """收尾并报告等待监听和任务运行的独立用时。"""
        if not self.busy:
            return
        task_seconds = 0.0 if self.task_started is None else self.clock() - self.task_started
        if self.state == 'waiting':
            self.wait_seconds = self.elapsed()
        self.state = 'idle'
        self.changed()
        if self.quit_callback is not None:
            self.quit_callback()
        else:
            self.completed(result, detail, self.wait_seconds, task_seconds)

    def restore(self):
        """使用保存的参数启动监听。成功受理后只消费一次。"""
        if not self.can_restore:
            return
        args, self.restore_args = self.restore_args, None
        try:
            self.listener.start(args)
        except OSError:
            self.restore_args = args
            raise
        finally:
            self.changed()
