맞아요. 지금 PC/Codex 환경에서 `git` 명령이 안 잡힙니다. 그래서 GitHub marketplace를 추가하려고 할 때 `git clone ... program not found`가 난 거예요.

해결은 둘 중 하나입니다.

1. **Git for Windows 설치**
   - [Git for Windows](https://git-scm.com/download/win) 설치
   - 설치 중 `Git from the command line and also from 3rd-party software` 선택
   - Codex 앱 완전히 껐다가 다시 켜기
   - 확인: `git --version`

2. **Git 없이 로컬 marketplace로 설치**
   - GitHub에서 repo를 ZIP으로 다운로드
   - 압축 풀어서 예를 들면:
     ```text
     C:\Users\chuum\PersonalWikiSkill\
       .agents\plugins\marketplace.json
       plugins\personalwiki-skill\...
     ```
   - Codex에서 로컬 marketplace 경로로 추가:
     ```powershell
     codex plugin marketplace add C:\Users\chuum\PersonalWikiSkill
     ```
   - 그 다음:
     ```powershell
     codex plugin add personalwiki-skill@personal
     ```

제일 깔끔한 건 1번입니다. Codex의 GitHub marketplace 설치는 내부적으로 `git clone`을 쓰는 흐름이라, Git CLI가 PATH에 있어야 합니다.
