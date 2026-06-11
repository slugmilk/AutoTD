# AutoTD Web Bridge

기획안 기준으로 먼저 필요한 `웹 -> TouchDesigner` 연결 단계만 정리한 최소 동작본입니다.  
템플릿 제작이 끝나기 전에도, 웹에서 프롬프트와 기본 파라미터를 보내고 TouchDesigner에서 응답을 다시 웹으로 돌려보는 흐름을 바로 확인할 수 있습니다.

## 구성

- `index.html`: 입력 UI, 연결 상태, 파라미터 매핑, 미리보기, 로그
- `styles.css`: MVP 데모용 화면 스타일
- `app.js`: WebSocket 연결과 메시지 처리
- `touchdesigner/autotd_web_callbacks.py`: TouchDesigner `Web Server DAT`에 붙일 콜백 예시

## 왜 이 방식인가

이번 단계에서는 백엔드 서버를 따로 두지 않고, 브라우저가 TouchDesigner의 `Web Server DAT`에 직접 WebSocket으로 붙습니다.  
그래서 지금은 다음 흐름만 먼저 안정적으로 확인할 수 있습니다.

1. 웹에서 프롬프트 입력
2. TouchDesigner로 JSON 전송
3. TouchDesigner에서 프롬프트 해석 또는 템플릿 매핑
4. 상태 / 파라미터 / 미리보기 정보를 다시 웹으로 반환

## 웹 실행

정적 페이지라서 바로 열어도 되지만, 로컬 서버로 여는 편이 안정적입니다.

### 방법 1

작업 폴더에서 아래 명령 실행:

```powershell
python -m http.server 8080
```

그 다음 브라우저에서 아래 주소 열기:

```text
http://127.0.0.1:8080/autotd-web-bridge/
```

### 방법 2

`index.html`을 브라우저로 직접 열기

## GitHub Pages 배포

이 폴더는 GitHub Pages로 정적 배포할 수 있습니다.  
다만 이 프로젝트의 핵심 연결은 브라우저가 `ws://127.0.0.1:9980/autotd`로 직접 붙는 구조라서, **GitHub Pages 배포본은 외부 공유용 UI 데모로는 적합하지만 TouchDesigner live 연결은 보장하지 않습니다.**

이유:

1. GitHub Pages는 `https://`로 열립니다.
2. 현재 TouchDesigner 브리지는 기본적으로 `ws://127.0.0.1:9980`을 사용합니다.
3. 대부분의 브라우저는 `https` 페이지에서 `ws://` localhost 연결을 제한하거나 차단합니다.

즉:

- **로컬 테스트용**
  - `http://127.0.0.1:8080/autotd-web-bridge/`
- **외부 공유용**
  - GitHub Pages URL
- **외부 공유 링크에서도 live TD 연결까지 원할 때**
  - 별도 백엔드 중계 서버 또는 `wss://` 보안 프록시가 필요합니다.

## TouchDesigner 설정

### 1. Web Server DAT 생성

- 네트워크에 `Web Server DAT`를 하나 만듭니다.
- 이름 예시: `autotd_web_server`
- `Port`: `9980`
- `Active`: On

### 2. Callbacks DAT 연결

- Text DAT 하나를 만들고 이름을 예시로 `autotd_callbacks`로 둡니다.
- 이 폴더의 `touchdesigner/autotd_web_callbacks.py` 내용을 붙여넣습니다.
- `Web Server DAT`의 `Callbacks DAT` 파라미터에 이 DAT를 연결합니다.

### 3. 웹 연결

웹의 WebSocket 주소를 아래처럼 둡니다.

```text
ws://127.0.0.1:9980/autotd
```

`연결` 버튼을 누르면 `hello` 메시지가 전달되고, TouchDesigner가 `hello_ack`를 돌려줍니다.

## 현재 메시지 포맷

### 웹 -> TouchDesigner

#### hello

```json
{
  "type": "hello",
  "client": "autotd-web-bridge",
  "version": 1
}
```

#### ping

```json
{
  "type": "ping"
}
```

#### generate

```json
{
  "type": "generate",
  "prompt": "어두운 공간에서 입자가 천천히 모였다가 퍼지는 미디어아트",
  "mood": "calm",
  "motionSpeed": "medium",
  "effectDensity": "medium",
  "duration": 24,
  "templateHint": "particle_field 우선"
}
```

### TouchDesigner -> 웹

#### hello_ack

세션 연결 확인

#### status

진행 상태 표시

#### mapping

TouchDesigner가 해석한 파라미터 JSON

#### preview

아래 중 하나를 보내면 웹에 표시됩니다.

- `previewText`
- `previewUrl`
- `imageUrl`
- `videoUrl`

#### error

오류 메시지

## TouchDesigner에서 추가로 해볼 것

템플릿이 아직 없으니, 지금은 `generate` 수신 시 아래 둘만 먼저 연결하면 충분합니다.

1. 프롬프트를 DAT에 저장
2. 간단한 매핑 결과를 JSON으로 웹에 반환

그 다음 단계에서 아래를 이어 붙이면 됩니다.

1. 템플릿별 COMP 또는 TOX 선택
2. 파라미터 주입
3. 렌더 시작
4. MP4 또는 이미지 경로를 웹으로 전달

## 콜백 DAT 재사용

콜백 DAT 내부에는 현재 접속한 클라이언트로 브로드캐스트하는 함수가 포함되어 있습니다.  
그래서 TouchDesigner 안의 다른 DAT나 Execute DAT에서 아래처럼 웹으로 상태를 밀어줄 수 있습니다.

```python
op('autotd_callbacks').module.broadcast_status('rendering', '렌더를 시작했습니다.', level='busy')
```

또는:

```python
op('autotd_callbacks').module.broadcast_preview(
    previewText='렌더 중입니다. 첫 프레임이 준비되면 이미지 URL을 보냅니다.'
)
```
