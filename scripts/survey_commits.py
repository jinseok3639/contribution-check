#!/usr/bin/env python3
"""
note/01~04(contribution_check 프로젝트의 설계 노트)에서 정리한 대로, 커밋을
하나씩 눈으로 보고 판단하기 전에 값싸게 계산할 수 있는 정량 정보를 뽑아 JSON으로
낸다. 이 스크립트가 하는 일은 딱 "어디를 자세히 봐야 하는지 좁혀주는 것"까지다.
최종 판단 — 이 커밋이 어떤 기능에 기여했는지, 메시지가 진짜 거짓말인지 — 는
여전히 diff를 직접 읽는 사람(LLM)의 몫이다.

계산하는 것:
  - 커밋별 변경 파일 / 추가·삭제 줄 수
  - 병합 커밋 여부 (parent 2개 이상)
  - 사전 필터 대상 여부 (lockfile / vendored / 빌드 산출물 등 패턴 매칭)
  - whitespace-only 여부 (--ignore-all-space 기준으로 실질 변경 0인 경우)
  - 메시지 위험 신호: "동작 안 바뀜" 주장(refactor/cleanup/정리 등, 다른 말이 아무리
    구체적이어도 위험) 또는 필러 단어로만 된 메시지(fix/error/수정/오류 등)
  - "진짜 작업" 커밋들(병합·사전필터·whitespace 제외) 기준 diff 크기 z-score
  - author를 이메일 완전 일치 / GitHub noreply 고유 ID 일치 기준으로 묶은 신원 그룹

사용:
  python survey_commits.py <repo_path> [--branch main] > survey.json

git log를 통째로 두 번만 호출한다 (전체 정보 1번 + whitespace 비교용 1번) —
커밋마다 subprocess를 새로 띄우지 않는다. 레포가 아주 커서 이마저 느리면
(map-reduce, 배치 처리 등은) note/03-scaling-large-repos.md에 정리된
다음 단계 문제이지 이 스크립트의 책임이 아니다.
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

# 카테고리 B: "이건 그냥 정리/리팩터라 동작은 안 바뀜"이라는 구체적 주장.
# 메시지에 이 단어가 들어있으면 다른 단어가 아무리 구체적이어도(예: "Refactor
# particle and debris definitions") 무조건 위험 신호로 본다 — "동작 안 바뀜"
# 자체가 diff를 안 읽으면 검증 불가능한 주장이기 때문. (실제로 sample_result의
# CG `7754eba`가 정확히 이 패턴: 특정 대상을 지목한 그럴싸한 "Refactor" 메시지였지만
# 실제로는 주석 삭제뿐이었다.)
NO_CHANGE_CLAIM_KEYWORDS = [
    "refactor", "cleanup", "clean up", "정리", "정상화", "formatting",
    "style only", "docs only", "typo",
]

# 카테고리 A: 메시지가 필러 단어(fix/update/수정/오류 등)로만 이루어져 있어
# "뭘 했는지"가 전혀 안 드러나는 경우. 메시지 전체가 필러 단어만으로 구성됐을
# 때만 위험으로 본다 — 단순히 "짧다"는 이유로는 플래그하지 않는다. 실제로
# CG의 짧은 한국어 메시지("엔딩 추가", "스카이박스 추가" 등)는 짧아도 구체적인
# 기능 명사가 들어있어 정보량이 있었다. "짧으면 다 의심"으로 하면 41개 중 21개가
# 걸려서(과잉 플래그) 전수 확인과 다를 바 없어진다 — 반드시 "필러 단어만 있는지"로
# 좁혀야 한다.
VAGUE_FILLER_WORDS = [
    "fix", "error", "update", "wip", "misc", "minor",
    "수정", "오류", "버그", "업데이트", "정상",
]

PREFILTER_PATTERNS = [
    r"package-lock\.json$", r"yarn\.lock$", r"pnpm-lock\.yaml$",
    r"Cargo\.lock$", r"poetry\.lock$", r"Gemfile\.lock$", r"composer\.lock$",
    r"(^|/)dist/", r"(^|/)build/", r"(^|/)vendor/", r"(^|/)node_modules/",
    r"\.min\.(js|css)$", r"\.generated\.",
]

MARKER = "@@CC_COMMIT@@"
FIELD_SEP = "\x1f"


def run(cmd, cwd):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def parse_log(output):
    """MARKER로 시작하는 헤더 줄 + 뒤따르는 numstat 줄들을 파싱한다."""
    commits = {}
    order = []
    current = None
    for line in output.splitlines():
        if line.startswith(MARKER):
            fields = line[len(MARKER):].split(FIELD_SEP)
            sha, parents, name, email, date, subject = fields
            current = sha
            order.append(sha)
            commits[sha] = {
                "sha": sha,
                "parents": parents.split() if parents else [],
                "author_name": name,
                "author_email": email,
                "date": date,
                "message": subject,
                "files_changed": [],
                "insertions": 0,
                "deletions": 0,
            }
        elif line.strip() and current is not None:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            commits[current]["files_changed"].append(path)
            commits[current]["insertions"] += int(a) if a.isdigit() else 0
            commits[current]["deletions"] += int(d) if d.isdigit() else 0
    return order, commits


def matches_prefilter(files):
    if not files:
        return False
    return all(any(re.search(p, f) for p in PREFILTER_PATTERNS) for f in files)


def message_risk_reason(msg):
    """None이면 위험 신호 없음. 문자열이면 왜 의심스러운지 (커버리지 사유로 그대로 씀)."""
    m = msg.strip().lower()
    for kw in NO_CHANGE_CLAIM_KEYWORDS:
        if kw in m:
            return "no_change_claim"
    tokens = re.findall(r"[\w가-힣]+", m)
    if tokens and all(t in VAGUE_FILLER_WORDS for t in tokens):
        return "vague_filler"
    return None


def noreply_id(email):
    m = re.match(r"(\d+)\+[^@]+@users\.noreply\.github\.com$", email)
    return m.group(1) if m else None


def group_identities(commits):
    by_email = {}
    for c in commits.values():
        info = by_email.setdefault(
            c["author_email"], {"names": set(), "count": 0, "noreply_id": noreply_id(c["author_email"])}
        )
        info["names"].add(c["author_name"])
        info["count"] += 1

    groups = {}
    for email, info in by_email.items():
        key = f"noreply:{info['noreply_id']}" if info["noreply_id"] else f"email:{email}"
        g = groups.setdefault(key, {"emails": [], "names": set(), "commit_count": 0})
        g["emails"].append(email)
        g["names"] |= info["names"]
        g["commit_count"] += info["count"]

    return [
        {"emails": g["emails"], "names": sorted(g["names"]), "commit_count": g["commit_count"]}
        for g in sorted(groups.values(), key=lambda g: -g["commit_count"])
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--branch", default="HEAD")
    args = ap.parse_args()
    repo = str(Path(args.repo).resolve())

    fmt = f"{MARKER}%H{FIELD_SEP}%P{FIELD_SEP}%an{FIELD_SEP}%ae{FIELD_SEP}%aI{FIELD_SEP}%s"

    full_out = run(["git", "log", args.branch, f"--pretty=format:{fmt}", "--numstat"], repo)
    order, commits = parse_log(full_out)

    ws_out = run(["git", "log", args.branch, f"--pretty=format:{fmt}", "--numstat", "-w"], repo)
    _, ws_commits = parse_log(ws_out)

    candidate_sizes = []
    for sha in order:
        c = commits[sha]
        c["is_merge"] = len(c["parents"]) > 1
        c["size"] = c["insertions"] + c["deletions"]
        c["prefilter_skip"] = (not c["is_merge"]) and matches_prefilter(c["files_changed"])

        if c["is_merge"] or c["prefilter_skip"]:
            c["whitespace_only"] = False
        else:
            ws = ws_commits.get(sha, {"insertions": 0, "deletions": 0})
            ws_size = ws["insertions"] + ws["deletions"]
            c["whitespace_only"] = c["size"] > 0 and ws_size == 0

        c["message_risk_reason"] = message_risk_reason(c["message"])

        is_candidate = not (c["is_merge"] or c["prefilter_skip"] or c["whitespace_only"])
        if is_candidate:
            candidate_sizes.append(c["size"])

        del c["parents"]  # is_merge로 대체됐으니 원본 parent 목록은 출력에서 제외

    # 평균/표준편차 대신 median/MAD(median absolute deviation)를 쓴다 — 초기
    # 스캐폴딩 커밋처럼 벤더 라이브러리를 통째로 넣는 극단값 하나가 mean/stdev를
    # 지배해버리면 나머지 커밋들의 z-score가 전부 0 근처로 뭉개진다(실제로 CG
    # 샘플에서 확인됨: 초기 커밋 1개가 34만 줄이라 나머지 커밋들의 z-score가
    # 전부 -0.2로 나왔었다). median/MAD는 이런 단일 극단값에 훨씬 덜 흔들린다.
    if candidate_sizes:
        median = statistics.median(candidate_sizes)
        mad = statistics.median([abs(s - median) for s in candidate_sizes]) or 1
    else:
        median = mad = 0

    for sha in order:
        c = commits[sha]
        is_candidate = not (c["is_merge"] or c["prefilter_skip"] or c["whitespace_only"])
        if is_candidate and mad > 0:
            # modified z-score (Iglewicz & Hoaglin), 이상치 판정 관례상 임계값 3.5
            z = 0.6745 * (c["size"] - median) / mad
        else:
            z = 0.0
        c["size_zscore"] = round(z, 2)
        c["message_reliability_flag"] = bool(
            is_candidate and (c["message_risk_reason"] is not None or abs(z) >= 3.5)
        )

    result = {
        "repo": repo,
        "branch": args.branch,
        "commit_count": len(order),
        "candidate_commit_count": len(candidate_sizes),
        "identity_groups": group_identities(commits),
        "commits": [commits[sha] for sha in order],
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
