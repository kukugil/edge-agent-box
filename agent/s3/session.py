"""管理一次 S3 会话和微信监听交接。"""

import time


class Session:
    """用监听与 S3 Runner 的接口驱动互斥，界面通过回调接收状态。"""

    def __init__(self, listener, worker, changed, completed):
        self.listener = listener
        self.worker = worker
        self.changed = changed
        self.completed = completed
        self.state = 'idle'
        self.started = 0.0
        self.restore_args = None
        self.quit_callback = None
        worker.finished.connect(self.finish)

    @property
    def busy(self):
        """本轮是否已经独占自动操作入口。"""
        return self.state != 'idle'

    def elapsed(self):
        """返回用户提交任务后的总秒数，包括等待监听退出。"""
        return time.monotonic() - self.started

    def start(self, task):
        """独占操作入口，必要时等监听进程退出后再启动 S3。"""
        if self.busy or self.quit_callback is not None:
            return
        self.started = time.monotonic()
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
        """监听退出后按当前状态启动 S3 或完成取消。"""
        if self.state == 'waiting':
            self._launch(task)
        elif self.state == 'cancelled':
            self.finish('STOPPED', '已取消启动')

    def _launch(self, task):
        """切换到运行态并在窗口隐藏后交给 worker。"""
        self.state = 'running'
        self.changed()
        self.worker.start(task)

    def stop(self):
        """撤销待启动的任务，或结束正在运行的 S3。"""
        if self.state == 'waiting':
            self.state = 'cancelled'
            self.changed()
        elif self.state == 'running':
            self.state = 'stopping'
            self.changed()
            self.worker.stop()

    def quit(self, then):
        """先结束当前 S3 会话，再交给托盘原来的退出流程。"""
        if self.quit_callback is not None:
            return
        self.quit_callback = then
        if self.busy:
            self.stop()
        else:
            then()

    def finish(self, result, detail):
        """收尾只消费一次监听快照，退出托盘时直接进入退出流程。"""
        if not self.busy:
            return
        args, self.restore_args = self.restore_args, None
        elapsed = self.elapsed()
        self.state = 'idle'
        if self.quit_callback is not None:
            self.quit_callback()
            return
        try:
            if args is not None:
                self.listener.start(args)
        except OSError as exc:
            detail = f'{detail} · 监听恢复失败（{type(exc).__name__}）'.strip(' ·')
        self.changed()
        self.completed(result, detail, elapsed)
