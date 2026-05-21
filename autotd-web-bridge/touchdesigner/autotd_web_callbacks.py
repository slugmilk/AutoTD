import json
from datetime import datetime
from pathlib import Path


PREVIEW_FILE = Path(r"C:\Users\kelly\OneDrive\문서\New project\autotd-web-bridge\preview\out1.png")
PREVIEW_URL = "http://127.0.0.1:8080/autotd-web-bridge/preview/out1.png"
ALLOWED_TEMPLATES = {
    "particle_field",
    "feedback_trails",
    "simple_3d_scene",
}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _remember_server(dat):
    me.store("autotd_server_path", dat.path)


def _server_from_storage():
    server_path = me.fetch("autotd_server_path", None)
    if not server_path:
        return None
    return op(server_path)


def _safe_op(path):
    try:
        return op(path)
    except Exception:
        return None


def _send_json(dat, client, payload):
    dat.webSocketSendText(client, json.dumps(payload, ensure_ascii=False))


def _broadcast(payload):
    dat = _server_from_storage()
    if dat is None:
        return False

    for client in dat.webSocketConnections:
        dat.webSocketSendText(client, json.dumps(payload, ensure_ascii=False))
    return True


def _pick_palette(prompt, mood):
    joined = f"{prompt or ''} {mood or ''}".lower()

    if any(keyword in joined for keyword in ("warm", "ember", "ritual", "fire", "red", "orange")):
        return "ember"
    if any(keyword in joined for keyword in ("forest", "organic", "green", "nature", "plant")):
        return "moss"
    if any(keyword in joined for keyword in ("dream", "night", "cool", "blue", "purple")):
        return "nocturne"
    return "chalk"


def _resolve_template(payload):
    template_type = payload.get("templateType", "particle_field")
    if template_type in ALLOWED_TEMPLATES:
        return template_type
    return "particle_field"


def _build_mapping(payload):
    prompt = payload.get("prompt", "")
    mood = payload.get("mood", "calm")

    return {
        "template_type": _resolve_template(payload),
        "mood": mood,
        "palette": _pick_palette(prompt, mood),
        "motion_speed": payload.get("motionSpeed", "medium"),
        "effect_density": payload.get("effectDensity", "medium"),
        "duration": payload.get("duration", 24),
        "camera_style": "slow_push",
    }


def _export_preview():
    out_top = _safe_op("out1")
    if out_top is None:
        return None, "out1 TOP을 찾을 수 없습니다."

    try:
        out_top.cook(force=True)
    except Exception:
        pass

    try:
        out_top.save(str(PREVIEW_FILE), asynchronous=False, createFolders=True)
    except Exception as error:
        return None, f"out1 미리보기 저장 실패: {error}"

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{PREVIEW_URL}?t={timestamp}", None


def _write_debug_dats(prompt, mapping):
    prompt_dat = _safe_op("prompt_in")
    mapping_dat = _safe_op("parsed_params")
    status_dat = _safe_op("render_status")

    if prompt_dat is not None:
        prompt_dat.text = prompt

    if mapping_dat is not None:
        mapping_dat.text = json.dumps(mapping, ensure_ascii=False, indent=2)

    if status_dat is not None:
        status_dat.text = f"updated {_now()}"


def _handle_generate(dat, client, payload):
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        _send_json(dat, client, {
            "type": "error",
            "message": "prompt 값이 비어 있습니다.",
            "timestamp": _now(),
        })
        return

    mapping = _build_mapping(payload)
    _write_debug_dats(prompt, mapping)

    _send_json(dat, client, {
        "type": "status",
        "state": "received",
        "stateLabel": "요청 수신",
        "detail": "프롬프트와 선택 템플릿을 받았습니다.",
        "level": "busy",
        "timestamp": _now(),
    })

    _send_json(dat, client, {
        "type": "mapping",
        "parameters": mapping,
        "prompt": prompt,
        "timestamp": _now(),
    })

    preview_url, preview_error = _export_preview()
    if preview_url:
        _send_json(dat, client, {
            "type": "preview",
            "previewUrl": preview_url,
            "previewText": (
                f"선택 템플릿: {mapping['template_type']}\n"
                f"무드: {mapping['mood']} / 팔레트: {mapping['palette']}"
            ),
            "timestamp": _now(),
        })
    else:
        _send_json(dat, client, {
            "type": "preview",
            "previewText": (
                "이미지 미리보기를 아직 만들지 못했습니다.\n"
                f"{preview_error}\n\n"
                f"선택 템플릿: {mapping['template_type']}\n"
                f"무드: {mapping['mood']}\n"
                f"팔레트: {mapping['palette']}\n"
                f"프롬프트: {prompt}"
            ),
            "timestamp": _now(),
        })
        return

    _send_json(dat, client, {
        "type": "status",
        "state": "ready_for_template",
        "stateLabel": "템플릿 연결 대기",
        "detail": "out1 미리보기를 웹에 전달했습니다.",
        "level": "ok",
        "timestamp": _now(),
    })


