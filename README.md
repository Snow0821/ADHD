# ADHD

**아이디어를 보존하며 현재 목표를 끝까지 실행하는 ChatGPT Work · Codex 플러그인.**

Stay focused on the current purpose while preserving creativity.

[![Validate](https://github.com/Snow0821/ADHD/actions/workflows/validate.yml/badge.svg)](https://github.com/Snow0821/ADHD/actions/workflows/validate.yml)

## 하는 일

- 하나의 목표를 진행하고, 새 작업은 FIFO 큐에 기록.
- 현재 목표를 막는 선행 과제는 LIFO 스택으로 처리한 뒤 원래 작업 재개.
- 지식그래프, 현재 규칙·결정, 완료 이력, 미해결 질문 보존.
- 중단 전 진행 상황과 다음 행동을 기록하고, 간결하게 설명.
- 긴 작업은 지원 호스트의 worker에 위임하거나 짧은 단위로 나누어 응답. 선택형 실행 큐로 인계·결과·복구 상태 보존.

작업 관리 워크플로우이며 의료적 ADHD 진단·치료 기능은 제공하지 않습니다.

## 연결

이 저장소에는 설치용 플러그인과 마켓플레이스가 있습니다. GitHub에 저장하는 것과 계정에 설치하는 것은 별도 단계입니다. 공개 플러그인 디렉터리에 자동 등록되지는 않습니다.

### ChatGPT에서 GitHub 원본 연결

워크스페이스 관리자에게 **Admin → Plugins → Add → Import marketplace**가 보이면 다음 값으로 가져옵니다.

| 입력 | 값 |
| --- | --- |
| Source | `https://github.com/Snow0821/ADHD` |
| Path | 비워 둠 |
| Branch, tag, or commit | `main` |

가져오기 결과를 확인한 뒤 Plugins에서 **ADHD → + 설치**를 선택하고 새 대화에서 사용합니다. 새 마켓플레이스는 기본적으로 매일 동기화되며, 즉시 반영하려면 **Admin → Plugins → Marketplaces → Sync now**를 사용합니다. 메뉴 접근은 계정·워크스페이스 권한에 따라 달라집니다.

근거: [OpenAI의 GitHub 마켓플레이스 가져오기·동기화 안내](https://learn.chatgpt.com/docs/enterprise/plugin-management).

### Codex 로컬 환경에서 연결

플러그인 명령을 지원하는 Codex CLI가 있는 컴퓨터에서 실행합니다.

```bash
codex plugin marketplace add Snow0821/ADHD --ref main
```

ChatGPT 데스크톱 앱의 Plugins에서 **Snow · ADHD** 소스를 열고 **ADHD**를 설치합니다. 마켓플레이스 식별자는 `personal`, 플러그인 식별자는 `adhd`입니다. 이미 같은 이름의 마켓플레이스가 있다면 기존 설정을 덮어쓰지 말고 충돌을 먼저 확인합니다.

근거: [OpenAI의 로컬·GitHub 마켓플레이스 연결 안내](https://developers.openai.com/plugins/build/plugins#add-a-marketplace-from-the-cli).

계정의 실제 설치·검색 결과는 연결 후 확인해야 합니다. 이 저장소의 검증 성공은 계정 설치 완료를 의미하지 않습니다.

## 사용

새 대화에서 ADHD를 선택하고 다음처럼 요청합니다.

> ADHD로 이 프로젝트의 현재 상태를 확인하고, 다음 목표 하나부터 진행해줘.

실행 환경: **Python 3.10+ / PyYAML / Linux·macOS·WSL**. 파일 잠금에 `fcntl`을 사용하므로 Windows 기본 Python은 현재 지원하지 않습니다. 저장소를 내려받은 환경에서 필요한 의존성을 설치할 수 있습니다.

```bash
python3 -m pip install -r plugins/adhd/requirements.txt
```

작업을 이어가기 위한 최소 실행 상태는 작업공간 `.adhd/`에 저장합니다. 기본 지식 기록은 사용자와 상황을 넘어 재사용할 수 있는 내용으로 제한합니다. 명시적으로 선택한 비공개 DB에는 허용한 범위의 작업 관련 프로젝트 지식도 저장할 수 있지만, 개인 프로필·취향·개인 대화를 무차별 축적하지 않습니다. 임시 실행 환경에서는 별도 저장·복원 절차가 필요합니다. 플러그인 설치만으로 대화 기억, 파일 동기화, 예약 실행, 외부 알림 수신이 자동 구성되지는 않습니다.

공개 저장소에는 코드·작업 규칙·관련된 보편적 지식·일반화된 검증 사례를 둡니다. 개인정보를 지웠다는 이유만으로 보편적 지식으로 간주하지 않으며, 근거와 적용 한계를 함께 남깁니다. 사용자별 연결 설정·프로젝트 지식·비공개 실행 기록은 공개 저장소에 포함하지 않습니다.

기존 개인 ADHD 스킬이 설치되어 있다면 같은 이름의 별도 사본입니다. 플러그인 설치를 확인한 다음 필요에 따라 기존 스킬을 비활성화할 수 있습니다. 이 저장소 업데이트는 기존 개인 스킬을 자동 교체하지 않습니다.

## 선택 기능: worker 실행 큐

긴 작업의 인계와 복구가 필요하면 `.adhd/execution/jobs.sqlite3`에 실행 상태를 따로 보존할 수 있습니다. 기존 작업 큐·스택, 숫자 작업·이력 ID와 DB 지식 기능은 바뀌지 않으며, 기본 사용에는 실행 큐 초기화가 필요 없습니다.

- coordinator가 기존 작업 ID에 job을 연결하고, worker는 자신의 실행 체크포인트·결과만 기록합니다. coordinator가 결과를 검증한 뒤 기존 작업을 완료합니다.
- 실행 job은 높은 우선순위부터, 동순위는 FIFO로 배정합니다. 시도 횟수 제한과 임대·시도별 토큰으로 중복 소유와 늦게 도착한 결과를 방어합니다.
- 임대가 만료되면 외부 작업이 실제로 끝났는지 불확실하므로 차단 상태로 둡니다. 결과 확인과 명시적인 안전 재시도 판단 없이 외부 효과를 반복하지 않습니다.

큐는 worker를 시작하거나 알림을 보내지 않습니다. 호스트에 허용된 worker 기능이 있으면 위임하고, 없으면 짧은 작업 단위 후 체크포인트를 저장하고 응답합니다. 호스트가 멈추거나 절전 상태일 때 계속 실행되는 서비스도, 실시간 완료 보장도 아닙니다.

요청한 리마인더는 긴 작업의 완료를 기다리지 않고 **호스트의 예약 기능에 즉시 등록**해야 합니다. 실제 예약 성공과 호스트 예약 ID를 확인한 경우에만 예약됐다고 알립니다. 로컬 큐에 기록하는 것만으로는 리마인더가 예약되지 않습니다.

[실행 큐 안내](plugins/adhd/skills/adhd/references/execution.md)에 따라 실제 설치 사본의 `ADHD_SCRIPT_DIR`와 기존 비공개 런타임 `ADHD_ROOT`를 지정한 뒤 확인합니다.

```bash
python3 "$ADHD_SCRIPT_DIR/execctl.py" version
python3 "$ADHD_SCRIPT_DIR/execctl.py" --root "$ADHD_ROOT" doctor
```

`version`은 런타임을 만들지 않고 `0.5.1`·실행 schema 1·작업 schema 2를 확인합니다. `doctor`는 첫 사용 시 선택형 DB를 만들고 무결성과 만료된 실행 수를 점검하지만 worker 실행·임대 회수는 하지 않습니다. 실행 DB와 이벤트도 비공개 작업 기록이며 공개 저장소에 넣지 않습니다.

## 선택 기능: DB 지식그래프

파일 모드가 기본입니다. 사용자가 저장 대상과 정보 범위를 명시적으로 선택하면, **이미 인증된 Supabase 커넥터**로 Knowledge만 DB에 저장할 수 있습니다. 새 MCP 서버, 브라우저 로그인, API 키 저장은 필요하지 않습니다. Task·Control·History·Unresolved와 프로젝트 산출물은 기존 위치에 유지됩니다.

- DB의 전체 그래프 스냅샷이 기준입니다. 로컬 파일은 검증된 캐시 또는 명시적으로 시작한 편집 초안입니다.
- 리비전 충돌을 검사하고 같은 작업 UUID로 재시도합니다. 게시 후 DB를 다시 읽어 확인해야 작업·프로젝트에서 지식 참조를 사용할 수 있습니다.
- 오류나 연결 중단 시 초안을 보존합니다. 자동 파일 모드 전환은 없으며, DB에 의존하지 않는 로컬 작업만 계속할 수 있습니다.

[설정·이전·복구 절차](plugins/adhd/skills/adhd/references/database.md)를 따라 선택한 DB에 스키마를 준비하고, 원본을 백업한 뒤 연결합니다. 기존 로컬 그래프를 새 원격 그래프로 이전할 때만 의도적으로 `configure --new`를 사용합니다. 연결만으로 기존 데이터를 덮어쓰거나 설치된 개인 스킬을 갱신하지 않습니다.

## 개선 의견

ADHD 자체에 대한 문제나 아이디어를 발견하면 처음 한 번 **정리하지 않음 / 일반화해서 정리** 중 선택을 묻습니다. 선택 전이나 미응답 상태에서는 수집하지 않으며, 현재 작업은 계속합니다. 개인 보관 기능은 제공하지 않습니다.

기록 동의 같은 최소 실행 설정은 Control에, 동의한 보편적 내용만 Knowledge에 남깁니다. 일반화한 내용도 게시할 내용과 공개 대상을 확인한 뒤 전송합니다. 공유된 제안은 [GitHub Issues](https://github.com/Snow0821/ADHD/issues)에서 관리하고, 실제 반영하기로 정한 제안만 작업으로 등록합니다. 기존 개인 보관 설정을 공개 동의로 전환하거나 과거 사용자 기록을 삭제하지 않습니다.

직접 제보하려면 [문제·아이디어 양식](https://github.com/Snow0821/ADHD/issues/new/choose)을 사용할 수 있습니다. 이 기능은 스킬의 작업 절차이며 별도의 상시 수집 서비스가 아닙니다.

## 유지보수

| 경로 | 역할 |
| --- | --- |
| `.agents/plugins/marketplace.json` | 플러그인 검색·설치용 목록 |
| `plugins/adhd/.codex-plugin/plugin.json` | 버전·소개·스킬 경로 |
| `plugins/adhd/skills/adhd/` | ADHD 지침·명령·복구·회귀 테스트의 기준 원본 |
| `plugins/adhd/skills/adhd/references/execution.md` | 선택형 worker 실행 큐·호스트 책임·안전 복구 |
| `plugins/adhd/skills/adhd/assets/knowledge_schema.sql` | 선택한 DB에 적용할 비공개 지식 스키마·함수 |
| `scripts/check.py` | 패키지 구조 및 런타임 검증 |
| `.github/workflows/validate.yml` | 변경 시 자동 검증 |

```bash
python3 scripts/check.py
```

지침이나 동작을 바꾸면 검증 후 버전과 [변경 이력](CHANGELOG.md)을 갱신합니다. GitHub 원본을 먼저 갱신하고 해당 리비전의 검증 결과를 확인합니다. 프로젝트 산출물과 개인 작업 기록은 이 공개 저장소에 포함하지 않습니다.

### 이미 설치한 사본 업데이트

- **워크스페이스 GitHub 가져오기:** Admin → Plugins → Marketplaces에서 해당 소스의 **Sync now**를 선택하고 저장된 결과 보고서를 확인합니다. 오류가 있으면 이전 정상 버전이 유지될 수 있습니다. 고정 커밋을 선택한 소스는 새 커밋을 자동 추적하지 않습니다. [공식 동기화 안내](https://learn.chatgpt.com/docs/enterprise/plugin-management#keep-plugins-up-to-date)
- **Codex Git 마켓플레이스:** `codex plugin marketplace list`로 소스와 이름을 확인한 뒤 `codex plugin marketplace upgrade personal`로 이 저장소의 소스를 새로 고칩니다. `personal`이 실제로 Snow0821/ADHD인지 먼저 확인합니다. 앱을 다시 시작하고 설치 사본을 확인합니다. [공식 CLI 안내](https://developers.openai.com/plugins/build/plugins#add-a-marketplace-from-the-cli)
- **로컬 개발 설치:** 원본 플러그인을 바꾼 뒤 `plugin-creator`의 업데이트 절차를 사용합니다. 수동 구성이라면 마켓플레이스가 가리키는 플러그인 디렉터리를 갱신하고 앱을 다시 시작합니다. [공식 로컬 설치 안내](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)
- **별도 개인 ADHD 스킬:** 플러그인과 독립된 사본입니다. 원본을 백업하고 허용된 방식으로 따로 동기화합니다. 마켓플레이스 갱신만으로 교체됐다고 판단하지 않습니다.

마지막으로 설치 플러그인의 버전(`0.5.1`)을 확인합니다. 별도 개인 스킬은 동기화한 원본 리비전과 파일을 대조하고, 새 대화가 실제로 읽는 사본에서 `execctl.py version`을 실행해 확인합니다. 실행 큐를 사용한다면 기존 비공개 런타임에서 `doctor`도 확인합니다. 저장소 push, 마켓플레이스 sync, 개인 설치 반영은 서로 다른 단계입니다. 여기의 검증 성공은 모든 계정의 자동 업데이트나 공개 디렉터리 등록을 보장하지 않습니다.
