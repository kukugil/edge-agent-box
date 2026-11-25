"""飞书周文档"代码（GitHub）"一栏的链接生成器（给 Claude 用）。

用途：每次代码推到 GitHub 后，按 code_links.json 里的清单，算出每个格子在最新提交上应该链到的地址
（文件 + 函数所在的行号），和上次写进飞书的地址（state.json）比，列出哪些格子要改。
改飞书这一步不在脚本里做，照 README.md 的步骤在浏览器里改，改完再用 --save 记下来。

用法（项目根目录）：
  python .tools/code-links/code_links.py              # 对比 origin/main，列出要改的格子
  python .tools/code-links/code_links.py --check      # 同上，另外逐个访问 GitHub 确认链接能打开
  python .tools/code-links/code_links.py --save       # 飞书改完后，把这次的链接记成"当前飞书里的样子"
  python .tools/code-links/code_links.py --commit abc1234   # 指定提交（必须已推送）

为什么用固定提交号而不用 main：main 上的行号会随代码变，固定提交号的链接永远指得准。
代价就是每次推送后要跑一遍这个脚本、把变了的格子改掉。
"""
import argparse
import ast
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAP_FILE = HERE / 'code_links.json'
STATE_FILE = HERE / 'state.json'


def git(*args: str) -> str:
    """跑一条 git 命令，返回去掉首尾空白的输出；失败直接抛错。"""
    out = subprocess.run(['git', *args], capture_output=True, cwd=HERE.parent.parent)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout.decode('utf-8').strip()


def resolve_commit(commit: str | None) -> str:
    """把用户给的提交（默认 origin/main）解析成完整提交号，并确认它已经在 GitHub 上。

    没推送的提交在 GitHub 上打不开，链接会是 404，所以这里先拦住。
    """
    git('fetch', '-q', 'origin')
    sha = git('rev-parse', commit or 'origin/main')
    remote = git('branch', '-r', '--contains', sha)
    if not remote:
        raise RuntimeError(f'{sha[:8]} 还没推送到 GitHub，先推送再生成链接')
    return sha


def symbol_range(source: str, name: str) -> tuple[int, int] | None:
    """在 Python 源码里找顶层函数、类或常量 name，返回 (起始行, 结束行)，行号从 1 开始。

    用 ast 而不是正则：能拿到准确的结束行，装饰器也算进起始行。找不到返回 None。
    """
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            return start, node.end_lineno
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return node.lineno, node.end_lineno
    return None


def build_url(repo: str, sha: str, item: dict) -> tuple[str | None, str]:
    """按清单里的一项算出链接。返回 (链接, 说明)；kind 为 none 时链接是 None。

    出错（文件不在、函数改名了）时链接为 None，说明里写原因，需要人去改 code_links.json。
    """
    short = sha[:8]
    kind = item.get('kind', 'symbol')
    if kind == 'none':
        return None, '没有代码，写 —'
    path = item['path']
    if kind == 'tree':
        return f'{repo}/tree/{short}/{path}', '整个文件夹'
    try:
        source = git('show', f'{sha}:{path}')
    except RuntimeError:
        return None, f'错误：这个提交里没有 {path}'
    if kind == 'file':
        return f'{repo}/blob/{short}/{path}', '整个文件'
    names = item['symbol'] if isinstance(item['symbol'], list) else [item['symbol']]
    ranges = []
    for n in names:
        r = symbol_range(source, n)
        if r is None:
            return None, f'错误：{path} 里找不到 {n}（改名或删了？去改 code_links.json）'
        ranges.append(r)
    start, end = min(r[0] for r in ranges), max(r[1] for r in ranges)
    return f'{repo}/blob/{short}/{path}#L{start}-L{end}', '、'.join(names)


def url_ok(url: str) -> bool:
    """访问一次 GitHub，能打开（200）就算好。行号锚点不会被服务器检查，只能查文件在不在。"""
    try:
        req = urllib.request.Request(url.split('#')[0], method='HEAD', headers={'User-Agent': 'code-links'})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--commit', help='指定提交，默认 origin/main')
    ap.add_argument('--check', action='store_true', help='逐个访问链接确认能打开')
    ap.add_argument('--save', action='store_true', help='把这次的链接记进 state.json（飞书改完后再用）')
    args = ap.parse_args()

    cfg = json.loads(MAP_FILE.read_text(encoding='utf-8'))
    state = json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {'links': {}}
    sha = resolve_commit(args.commit)
    print(f'提交：{sha[:8]}（上次写进飞书的是 {state.get("commit", "无记录")[:8]}）\n')

    new_links, errors, changes = {}, 0, 0
    for it in cfg['items']:
        url, why = build_url(cfg['repo'], sha, it)
        old = state['links'].get(it['id'])
        if why.startswith('错误'):
            errors += 1
            mark = '!! 出错'
        elif url == old or (url is None and old in (None, '—')):
            mark = '不变'
        else:
            changes += 1
            mark = '要改'
        if args.check and url and not url_ok(url):
            errors += 1
            mark += '，链接打不开'
        new_links[it['id']] = url or '—'
        print(f"[{mark}] {it['id']} {it['item']}（{it['where']}）")
        if mark != '不变':
            print(f'    旧：{old or "（无）"}')
            print(f'    新：{url or "—"}    ← {why}')

    print(f'\n共 {len(cfg["items"])} 格：要改 {changes}，出错 {errors}')
    if args.save:
        if errors:
            print('有出错的格子，没有保存。先修好 code_links.json 再跑。')
            return 1
        STATE_FILE.write_text(json.dumps({'commit': sha, 'links': new_links}, ensure_ascii=False, indent=1),
                              encoding='utf-8')
        print(f'已记下：飞书里现在是 {sha[:8]} 的链接')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
