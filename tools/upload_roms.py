#!/usr/bin/env python3
"""把 ROM 上传到 R2 对象存储（默认 Cloudflare R2）。

配置读取优先级：--config 参数 > 仓库根 tools/s3-config.json。
凭据从环境变量读取（见配置 auth 字段）：
  RETROGAME_R2_API_TOKEN   Cloudflare API token（仅管理 API 可用，无法用于 S3 签名）
  RETROGAME_R2_ACCESS_KEY  R2 S3 Access Key ID（SigV4）
  RETROGAME_R2_SECRET_KEY  R2 S3 Secret Access Key（SigV4）

用法：
  py tools/upload_roms.py --dry-run          # 仅列出待上传文件
  py tools/upload_roms.py --category fc      # 上传 FC 分类所有 ROM
  py tools/upload_roms.py --category fc --slug 0001   # 上传单个游戏
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROM_EXTENSIONS = {".nes", ".fds"}


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        sys.exit(f"配置文件不存在：{config_path}")
    return json.loads(config_path.read_bytes().decode("utf-8"))


def read_credentials(config: dict[str, Any]) -> dict[str, str]:
    auth = config.get("auth", {})
    api_token = os.environ.get(auth.get("apiTokenEnv", ""), "")
    access_key = os.environ.get(auth.get("accessKeyEnv", ""), "")
    secret_key = os.environ.get(auth.get("secretKeyEnv", ""), "")
    if access_key and secret_key:
        return {"access_key": access_key, "secret_key": secret_key}
    if api_token:
        if api_token.startswith("cfut_"):
            sys.exit(
                "检测到 RETROGAME_R2_API_TOKEN 是 Cloudflare 管理 API token（cfut_ 前缀），"
                "无法用于 R2 S3 SigV4 签名。请设置 RETROGAME_R2_ACCESS_KEY / RETROGAME_R2_SECRET_KEY"
                "（在 Cloudflare 控制台 R2 -> 管理 R2 API 令牌 创建 S3 兼容凭据）。"
            )
        return {"access_key": api_token, "secret_key": api_token}
    sys.exit("缺少凭据：请设置 RETROGAME_R2_ACCESS_KEY / RETROGAME_R2_SECRET_KEY（或 RETROGAME_R2_API_TOKEN）。")


def sign_request(
    method: str,
    host: str,
    path: str,
    query: str,
    payload: bytes,
    access_key: str,
    secret_key: str,
    region: str,
) -> tuple[str, str, str]:
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical_uri = path if path else "/"
    canonical_query = query
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_headers = "".join(
        f"{key}:{value}\n" for key, value in sorted(headers.items())
    )
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
    )

    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )

    def sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = sign(("AWS4" + secret_key).encode(), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, "s3")
    k_signing = sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return auth_header, amz_date, payload_hash


def s3_request(
    method: str,
    url: str,
    payload: bytes,
    creds: dict[str, str],
    region: str,
    extra_headers: dict[str, str] | None = None,
    proxy: str | None = None,
) -> tuple[int, bytes]:
    import requests

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    params = sorted(
        urllib.parse.parse_qsl(parsed.query, keep_blank_values=True),
        key=lambda item: item[0],
    )
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in params
    )
    if canonical_query != parsed.query:
        url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, canonical_query, parsed.fragment)
        )
        parsed = urllib.parse.urlparse(url)
    auth_header, amz_date, payload_hash = sign_request(
        method, host, path, canonical_query, payload, creds["access_key"], creds["secret_key"], region
    )
    headers = {
        "Authorization": auth_header,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Host": host,
    }
    for key, value in (extra_headers or {}).items():
        headers[key] = value
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        response = requests.request(
            method,
            url,
            data=payload,
            headers=headers,
            proxies=proxies,
            timeout=(30, 240),
        )
        return response.status_code, response.content
    except requests.RequestException as error:
        raise RuntimeError(f"请求失败：{error}") from error


def collect_rom_files(repo_root: Path, category: str, slug_filter: str | None) -> list[tuple[str, Path]]:
    base = repo_root / "games" / category
    if not base.is_dir():
        sys.exit(f"分类目录不存在：{base}")
    items: list[tuple[str, Path]] = []
    for game_dir in sorted(base.iterdir()):
        if not game_dir.is_dir():
            continue
        if slug_filter and game_dir.name != slug_filter:
            continue
        roms = game_dir / "roms"
        if not roms.is_dir():
            continue
        for rom in sorted(roms.iterdir()):
            if rom.is_file() and rom.suffix.lower() in ROM_EXTENSIONS:
                items.append((f"{game_dir.name}/roms/{rom.name}", rom))
    return items


def upload_one(
    url: str,
    payload: bytes,
    creds: dict[str, str],
    region: str,
    proxy: str | None,
    max_retries: int,
) -> tuple[int, bytes]:
    status, body = 0, b""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            status, body = s3_request(
                "PUT", url, payload, creds, region,
                {"Content-Type": "application/octet-stream"}, proxy=proxy,
            )
            last_error = None
            if status in (200, 201):
                break
        except Exception as error:
            last_error = error
            status = 0
        if status not in (200, 201) and attempt < max_retries:
            time.sleep(2 * attempt)
    if status not in (200, 201) and last_error and not body:
        body = str(last_error).encode("utf-8", "replace")
    return status, body


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload ROMs to R2.")
    parser.add_argument("--repo-root", default=".", help="RetroGame 仓库根路径。")
    parser.add_argument("--config", default=None, help="S3 配置文件路径。")
    parser.add_argument("--category", default="fc", help="分类 ID（默认 fc）。")
    parser.add_argument("--slug", default=None, help="只上传指定 slug 的游戏。")
    parser.add_argument("--dry-run", action="store_true", help="只列出待上传文件，不执行上传。")
    parser.add_argument("--skip-existing", action="store_true", help="已存在且大小匹配的对象跳过（断点续传）。")
    parser.add_argument("--limit", type=int, default=0, help="最多上传 N 个待上传文件（0 表示不限制）。")
    parser.add_argument("--workers", type=int, default=1, help="并发上传线程数（默认 1）。")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    config_path = Path(args.config) if args.config else repo_root / "tools" / "s3-config.json"
    config = load_config(config_path)
    creds = read_credentials(config)

    endpoint = config["endpoint"].rstrip("/")
    bucket = config["bucket"]
    region = config.get("region", "auto")
    prefix = config.get("objectPrefix", "games")
    public_domain = config.get("publicDomain", "").rstrip("/")

    items = collect_rom_files(repo_root, args.category, args.slug)
    print(f"待上传 {len(items)} 个 ROM（分类 {args.category}，桶 {bucket}，目标 {endpoint}）")

    if args.dry_run:
        for key, _ in items:
            object_key = f"{prefix}/{args.category}/{key}"
            print(f"  {object_key}")
        return 0

    total_bytes = 0
    ok = 0
    skipped = 0
    uploaded_count = 0
    proxy = config.get("proxy") or os.environ.get("RETROGAME_R2_PROXY") or None
    max_retries = int(os.environ.get("RETROGAME_UPLOAD_RETRIES", "8"))

    pending: list[tuple[str, bytes]] = []
    for key, rom_path in items:
        object_key = f"{prefix}/{args.category}/{key}"
        encoded_key = urllib.parse.quote(object_key, safe="/-_.~")
        url = f"{endpoint}/{bucket}/{encoded_key}"
        payload = rom_path.read_bytes()
        if args.skip_existing:
            exists = False
            for _ in range(3):
                try:
                    head_status, _ = s3_request("HEAD", url, b"", creds, region, proxy=proxy)
                    exists = head_status == 200
                    break
                except Exception:
                    time.sleep(2)
            if exists:
                skipped += 1
                continue
        if args.limit and len(pending) >= args.limit:
            break
        pending.append((url, payload))

    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = max(1, args.workers)
    total_bytes = sum(len(payload) for _, payload in pending)
    if workers == 1:
        for url, payload in pending:
            status, body = upload_one(url, payload, creds, region, proxy, max_retries)
            if status in (200, 201):
                ok += 1
            else:
                print(f"  失败 {url.split('/')[-1]}: HTTP {status} {body[:200]}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(upload_one, url, payload, creds, region, proxy, max_retries): url
                for url, payload in pending
            }
            for future in as_completed(futures):
                url = futures[future]
                status, body = future.result()
                if status in (200, 201):
                    ok += 1
                else:
                    print(f"  失败 {url.split('/')[-1]}: HTTP {status} {body[:200]}")
    print(f"完成：{ok} 上传，{skipped} 跳过，{len(items)} 总数，{total_bytes / 1024 / 1024:.1f} MiB")
    if public_domain:
        print(f"公开访问域名：{public_domain}（示例：{public_domain}/{prefix}/{args.category}/{items[0][0] if items else ''}）")
    return 0 if ok + skipped == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
