"""知识库检索（D15 只留接口，默认关）。

以后接 WeKnora：POST /api/v1/knowledge-search。现在不连它，也不启动 Spark 上的任何服务。
开关在 agent/config.json 的 "kb_enabled"，不写就是关。

输出格式固定：[{'content': 资料原文, 'source': 来自哪里, 'score': 相关度}]，关着时返回 []。
reply.write_reply 拿到 [] 就不往提示词里写资料。
"""
from .models import load_config

_warned = False


def search(question: str) -> list[dict]:
    """输入对方的问题，输出相关资料；开关关着、或开着但还没接上时都返回 []。"""
    global _warned
    if not load_config().get('kb_enabled', False):
        return []
    # 开关打开了但还没接：照样返回空，只提醒一次，免得以为资料生效了
    if not _warned:
        print('  知识库开关是开的，但还没接 WeKnora，这次按没查到处理')
        _warned = True
    return []
