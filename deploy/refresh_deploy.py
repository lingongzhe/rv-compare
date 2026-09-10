# -*- coding: utf-8 -*-
"""自动刷新并部署静态站点：爬虫更新 -> 导出数据 -> git 提交 -> 推送到 GitHub Pages 源。

用法:
    python deploy/refresh_deploy.py [--no-crawl]

Token 读取顺序：环境变量 GH_TOKEN > deploy/.gh_token 文件（已被 .gitignore 忽略，勿提交）。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GIT = None
for cand in (r"C:\Program Files\Git\cmd\git.exe", "git"):
    if os.path.exists(cand):
        GIT = cand
        break
REPO = "lingongzhe/rv-compare"
BRANCH = "main"
LOG = os.path.join(ROOT, "deploy", "refresh.log")


def log(msg):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # 日志被占用时不阻断刷新主流程


def sh(args, **kw):
    log("$ " + " ".join(args))
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def get_token():
    t = os.environ.get("GH_TOKEN")
    if not t:
        fp = os.path.join(HERE, ".gh_token")
        if os.path.exists(fp):
            t = open(fp, encoding="utf-8-sig").read().strip()
    return t


def crawl(py, args):
    r = sh([sys.executable, py] + args)
    if r.stdout:
        log("  " + r.stdout.strip())
    if r.stderr:
        for ln in r.stderr.strip().splitlines():
            log("  | " + ln)
    return r.returncode == 0


def main():
    log("==== 刷新开始 ====")
    token = get_token()
    if not token:
        log("!! 缺少 GH_TOKEN 或 deploy/.gh_token，跳过推送")
        return 1

    if "--no-crawl" not in sys.argv:
        # 各源增量刷新（礼貌限速）
        crawl("crawl_21rv.py", ["--pages", "2"])
        crawl("crawl.py", ["--pages", "2"])
        crawl("crawl_cn2rv.py", [])
    else:
        log("--no-crawl：跳过爬虫，直接导出现有库")

    r = sh([sys.executable, "export_data.py"])
    if r.stdout:
        log("导出: " + r.stdout.strip())

    # 检查 docs 是否有改动，避免空提交
    d = sh([GIT, "status", "--porcelain", "--", "docs"])
    if d.stdout.strip():
        sh([GIT, "add", "docs"])
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "lingongzhe",
            "GIT_AUTHOR_EMAIL": "lingongzhe@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "lingongzhe",
            "GIT_COMMITTER_EMAIL": "lingongzhe@users.noreply.github.com",
        })
        c = sh([GIT, "commit", "-m", "docs: 自动刷新静态数据 " + time.strftime("%Y-%m-%d %H:%M")], env=env)
        if c.stderr.strip():
            log("commit: " + c.stderr.strip())
        push_url = f"https://x-access-token:{token}@github.com/{REPO}.git"
        p = sh([GIT, "push", push_url, f"HEAD:{BRANCH}"], env=env)
        if p.returncode == 0:
            log("push 成功 -> " + REPO + " (" + BRANCH + ")")
            # 刷新 origin/main 跟踪引用：直连 URL 推送不会自动更新，
            # 否则 git status 会一直显示 ahead N，误以为没推上去
            sh([GIT, "fetch", "origin"])
        else:
            log("push 失败: " + (p.stderr.strip()[-400:] or "无错误输出"))
    else:
        log("无数据变化，无需提交/推送")
    log("==== 刷新完成 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())