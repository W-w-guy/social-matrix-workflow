"""本地存储：原子写 + 追加式台账。

为什么不用数据库：客户要的是「样例可演示」，装一个 SQLite 也引入依赖面。
这里用「原子 JSON（临时文件 + os.replace）+ 追加式 JSONL 台账」两件东西，
零第三方依赖，Windows 与 Linux 上行为一致（都显式 newline="\n"）。

★ 台账为什么要追加式而不是每轮重写：发布过程可能被杀。重写式台账在进程
  中途死亡时会丢记录，追加式最坏只丢「正在写的那一行」——而这一行本身就是
  判断「这次是否真的发出去了」的关键证据。
"""
import json
import os


def _ensure_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj):
    """原子写：先写 .tmp 再 replace。中途崩溃不会留下半个 JSON。"""
    _ensure_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def append_jsonl(path, rec):
    """追加一行台账。flush + fsync，保证进程被杀时这行已在盘上。"""
    _ensure_dir(path)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
