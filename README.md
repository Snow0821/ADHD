# ADHD

**아이디어를 보존하며 현재 목표를 끝까지 실행하는 ChatGPT Work · Codex 플러그인.**

Stay focused on the current purpose while preserving creativity.

[![Validate](https://github.com/Snow0821/ADHD/actions/workflows/validate.yml/badge.svg)](https://github.com/Snow0821/ADHD/actions/workflows/validate.yml)

## 하는 일

- 하나의 목표를 진행하고, 새 작업은 FIFO 큐에 기록.
- 현재 목표를 막는 선행 과제는 LIFO 스택으로 처리한 뒤 원래 작업 재개.
- 지식그래프, 현재 규칙·결정, 완료 이력, 미해결 질문 보존.
- 중단 전 진행 상황과 다음 행동을 기록하고, 간결하게 설명.

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

작업을 이어가기 위한 최소 실행 상태는 작업공간 `.adhd/`에 저장합니다. 지식은 사용자와 상황을 넘어 재사용할 수 있는 내용만 기록하며, 개인 프로필·취향·개인 대화·개인별 프로젝트 사실은 지식으로 축적하지 않습니다. 임시 실행 환경에서는 별도 저장·복원 절차가 필요합니다. 플러그인 설치만으로 대화 기억, 파일 동기화, 예약 실행, 외부 알림 수신이 자동 구성되지는 않습니다.

공개 저장소에는 코드·작업 규칙·관련된 보편적 지식·일반화된 검증 사례를 둡니다. 개인정보를 지웠다는 이유만으로 보편적 지식으로 간주하지 않으며, 근거와 적용 한계를 함께 남깁니다. 개인화는 향후 사용자가 선택한 외부 DB를 연결하는 별도 확장으로 검토합니다. 현재는 DB 선정·연결·개인화용 저장 구조를 구현하지 않습니다.

기존 개인 ADHD 스킬이 설치되어 있다면 같은 이름의 별도 사본입니다. 플러그인 설치를 확인한 다음 필요에 따라 기존 스킬을 비활성화할 수 있습니다. 이 저장소 업데이트는 기존 개인 스킬을 자동 교체하지 않습니다.

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
| `scripts/check.py` | 패키지 구조 및 런타임 검증 |
| `.github/workflows/validate.yml` | 변경 시 자동 검증 |

```bash
python3 scripts/check.py
```

지침이나 동작을 바꾸면 검증 후 버전과 [변경 이력](CHANGELOG.md)을 갱신합니다. 이미 설치된 환경은 연결 방식에 맞춰 동기화·업데이트하고 새 대화에서 확인합니다. 프로젝트 산출물과 개인 작업 기록은 이 공개 저장소에 포함하지 않습니다.
