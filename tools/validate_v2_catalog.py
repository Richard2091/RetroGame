#!/usr/bin/env python3
"""校验 RetroGame v2 资源仓库结构。

检查项：
  - 所有 JSON 可解析、schemaVersion 正确
  - catalog/index.v2.json 分类与列表文件存在且一致
  - 列表项 detailUrl / coverUrl 相对解析后文件存在
  - game.json 的 files / media 相对引用文件存在
  - 文件 size 与实际一致，sha256 与实际一致
  - 全部/分类列表条目数量一致，id 唯一
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

ERRORS: list[str] = []
WARNINGS: list[str] = []
CHECKED_FILES = 0
CHECKED_HASHES = 0


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except Exception as error:
        ERRORS.append(f"JSON 解析失败 {path.name}: {error}")
        return None


def resolve_rel(base_file: Path, ref: str) -> Path | None:
    ref = unquote(ref)
    if ref.startswith(("http://", "https://", "/")):
        return None
    return (base_file.parent / ref).resolve()


def check_rel(base_file: Path, ref: str, label: str, required: bool) -> None:
    global CHECKED_FILES
    if not ref:
        if required:
            ERRORS.append(f"{base_file}: {label} 为空但必填")
        return
    target = resolve_rel(base_file, ref)
    if target is None:
        WARNINGS.append(f"{base_file}: {label} 是绝对/站根 URL（{ref}），跳过")
        return
    CHECKED_FILES += 1
    if not target.is_file():
        ERRORS.append(f"{base_file}: {label} -> {ref} 指向不存在的文件 {target}")


def check_file_hashes(base_file: Path, rel: str, size: int, sha256: str, label: str) -> None:
    global CHECKED_HASHES
    if not rel or not sha256:
        return
    target = resolve_rel(base_file, rel)
    if target is None or not target.is_file():
        return
    CHECKED_HASHES += 1
    data = target.read_bytes()
    if len(data) != size:
        ERRORS.append(f"{base_file}: {label} size 不符（声明 {size}，实际 {len(data)}）")
    if hashlib.sha256(data).hexdigest().upper() != sha256.upper():
        ERRORS.append(f"{base_file}: {label} sha256 不符")


def validate_games_dir(repo_root: Path) -> None:
    games_root = repo_root / "games"
    if not games_root.is_dir():
        ERRORS.append("games/ 目录不存在")
        return
    for game_json_path in sorted(games_root.rglob("game.json")):
        game_json = load_json(game_json_path)
        if game_json is None:
            continue
        if game_json.get("schemaVersion") != 2:
            ERRORS.append(f"{game_json_path}: schemaVersion 应为 2")
        if not game_json.get("id") or not game_json.get("slug"):
            ERRORS.append(f"{game_json_path}: 缺少 id/slug")
        if game_json.get("categoryId") not in {"fc", "all"}:
            ERRORS.append(f"{game_json_path}: categoryId 非法 {game_json.get('categoryId')}")

        media = game_json.get("media", {})
        cover = media.get("cover", {})
        check_rel(game_json_path, cover.get("url", ""), "media.cover.url", required=False)
        for i, shot in enumerate(media.get("screenshots", []), start=1):
            check_rel(game_json_path, shot.get("url", ""), f"media.screenshots[{i}].url", required=False)

        files = game_json.get("files", [])
        for f in files:
            rel = f.get("path", "")
            check_rel(game_json_path, rel, f"files[{f.get('id')}].path", required=True)
            check_file_hashes(game_json_path, rel, f.get("size", -1), f.get("hashes", {}).get("sha256", ""), f"files[{f.get('id')}]")

        runtime = game_json.get("runtime", {})
        if runtime.get("family") == "libretro" and not runtime.get("requiredCorePlatformId"):
            ERRORS.append(f"{game_json_path}: libretro 运行时缺少 requiredCorePlatformId")


def validate_catalog(repo_root: Path) -> None:
    index_path = repo_root / "catalog/index.v2.json"
    index = load_json(index_path)
    if index is None:
        return
    if index.get("schemaVersion") != 2:
        ERRORS.append(f"{index_path}: schemaVersion 应为 2")

    seen_ids: set[str] = set()
    total = 0
    for category in index.get("categories", []):
        cat_id = category.get("id")
        list_rel = category.get("listUrl", "")
        list_path = repo_root / "catalog" / list_rel
        if not list_path.is_file():
            ERRORS.append(f"{index_path}: 分类 {cat_id} 的 listUrl {list_rel} 文件不存在")
            continue
        list_data = load_json(list_path)
        if list_data is None:
            continue
        if list_data.get("categoryId") not in {cat_id, "all"}:
            ERRORS.append(f"{list_path}: categoryId 与清单不匹配（{cat_id} vs {list_data.get('categoryId')}）")
        games = list_data.get("games", [])
        if cat_id == "all":
            continue
        total += len(games)
        for item in games:
            gid = item.get("id")
            if gid in seen_ids:
                ERRORS.append(f"{list_path}: 重复 id {gid}")
            seen_ids.add(gid)
            if item.get("categoryId") != cat_id:
                ERRORS.append(f"{list_path}: 条目 {gid} categoryId 应为 {cat_id}")
            check_rel(list_path, item.get("detailUrl", ""), f"条目 {gid} detailUrl", required=True)
            check_rel(list_path, item.get("coverUrl", ""), f"条目 {gid} coverUrl", required=False)
            detail = resolve_rel(list_path, item.get("detailUrl", ""))
            if detail is not None and detail.is_file():
                game_json = load_json(detail)
                if game_json and game_json.get("id") != gid:
                    ERRORS.append(f"{detail}: game.json id {game_json.get('id')} 与列表条目 {gid} 不一致")

    if total != len(seen_ids):
        WARNINGS.append(f"分类清单合计 {total} 条，唯一 id {len(seen_ids)} 条")

    search_path = repo_root / "catalog/search-index.v2.json"
    search = load_json(search_path)
    if search is not None and len(search.get("entries", [])) != len(seen_ids):
        WARNINGS.append(f"搜索索引 {len(search.get('entries', []))} 条与列表 {len(seen_ids)} 条不一致")

    all_list = load_json(repo_root / "catalog/all/manifest.list.v2.json")
    if all_list is not None:
        if len(all_list.get("games", [])) != len(seen_ids):
            WARNINGS.append(f"all 清单 {len(all_list.get('games', []))} 条与分类 {len(seen_ids)} 条不一致")


def validate_legacy(repo_root: Path) -> None:
    legacy_path = repo_root / "legacy/manifest.v1.json"
    legacy = load_json(legacy_path)
    if legacy is None:
        return
    if legacy.get("schemaVersion") != "1.0":
        ERRORS.append(f"{legacy_path}: schemaVersion 应为 1.0")
    ids = [g.get("id") for g in legacy.get("games", [])]
    if len(ids) != len(set(ids)):
        ERRORS.append(f"{legacy_path}: 存在重复 id")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RetroGame v2 catalog.")
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()

    validate_games_dir(repo_root)
    validate_catalog(repo_root)
    validate_legacy(repo_root)

    print(f"校验完成：检查 {CHECKED_FILES} 个引用，{CHECKED_HASHES} 个哈希")
    print(f"错误 {len(ERRORS)} 个，警告 {len(WARNINGS)} 个")
    for error in ERRORS:
        print(f"  [错误] {error}")
    for warning in WARNINGS[:30]:
        print(f"  [警告] {warning}")
    if len(WARNINGS) > 30:
        print(f"  ... 另有 {len(WARNINGS) - 30} 条警告")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
