#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Daily News — collect AI news from sources worldwide and generate a
bilingual (中文 / English) Markdown report every day.

Usage:
    python main.py            # normal run (fetch + LLM summarize/translate)
    python main.py --offline  # no LLM: use raw titles/descriptions
    python main.py --dry-run  # fetch only, print stats, no report
    python main.py --hours 48 --max 30   # override window / cap
"""

import argparse
import calendar
import html
import json
import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

BASE_DIR = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------------------
# Categories: (key, 中文, English, emoji)
# ---------------------------------------------------------------------------
CATEGORIES = [
    ("product",  "产品发布", "Product & Launches",      "🚀"),
    ("research", "研究与突破", "Research & Breakthroughs", "🔬"),
    ("funding",  "融资与商业", "Funding & Business",      "💰"),
    ("policy",   "政策与监管", "Policy & Regulation",     "🏛️"),
    ("industry", "行业动态",   "Industry News",           "📈"),
    ("other",    "其他",       "Other",                   "📌"),
]
CATEGORY_KEYS = [c[0] for c in CATEGORIES]
CATEGORY_MAP = {c[0]: c for c in CATEGORIES}

# Keywords used to filter general-tech feeds (AI-related stories only).
KEYWORDS = [
    "ai", "artificial intelligence", "gpt", "llm", "large language model",
    "openai", "anthropic", "deepmind", "gemini", "claude", "copilot",
    "machine learning", "deep learning", "neural network", "transformer",
    "diffusion model", "agent", "chatbot", "hallucination", "prompt",
    "multimodal", "open-source model", "open source model", "robot",
    "self-driving", "autonomous", "芯片", "算力", "人工智能", "大模型",
    "机器学习", "深度学习", "神经网络", "生成式", "智能体", "机器人",
    "自动驾驶", "多模态", "算法", "AI",
]
# Suffixes frequently appended to syndicated titles, stripped before dedup.
TITLE_SUFFIXES = re.compile(
    r"\s*[-–—|:]\s*(The Verge|TechCrunch|VentureBeat|MIT Technology Review|"
    r"The Guardian|BBC News|CNBC|MarkTechPost|Ars Technica|InfoQ|量子位|"
    r"雷锋网|爱范儿|36氪|机器之心)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool) -> logging.Logger:
    # Windows consoles default to GBK/CP1252 and crash on Unicode; force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"ai-news-{datetime.now():%Y-%m-%d}.log"
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("ai-news")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "llm": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key": "",
        "temperature": 0.3,
        "max_tokens": 600,
        "timeout_seconds": 120,
        "concurrency": 4,
    },
    "news": {
        "hours_back": 24,
        "max_articles": 20,
        "min_importance": 2,
        "request_timeout": 30,
    },
    "network": {
        "proxy": "",
        "proxy_on_error": True,
    },
    "report": {
        "reports_dir": "reports",
        "top_cap": 5,
        "save_json": True,
    },
    "git": {
        "enabled": False,
        "remote": "origin",
        "commit_prefix": "ai-news: daily report ",
        "auto_add_remote_url": "",
    },
    "feeds": [],
}


def proxies_for(config: dict) -> dict | None:
    """Return requests proxies dict for the configured proxy, or None."""
    proxy = (config.get("network") or {}).get("proxy", "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def load_config(path: str) -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # merge defaults so missing keys don't crash the script
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for section in ("llm", "news", "network", "report", "git"):
        if isinstance(cfg.get(section), dict):
            merged[section].update(cfg[section])
    if isinstance(cfg.get("feeds"), list) and cfg["feeds"]:
        merged["feeds"] = cfg["feeds"]
    return merged


def resolve_api_key(cfg: dict) -> str:
    llm = cfg["llm"]
    env_name = llm.get("api_key_env") or ""
    key = os.environ.get(env_name, "") if env_name else ""
    if not key:
        key = (llm.get("api_key") or "").strip()
    return key


# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------
def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _entry_date(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (ValueError, TypeError, OverflowError):
                continue
    return None


def fetch_feed(feed_cfg: dict, request_timeout: int, proxies: dict | None = None,
               proxy_on_error: bool = True) -> list[dict]:
    """Fetch one feed, return a list of raw item dicts. Never raises.

    Tries direct access first; if that fails and a proxy is configured,
    retries once through the proxy (proxy_on_error).
    """
    name = feed_cfg.get("name", "?")
    url = feed_cfg.get("url", "")
    lang = feed_cfg.get("lang", "en")
    verify = feed_cfg.get("verify_ssl", True)
    log = logging.getLogger("ai-news")
    items: list[dict] = []

    def _do_fetch(use_proxy: bool):
        kwargs = {
            "headers": {"User-Agent": UA,
                        "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            "timeout": request_timeout,
            "verify": verify,
        }
        if use_proxy:
            kwargs["proxies"] = proxies
        resp = requests.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    try:
        try:
            resp = _do_fetch(use_proxy=False)
        except Exception as direct_exc:  # noqa: BLE001
            if proxies and proxy_on_error:
                log.warning("feed RETRY %-20s via proxy %s after direct fail: %s: %s",
                            name, proxies.get("https"), type(direct_exc).__name__,
                            str(direct_exc)[:120])
                resp = _do_fetch(use_proxy=True)
            else:
                raise
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            raise ValueError(f"bozo parse error: {feed.bozo_exception}")
        for entry in feed.entries[:80]:
            title = _strip_html(entry.get("title", "")).strip()
            if not title:
                continue
            link = entry.get("link", "").strip()
            summary = _strip_html(
                entry.get("summary")
                or entry.get("description")
                or (entry.get("content") or [{}])[0].get("value", "")
            )
            published = _entry_date(entry)
            items.append({
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
                "published_str": published.strftime("%Y-%m-%d %H:%M") if published else "unknown",
                "source": name,
                "lang": lang,
                "title_norm": normalize_title(title),
            })
        log.info("feed OK  %-22s %d items", name, len(items))
    except Exception as exc:  # noqa: BLE001 - one bad feed must not kill the run
        log.warning("feed ERR %-22s %s: %s", name, type(exc).__name__, exc)
    return items


def fetch_all(config: dict) -> list[dict]:
    feeds = config["feeds"]
    timeout = config["news"]["request_timeout"]
    proxies = proxies_for(config)
    proxy_on_error = bool((config.get("network") or {}).get("proxy_on_error", True))
    all_items: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(10, len(feeds) or 1)) as pool:
        futures = [pool.submit(fetch_feed, f, timeout, proxies, proxy_on_error)
                   for f in feeds]
        for fut in as_completed(futures):
            all_items.extend(fut.result())
    return all_items


# ---------------------------------------------------------------------------
# Filtering / dedup
# ---------------------------------------------------------------------------
def normalize_title(title: str) -> str:
    t = TITLE_SUFFIXES.sub("", title).lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    return t.strip()


def _matches_keywords(text: str) -> bool:
    lower = text.lower()
    for kw in KEYWORDS:
        if kw.isascii() and re.fullmatch(r"\w+", kw):
            if re.search(rf"\b{re.escape(kw)}\b", lower):
                return True
        else:
            if kw in lower:
                return True
    return False


def filter_and_dedupe(items: list[dict], config: dict) -> list[dict]:
    hours = config["news"]["hours_back"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen: dict[str, dict] = {}
    kept: list[dict] = []
    for it in items:
        feed_cfg = next((f for f in config["feeds"] if f.get("name") == it["source"]), {})
        if feed_cfg.get("keywords"):
            hay = f"{it['title']} {it['summary']}"
            if not _matches_keywords(hay):
                continue
        pub = it["published"]
        if pub is not None and pub < cutoff:
            continue  # older than the window
        norm = it["title_norm"]
        if not norm or norm in seen:
            continue
        seen[norm] = it
        kept.append(it)
    return kept


# ---------------------------------------------------------------------------
# LLM summarization / translation
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a professional AI news editor producing a bilingual daily digest. "
    "For each news item, respond with ONLY a single JSON object (no markdown "
    "fences, no extra text) containing exactly these keys:\n"
    '{"title_en": <English title>, "title_zh": <Chinese title>, '
    '"summary_en": <English summary, max 45 words>, '
    '"summary_zh": <Chinese summary, max 80 characters>, '
    '"category": <one of "product" | "research" | "funding" | "policy" | "industry" | "other">, '
    '"importance": <integer 1-5, 5 = most important>}'
)


def call_llm(item: dict, config: dict) -> dict | None:
    llm = config["llm"]
    url = llm["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {resolve_api_key(config)}",
        "Content-Type": "application/json",
    }
    user_text = (
        f"Title: {item['title']}\n"
        f"Source: {item['source']}\n"
        f"Date: {item['published_str']}\n"
        f"Description: {item['summary'][:1200] or '(none)'}\n\n"
        'Output the JSON object now.'
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    payload = {
        "model": llm["model"],
        "messages": messages,
        "temperature": llm.get("temperature", 0.3),
        "max_tokens": llm.get("max_tokens", 600),
    }
    proxies = proxies_for(config)
    for attempt in range(2):
        body = dict(payload)
        if attempt == 0:
            body["response_format"] = {"type": "json_object"}
        # try direct, then via proxy on the next attempt if configured
        use_proxy = bool(proxies and attempt >= 1)
        try:
            resp = requests.post(url, headers=headers, json=body,
                                 timeout=llm.get("timeout_seconds", 120),
                                 proxies=proxies if use_proxy else None)
            if resp.status_code != 200:
                err = resp.text[:300]
                if attempt == 0 and ("response_format" in err or "json" in err.lower()):
                    continue  # provider doesn't support json mode -> retry without
                raise RuntimeError(f"HTTP {resp.status_code}: {err}")
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = parse_llm_json(content)
            if parsed:
                return parsed
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("ai-news").warning(
                "LLM attempt %d (%s) failed for %r: %s",
                attempt + 1, "proxy" if use_proxy else "direct",
                item["title"][:40], exc)
            time.sleep(1.5 * (attempt + 1))
    return None


def parse_llm_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    cat = data.get("category", "other")
    if cat not in CATEGORY_MAP:
        cat = "other"
    try:
        importance = int(data.get("importance", 3))
    except (TypeError, ValueError):
        importance = 3
    importance = max(1, min(5, importance))
    return {
        "title_en": str(data.get("title_en") or "").strip(),
        "title_zh": str(data.get("title_zh") or "").strip(),
        "summary_en": str(data.get("summary_en") or "").strip(),
        "summary_zh": str(data.get("summary_zh") or "").strip(),
        "category": cat,
        "importance": importance,
    }


def offline_item(item: dict) -> dict:
    """Fallback when no API key / offline mode: raw titles, no translation."""
    summary = item["summary"][:400]
    # Some feeds (e.g. InfoQ) only expose a "read more" stub as description;
    # fall back to the title so the report stays informative.
    if len(summary) < 15 or "点击查看原文" in summary or "read more" in summary.lower():
        summary = item["title"]
    return {
        "title_en": item["title"],
        "title_zh": item["title"] if item["lang"] == "zh" else "",
        "summary_en": summary,
        "summary_zh": summary if item["lang"] == "zh" else "",
        "category": "other",
        "importance": 3,
    }


def summarize_items(items: list[dict], config: dict, offline: bool) -> list[dict]:
    if offline or not resolve_api_key(config):
        mode = "offline" if offline else "no-api-key"
        logging.getLogger("ai-news").warning(
            "LLM disabled (%s): using raw titles/descriptions (no translation). "
            "Set the API key (env %s or config llm.api_key) for bilingual summaries.",
            mode, config["llm"].get("api_key_env", "?"))
        for it in items:
            it.update(offline_item(it))
        return items

    llm_cfg = config["llm"]
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=llm_cfg.get("concurrency", 4)) as pool:
        futures = {pool.submit(call_llm, it, config): it for it in items}
        for fut in as_completed(futures):
            it = futures[fut]
            parsed = fut.result()
            if parsed:
                it.update(parsed)
            else:
                it.update(offline_item(it))
                it["importance"] = 2  # demote items we could not summarize
            results.append(it)
    results.sort(key=lambda x: x["importance"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def build_report(items: list[dict], meta: dict) -> str:
    total = len(items)
    sources = len({it["source"] for it in items})
    lines = [
        "# 🤖 全球 AI 每日新闻 | AI Daily News (Worldwide)",
        "",
        f"> **📅 日期 Date:** {meta['date']}",
        f"> **🌍 收录 Coverage:** {total} 条新闻 / {sources} 个来源 (过去 {meta['hours']} 小时)",
        f"> **⚙️ 生成 Generated:** {meta['generated_at']} · 模式: {meta['mode']}",
        "",
        "---",
        "",
    ]

    top = [it for it in items if it.get("importance", 3) >= 4][: meta.get("top_cap", 5)]
    if top:
        lines.append("## 🏆 今日头条 Top Stories")
        lines.append("")
        for idx, it in enumerate(top, 1):
            lines += _item_block(it, heading=f"### {idx}. ")
        lines.append("---")
        lines.append("")

    lines.append("## 📂 分类浏览 By Category")
    lines.append("")
    top_ids = {id(it) for it in top}
    for key, zh, en, emoji in CATEGORIES:
        group = [it for it in items if it.get("category") == key and id(it) not in top_ids]
        if not group:
            continue
        group.sort(key=lambda x: x.get("importance", 3), reverse=True)
        lines.append(f"### {emoji} {zh} · {en}")
        lines.append("")
        for it in group:
            lines += _item_block(it, heading="#### ")
    return "\n".join(lines).rstrip() + "\n"


def _item_block(it: dict, heading: str = "") -> list[str]:
    stars = "⭐" * it.get("importance", 3)
    cat = CATEGORY_MAP.get(it.get("category", "other"))
    cat_label = f"{cat[3]} {cat[1]} · {cat[2]}" if cat else ""
    title_zh = it.get("title_zh") or "—"
    summary_en = it.get("summary_en") or "—"
    summary_zh = it.get("summary_zh") or "—"
    return [
        f"{heading}{it['title'] if not it.get('title_en') else it['title_en']} | {title_zh}",
        f"> {stars} · **来源 Source:** {it['source']} · **日期 Date:** {it['published_str']}"
        + (f" · {cat_label}" if cat_label else ""),
        "",
        f"- **English:** {summary_en}",
        f"- **中文:** {summary_zh}",
        f"- 🔗 [阅读原文 / Read more]({it['link']})",
        "",
    ]


def save_report(md: str, items: list[dict], config: dict, date_str: str) -> Path:
    reports_dir = BASE_DIR / config["report"]["reports_dir"]
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / f"ai-news-{date_str}.md"
    md_path.write_text(md, encoding="utf-8")
    if config["report"].get("save_json", True):
        json_path = reports_dir / f"ai-news-{date_str}.json"
        payload = []
        for it in items:
            p = {k: v for k, v in it.items() if k != "published"}
            p["published"] = it["published"].isoformat() if it.get("published") else None
            payload.append(p)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path


# ---------------------------------------------------------------------------
# Git commit + push (best-effort: failures are logged, never crash the task)
# ---------------------------------------------------------------------------
def git_commit_and_push(report_path: Path, config: dict, date_str: str) -> None:
    git_cfg = config.get("git") or {}
    if not git_cfg.get("enabled", False):
        logging.getLogger("ai-news").info("Git auto-commit disabled (git.enabled=false)")
        return
    log = logging.getLogger("ai-news")
    repo_root = BASE_DIR
    proxy = proxies_for(config)

    def run_git(*args: str, timeout: int = 120, use_proxy: bool = False) -> subprocess.CompletedProcess:
        env = None
        if use_proxy and proxy:
            env = os.environ.copy()
            env["HTTP_PROXY"] = proxy["http"]
            env["HTTPS_PROXY"] = proxy["https"]
            env["http_proxy"] = proxy["http"]
            env["https_proxy"] = proxy["https"]
        return subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace", env=env)

    try:
        # 1. Is this a git repo?
        probe = run_git("rev-parse", "--is-inside-work-tree")
        if probe.returncode != 0:
            log.warning("Git: %s is not a git repository — skipping commit/push. "
                        "Run `git init` in %s to enable.", repo_root, repo_root)
            return

        # 2. Auto-add remote if configured and missing.
        remote = git_cfg.get("remote", "origin")
        has_remote = run_git("remote", "get-url", remote).returncode == 0
        auto_url = (git_cfg.get("auto_add_remote_url") or "").strip()
        if not has_remote and auto_url:
            log.info("Git: adding remote %s -> %s", remote, auto_url)
            run_git("remote", "add", remote, auto_url)

        # 3. Stage the report file(s).
        files = [str(report_path)]
        json_path = report_path.with_suffix(".json")
        if json_path.exists():
            files.append(str(json_path))
        add = run_git("add", "--", *files)
        if add.returncode != 0:
            log.warning("Git: `git add` failed: %s", add.stderr.strip())
            return

        # 4. Commit if there is anything new.
        staged = run_git("diff", "--cached", "--quiet")
        if staged.returncode == 0:
            log.info("Git: nothing new to commit (report unchanged).")
        else:
            msg = f"{git_cfg.get('commit_prefix', 'ai-news: daily report ')}{date_str}"
            commit = run_git("commit", "-m", msg)
            if commit.returncode != 0:
                log.warning("Git: commit failed: %s", commit.stderr.strip())
                return
            log.info("Git: committed %s", msg)

        # 5. Push (only if a remote exists).
        if run_git("remote", "get-url", remote).returncode != 0:
            log.warning("Git: remote '%s' not configured — committed locally, "
                        "push skipped. Set it with: git remote add %s <url>", remote, remote)
            return
        branch = run_git("branch", "--show-current").stdout.strip() or "HEAD"
        # Try direct push first; if it fails and a proxy is configured, retry via proxy.
        push = run_git("push", remote, branch, timeout=300)
        if push.returncode != 0 and proxy:
            log.warning("Git: direct push failed (%s) — retrying via proxy %s",
                        push.stderr.strip().splitlines()[-1][:120] if push.stderr.strip() else "?",
                        proxy["https"])
            push = run_git("push", remote, branch, timeout=300, use_proxy=True)
        if push.returncode != 0:
            log.warning("Git: push to %s/%s failed: %s", remote, branch,
                        push.stderr.strip().replace("\n", " "))
            return
        log.info("Git: pushed report to %s/%s", remote, branch)
    except subprocess.TimeoutExpired:
        log.warning("Git: operation timed out — skipped.")
    except Exception as exc:  # noqa: BLE001 - never let git break the daily task
        log.warning("Git: unexpected error during commit/push: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="AI daily news bilingual digest")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"))
    parser.add_argument("--offline", action="store_true",
                        help="skip the LLM; use raw titles/descriptions")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch feeds only, print stats, write no report")
    parser.add_argument("--hours", type=int, default=None, help="override hours_back")
    parser.add_argument("--max", dest="max_articles", type=int, default=None,
                        help="override max_articles")
    parser.add_argument("--no-git", action="store_true",
                        help="skip git commit/push even if enabled in config")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    log = setup_logging(args.verbose)
    log.info("=== AI Daily News run started ===")
    config = load_config(args.config)
    if args.hours:
        config["news"]["hours_back"] = args.hours
    if args.max_articles:
        config["news"]["max_articles"] = args.max_articles
    if not config["feeds"]:
        log.error("No feeds configured in %s", args.config)
        return 1

    t0 = time.time()
    log.info("Fetching %d feeds ...", len(config["feeds"]))
    items = fetch_all(config)
    log.info("Fetched %d raw items", len(items))
    items = filter_and_dedupe(items, config)
    log.info("After recency/keyword/dedup filter: %d items", len(items))
    if not items:
        log.warning("No items in the last %d hours — report will be empty.",
                    config["news"]["hours_back"])

    if args.dry_run:
        log.info("DRY RUN — no report written.")
        for it in sorted(items, key=lambda x: x["published_str"], reverse=True)[:25]:
            log.info("  • [%s] %s — %s", it["source"], it["title"], it["link"])
        log.info("Dry run done in %.1fs", time.time() - t0)
        return 0

    cap = config["news"]["max_articles"]
    if cap and len(items) > cap:
        # keep the most recent when over cap (no LLM info yet)
        items = sorted(items, key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc),
                       reverse=True)[:cap]
        log.info("Capped to %d articles", len(items))

    items = summarize_items(items, config, args.offline)

    min_imp = config["news"].get("min_importance", 2)
    before = len(items)
    items = [it for it in items if it.get("importance", 3) >= min_imp]
    if len(items) != before:
        log.info("Dropped %d items below min_importance=%d", before - len(items), min_imp)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    meta = {
        "date": date_str,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "hours": config["news"]["hours_back"],
        "mode": "LLM 双语摘要 / bilingual LLM" if not (args.offline or not resolve_api_key(config))
                else "离线模式 / offline (no translation)",
        "top_cap": config["report"].get("top_cap", 5),
    }
    md = build_report(items, meta)
    out = save_report(md, items, config, date_str)
    log.info("Report written: %s", out)
    if args.no_git:
        log.info("Git step skipped (--no-git).")
    else:
        git_commit_and_push(out, config, date_str)
    log.info("Run finished in %.1fs — %d articles from %d sources",
             time.time() - t0, len(items), len({it['source'] for it in items}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
