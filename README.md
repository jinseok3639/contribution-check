# Contribution Check

로컬 git 레포의 커밋 히스토리를 읽고, **팀원별로 무엇을 만들고 무엇을 고쳤는지**를 기능 단위 서술형 리포트로 정리하는 Claude Code 스킬.

커밋 수, 라인 수, "A 30% / B 70%" 같은 퍼센트는 내지 않는다. 대신 대/중/소분류 기능 트리를 세우고 각 기능에 누가 어떻게 관여했는지를 문장으로 쓰되, **모든 판단에 근거 커밋 SHA를 붙인다.** 팀원이 직접 열어보고 반박할 수 있게 하는 것이 목적이다.

## 설치

모든 프로젝트에서 쓰려면:

```
git clone https://github.com/jinseok3639/contribution_check.git ~/.claude/skills/contribution-check
```

특정 프로젝트에서만 쓰려면 그 프로젝트 루트에서:

```
git clone https://github.com/jinseok3639/contribution_check.git .claude/skills/contribution-check
```

zip으로 받았다면 압축을 풀고 폴더 이름을 `contribution-check`로 바꿔서 위 경로에 넣는다. 어느 쪽이든 설치 후 Claude Code를 다시 켜야 스킬이 잡힌다.

업데이트:

```
git -C ~/.claude/skills/contribution-check pull
```

## 사용

분석할 레포에서 Claude Code를 켜고 말로 시키면 된다.

```
이 레포 기여도 분석해줘
누가 뭘 만들었는지 정리해줘
우리 팀 프로젝트 회고 리포트 만들어줘
```

브랜치를 안 알려주면 물어본다. 팀원 신원(같은 사람의 여러 git author)을 이메일만으로 확신할 수 없을 때도 근거를 보여주고 물어본다.

## 결과물

호출한 세션의 작업 디렉터리에 세 파일이 나온다 (분석 대상 레포 안에는 쓰지 않는다).

| 파일 | 내용 |
| --- | --- |
| `{repo}-{branch}_result.html` | 탭 4개짜리 단일 파일 대시보드. 외부 라이브러리 없음 |
| `{repo}-{branch}_result.md` | 같은 내용의 Markdown |
| `{repo}-{branch}_analysis.json` | 판단 원본. 고쳐서 렌더러만 다시 돌릴 수 있다 |

HTML 탭 구성:

1. **대시보드** — 팀원 카드, 누적 커밋 추이 차트(가로축은 실제 날짜 간격), 사람별 소분류 커버리지, 주도/참여/수정 역할 구성
2. **기능 분류 × 기여** — 기능 트리와 귀속을 합친 통합표
3. **기능별 서술** — 중분류 단위 서술
4. **분석 근거** — 신원 병합 근거, diff 커버리지, 분석의 한계

서술을 한 줄 고치고 싶으면 `analysis.json`을 편집하고 렌더러만 다시 돌리면 된다. 레포를 처음부터 다시 읽지 않는다.

```
python ~/.claude/skills/contribution-check/scripts/render_report.py myrepo-main_analysis.json
```

## 요구사항

- Claude Code
- git
- Python 3 — 표준 라이브러리만 쓴다. 설치할 패키지 없음

## 알아둘 것

- **인원 제한은 없다.** 다만 10명을 넘으면 차트 선이 서로를 가리고, 15명을 넘으면 사람 색이 앞에서부터 다시 쓰인다. 둘 다 리포트에 그 한계가 적힌 채로 나온다.
- **diff를 전수 확인하지는 않는다.** 기능 커버리지와 커밋 메시지 신뢰도를 기준으로 골라 읽고, "N개 중 M개를 어떤 기준으로 읽었다"를 리포트에 남긴다.
- **AI 사용 여부는 판별하지 않는다.** `Co-Authored-By` 트레일러는 꺼두는 경우가 흔해 신뢰할 수 없다. git author 자체를 귀속 기준으로 삼는다.
- **git author ≠ 실제 작업자**일 수 있다 (squash merge, 대표 1인이 대신 push, 페어 프로그래밍). 그래서 판단마다 SHA를 붙인다. 자동 판정을 믿으라는 도구가 아니라 확인할 거리를 만들어주는 도구다.

## 구성

```
SKILL.md                          분석 절차 — Claude가 읽는 본문
scripts/survey_commits.py         정량 조사: 커밋·신원 후보·메시지 신뢰도
scripts/render_report.py          analysis.json -> HTML + Markdown
references/report-template.html   리포트 스타일·스크립트 원본
references/analysis-example.json  analysis.json 스키마 예시
```

## 개발

이 브랜치(`master`)는 배포용이라 스킬 파일만 있다. 설계 노트와 시험 분석 기록은 [`dev`](https://github.com/jinseok3639/contribution_check/tree/dev) 브랜치에 있다.
