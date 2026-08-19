# RetroGame

Retro Hall 模拟器项目的游戏资源仓库（v2 目录结构），按《资源仓库规范》组织，通过 GitHub Pages 发布供客户端读取。

## 结构

```text
catalog/
  index.v2.json                # 总入口与分类清单
  all/manifest.list.v2.json    # 全部游戏列表（自动生成）
  fc/manifest.list.v2.json     # FC 分类列表
  search-index.v2.json         # 全局搜索索引
games/
  fc/<slug>/                   # 单个游戏文件夹
    game.json                  # 权威详情
    cover.*                    # 封面
    screenshots/               # 截图
    roms/                      # 游戏文件
legacy/
  manifest.v1.json             # 旧版 v1 兼容投影
tools/
  generate_v2_catalog.py       # v1 → v2 转换脚本
  validate_v2_catalog.py       # v2 结构校验脚本
```

## 数据来源

FC 数据整理自 [FC_ROMS](https://github.com/Richard2091/FC_ROMS)：

- 游戏清单与 ROM：参考《红白机终极档案》收录的 NES/FDS 官方发售游戏，ROM 主要整理自 [FC 图库](https://fcpic.nesbbs.com)。
- 身份与哈希：No-Intro 命名体系。
- 中文简介：GameWiki（wiki.nesbbs.com）等渠道整理。
- 封面：libretro-thumbnails 标题画面（title screen）预处理。

## 版权边界

仓库中的商业游戏 ROM 权利状态不明确，版权归各自厂商和作者所有，仅用于学习和研究，请支持正版。元数据与脚本可自由使用。

## 维护

数据由脚本自动生成，不要手工维护汇总清单：

```bash
py tools/generate_v2_catalog.py --source-root ../FC_ROMS --repo-root .
py tools/validate_v2_catalog.py --repo-root .
```
