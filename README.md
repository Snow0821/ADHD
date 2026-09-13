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

실제 작업 기록은 사용자의 지속 보존되는 비공개 작업공간 `.adhd/`에 저장합니다. 임시 실행 환경에서는 별도 저장·복원 절차가 필요합니다. 플러그인 설치만으로 대화 기억, 파일 동기화, 예약 실행, 외부 알림 수신이 자동 구성되지는 않습니다.

기존 개인 ADHD 스킬이 설치되어 있다면 같은 이름의 별도 사본입니다. 플러그인 설치를 확인한 다음 필요에 따라 기존 스킬을 비활성화할 수 있습니다. 이 저장소 업데이트는 기존 개인 스킬을 자동 교체하지 않습니다.

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
