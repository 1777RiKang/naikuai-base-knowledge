"""Count confirmed new entries, crediting their original merged PR author."""
import argparse
import html
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from common import ROOT, UniqueKeyLoader, load_terms
from privacy import contains_secret, LONG_NUMBER


def public_name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 32
            or re.search(r"[\x00-\x1f\x7f]", value)
            or contains_secret(value) or LONG_NUMBER.search(value)):
        raise ValueError("贡献名必须是 1–32 字的自选公开名称，不能含控制字符、凭据或疑似 QQ 号")
    return value.strip()


def load_profiles(path):
    profiles = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profiles, dict):
        raise ValueError("贡献名配置必须是 GitHub 数字 ID 到贡献名的映射")
    for user_id, name in profiles.items():
        if not re.fullmatch(r"[1-9][0-9]{0,19}", user_id):
            raise ValueError("贡献名配置的键必须是 GitHub 数字用户 ID")
        public_name(name)
    return profiles


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, encoding="utf-8").stdout.strip()


def github_api(repository, endpoint):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("无效 GitHub 仓库路径")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "naikuai-contribution-board"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(f"https://api.github.com/repos/{repository}/{endpoint}", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


class GitHubOrigins:
    def __init__(self, repository):
        self.repository = repository
        self.cache = {}

    def __call__(self, path, doc):
        rel = path.relative_to(ROOT).as_posix()
        commits = git("log", "--reverse", "--diff-filter=A", "--format=%H", "--", rel).splitlines()
        if not commits:
            raise ValueError(f"{rel}: 找不到首次引入记录，排行榜需要完整 Git 历史")
        commit = commits[0]
        if commit not in self.cache:
            pulls = github_api(self.repository, f"commits/{commit}/pulls?per_page=100")
            merged = [p for p in pulls if p.get("merged_at") and p["base"]["ref"] == "main"
                      and p["base"]["repo"]["full_name"].lower() == self.repository.lower()]
            if not merged:
                raise ValueError(f"{rel}: 新词条没有已合并到 main 的来源 PR，请由维护者检查")
            self.cache[commit] = min(merged, key=lambda p: (p["merged_at"], p["number"]))
        pull = self.cache[commit]
        original = yaml.load(git("show", f"{commit}:{rel}"), Loader=UniqueKeyLoader)
        annotators = [c for c in original["contributors"] if c["role"] == "annotator"]
        if not annotators:
            raise ValueError(f"{rel}: 新词条须填写 annotator 的自选公开贡献名")
        return {"github_user_id": pull["user"]["id"], "display_name": public_name(annotators[0]["name"]),
                "merged_at": pull["merged_at"], "pull_request": pull["number"]}


def calculate(docs, seed_ids, profiles, resolve):
    people = {}
    seen = set()
    for path, doc in docs:
        entry_id = doc["id"]
        if entry_id in seen:
            raise ValueError("排行榜输入含重复词条 ID")
        seen.add(entry_id)
        if entry_id in seed_ids or doc["review"]["status"] != "confirmed":
            continue
        origin = resolve(path, doc)
        user_id = origin["github_user_id"]
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ValueError("来源 PR 缺少有效 GitHub 用户 ID")
        person = people.setdefault(user_id, {"github_user_id": user_id, "points": 0, "entry_ids": [],
                                             "display_name": "", "latest": ""})
        person["points"] += 1
        person["entry_ids"].append(entry_id)
        if origin["merged_at"] >= person["latest"]:
            person["display_name"] = public_name(profiles.get(str(user_id), origin["display_name"]))
            person["latest"] = origin["merged_at"]
    rows = sorted(people.values(), key=lambda p: (-p["points"], p["github_user_id"]))
    previous = rank = None
    for position, person in enumerate(rows, 1):
        if person["points"] != previous:
            rank = position
        previous = person["points"]
        person["rank"] = rank
        person["entry_ids"].sort()
        person.pop("latest")
    return {"schema_version": 1, "scoring": "one-confirmed-entry-one-point",
            "seed_entries_excluded": len(seed_ids), "contributors": rows,
            "total_points": sum(p["points"] for p in rows)}


def markdown(board):
    lines = ["# 贡献排行榜", "", "每个非种子且当前 confirmed 的独立词条计 1 分；同分并列。", "",
             f"来源提交：`{board['source_commit']}`", "", "| 排名 | 公开贡献名 | GitHub 数字 ID | 知识点 |",
             "| --- | --- | --- | --- |"]
    for row in board["contributors"]:
        name = html.escape(row["display_name"]).replace("|", "&#124;").replace("`", "&#96;")
        name = re.sub(r"([\\\[\]*_~!])", r"\\\1", name)
        lines.append(f"| {row['rank']} | {name} | {row['github_user_id']} | {row['points']} |")
    if not board["contributors"]:
        lines.append("| — | 等待第一份审核通过的新知识贡献 | — | 0 |")
    return "\n".join(lines) + "\n"


def svg(board):
    rows = board["contributors"][:15]
    height = 170 + max(len(rows), 1) * 48
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="920" height="{height}" viewBox="0 0 920 {height}" role="img" aria-labelledby="title desc">',
             '<title id="title">奶块知识库 · 贡献排行榜</title>',
             '<desc id="desc">每个审核确认的新词条计一分，现有五十条种子数据不计个人积分。</desc>',
             f'<rect width="920" height="{height}" rx="16" fill="#f7faf8"/>',
             '<g font-family="Noto Sans CJK SC,Microsoft YaHei,sans-serif" fill="#173c30">',
             '<text x="30" y="42" font-size="24" font-weight="bold">奶块知识库 · 贡献排行榜</text>',
             '<text x="30" y="72" font-size="14">审核确认的新词条 · 每条 1 分 · 合并后自动更新 · 展示前 15 名</text>',
             '<text x="30" y="112" font-size="14">排名</text><text x="110" y="112" font-size="14">公开贡献名</text>',
             '<text x="650" y="112" font-size="14">GitHub ID</text><text x="810" y="112" font-size="14">知识点</text>']
    if not rows:
        parts.append('<text x="30" y="158" font-size="17">等待第一份审核通过的新知识贡献；50 条种子数据不计个人积分。</text>')
    for i, row in enumerate(rows):
        y = 158 + i * 48
        name = row["display_name"]
        if len(name) > 23:
            name = name[:22] + "…"
        parts.extend([f'<path d="M30 {y+17} H890" stroke="#dce8e1"/>',
                      f'<text x="38" y="{y}" font-size="18">{row["rank"]}</text>',
                      f'<text x="110" y="{y}" font-size="17">{html.escape(name)}</text>',
                      f'<text x="650" y="{y}" font-size="14">{row["github_user_id"]}</text>',
                      f'<text x="825" y="{y}" font-size="18" font-weight="bold">{row["points"]}</text>'])
    parts.append(f'<text x="30" y="{height-18}" font-size="12">更新于 {html.escape(board["built_at"])} · 完整榜单见下方链接</text></g></svg>')
    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "1777RiKang/naikuai-base-knowledge"))
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    docs = load_terms()
    from validate import collect_errors
    errors = collect_errors(docs)
    if errors:
        raise ValueError("排行榜生成前词条校验失败")
    seeds = json.loads((ROOT / "credits/m0-seed-ids.json").read_text(encoding="utf-8"))
    if not isinstance(seeds, list) or len(set(seeds)) != len(seeds):
        raise ValueError("种子词条列表格式错误")
    profiles = load_profiles(ROOT / "credits/display-names.json")
    board = calculate(docs, set(seeds), profiles, GitHubOrigins(args.repository))
    board["source_commit"] = git("rev-parse", "HEAD")
    board["built_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "leaderboard.json").write_text(json.dumps(board, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "leaderboard.md").write_text(markdown(board), encoding="utf-8")
    (args.out / "leaderboard.svg").write_text(svg(board), encoding="utf-8")
    print(f"贡献榜生成：{len(board['contributors'])} 位贡献者，{board['total_points']} 个知识点")


if __name__ == "__main__":
    main()
