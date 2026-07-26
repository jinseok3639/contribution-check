#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analysis.json -> HTML 대시보드 + Markdown 리포트.

이 스크립트가 존재하는 이유: 리포트에 들어가는 파생 수치(소분류 개수, 사람별
관여 기능 수, 역할 집계, rowspan, SVG 좌표, 커버리지 검산)를 사람이 손으로
적으면 반드시 어긋난다. 실제로 시험 실행에서 "중분류 17"이라고 적어둔 값이
실제 16이었고, 커버리지를 19/7로 적었지만 실제로는 20/6이었다. 그래서 서술과
귀속(=판단)만 analysis.json에 담고, 셀 수 있는 것은 전부 여기서 계산한다.

스타일과 스크립트는 references/report-template.html에서 그대로 읽어온다 —
디자인의 단일 출처를 유지하기 위해서다.

사용:
    python scripts/render_report.py analysis.json --out-dir <디렉터리>

표준 라이브러리만 사용한다.
"""
import argparse
import datetime
import html
import json
import math
import os
import re
import sys

LEAD, PART, FIX = "주도", "참여", "수정"
ROLES = (LEAD, PART, FIX)
DOT = {LEAD: "●", PART: "◐", FIX: "○"}

# 템플릿에 정의된 사람 색(--p1 ~ --pN)의 개수. 넘어가면 앞에서부터 다시 쓴다 —
# 색은 보조 단서일 뿐 이름이 항상 글자로 함께 나오므로 정보가 사라지지는 않는다.
PALETTE = 15
# 이 인원을 넘으면 차트 선이 서로를 가려 읽기 어려워진다. 막지는 않고 알리기만 한다.
CROWDED = 10


# ----------------------------------------------------------------- 검증
class Invalid(Exception):
    pass


def validate(a):
    """리포트가 조용히 틀리게 나오는 대신 여기서 죽게 한다.

    막는 것은 '결과가 틀리게 나오는' 조건뿐이다. '보기 나빠지는' 조건은
    경고만 하고 통과시킨다 — 어떤 팀 규모든 일단 결과는 나와야 한다.
    (errors, warnings) 를 돌려준다.
    """
    errs, warns = [], []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    repo, people = a.get("repo", {}), a.get("people", [])
    need(repo.get("name"), "repo.name 이 없다")
    need(repo.get("branch"), "repo.branch 가 없다")
    need(repo.get("levels") in (2, 3), "repo.levels 는 2 또는 3 이어야 한다")
    need(people, "people 이 비어 있다")
    if len(people) > PALETTE:
        warns.append("사람이 %d명이라 색이 %d개를 넘는다 — %d번째부터 앞의 색을 다시 쓴다. "
                     "이름은 항상 색 옆에 글자로 나오므로 정보가 사라지진 않지만, 색만으로 "
                     "사람을 구분할 수는 없다." % (len(people), PALETTE, PALETTE + 1))
    if len(people) > CROWDED:
        warns.append("사람이 %d명이면 누적 커밋 추이 차트의 선 %d개가 서로를 가려 읽기 어렵다. "
                     "표와 커버리지는 인원수와 무관하게 유지되지만, 차트만 보고 판단하지는 "
                     "말라고 결과물에 적어두거나 팀을 나눠 돌리는 편이 낫다." % (len(people), len(people)))

    slugs = [p["slug"] for p in people]
    need(len(set(slugs)) == len(slugs), "people.slug 가 중복된다")

    tax = a.get("taxonomy", [])
    need(tax, "taxonomy 가 비어 있다")
    nleaf = 0
    for mj in tax:
        if repo.get("levels") == 3:
            need(mj.get("name"), "3단계인데 대분류 이름이 없다")
        need(mj.get("mids"), "대분류 %r 에 중분류가 없다" % mj.get("name"))
        for md in mj.get("mids", []):
            need(md.get("name"), "중분류 이름이 없다")
            leaves = md.get("leaves") or []
            # 소분류 0개인 중분류는 표에서 행 없이 사라진다 (실제로 겪은 버그)
            need(leaves, "중분류 %r 에 소분류가 하나도 없다 — 트리를 덜 판 것이다. "
                         "채우거나, 채울 수 없으면 그건 중분류가 아니라 소분류다" % md.get("name"))
            for lf in leaves:
                nleaf += 1
                need(lf.get("name"), "소분류 이름이 없다 (중분류 %r)" % md.get("name"))
                cs = lf.get("contribs") or []
                need(cs, "소분류 %r 에 기여자가 없다 — 아무도 만들지 않은 기능은 없다" % lf.get("name"))
                for c in cs:
                    need(c.get("who") in slugs,
                         "소분류 %r 의 기여자 %r 가 people 에 없다" % (lf.get("name"), c.get("who")))
                    need(c.get("role") in ROLES,
                         "소분류 %r 의 역할 %r 는 %s 중 하나여야 한다" % (lf.get("name"), c.get("role"), "/".join(ROLES)))
                    need(c.get("shas"), "소분류 %r 의 %r 기여에 근거 SHA가 없다" % (lf.get("name"), c.get("who")))

    cov = a.get("coverage", {})
    for k in ("total", "merge", "prefilter", "message_only", "diff_read"):
        need(isinstance(cov.get(k), int), "coverage.%s 가 정수가 아니다" % k)
    if all(isinstance(cov.get(k), int) for k in ("total", "merge", "prefilter", "message_only", "diff_read")):
        s = cov["merge"] + cov["prefilter"] + cov["message_only"] + cov["diff_read"]
        need(s == cov["total"],
             "커버리지 검산 불일치: %d(병합)+%d(사전필터)+%d(메시지만)+%d(diff읽음) = %d ≠ %d(전체)"
             % (cov["merge"], cov["prefilter"], cov["message_only"], cov["diff_read"], s, cov["total"]))
        tot = sum(p.get("commits", 0) for p in people)
        need(tot == cov["total"],
             "인물별 커밋 수 합(%d)이 전체 커밋 수(%d)와 다르다 — 신원 병합을 다시 확인하라" % (tot, cov["total"]))

    tl = a.get("timeline", [])
    need(tl, "timeline 이 비어 있다")
    if tl:
        last = tl[-1].get("counts", {})
        for p in people:
            need(last.get(p["slug"]) == p.get("commits"),
                 "timeline 마지막 값(%r=%s)이 people.commits(%s)와 다르다"
                 % (p["slug"], last.get(p["slug"]), p.get("commits")))

    if errs:
        raise Invalid("\n".join("  - " + e for e in errs))
    return warns


# ----------------------------------------------------------------- 집계
class Agg(object):
    def __init__(self, a):
        self.a = a
        self.people = a["people"]
        self.var = {}
        for i, p in enumerate(sorted(self.people, key=lambda x: -x.get("commits", 0))):
            self.var[p["slug"]] = "--p%d" % (i % PALETTE + 1)  # 팔레트를 넘으면 순환
        self.name = {p["slug"]: p["name"] for p in self.people}
        self.order = [p["slug"] for p in
                      sorted(self.people, key=lambda x: -x.get("commits", 0))]

        self.leaves = []
        for mj in a["taxonomy"]:
            for md in mj["mids"]:
                for lf in md["leaves"]:
                    self.leaves.append(lf)
        self.nleaf = len(self.leaves)
        self.nmid = sum(len(mj["mids"]) for mj in a["taxonomy"])
        self.nmajor = len(a["taxonomy"])

        self.cover = {s: 0 for s in self.name}
        self.roles = {s: {r: 0 for r in ROLES} for s in self.name}
        self.multi = 0
        self.mismatch = set()
        self.shas = set()
        for lf in self.leaves:
            seen = []
            for c in lf["contribs"]:
                self.roles[c["who"]][c["role"]] += 1
                self.shas.update(c["shas"])
                if c.get("mismatch"):
                    self.mismatch.add(c["shas"][0])
                if c["who"] not in seen:
                    seen.append(c["who"])
            for s in seen:
                self.cover[s] += 1
            if len(seen) > 1:
                self.multi += 1

    def person(self, slug):
        return [p for p in self.people if p["slug"] == slug][0]


# ----------------------------------------------------------------- 차트
CW, CH = 900, 300
PADL, PADR, PADT, PADB = 42, 104, 14, 34


def build_chart(agg):
    tl = agg.a["timeline"]
    d0 = datetime.date.fromisoformat(tl[0]["date"])
    offs = [(datetime.date.fromisoformat(t["date"]) - d0).days for t in tl]
    dmax = max(offs) or 1
    peak = max(max(t["counts"].values()) for t in tl)
    step = max(1, int(math.ceil(peak / 5.0)))
    step = next((s for s in (1, 2, 5, 10, 20, 25, 50, 100) if s >= step), step)
    ymax = int(math.ceil(peak / float(step))) * step

    def py(v):
        return (CH - PADB) - v / float(ymax) * (CH - PADT - PADB)

    # 선 끝의 이름 라벨이 겹치지 않도록 아래에서 위로 최소 간격을 확보한다.
    # 그래도 차트 위로 넘치면(인원이 많거나 최종값이 몰려 있는 경우) 선 옆 라벨을
    # 포기하고 차트 아래 범례로 돌린다 — 카드 제목을 덮는 것보다 낫다.
    ends = sorted(((tl[-1]["counts"][s], s) for s in agg.order), key=lambda x: x[0])
    label_y, prev = {}, None
    for v, s in ends:
        y = py(v) + 4
        if prev is not None and prev - y < 13:
            y = prev - 13
        label_y[s] = y
        prev = y
    inline_labels = min(label_y.values()) >= PADT
    padr = PADR if inline_labels else 16  # 범례로 돌리면 라벨 자리를 그래프에 돌려준다

    def px(d):
        return PADL + d / float(dmax) * (CW - PADL - padr)

    o = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="사람별 누적 커밋 추이">' % (CW, CH)]
    for v in range(0, ymax + 1, step):
        o.append('<line class="grid" x1="%d" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                 % (PADL, py(v), CW - padr, py(v)))
        o.append('<text class="ax" x="%d" y="%.1f" text-anchor="end">%d</text>' % (PADL - 9, py(v) + 4, v))

    # 날짜 라벨은 최대 5개만 — 더 넣으면 겹친다
    nlab = min(5, len(tl))
    picks = sorted({int(round(i * (len(tl) - 1) / float(nlab - 1))) for i in range(nlab)}) if nlab > 1 else [0]
    for i in picks:
        o.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                 % (px(offs[i]), CH - 12, tl[i]["date"][5:]))

    for slug in agg.order:
        vals = [(offs[i], tl[i]["counts"][slug]) for i in range(len(tl))]
        o.append('<g class="series" data-person="%s" style="--pc: var(%s)">' % (slug, agg.var[slug]))
        o.append('<polyline class="ln" points="%s"/>'
                 % " ".join("%.1f,%.1f" % (px(d), py(v)) for d, v in vals))
        prev_v = None
        for d, v in vals:
            if v != prev_v:  # 값이 바뀐 날에만 점 — 전부 찍으면 선이 안 보인다
                o.append('<circle class="pt" cx="%.1f" cy="%.1f" r="2.5"/>' % (px(d), py(v)))
            prev_v = v
        if inline_labels:
            o.append('<text class="lbl" x="%.1f" y="%.1f">%s %d</text>'
                     % (CW - padr + 10, label_y[slug], html.escape(agg.name[slug]), vals[-1][1]))
        o.append("</g>")
    o.append("</svg>")

    svg = "\n          ".join(o)
    if inline_labels:
        return svg
    legend = "".join(
        '<span class="clg-item" data-person="%s" style="--pc: var(%s)">'
        '<i class="clg-chip"></i>%s <b>%d</b></span>'
        % (s, agg.var[s], html.escape(agg.name[s]), tl[-1]["counts"][s]) for s in agg.order)
    return svg + '\n          <div class="chart-legend">%s</div>' % legend


# ----------------------------------------------------------------- 조각
def person_cards(agg):
    o = []
    for slug in agg.order:
        p = agg.person(slug)
        v = agg.var[slug]
        alias = ' <span class="alias">(%s)</span>' % html.escape(p["alias"]) if p.get("alias") else ""
        mg = ('<span style="color:var(--ink-3)"> · 병합 %d</span>' % p["merges"]) if p.get("merges") else ""
        o.append(
            '<button class="person-card" type="button" aria-pressed="false" data-person="%s"\n'
            '                style="--pc: var(%s); --pc-soft: var(%s-soft)">\n'
            '          <span class="person-name">%s%s</span>\n'
            '          <span class="person-stats">\n'
            '            <span><b>%d</b> 커밋%s</span><span><b>%d</b>/%d 기능</span><span><b>%d</b> 주도</span>\n'
            '          </span>\n'
            '          <span class="person-blurb">%s</span>\n'
            '        </button>'
            % (slug, v, v, html.escape(p["name"]), alias, p.get("commits", 0), mg,
               agg.cover[slug], agg.nleaf, agg.roles[slug][LEAD], html.escape(p.get("blurb", ""))))
    return "\n        ".join(o)


def coverage_rows(agg):
    o = []
    for slug in agg.order:
        o.append(
            '<div class="mrow" data-person="%s" style="--pc: var(%s)">\n'
            '            <span class="mrow-nm">%s</span>\n'
            '            <span class="mrow-track"><span class="mrow-fill" style="width: %.1f%%"></span></span>\n'
            '            <span class="mrow-val">%d<span class="mrow-den">/%d</span></span>\n'
            '          </div>'
            % (slug, agg.var[slug], html.escape(agg.name[slug]),
               agg.cover[slug] * 100.0 / agg.nleaf, agg.cover[slug], agg.nleaf))
    return "\n          ".join(o)


def role_rows(agg):
    cls = {LEAD: "s-lead", PART: "s-part", FIX: "s-fix"}
    o = []
    for slug in agg.order:
        r = agg.roles[slug]
        segs = "".join('<span class="seg %s" style="flex: %d" title="%s %d">%d</span>'
                       % (cls[k], r[k], k, r[k], r[k]) for k in ROLES if r[k])
        o.append(
            '<div class="mrow" data-person="%s" style="--pc: var(%s)">\n'
            '            <span class="mrow-nm">%s</span>\n'
            '            <span class="mrow-stack">%s</span>\n'
            '            <span class="mrow-val">%d</span>\n'
            '          </div>'
            % (slug, agg.var[slug], html.escape(agg.name[slug]), segs, sum(r.values())))
    return "\n          ".join(o)


def ledger(agg):
    """기능 트리와 귀속을 rowspan 하나로 합친 표. 사람은 절대 열이 되지 않는다."""
    three = agg.a["repo"]["levels"] == 3
    out, idx = [], 0
    for mj in agg.a["taxonomy"]:
        mj_leaves = sum(len(md["leaves"]) for md in mj["mids"])
        first_major = True
        for md in mj["mids"]:
            first_mid = True
            for lf in md["leaves"]:
                seen = []
                for c in lf["contribs"]:
                    if c["who"] not in seen:
                        seen.append(c["who"])
                row = ['<tr class="leaf-row" data-people="%s">' % " ".join(seen)]
                if three and first_major:
                    row.append('<td class="spine spine-major" rowspan="%d">%s</td>'
                               % (mj_leaves, html.escape(mj["name"])))
                    first_major = False
                if first_mid:
                    row.append('<td class="spine spine-mid" rowspan="%d">'
                               '<span class="idx">%02d</span>%s'
                               '<span class="leafcount">소분류 %d</span></td>'
                               % (len(md["leaves"]), idx, html.escape(md["name"]), len(md["leaves"])))
                    first_mid = False
                    idx += 1
                anchor = ('<span class="anchor">%s</span>' % html.escape(lf["anchor"])) if lf.get("anchor") else ""
                row.append('<td class="leaf">%s%s</td>' % (html.escape(lf["name"]), anchor))

                lines = []
                for c in lf["contribs"]:
                    v = agg.var[c["who"]]
                    mark = "mark lead" if c["role"] == LEAD else "mark"
                    conf = ('<span class="conf read">diff 확인</span>' if c.get("diff_read")
                            else '<span class="conf">메시지 추정</span>')
                    flag = ('<span class="flag" title="%s">&ne;</span>' % html.escape(c["mismatch"])) if c.get("mismatch") else ""
                    lines.append(
                        '<div class="contrib-line">'
                        '<span class="%s" style="--mc: var(%s); --mc-soft: var(%s-soft)">'
                        '<span class="dot">%s</span>%s</span>'
                        '<b style="color: var(%s)">%s</b>%s%s%s</div>'
                        % (mark, v, v, DOT[c["role"]], c["role"], v, html.escape(agg.name[c["who"]]),
                           "".join('<code class="sha">%s</code>' % html.escape(s) for s in c["shas"]),
                           flag, conf))
                row.append('<td class="contrib">%s</td>' % "".join(lines))
                row.append("</tr>")
                out.append("\n            ".join(row))
    return "\n\n            ".join(out)


def narratives(agg):
    o = []
    for i, n in enumerate(agg.a.get("narratives", [])):
        pips = "".join('<i class="who-pip" style="--pc: var(%s)"></i>' % agg.var[s]
                       for s in n.get("people", []))
        b = ['<details class="nar"%s>' % (" open" if i == 0 else ""),
             '  <summary>%s\n            <span class="who-strip">%s</span>\n          </summary>' % (n["title"], pips),
             '  <div class="nar-body">']
        for p in n.get("paras", []):
            b.append("    <p>%s</p>" % p)
        if n.get("callout"):
            b.append('    <div class="callout"><b>메시지 &ne; 실제 diff</b> &mdash; %s</div>' % n["callout"])
        if n.get("evidence"):
            b.append('    <div class="evidence"><div class="ev-title">근거 커밋</div>')
            for e in n["evidence"]:
                b.append('      <div class="ev-item">%s</div>' % e)
            b.append("    </div>")
        b += ["  </div>", "</details>"]
        o.append("\n        ".join(b))
    return "\n\n        ".join(o)


def plain_table(headers, rows):
    """rows: 각 행은 이미 완성된 '<td>...</td>' 문자열들의 튜플/리스트 — 여기서 또
    <td>로 감싸면 중첩 <td>가 되어 브라우저가 암묵적으로 셀을 잘라 열이 두 배로
    갈라진다 (실제로 겪은 표 정렬 붕괴 버그)."""
    o = ['<table class="plain">',
         "  <thead><tr>%s</tr></thead>" % "".join('<th scope="col">%s</th>' % h for h in headers),
         "  <tbody>"]
    for r in rows:
        o.append("    <tr>%s</tr>" % "".join(r))
    o += ["  </tbody>", "</table>"]
    return "\n      ".join(o)


# ----------------------------------------------------------------- HTML
def render_html(agg, style, script):
    a = agg.a
    repo, cov = a["repo"], a["coverage"]
    tree_note = ("대분류 %d · 중분류 %d · 소분류 %d" % (agg.nmajor, agg.nmid, agg.nleaf)
                 if repo["levels"] == 3 else "중분류 %d · 소분류 %d" % (agg.nmid, agg.nleaf))
    head_cols = (["대분류"] if repo["levels"] == 3 else []) + \
                ["중분류 — 기능 영역", "소분류 — 개별 기능", "기여"]

    merge_rows = [('<td style="color: var(%s); font-weight: 600">%s</td>' % (agg.var[m["who"]], html.escape(agg.name[m["who"]])),
                   "<td>%d건</td>" % m["count"],
                   '<td class="mono-cell">%s</td>' % html.escape(m.get("detail", "")))
                  for m in a.get("merges", [])]
    ident_rows = []
    for i in a.get("identities", []):
        p = agg.person(i["slug"])
        ident_rows.append((
            '<td style="color: var(%s); font-weight: 600">%s<br>'
            '<span style="font-weight:400;color:var(--ink-3);font-size:13px">%d</span></td>'
            % (agg.var[i["slug"]], html.escape(agg.name[i["slug"]]), p.get("commits", 0)),
            '<td class="mono-cell">%s</td>' % "<br>".join(html.escape(x) for x in i["authors"]),
            "<td>%s</td>" % i["basis"]))
    checksum = " + ".join(str(agg.person(s).get("commits", 0)) for s in agg.order)

    segs = [("diff_read", "--p1"), ("message_only", "--p2"), ("prefilter", "--p3")]
    cov_bar = "".join('<div class="cov-seg" style="flex: %d; background: var(%s)"></div>' % (cov[k], v)
                      for k, v in segs if cov[k]) + \
              '<div class="cov-seg" style="flex: %d; background: var(--rule-strong)"></div>' % cov["merge"]

    reasons = ""
    if cov.get("read_reason_coverage") is not None:
        reasons = ('<br><span style="color:var(--ink-3)">커버리지 %d · 신뢰도 %d</span>'
                   % (cov["read_reason_coverage"], cov["read_reason_reliability"]))
    cov_cells = [
        ('--p1', cov["diff_read"], "diff를 직접 읽음" + reasons),
        ('--p2', cov["message_only"], "메시지 + 파일 경로만으로 분류"),
        ('--p3', cov["prefilter"], "사전 필터 스킵"),
        ('--flag', len(agg.mismatch), "커밋 메시지 &ne; 실제 diff"),
    ]
    cov_grid = "\n        ".join(
        '<div class="cov-cell"><span class="cov-num" style="color: var(%s)">%d</span>'
        '<span class="cov-lab">%s</span></div>' % (v, n, lab)
        for v, n, lab in cov_cells if n)

    facts = [("Repository", repo.get("path", repo["name"])), ("Branch", repo["branch"]),
             ("Commits", "%d <span style=\"font-size:13px;color:var(--ink-3)\">(병합 %d)</span>"
              % (cov["total"], cov["merge"])),
             ("Period", "%s → %s" % (repo.get("period_start", ""), repo.get("period_end", ""))),
             ("구조", "%s <span style=\"font-size:13px;color:var(--ink-3)\">(%d단계)</span>"
              % (repo.get("structure", ""), repo["levels"]))]

    return u"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(name)s 기여도 분석 — %(title)s (%(branch)s)</title>
%(style)s
</head>
<body>

<div class="wrap">

  <header class="masthead">
    <div class="eyebrow">Contribution Check · 기능 단위 기여 분석</div>
    <h1>%(name)s — %(title)s</h1>
    <dl class="facts">
      %(facts)s
    </dl>
  </header>

  <nav class="tabs" role="tablist" aria-label="보기 전환">
    <button class="tab-btn" role="tab" type="button" data-tab="dash"   aria-selected="true">대시보드</button>
    <button class="tab-btn" role="tab" type="button" data-tab="matrix" aria-selected="false">기능 분류 × 기여</button>
    <button class="tab-btn" role="tab" type="button" data-tab="narr"   aria-selected="false">기능별 서술</button>
    <button class="tab-btn" role="tab" type="button" data-tab="basis"  aria-selected="false">분석 근거</button>
  </nav>

  <div class="panel" id="p-dash" role="tabpanel">

    <section>
      <h2>팀원 <span class="h2-note">클릭하면 그 사람 기여만 강조됩니다 (모든 탭에 적용)</span></h2>
      <div class="people">
        %(cards)s
      </div>
      <div class="filter-hint">
        <button class="clear-btn" type="button" id="clearFilter" hidden>필터 해제</button>
        <span id="filterStatus"></span>
      </div>
    </section>

    <div class="card">
      <div class="card-h">누적 커밋 추이</div>
      <div>
          %(chart)s
      </div>
    </div>

    <div class="duo">
      <div class="card">
        <div class="card-h">기능 커버리지 <span>소분류 %(nleaf)d개 중 관여</span></div>
        <div class="mrow-group">
          %(cov_rows)s
        </div>
      </div>
      <div class="card">
        <div class="card-h">역할 구성 <span><i class="swatch"></i>주도 <i class="swatch w2"></i>참여 <i class="swatch w3"></i>수정</span></div>
        <div class="mrow-group">
          %(role_rows)s
        </div>
      </div>
    </div>

    <div class="kpi-strip">
      <div class="kpi"><span class="kpi-num">%(nleaf)d</span><span class="kpi-lab">소분류 — 기여 판단의 최소 단위</span></div>
      <div class="kpi"><span class="kpi-num">%(multi)d</span><span class="kpi-lab">2인 이상이 손댄 소분류</span></div>
      <div class="kpi"><span class="kpi-num" style="color: var(--flag)">%(nmis)d</span><span class="kpi-lab">커밋 메시지 &ne; 실제 diff</span></div>
      <div class="kpi"><span class="kpi-num">%(read)d<span style="font-size:15px;color:var(--ink-3)">/%(nonmerge)d</span></span><span class="kpi-lab">diff를 직접 읽은 커밋 (병합 제외)</span></div>
    </div>

  </div>

  <div class="panel" id="p-matrix" role="tabpanel" hidden>
    <section>
      <h2>기능 분류 × 기여 <span class="h2-note">%(tree_note)s</span></h2>

      <div class="legend-key">
        <span><span class="mark lead" style="--mc: var(--ink-2); --mc-soft: var(--surface-2)"><span class="dot">●</span> 주도</span> &nbsp;처음 만든 사람</span>
        <span><span class="mark" style="--mc: var(--ink-2); --mc-soft: var(--surface-2)"><span class="dot">◐</span> 참여</span> &nbsp;함께 구성</span>
        <span><span class="mark" style="--mc: var(--ink-2); --mc-soft: var(--surface-2)"><span class="dot">○</span> 수정</span> &nbsp;이후 고침·조정</span>
        <span><span class="flag">&ne;</span> &nbsp;메시지와 실제 diff가 다름</span>
        <span><span class="conf read">diff 확인</span> / <span class="conf">메시지 추정</span> &nbsp;귀속 근거</span>
      </div>

      <div class="ledger-scroll">
        <table class="ledger">
          <thead>
            <tr>
              %(head_cols)s
            </tr>
          </thead>
          <tbody>

            %(ledger)s

          </tbody>
        </table>
      </div>
    </section>
  </div>

  <div class="panel" id="p-narr" role="tabpanel" hidden>
    <section>
      <h2>기능별 서술</h2>
      <div class="narratives">

        %(narr)s

      </div>
    </section>
  </div>

  <div class="panel" id="p-basis" role="tabpanel" hidden>
%(merge_sec)s
    <section>
      <h2>신원 병합 근거</h2>
      %(ident)s
      <p class="lede" style="font-size:14px">%(checksum)s = %(total)d — 전체 커밋 수와 일치한다.</p>
    </section>

    <section>
      <h2>분석 커버리지 <span class="h2-note">%(total)d건을 모두 같은 깊이로 읽지는 않았다</span></h2>

      <div class="cov-bar" role="img" aria-label="커버리지 구성">%(cov_bar)s</div>

      <div class="cov-grid">
        %(cov_grid)s
      </div>

      <ul class="notes">
        <li>%(total)d = %(merge)d(병합) + %(pre)d(사전 필터) + %(msg)d(메시지만) + %(read)d(diff 읽음) — 검산 일치.</li>
        %(notes)s
      </ul>
    </section>

  </div>

</div>

%(script)s
</body>
</html>
""" % {
        "name": html.escape(repo["name"]), "title": html.escape(repo.get("title", "")),
        "branch": html.escape(repo["branch"]), "style": style, "script": script,
        "facts": "\n      ".join('<div class="fact"><dt>%s</dt><dd>%s</dd></div>' % (k, v) for k, v in facts),
        "cards": person_cards(agg), "chart": build_chart(agg),
        "cov_rows": coverage_rows(agg), "role_rows": role_rows(agg),
        "nleaf": agg.nleaf, "multi": agg.multi, "nmis": len(agg.mismatch),
        "read": cov["diff_read"], "nonmerge": cov["total"] - cov["merge"],
        "tree_note": tree_note,
        "head_cols": "\n              ".join('<th scope="col">%s</th>' % h for h in head_cols),
        "ledger": ledger(agg), "narr": narratives(agg),
        "merge_sec": ("""
    <section>
      <h2>브랜치 통합 역할 <span class="h2-note">병합 %d건 — 전부 코드 변경 없는 순수 통합</span></h2>
      %s
    </section>
""" % (cov["merge"], plain_table(["사람", "병합", "내역"], merge_rows))) if merge_rows else "",
        "ident": plain_table(["사람", "Git 계정", "근거"], ident_rows),
        "checksum": checksum, "total": cov["total"], "merge": cov["merge"],
        "pre": cov["prefilter"], "msg": cov["message_only"],
        "cov_bar": cov_bar, "cov_grid": cov_grid,
        "notes": "\n        ".join("<li>%s</li>" % n for n in a.get("notes", [])),
    }


