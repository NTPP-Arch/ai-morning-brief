#!/usr/bin/env python3
"""在 github.com:443 被阻断时，用 api.github.com 推送脚本改动。

只更新本地 git 里已提交的 scripts/ 与 .github/workflows/ 文件，
其余远端文件（Actions 产出的 docs/、briefs/）通过 base_tree 原样保留。
"""
import base64
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "NTPP-Arch/ai-morning-brief"
ROOT = Path(__file__).resolve().parents[1]
MESSAGE = sys.argv[1] if len(sys.argv) > 1 else "chore: sync scripts via API"
PREFIXES = ("scripts/", ".github/workflows/")


def token():
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                          check=True).stdout.strip()


TOK = token()


def api(method, path, payload=None):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + TOK, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "brief-push"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        print("API %s %s -> %s %s" % (method, path, e.code, e.read().decode()[:400]))
        raise


def head():
    return api("GET", "/repos/%s/git/ref/heads/main" % REPO)["object"]["sha"]


def files():
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\n") if f.strip() and f.startswith(PREFIXES)]


def main():
    remote = head()
    base_tree = api("GET", "/repos/%s/git/commits/%s" % (REPO, remote))["tree"]["sha"]
    names = files()
    tree = []
    for name in names:
        # 用 git 里规范化后的内容（LF），与本地 index 完全一致
        sha = subprocess.run(["git", "rev-parse", "HEAD:" + name], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        blob = subprocess.run(["git", "cat-file", "-p", sha], cwd=ROOT,
                              capture_output=True, check=True).stdout
        api("POST", "/repos/%s/git/blobs" % REPO,
            {"content": base64.b64encode(blob).decode(), "encoding": "base64"})
        tree.append({"path": name, "mode": "100644", "type": "blob", "sha": sha})
    new_tree = api("POST", "/repos/%s/git/trees" % REPO,
                   {"base_tree": base_tree, "tree": tree})["sha"]
    sig = {"name": "brief-bot", "email": "brief-bot@users.noreply.github.com"}
    commit = api("POST", "/repos/%s/git/commits" % REPO,
                 {"message": MESSAGE, "tree": new_tree, "parents": [remote],
                  "author": sig, "committer": sig})["sha"]
    api("PATCH", "/repos/%s/git/refs/heads/main" % REPO, {"sha": commit, "force": False})
    print("已推送 %d 个文件；%s -> %s" % (len(tree), remote[:7], commit[:7]))
    (ROOT / "notes" / "cloud-sync.md").write_text(
        "远端 main: %s（本地 git 落后，push 走 api.github.com）\n" % commit, encoding="utf-8")


if __name__ == "__main__":
    main()
