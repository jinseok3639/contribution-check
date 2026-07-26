#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dev의 스킬 폴더를 배포 브랜치(master)로 내보낸다.

master는 "루트가 곧 스킬" 레이아웃이라 dev와 파일 경로가 다르다. 그래서 두 브랜치는
merge로 이어붙일 수 없다 — merge 하면 dev의 `.claude/` 경로가 그대로 master에 딸려
들어간다. 대신 이 스크립트가 master의 트리를 매번 dev 기준으로 통째로 다시 만든다.

    master 트리 = .claude/skills/contribution-check/ 의 내용물
                + release/ 안의 파일들 (루트에 겹쳐 쓴다)

master를 별도 worktree로 꺼내서 작업하므로 지금 체크아웃된 dev 작업본은 건드리지
않는다. push는 기본으로 하지 않는다.

사용:
    python scripts/release.py             # 바뀔 내용 보여주고 master에 커밋
    python scripts/release.py --dry-run   # 보여주기만 하고 커밋하지 않는다
    python scripts/release.py --push      # 커밋 후 origin master 까지 밀어 올린다
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

SKILL_DIR = os.path.join(".claude", "skills", "contribution-check")
OVERLAY_DIR = "release"
TARGET = "master"


def die(msg):
    sys.exit("release: " + msg)


def git(*args, **kw):
    cwd = kw.pop("cwd", None)
    check = kw.pop("check", True)
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        die("git %s 실패:\n%s%s" % (" ".join(args), r.stdout, r.stderr))
    return (r.stdout or "").strip()


def copy_into(src, dst):
    """src 안의 항목들을 dst 루트에 편다 (src 폴더 자체는 만들지 않는다)."""
    for name in sorted(os.listdir(src)):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(s, d)


def files_under(root):
    out = set()
    for base, _dirs, names in os.walk(root):
        for n in names:
            rel = os.path.relpath(os.path.join(base, n), root).replace(os.sep, "/")
            if rel != ".git" and not rel.startswith(".git/"):
                out.add(rel)
    return out


def main():
    ap = argparse.ArgumentParser(description="dev의 스킬을 %s 브랜치로 내보낸다." % TARGET)
    ap.add_argument("--dry-run", action="store_true", help="커밋하지 않고 바뀔 내용만 보여준다")
    ap.add_argument("--push", action="store_true", help="커밋 후 origin %s 로 push" % TARGET)
    args = ap.parse_args()

    root = git("rev-parse", "--show-toplevel")
    os.chdir(root)

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == TARGET:
        die("%s 위에서는 실행할 수 없다 — 이 스크립트가 %s를 덮어쓴다. dev로 옮기고 다시 실행한다."
            % (TARGET, TARGET))
    if git("status", "--porcelain", "--untracked-files=no"):
        die("커밋되지 않은 변경이 있다. 배포는 커밋된 상태만 내보낸다.")

    skill = os.path.join(root, SKILL_DIR)
    if not os.path.isfile(os.path.join(skill, "SKILL.md")):
        die("%s/SKILL.md 를 찾을 수 없다." % SKILL_DIR)

    head = git("rev-parse", "--short", "HEAD")
    subject = git("log", "-1", "--format=%s")

    tmp = tempfile.mkdtemp(prefix="cc-release-")
    wt = os.path.join(tmp, TARGET)
    git("worktree", "add", "--quiet", wt, TARGET)
    try:
        for name in os.listdir(wt):
            if name == ".git":
                continue
            p = os.path.join(wt, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

        copy_into(skill, wt)
        overlay = os.path.join(root, OVERLAY_DIR)
        if os.path.isdir(overlay):
            copy_into(overlay, wt)

        git("add", "--all", cwd=wt)

        # .gitignore가 배포 파일을 조용히 걸러내는 사고를 막는다.
        dropped = sorted(files_under(wt) - set(git("ls-files", cwd=wt).splitlines()))
        if dropped:
            die("%s/.gitignore 가 배포 파일을 걸러냈다: %s" % (OVERLAY_DIR, ", ".join(dropped)))

        if not git("status", "--porcelain", cwd=wt):
            print("%s 는 이미 %s@%s 와 같다. 낼 것이 없다." % (TARGET, branch, head))
            return
        print(git("diff", "--cached", "--stat", cwd=wt))

        if args.dry_run:
            print("\n--dry-run: 커밋하지 않았다.")
            return

        git("commit", "--quiet", "-m", "Release from %s@%s" % (branch, head), "-m", subject, cwd=wt)
        print("\n%s <- %s@%s (%s)" % (TARGET, branch, head, git("rev-parse", "--short", TARGET)))

        if args.push:
            git("push", "origin", TARGET, cwd=wt)
            print("origin/%s 갱신 완료." % TARGET)
        else:
            print("push 하려면: git push origin %s" % TARGET)
    finally:
        git("worktree", "remove", "--force", wt, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