# ----------------------------------------------------------------- Markdown
def detag(x):
    x = re.sub(r'<code class="sha">([^<]*)</code>', r"`\1`", x)
    x = re.sub(r"<[^>]+>", "", x)
    for a, b in (("&ne;", "≠"), ("&mdash;", "—"), ("&middot;", "·"), ("&amp;", "&"), ("&nbsp;", " ")):
        x = x.replace(a, b)
    return x.strip()


def render_md(agg):
    a, cov = agg.a, agg.a["coverage"]
    repo = a["repo"]
    o = []
    w = o.append
    w("# %s 기여도 분석\n" % repo["name"])
    w("## 개요\n")
    w("- 레포: `%s` (branch `%s`)%s" % (repo.get("path", repo["name"]), repo["branch"],
                                        " — " + repo["title"] if repo.get("title") else ""))
    if repo.get("period_start"):
        w("- 기간: %s → %s" % (repo["period_start"], repo["period_end"]))
    w("- 커밋 %d건 (병합 %d건 포함), 팀원 %d명" % (cov["total"], cov["merge"], len(agg.people)))
    w("- 레포 구조: **%s** → %s의 %d단계"
      % (repo.get("structure", ""),
         ("대분류 %d · 중분류 %d · 소분류 %d" % (agg.nmajor, agg.nmid, agg.nleaf)) if repo["levels"] == 3
         else ("중분류 %d · 소분류 %d" % (agg.nmid, agg.nleaf)), repo["levels"]))
    w("\n## 기능 트리\n")
    for mj in a["taxonomy"]:
        ind = ""
        if repo["levels"] == 3:
            w("- **%s**" % mj["name"])
            ind = "  "
        for md in mj["mids"]:
            w("%s- %s" % (ind, md["name"]))
            for lf in md["leaves"]:
                w("%s  - %s%s" % (ind, lf["name"], (" — `%s`" % lf["anchor"]) if lf.get("anchor") else ""))
    w("\n## 중분류별 기여 서술\n")
    for n in a.get("narratives", []):
        w("### %s\n" % detag(n["title"]))
        for p in n.get("paras", []):
            w(detag(p) + "\n")
        if n.get("callout"):
            w("> **메시지 ≠ 실제 diff** — %s\n" % detag(n["callout"]))
        if n.get("evidence"):
            w("근거 커밋:\n")
            for e in n["evidence"]:
                w("- " + detag(e))
            w("")
    w("## 팀원별 종합 요약\n")
    w("| 사람 | 커밋 | 병합 | 관여 소분류 | 주도 | 참여 | 수정 |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for s in agg.order:
        p, r = agg.person(s), agg.roles[s]
        nm = p["name"] + (" (%s)" % p["alias"] if p.get("alias") else "")
        w("| %s | %d | %d | %d / %d | %d | %d | %d |"
          % (nm, p.get("commits", 0), p.get("merges", 0), agg.cover[s], agg.nleaf,
             r[LEAD], r[PART], r[FIX]))
    w("")
    for s in agg.order:
        p = agg.person(s)
        if p.get("blurb"):
            w("- **%s** — %s" % (p["name"], p["blurb"]))
    w("")
    if a.get("merges"):
        w("## 브랜치 통합 역할\n")
        w("병합 %d건은 코드 변경이 없는 순수 통합이므로 기능 기여와 분리해 기록한다.\n" % cov["merge"])
        w("| 사람 | 병합 | 내역 |")
        w("|---|---:|---|")
        for m in a["merges"]:
            w("| %s | %d건 | %s |" % (agg.name[m["who"]], m["count"], m.get("detail", "")))
        w("")
    w("## 신원 병합 근거\n")
    w("| 사람 | Git 계정 | 근거 |")
    w("|---|---|---|")
    for i in a.get("identities", []):
        w("| %s (%d) | %s | %s |"
          % (agg.name[i["slug"]], agg.person(i["slug"]).get("commits", 0),
             "<br>".join("`%s`" % x for x in i["authors"]), detag(i["basis"])))
    w("\n%s = %d — 전체 커밋 수와 일치한다.\n"
      % (" + ".join(str(agg.person(s).get("commits", 0)) for s in agg.order), cov["total"]))
    w("## 커버리지 / 분석의 한계\n")
    w("- 전체 커밋 **%d건** (병합 **%d건** 포함)" % (cov["total"], cov["merge"]))
    w("- 사전 필터/공백으로 스킵: **%d건**" % cov["prefilter"])
    w("- 메시지 + 파일 경로만으로 분류(diff 안 읽음): **%d건**" % cov["message_only"])
    rr = ""
    if cov.get("read_reason_coverage") is not None:
        rr = " — 기능 커버리지 부족/애매함 %d건, 메시지 신뢰도 낮음 %d건" % (
            cov["read_reason_coverage"], cov["read_reason_reliability"])
    w("- diff를 직접 읽음: **%d건**%s" % (cov["diff_read"], rr))
    w("- 검산: %d = %d + %d + %d + %d ✓"
      % (cov["total"], cov["merge"], cov["prefilter"], cov["message_only"], cov["diff_read"]))
    w("- 메시지 ≠ 실제 diff로 확인된 커밋: **%d건**" % len(agg.mismatch))
    w("- 2인 이상이 손댄 소분류: **%d / %d개**" % (agg.multi, agg.nleaf))
    if a.get("notes"):
        w("\n### 판단의 한계\n")
        for n in a["notes"]:
            w("- " + detag(n))
    w("")
    return "\n".join(o)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="analysis.json에서 HTML 대시보드와 Markdown 리포트를 만든다")
    ap.add_argument("analysis", help="analysis.json 경로")
    ap.add_argument("--out-dir", default=".", help="결과물 디렉터리 (기본: 현재 디렉터리)")
    ap.add_argument("--template", default=None,
                    help="report-template.html 경로 (기본: 이 스크립트 옆의 ../references/)")
    ap.add_argument("--basename", default=None, help="파일 이름 (기본: {repo}-{branch}_result)")
    args = ap.parse_args()

    with open(args.analysis, encoding="utf-8") as f:
        a = json.load(f)

    try:
        warns = validate(a)
    except Invalid as e:
        sys.stderr.write("analysis.json 검증 실패:\n%s\n" % e)
        return 1
    for w in warns:
        sys.stderr.write("경고: %s\n" % w)

    tpl = args.template or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "references", "report-template.html")
    if not os.path.exists(tpl):
        sys.stderr.write("템플릿을 찾을 수 없다: %s\n" % tpl)
        return 1
    with open(tpl, encoding="utf-8") as f:
        t = f.read()
    style = t[t.index("<style>"):t.index("</style>") + len("</style>")]
    script = t[t.index("<script>"):t.index("</script>") + len("</script>")]

    agg = Agg(a)
    base = args.basename or "%s-%s_result" % (a["repo"]["name"], a["repo"]["branch"])
    if not os.path.isdir(args.out_dir):
        os.makedirs(args.out_dir)
    hp = os.path.join(args.out_dir, base + ".html")
    mp = os.path.join(args.out_dir, base + ".md")
    with open(hp, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_html(agg, style, script))
    with open(mp, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_md(agg))

    sys.stderr.write(
        "%s\n%s\n소분류 %d · 중분류 %d · 대분류 %d | 2인 이상 %d | 메시지≠diff %d\n"
        % (hp, mp, agg.nleaf, agg.nmid, agg.nmajor, agg.multi, len(agg.mismatch)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
