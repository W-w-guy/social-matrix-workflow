"""素材登记：把客户的原始素材读进来，登记成可追溯的资产。

客户原文「优先使用自有素材」。真实素材库的形态是不可控的：
  * 文件名带中文、空格、全角括号
  * 编码可能是 UTF-8 BOM 或 GBK（Excel/ERP 导出）
  * 同一张图可能重复（同 sha256），不能当两份资产
  * 有些素材没有可读文本（纯图），草稿生成必须知道「这张图没有文字」

这里只做登记，不改素材、不搬走素材：
  asset_id 由内容哈希派生 —— 同一个文件无论如何改名，asset_id 不变。
  这样「草案引用了哪份素材」可以事后核对，且素材改名不会伪造出新资产。
"""
import hashlib
import json
import os

TEXT_EXT = {".txt", ".md", ".csv", ".json"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_text_encoding(raw):
    """按 BOM -> UTF-8 -> GBK -> GB18030 顺序试。失败返回 None。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return None


def read_text(path):
    """读素材文本。非文本文件返回 (None, None) —— 不假装读到了东西。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    enc = detect_text_encoding(raw)
    if enc is None:
        return None, None
    return raw.decode(enc), enc


def register(folder, out_path):
    """扫描 folder，把每个文件登记成一条 asset。返回 assets 列表。"""
    if not os.path.isdir(folder):
        raise FileNotFoundError("素材目录不存在：%s" % folder)

    assets, seen = [], {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        digest = _sha256(path)
        asset_id = "as_" + digest[:12]

        # ★ 同一内容重复出现：登记一次，把别名记下来。
        #   不这样做的话，客户把 logo.png 复制成 logo-副本.png，
        #   草稿就会以为「有两张不同的图可以用」，生成重复内容。
        if digest in seen:
            seen[digest]["aliases"].append(name)
            continue

        rec = {
            "asset_id": asset_id,
            "file": name,
            "aliases": [],
            "bytes": os.path.getsize(path),
            "sha256": digest,
            "kind": ("image" if ext in IMAGE_EXT
                     else "text" if ext in TEXT_EXT else "other"),
            "encoding": None,
            "text": None,
            "text_chars": 0,
        }
        if rec["kind"] == "text":
            text, enc = read_text(path)
            rec["encoding"] = enc
            rec["text"] = text
            rec["text_chars"] = len(text or "")
        elif rec["kind"] == "image":
            # 不做 OCR：样例阶段没有 OCR 依赖，如实标记「未提取文字」
            rec["text"] = None
        seen[digest] = rec
        assets.append(rec)

    # ★ 2026-10-30 修：这里原来写 os.path.abspath(folder)，于是同一份素材
    #   在 Windows 与 Ubuntu 上登记出的 assets.json 哈希不同（C:\... vs /tmp/...）。
    #   客户会拿这个文件做 diff 与 MANIFEST 核对，"看起来不一致"就解释不清。
    #   绝对路径本来也不该进交付产物 —— 它把本机目录结构泄给了对方。
    #   改成只记目录名，两平台产物逐字节相同（已实测）。
    doc = {"folder": os.path.basename(os.path.abspath(folder)),
           "count": len(assets), "assets": assets}
    from .store import write_json
    write_json(out_path, doc)
    return assets
