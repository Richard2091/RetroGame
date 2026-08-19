#!/usr/bin/env python3
"""从 FC_ROMS v1 数据生成 RetroGame v2 资源仓库结构。

输出结构：
  catalog/index.v2.json
  catalog/all/manifest.list.v2.json
  catalog/fc/manifest.list.v2.json
  catalog/search-index.v2.json
  games/fc/<slug>/game.json
  games/fc/<slug>/cover.*
  games/fc/<slug>/screenshots/*
  games/fc/<slug>/roms/*
  legacy/manifest.v1.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

CATALOG_ID = "retrogame"
CATALOG_NAME = "RetroGame"
DEFAULT_CATEGORY_ID = "all"
LEGACY_SOURCE_REPO = "Richard2091/FC_ROMS"
NEW_REPO = "Richard2091/RetroGame"
NEW_REPO_REF = "main"

ROM_MIME = "application/octet-stream"
NES_SIG = b"NES\x1a"
INES_HEADER_SIZE = 16

SEARCH_TERMS = {"cover", "boxart", "folder", "logo", "screenshot", "snap", "shot", "screen"}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def safe_filename(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", value)


def headerless_crc32(data: bytes) -> str:
    if data[:4] == NES_SIG and len(data) > INES_HEADER_SIZE:
        data = data[INES_HEADER_SIZE:]
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def media_source_meta(
    cover_source: dict[str, Any], game_id: str
) -> dict[str, str]:
    entries = cover_source.get("entries", [])
    match = next((e for e in entries if e.get("id") == game_id), None)
    if not match or match.get("status") != "written":
        return {"source": "manual", "sourceUrl": "", "licenseHint": "unknown"}
    return {
        "source": match.get("sourceRepository") or "libretro-thumbnails",
        "sourceUrl": match.get("sourceUrl") or "",
        "licenseHint": "unknown",
    }


def copy_media(
    source_root: Path, repo_root: Path, relative_path: str, dest_rel: str
) -> Path | None:
    src = source_root / relative_path
    if not src.is_file():
        return None
    dest = repo_root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def copy_rom(source_root: Path, repo_root: Path, game_dir_rel: str, rom_path_rel: str) -> None:
    src = source_root / rom_path_rel
    if not src.is_file():
        raise FileNotFoundError(f"ROM 源文件缺失：{src}")
    dest = repo_root / game_dir_rel / "roms" / safe_filename(src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def pick_primary_rom(roms: list[dict[str, Any]]) -> int:
    """选择主 ROM：优先带 ! 质量标记且地区非空的版本。没有 ROM 时返回 -1。"""
    if not roms:
        return -1

    def score(idx: int) -> tuple[int, int]:
        rom = roms[idx]
        quality = "!" in rom.get("qualityTags", []) or "!" in rom.get("tags", [])
        region = bool(rom.get("region"))
        return (1 if quality else 0, 1 if region else 0)

    return max(range(len(roms)), key=score)


def build_game_json(
    game: dict[str, Any],
    zh_meta: dict[str, Any],
    cover_source: dict[str, Any],
    source_root: Path,
    repo_root: Path,
    now: str,
    storage: dict[str, str] | None = None,
    copy_roms: bool = True,
) -> dict[str, Any]:
    game_id = game["id"]
    slug = game["slug"]
    game_dir = f"games/fc/{slug}"
    roms = game.get("roms", [])
    zh = zh_meta.get(game_id, {}) if isinstance(zh_meta, dict) else {}

    cover_rel = game.get("assets", {}).get("cover") or ""
    cover_src = media_source_meta(cover_source, game_id)
    cover_dest = ""
    if cover_rel:
        suffix = Path(cover_rel).suffix.lower() or ".webp"
        dest = copy_media(source_root, repo_root, cover_rel, f"{game_dir}/cover{suffix}")
        if dest is not None:
            cover_dest = dest.name

    screenshot_dest: list[str] = []
    for i, shot_rel in enumerate(game.get("assets", {}).get("screenshots", []) or [], start=1):
        suffix = Path(shot_rel).suffix.lower()
        dest = copy_media(source_root, repo_root, shot_rel, f"{game_dir}/screenshots/{i:02d}{suffix}")
        if dest is not None:
            screenshot_dest.append(f"screenshots/{dest.name}")

    logo_dest: list[str] = []
    for i, logo_rel in enumerate(game.get("assets", {}).get("logos", []) or [], start=1):
        suffix = Path(logo_rel).suffix.lower()
        dest = copy_media(source_root, repo_root, logo_rel, f"{game_dir}/logos/{i:02d}{suffix}")
        if dest is not None:
            logo_dest.append(f"logos/{dest.name}")

    primary_idx = pick_primary_rom(roms)
    files: list[dict[str, Any]] = []
    for i, rom in enumerate(roms):
        rom_path_rel = rom.get("path", "")
        filename = safe_filename(rom.get("filename") or Path(rom_path_rel).name)
        if copy_roms:
            copy_rom(source_root, repo_root, game_dir, rom_path_rel)
        hashes = rom.get("hash", {})
        headers: dict[str, str] = {}
        if rom.get("format") == "NES":
            headers["headerlessCrc32"] = headerless_crc32((source_root / rom_path_rel).read_bytes())
        file_entry: dict[str, Any] = {
            "id": "main" if i == primary_idx else f"alt-{i + 1}",
            "kind": "rom",
            "role": "primary" if i == primary_idx else "alternate",
            "path": f"roms/{filename}",
            "mime": ROM_MIME,
            "size": hashes.get("sizeBytes", 0),
            "hashes": {
                "crc32": hashes.get("crc32", ""),
                "md5": hashes.get("md5", ""),
                "sha1": hashes.get("sha1", ""),
                "sha256": hashes.get("sha256", ""),
            },
            "availability": "public",
        }
        if storage:
            encoded = quote(filename, safe="")
            file_entry["url"] = (
                f"{storage['publicDomain'].rstrip('/')}/"
                f"{storage['objectPrefix'].strip('/')}/fc/{slug}/roms/{encoded}"
            )
        if headers:
            file_entry["headers"] = headers
        files.append(file_entry)

    title = game.get("title", {})
    release_year = ""
    release_date = game.get("releaseDate", "")
    if release_date and re.match(r"^\d{4}-\d{2}-\d{2}$", release_date):
        release_year = release_date[:4]

    description_zh = ""
    if zh and zh.get("intro"):
        description_zh = zh["intro"]

    game_json: dict[str, Any] = {
        "schemaVersion": 2,
        "id": f"fc-{game_id}",
        "slug": slug,
        "categoryId": "fc",
        "platformIds": ["nes"],
        "primaryPlatformId": "nes",
        "runtimeFamily": "libretro",
        "title": {
            "zh": title.get("zh", ""),
            "en": title.get("en", ""),
            "ja": title.get("ja", ""),
        },
        "alternateTitles": [],
        "description": {
            "zh": description_zh,
            "en": "",
        },
        "media": {
            "cover": {
                "url": cover_dest or "",
                "source": cover_src["source"],
                "licenseHint": cover_src["licenseHint"],
            },
            "screenshots": [
                {"url": u, "source": "manual", "licenseHint": "unknown"} for u in screenshot_dest
            ],
            "logos": [
                {"url": u, "source": "manual", "licenseHint": "unknown"} for u in logo_dest
            ],
        },
        "files": files,
        "runtime": {
            "family": "libretro",
            "requiredCorePlatformId": "nes",
            "recommendedCoreIds": ["fceumm", "mesen"],
        },
        "links": {
            "officialPageUrl": "",
            "sourcePageUrl": "",
            "downloadPageUrl": "",
        },
        "legal": {
            "copyrightStatus": "unknown",
            "license": "",
            "licenseUrl": "",
            "rightsHolder": "",
            "notes": (
                "商业游戏 ROM 收集自公开渠道，权利状态不明确；"
                "由 FC_ROMS 仓库整理提供，仅供学习研究，请支持正版。"
            ),
        },
        "sources": [
            {
                "name": "FC图库",
                "kind": "collection",
                "url": "https://fcpic.nesbbs.com",
            },
            {
                "name": "No-Intro",
                "kind": "identity",
                "url": "https://no-intro.org/",
            },
        ],
        "updatedAt": now,
    }
    if release_year:
        game_json["releaseYear"] = int(release_year)
    return game_json


def build_list_item(game_json: dict[str, Any]) -> dict[str, Any]:
    title = {k: v for k, v in game_json["title"].items() if v}
    has_files = bool(game_json.get("files"))
    cover_url = ""
    if game_json["media"]["cover"].get("url"):
        cover_url = f"../../games/fc/{game_json['slug']}/{game_json['media']['cover']['url']}"
    return {
        "id": game_json["id"],
        "slug": game_json["slug"],
        "categoryId": "fc",
        "primaryPlatformId": "nes",
        "platformName": "FC",
        "runtimeFamily": "libretro",
        "title": title,
        "coverUrl": cover_url,
        "detailUrl": f"../../games/fc/{game_json['slug']}/game.json",
        "tags": [],
        "releaseYear": game_json.get("releaseYear", 0),
        "availability": {"binary": "public" if has_files else "metadata_only"},
    }


def legacy_rom_url(relative_path: str, storage: dict[str, str] | None = None) -> str:
    if storage:
        return f"{storage['publicDomain'].rstrip('/')}/{quote(relative_path, safe='/-_.~')}"
    return (
        "https://raw.githubusercontent.com/"
        f"{NEW_REPO}/{NEW_REPO_REF}/{quote(relative_path)}"
    )


def legacy_asset_url(relative_path: str, storage: dict[str, str] | None = None) -> str | None:
    if not relative_path:
        return None
    return legacy_rom_url(relative_path, storage)


def build_legacy_manifest(
    games: list[dict[str, Any]], now: str, storage: dict[str, str] | None = None
) -> dict[str, Any]:
    legacy_games: list[dict[str, Any]] = []
    for game_json in games:
        files = game_json["files"]
        roms = []
        for f in files:
            region_tags = []
            roms.append(
                {
                    "path": f"games/fc/{game_json['slug']}/roms/{Path(f['path']).name}",
                    "filename": Path(f["path"]).name,
                    "format": "NES" if Path(f["path"]).suffix.lower() == ".nes" else "FDS",
                    "titleFromFilename": "",
                    "region": "",
                    "revision": "",
                    "tags": region_tags,
                    "qualityTags": [],
                    "url": legacy_rom_url(
                        f"games/fc/{game_json['slug']}/roms/{Path(f['path']).name}",
                        storage,
                    ),
                    "hash": {
                        "sizeBytes": f.get("size", 0),
                        "crc32": f["hashes"].get("crc32", ""),
                        "md5": f["hashes"].get("md5", ""),
                        "sha1": f["hashes"].get("sha1", ""),
                        "sha256": f["hashes"].get("sha256", ""),
                    },
                }
            )
        source_id = game_json["id"].removeprefix("fc-")
        legacy_games.append(
            {
                "id": source_id,
                "slug": game_json["slug"],
                "title": game_json["title"],
                "displayTitle": game_json["title"].get("zh") or game_json["title"].get("en") or source_id,
                "releaseDate": "",
                "platform": "NES",
                "category": "",
                "description": game_json["description"].get("zh", ""),
                "romDir": f"games/fc/{game_json['slug']}/roms",
                "romCount": len(roms),
                "roms": roms,
                "assets": {
                    "cover": (
                        f"games/fc/{game_json['slug']}/{game_json['media']['cover'].get('url', '')}"
                        if game_json["media"]["cover"].get("url")
                        else None
                    ),
                    "coverUrl": legacy_asset_url(
                        f"games/fc/{game_json['slug']}/{game_json['media']['cover'].get('url', '')}"
                    ),
                    "screenshots": [],
                    "screenshotUrls": [],
                    "logos": [],
                    "logoUrls": [],
                },
                "metadataWarnings": [],
            }
        )
    return {
        "schemaVersion": "1.0",
        "generatedAt": now,
        "source": {
            "type": "github",
            "repository": NEW_REPO,
            "ref": NEW_REPO_REF,
        },
        "gameCount": len(legacy_games),
        "romCount": sum(g["romCount"] for g in legacy_games),
        "warningCount": 0,
        "warnings": [],
        "games": legacy_games,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RetroGame v2 catalog from FC_ROMS v1 data.")
    parser.add_argument("--source-root", required=True, help="FC_ROMS 本地仓库路径。")
    parser.add_argument("--repo-root", required=True, help="RetroGame 仓库根路径。")
    parser.add_argument(
        "--remote-only",
        action="store_true",
        help="ROM 外置模式：不复制 ROM 到仓库，files[].url 指向对象存储公开地址。",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    repo_root = Path(args.repo_root).resolve()
    if not (source_root / "manifest.v1.json").is_file():
        print(f"错误：{source_root}/manifest.v1.json 不存在", file=sys.stderr)
        return 1

    storage: dict[str, str] | None = None
    if args.remote_only:
        s3_config_path = repo_root / "tools" / "s3-config.json"
        if not s3_config_path.is_file():
            print(f"错误：远程模式需要 {s3_config_path}", file=sys.stderr)
            return 1
        s3_config = load_json(s3_config_path)
        storage = {
            "publicDomain": s3_config.get("publicDomain", ""),
            "objectPrefix": s3_config.get("objectPrefix", "games"),
        }
        if not storage["publicDomain"]:
            print("错误：s3-config.json 缺少 publicDomain", file=sys.stderr)
            return 1

    manifest = load_json(source_root / "manifest.v1.json")
    zh_meta_path = source_root / "zh-metadata.v1.json"
    zh_meta = load_json(zh_meta_path).get("games", {}) if zh_meta_path.is_file() else {}
    cover_source_path = source_root / "cover-source.v1.json"
    cover_source = load_json(cover_source_path) if cover_source_path.is_file() else {"entries": []}
    now = utc_now()

    game_jsons: list[dict[str, Any]] = []
    print(f"转换 {manifest['gameCount']} 个游戏...")
    for index, game in enumerate(manifest["games"], start=1):
        try:
            game_json = build_game_json(
                game, zh_meta, cover_source, source_root, repo_root, now,
                storage=storage, copy_roms=not args.remote_only,
            )
        except Exception as error:
            print(f"  [{index}] 跳过 {game.get('id')}: {error}", file=sys.stderr)
            continue
        write_json(repo_root / f"games/fc/{game['slug']}/game.json", game_json)
        game_jsons.append(game_json)
        if index % 200 == 0:
            print(f"  已处理 {index}/{manifest['gameCount']}")

    if not game_jsons:
        print("错误：没有生成任何游戏", file=sys.stderr)
        return 1

    list_items = [build_list_item(g) for g in game_jsons]
    list_items.sort(key=lambda item: item["slug"])

    index_data = {
        "schemaVersion": 2,
        "catalogId": CATALOG_ID,
        "catalogName": CATALOG_NAME,
        "generatedAt": now,
        "defaultCategoryId": DEFAULT_CATEGORY_ID,
        "categories": [
            {
                "id": "all",
                "displayName": "全部",
                "runtimeFamily": "mixed",
                "listUrl": "all/manifest.list.v2.json",
                "gameCount": len(list_items),
                "updatedAt": now,
            },
            {
                "id": "fc",
                "displayName": "FC",
                "runtimeFamily": "libretro",
                "platformIds": ["nes"],
                "listUrl": "fc/manifest.list.v2.json",
                "gameCount": len(list_items),
                "updatedAt": now,
            },
        ],
        "searchIndexUrl": "search-index.v2.json",
    }
    fc_list = {
        "schemaVersion": 2,
        "categoryId": "fc",
        "categoryName": "FC",
        "generatedAt": now,
        "games": list_items,
    }
    all_list = {
        "schemaVersion": 2,
        "categoryId": "all",
        "categoryName": "全部",
        "generatedAt": now,
        "games": list_items,
    }
    search_index = {
        "schemaVersion": 2,
        "generatedAt": now,
        "gameCount": len(list_items),
        "entries": [
            {
                "id": item["id"],
                "slug": item["slug"],
                "categoryId": item["categoryId"],
                "title": item["title"],
                "primaryPlatformId": item["primaryPlatformId"],
                "detailUrl": item["detailUrl"],
                "releaseYear": item.get("releaseYear", 0),
            }
            for item in list_items
        ],
    }

    write_json(repo_root / "catalog/index.v2.json", index_data)
    write_json(repo_root / "catalog/fc/manifest.list.v2.json", fc_list)
    write_json(repo_root / "catalog/all/manifest.list.v2.json", all_list)
    write_json(repo_root / "catalog/search-index.v2.json", search_index)
    write_json(repo_root / "legacy/manifest.v1.json", build_legacy_manifest(game_jsons, now, storage))

    rom_count = sum(len(g["files"]) for g in game_jsons)
    print(f"完成：{len(game_jsons)} 个游戏，{rom_count} 个 ROM")
    print(f"  catalog/index.v2.json")
    print(f"  catalog/fc/manifest.list.v2.json（{len(fc_list['games'])} 条）")
    print(f"  catalog/all/manifest.list.v2.json（{len(all_list['games'])} 条）")
    print(f"  catalog/search-index.v2.json（{len(search_index['entries'])} 条）")
    print(f"  legacy/manifest.v1.json（兼容投影）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
