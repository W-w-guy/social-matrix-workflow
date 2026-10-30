# Hermes skill tap — social matrix

一个符合 Hermes tap 规范的仓库布局：

```
skills/
└── social-matrix/
    ├── SKILL.md          # frontmatter: name/description/version/author/license/platforms/tags
    └── scripts/
        └── run.py        # 转发到仓库根的 workflow.py（只有一份实现）
```

安装（Hermes 侧）：

```bash
hermes skills tap add <owner/repo>
```

> 注意：这个 skill 依赖仓库根目录的 `workflow.py` 与 `lib/`，
> 不是只靠 `skills/social-matrix/` 就能独立运行。安装时请用完整仓库。
