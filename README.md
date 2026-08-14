# Contribution Check

로컬 git 레포의 커밋 히스토리를 읽고, **팀원별로 무엇을 만들고 무엇을 고쳤는지**를 기능 단위 서술형 리포트로 정리하는 Claude Code 스킬.

GitHub Insights나 라인 수 기반 기여도 도구는 두 가지 문제가 있다. 포맷팅·보일러플레이트·리네이밍만으로도 라인 수는 쉽게 부풀릴 수 있고(게이밍하기 쉽다), 버그를 잡은 한 줄과 자동 생성된 코드 500줄이 라인 수로는 구분되지 않는다(가치를 반영 못 한다). 이 스킬은 커밋 수·라인 수·퍼센트 대신 대/중/소분류 기능 트리를 세우고 각 기능에 누가 어떻게 관여했는지를 문장으로 쓰되, **모든 판단에 근거 커밋 SHA를 붙인다.** 팀원이 직접 열어보고 반박할 수 있게 하는 것이 목적이다.

**[예시 리포트 보기](https://htmlpreview.github.io/?https://github.com/jinseok3639/contribution-check/blob/samples/atio-main_result.html)**

## 설치

모든 프로젝트에서 쓰려면:

```
git clone https://github.com/jinseok3639/contribution-check.git ~/.claude/skills/contribution-check
```

특정 프로젝트에서만 쓰려면 그 프로젝트 루트에서:

```
git clone https://github.com/jinseok3639/contribution-check.git .claude/skills/contribution-check
```

zip으로 받았다면 압축을 풀고 폴더 이름을 `contribution-check`로 바꿔서 위 경로에 넣는다. 설치 후 Claude Code를 다시 켜야 스킬이 잡힌다.

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

브랜치를 안 알려주면 저장소의 기본 브랜치(`origin/HEAD`)를 쓴다. 기본 브랜치가 불명확하면(로컬 전용 레포거나, 똑같이 활발한 브랜치가 여럿이면) 물어본다. 팀원 신원(같은 사람의 여러 git author)을 이메일만으로 확신할 수 없을 때도 근거를 보여주고 물어본다.

## 작동 방식

내부적으로는 아래 순서로 진행된다. 분석 대상 레포 안에 `.temp_contribution_check/`를 만들어 단계마다 파일을 쌓아가면서 판단을 이어가고, 다 끝나면 그 폴더는 지운다 — 중간에 사용량 제한 등으로 끊겨도 이미 쌓인 파일을 보고 그 지점부터 다시 이어간다.

1. **정량 조사** (`scripts/survey_commits.py`) — 레포의 모든 커밋을 훑어서 변경 파일, 추가/삭제 줄 수, 병합 여부, 사전 필터 대상(락파일·vendored·빌드 산출물), 공백만 바뀐 커밋, 커밋 메시지의 위험 신호("정리"·"refactor"처럼 동작이 안 바뀌었다는 주장은 항상 위험 신호, "fix"·"오류" 같은 필러 단어로만 된 메시지도 위험 신호), 레포 내 diff 크기 기준 통계적 이상치, git author 자동 병합 후보를 계산해서 `.temp_contribution_check/survey.json`에 저장한다. 사람이 눈으로 셀 필요 없는 값들이라 스크립트가 결정론적으로 계산한다.

2. **신원 병합** — survey.json의 author 후보군을 본다. 이메일 완전 일치·GitHub noreply 고유 ID 일치처럼 강한 신호는 자동으로 합치고, 이름 유사성처럼 약한 신호만 있으면 근거(각 그룹의 커밋 수·시기·겹치는 작업 영역)를 보여주고 사용자에게 직접 확인받는다. 커밋 메시지 트레일러(`Co-Authored-By` 등)는 이 병합에 절대 들어가지 않는다 — git author만 본다.

3. **기능 트리 만들기** — README·문서·이슈(하향식)와 코드 디렉터리 구조(상향식)를 같이 보고 기능 트리를 세운다. 레포가 **계층형**(라이브러리·서비스처럼 층이 쌓이는 구조)이면 대분류(시스템) → 중분류(통합 컴포넌트) → 소분류(컴포넌트) 3단계로, **병렬형**(게임·앱처럼 화면·기능이 나란한 구조)이면 중분류 → 소분류 2단계로 잡는다. 소분류가 최종 표의 행 단위이자 기여 귀속의 최소 단위라서, 모든 중분류는 소분류를 최소 1개 가져야 한다. 이 트리를 `.temp_contribution_check/taxonomy.md`에 저장해서 여기서 끊겨도 다시 안 짜도 되게 한다.

4. **커밋별 분류** — survey.json의 커밋을 순서대로 훑으며 `.temp_contribution_check/commits.jsonl`에 이미 처리한 커밋은 건너뛰고 이어간다(사용량 제한으로 가장 끊기기 쉬운 단계). 각 커밋을:
   - 병합 커밋이면 diff 없이 "통합 역할"로,
   - 사전 필터·공백뿐인 커밋이면 diff 없이 "스킵"으로 기록하고,
   - 나머지는 먼저 메시지 + 변경 파일 경로만으로 소분류에 매핑한다(아직 diff 안 읽음).

   그리고 아래 중 하나라도 해당하면 그 커밋의 **diff를 실제로 연다**: 매핑된 소분류에 아직 커밋이 3개 이하로 적다(그 기능의 초기 커밋), 여러 소분류에 걸쳐 있어 애매하다, 또는 1단계에서 메시지가 위험 신호로 표시됐다. 나머지는 메시지만으로 분류를 확정하고 diff는 읽지 않는다 — 즉 diff는 전수 확인하지 않고, 애매하거나 못 미더운 것 위주로 골라 읽는다. diff를 읽었는데 메시지와 실제 내용이 다르면("메시지는 X지만 실제로는 Y") 그대로 남긴다. 결과(소속 소분류, diff를 열었는지/왜, 한 줄 요약)를 commits.jsonl에 한 줄씩 추가하고 `.temp_contribution_check/progress.json`의 진행 지점을 갱신한다.

5. **서술 작성** — commits.jsonl이 다 채워지면 중분류 단위로 문단을 쓴다. 소분류마다 헤더를 새로 만들지 않고, 한 중분류 안에서 누가 무엇을 어떤 순서로 만들었는지 커밋 SHA를 인용하며 서술한다.

6. **최종 조립** — 지금까지의 판단(기능 트리·귀속·서술·근거 SHA, diff 커버리지 집계)을 `analysis.json` 하나로 정리하고 `scripts/render_report.py`를 돌린다. 소분류 개수·표의 rowspan·사람 색·차트 좌표처럼 셀 수 있는 값은 전부 렌더러가 계산한다 — 손으로 세면 어긋난다는 게 실제 시험에서 확인됐다. 스키마가 안 맞으면(소분류 없는 중분류, 근거 SHA 없는 기여, 커버리지 합 불일치 등) 렌더러가 결과물을 만들지 않고 종료한다.

7. **마무리** — `analysis.json`을 결과물 옆에 남겨서 나중에 서술 한 줄만 고쳐 다시 렌더링할 수 있게 하고, `.temp_contribution_check/`는 지운다.

## 결과물

분석 대상 레포 루트의 `contribution-check-result/` 폴더에 세 파일이 나온다 (출력 경로를 직접 지정했으면 그곳에).

| 파일 | 내용 |
| --- | --- |
| `{repo}-{branch}_result.html` | 탭 4개짜리 단일 파일 대시보드. 외부 라이브러리 없음 |
| `{repo}-{branch}_result.md` | 같은 내용의 Markdown |
| `{repo}-{branch}_analysis.json` | 판단 원본. 고쳐서 렌더러만 다시 돌릴 수 있다 |

**[예시 리포트 보기](https://htmlpreview.github.io/?https://github.com/jinseok3639/contribution-check/blob/samples/atio-main_result.html)**

HTML 탭 구성:

1. **대시보드** — 팀원 카드, 누적 커밋 추이 차트(가로축은 실제 날짜 간격), 사람별 소분류 커버리지, 주도/참여/수정 역할 구성
2. **기능 분류 × 기여** — 기능 트리와 귀속을 합친 통합표
3. **기능별 서술** — 중분류 단위 서술
4. **분석 근거** — 신원 병합 근거, diff 커버리지, 분석의 한계

서술을 한 줄 고치고 싶으면 `analysis.json`을 편집하고 렌더러만 다시 돌리면 된다 — 레포를 처음부터 다시 읽지 않는다.

```
python scripts/render_report.py myrepo-main_analysis.json
```

## 요구사항

- Claude Code
- git
- Python 3 — 표준 라이브러리만 쓴다. 설치할 패키지 없음

## 알아둘 것

- **인원 제한은 없지만 기여자가 너무 많을 경우 한계.** 다만 10명을 넘으면 차트 선이 서로를 가리고, 15명을 넘으면 사람 색이 앞에서부터 다시 쓰인다. 둘 다 리포트에 그 한계가 적힌 채로 나온다.
- **diff를 전수 확인하지는 않음.** 기능 커버리지와 커밋 메시지 신뢰도를 기준으로 골라 읽고, "N개 중 M개를 어떤 기준으로 읽었다"를 리포트에 남긴다.
- **AI 사용 여부는 판별하지 않음.** `Co-Authored-By` 트레일러는 꺼두는 경우가 흔하고 AI 코딩 도구가 사람 이름처럼 보이는 트레일러를 남기기도 해서 신뢰할 수 없다. git author 자체를 귀속 기준으로 삼는다.
- **git author ≠ 실제 작업자**일 수 있음 (squash merge, 대표 1인이 대신 push, 페어 프로그래밍). 그래서 판단마다 SHA를 붙인다 — 자동 판정을 믿으라는 도구가 아니라 확인할 거리를 만들어주는 도구다.

## 구성

```
SKILL.md                          분석 절차 — Claude가 읽는 본문
scripts/survey_commits.py         정량 조사: 커밋·신원 후보·메시지 신뢰도
scripts/render_report.py          analysis.json -> HTML + Markdown
references/report-template.html   리포트 스타일·스크립트 원본
references/analysis-example.json  analysis.json 스키마 예시
references/design-notes.md        규칙별 근거와 설계 배경
```

## 라이선스

[MIT LICENSE](LICENSE)