def broadcast(payload):
    payload.setdefault("timestamp", _now())
    return _broadcast(payload)


def broadcast_status(state, detail="", level="ok"):
    return _broadcast({
        "type": "status",
        "state": state,
        "stateLabel": state,
        "detail": detail,
        "level": level,
        "timestamp": _now(),
    })


def broadcast_preview(previewText=None, previewUrl=None, imageUrl=None, videoUrl=None):
    return _broadcast({
        "type": "preview",
        "previewText": previewText,
        "previewUrl": previewUrl,
        "imageUrl": imageUrl,
        "videoUrl": videoUrl,
        "timestamp": _now(),
    })


def onHTTPRequest(dat, request, response):
    _remember_server(dat)
    response["statusCode"] = 200
    response["statusReason"] = "OK"
    response["content-type"] = "application/json; charset=utf-8"
    response["data"] = json.dumps({
        "service": "AutoTD Web Bridge",
        "status": "ok",
        "uri": request.get("uri"),
        "timestamp": _now(),
    }, ensure_ascii=False)
    return response


def onWebSocketOpen(dat, client, uri):
    _remember_server(dat)
    _send_json(dat, client, {
        "type": "hello_ack",
        "sessionId": client,
        "statusLabel": "TouchDesigner 연결됨",
        "uri": uri,
        "timestamp": _now(),
    })
    _send_json(dat, client, {
        "type": "status",
        "state": "connected",
        "stateLabel": "연결 완료",
        "detail": "Web Server DAT가 새 클라이언트를 받았습니다.",
        "level": "ok",
        "timestamp": _now(),
    })

    if PREVIEW_FILE.exists():
        _send_json(dat, client, {
            "type": "preview",
            "previewUrl": f"{PREVIEW_URL}?t={datetime.now().strftime('%Y%m%d%H%M%S')}",
            "previewText": "최근 out1 미리보기를 불러왔습니다.",
            "timestamp": _now(),
        })
    return


def onWebSocketClose(dat, client):
    _remember_server(dat)
    return


def onWebSocketReceiveText(dat, client, data):
    _remember_server(dat)

    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        _send_json(dat, client, {
            "type": "error",
            "message": "JSON 파싱에 실패했습니다.",
            "raw": data,
            "timestamp": _now(),
        })
        return

    message_type = payload.get("type")

    if message_type == "hello":
        _send_json(dat, client, {
            "type": "hello_ack",
            "sessionId": client,
            "statusLabel": "핸드셰이크 완료",
            "supported": ["hello", "ping", "generate"],
            "timestamp": _now(),
        })
        return

    if message_type == "ping":
        _send_json(dat, client, {
            "type": "status",
            "state": "pong",
            "stateLabel": "응답 확인",
            "detail": "TouchDesigner가 연결 상태를 응답했습니다.",
            "level": "ok",
            "timestamp": _now(),
        })
        return

    if message_type == "generate":
        _handle_generate(dat, client, payload)
        return

    _send_json(dat, client, {
        "type": "error",
        "message": f"지원하지 않는 메시지 타입입니다: {message_type}",
        "timestamp": _now(),
    })


def onWebSocketReceiveBinary(dat, client, data):
    _remember_server(dat)
    _send_json(dat, client, {
        "type": "error",
        "message": "현재는 텍스트 메시지만 처리합니다.",
        "timestamp": _now(),
    })
    return


def onServerStart(dat):
    _remember_server(dat)
    return


def onServerStop(dat):
    _remember_server(dat)
    return
