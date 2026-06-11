import logging
import asyncio
import json
import os
import shutil
import socket
import tempfile
from pathlib import Path

import httpx
import trimesh
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from llm_router import LLMRouter
from schema import GenerateRequest, GenerateResponse, StatusResponse, TDParameters, AssetPlan, SourceOptions
from tool_image import LocalImageTool
from tool_trellis import TrellisTool

load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent
TD_ASSET_DIR = Path(tempfile.gettempdir()) / 'autotd_assets'
TD_HOST = os.getenv('TD_HOST', '127.0.0.1')
TD_PORT = os.getenv('TD_PORT', '9980')
TD_BASE_URL = f'http://{TD_HOST}:{TD_PORT}'
EXTERNAL_SOURCE_GENERATION_ENABLED = os.getenv('AUTOTD_ENABLE_EXTERNAL_SOURCES', '0').lower() in {'1', 'true', 'yes'}
MVP_FAST_RECIPE_MODE = os.getenv('AUTOTD_MVP_FAST_RECIPE_MODE', '1').lower() in {'1', 'true', 'yes'}

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('autotd.server')

llm_router = LLMRouter()
image_tool = LocalImageTool()
trellis_tool = TrellisTool()
source_generation_lock = asyncio.Lock()


class TrellisGenerateRequest(BaseModel):
    prompt: str = ''
    image_path: str = ''
    seed: int = Field(default=0, ge=0)
    simplify: float = Field(default=0.95, ge=0.0, le=1.0)
    texture_size: int = Field(default=1024, ge=256, le=4096)


def _inspect_obj_sidecars(obj_path: str) -> dict:
    obj_p = Path(obj_path)
    parent_dir = obj_p.parent
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}
    mtl_candidates = [
        obj_p.with_suffix('.mtl'),
        obj_p.with_name(obj_p.name + '.mtl'),
        parent_dir / 'material.mtl',
    ]
    mtl_files = [p.as_posix() for p in mtl_candidates if p.exists() and p.stat().st_size > 0]
    texture_files = [
        f.as_posix()
        for f in parent_dir.iterdir()
        if f.is_file() and f.suffix.lower() in image_extensions
    ]
    return {
        'obj_path': obj_p.as_posix(),
        'mtl_files': mtl_files,
        'texture_files': texture_files,
        'has_mtl': bool(mtl_files),
        'has_texture': bool(texture_files),
    }


def _mirror_asset_for_td(path: str) -> str:
    """Copy generated assets to an ASCII-only temp path for TouchDesigner."""
    if not path:
        return ''
    src = Path(path)
    if not src.is_absolute():
        src = BASE_DIR / src
    if not src.exists():
        return path

    TD_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    dst = TD_ASSET_DIR / src.name
    shutil.copy2(src, dst)

    if src.suffix.lower() == '.obj':
        for sidecar in src.parent.iterdir():
            if sidecar.suffix.lower() in {'.mtl', '.png', '.jpg', '.jpeg', '.bmp', '.webp'}:
                try:
                    shutil.copy2(sidecar, TD_ASSET_DIR / sidecar.name)
                except Exception as exc:
                    log.warning(f"Could not mirror OBJ sidecar {sidecar}: {exc}")

    return dst.as_posix()


def _convert_glb_to_obj(glb_path: str) -> tuple[str, dict]:
    if not glb_path:
        return '', {'error': 'missing glb_path'}
    try:
        glb_p = Path(glb_path)
        if not glb_p.exists():
            return '', {'error': f'glb not found: {glb_path}'}
        obj_p = glb_p.with_suffix('.obj')
        log.info(f"[AutoTD] Converting GLB to OBJ: {glb_p.as_posix()} -> {obj_p.as_posix()}")
        
        # Load using trimesh
        scene = trimesh.load(str(glb_p))
        
        # Exporting scene writes .obj, .mtl and related textures to the same folder automatically
        scene.export(str(obj_p))
        
        sidecars = _inspect_obj_sidecars(obj_p.as_posix())
        
        if not sidecars['has_mtl'] or not sidecars['has_texture']:
            log.warning(
                "[AutoTD] Texture loss warning: .mtl exists=%s, texture images count=%s",
                sidecars['has_mtl'],
                len(sidecars['texture_files']),
            )
        else:
            log.info(
                "[AutoTD] Successfully exported OBJ assets (mtl and %s textures included) to %s",
                len(sidecars['texture_files']),
                obj_p.as_posix(),
            )
            
        return obj_p.as_posix(), sidecars
    except Exception as e:
        log.error(f"[AutoTD] Failed to convert GLB to OBJ: {e}")
        return '', {'error': str(e)}


class ImageGenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ''
    workflow: dict | None = None


app = FastAPI(title='AutoTD Web API', version='1.2.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

(BASE_DIR / 'generated_assets').mkdir(exist_ok=True)
app.mount('/generated_assets', StaticFiles(directory=BASE_DIR / 'generated_assets'), name='generated_assets')


def _asset_url(path: str) -> str:
    if not path:
        return ''
    try:
        asset_path = Path(path)
        if asset_path.is_absolute():
            asset_path = asset_path.resolve()
            assets_dir = (BASE_DIR / 'generated_assets').resolve()
            if assets_dir in asset_path.parents or asset_path == assets_dir:
                return f'/generated_assets/{asset_path.name}'
    except Exception:
        pass
    return ''


def _parse_td_message(message: str) -> dict:
    try:
        data = json.loads(message)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _split_resolution(value: str | None) -> tuple[int, int]:
    if not value:
        return 0, 0
    try:
        left, right = str(value).lower().split('x', 1)
        return int(float(left)), int(float(right))
    except Exception:
        return 0, 0


def _normalize_td_telemetry(params: TDParameters, td_applied: dict) -> dict:
    raw = dict(td_applied or {})
    plan = raw.get('mcp_plan') if isinstance(raw.get('mcp_plan'), dict) else {}
    nested = raw.get('telemetry') if isinstance(raw.get('telemetry'), dict) else {}
    width, height = _split_resolution(raw.get('image_resolution') or plan.get('image_resolution'))
    failed_nodes = []
    cook_errors = []
    for source in (nested, plan):
        if isinstance(source.get('failed_nodes'), list):
            failed_nodes.extend(source['failed_nodes'])
        if isinstance(source.get('cook_errors'), list):
            cook_errors.extend(source['cook_errors'])

    obj_path = params.asset_3d_path if params.asset_3d_path.lower().endswith('.obj') else ''
    glb_path = params.asset_3d_glb_path or (params.asset_3d_path if params.asset_3d_path.lower().endswith('.glb') else '')
    final_output_top = raw.get('output_top') or plan.get('final_output_top') or plan.get('output_top') or plan.get('out1') or ''
    composite_status = raw.get('composite_status') or plan.get('composite_status') or ''

    telemetry = {
        'obj_path': obj_path,
        'glb_path': glb_path,
        'image_path': params.asset_image_path,
        'td_model_loaded': bool(raw.get('td_model_loaded') or raw.get('load_success') or plan.get('td_model_loaded') or plan.get('mesh_load_success')),
        'td_image_loaded': bool(raw.get('td_image_loaded') or plan.get('td_image_loaded') or (width > 0 and height > 0)),
        'point_count': int(raw.get('point_count') or plan.get('point_count') or 0),
        'primitive_count': int(raw.get('primitive_count') or plan.get('primitive_count') or 0),
        'image_width': int(raw.get('image_width') or plan.get('image_width') or width),
        'image_height': int(raw.get('image_height') or plan.get('image_height') or height),
        'geometry_bbox': raw.get('geometry_bbox') or plan.get('geometry_bbox') or {},
        'normalized_scale': raw.get('normalized_scale') or plan.get('normalized_scale') or 1.0,
        'camera_distance': raw.get('camera_distance') or plan.get('camera_distance') or 0.0,
        'composite_connected': bool(raw.get('composite_connected') or plan.get('composite_connected') or composite_status == 'connected'),
        'render_top': raw.get('render_top') or plan.get('render_top') or '',
        'final_output_top': final_output_top,
        'failed_nodes': failed_nodes,
        'cook_errors': cook_errors,
        'image_usage': raw.get('image_usage') or plan.get('image_usage') or 'none',
    }
    return telemetry


def _collect_source_assets(params: TDParameters, asset_plan: AssetPlan) -> list[dict]:
    assets = []
    if params.asset_image_path:
        assets.append({
            'kind': 'image',
            'path': params.asset_image_path,
            'url': _asset_url(params.asset_image_path),
            'prompt': asset_plan.image_prompt or asset_plan.trellis_prompt,
            'td_role': 'moviefilein source_image',
        })
    if params.asset_3d_path:
        assets.append({
            'kind': '3d',
            'path': params.asset_3d_path,
            'url': _asset_url(params.asset_3d_path),
            'prompt': asset_plan.trellis_prompt,
            'td_role': 'file SOP mesh_source',
        })
    return assets


async def send_to_touchdesigner(params: dict) -> tuple[str, str]:
    payload = dict(params)
    payload['action'] = 'generate'
    td_timeout = float(os.getenv('AUTOTD_TD_SEND_TIMEOUT', '20'))
    body = json.dumps(payload).encode('utf-8')

    try:
        _headers, response_body = await asyncio.to_thread(
            _raw_http_request,
            'POST',
            '/generate',
            body,
            'application/json',
            td_timeout,
        )
        message = response_body.decode('utf-8', errors='replace').strip()
        if not message:
            return 'sent', json.dumps({'applied': {'recipe_id': payload.get('recipe_id'), 'warning': 'Empty TD response body'}})
        return 'sent', message
    except Exception as exc:
        return 'error', str(exc)


def _raw_http_request(method: str, path: str, body: bytes = b'', content_type: str = 'application/json', timeout: float = 5.0) -> tuple[dict[str, str], bytes]:
    headers = (
        f'{method} {path} HTTP/1.1\r\n'
        f'Host: {TD_HOST}:{TD_PORT}\r\n'
        'Connection: close\r\n'
    )
    if body:
        headers += f'Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n'
    request = (headers + '\r\n').encode('ascii') + body
    chunks: list[bytes] = []
    with socket.create_connection((TD_HOST, int(TD_PORT)), timeout=timeout) as sock:
        sock.settimeout(0.5)
        sock.sendall(request)
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
    raw = b''.join(chunks)
    head, _, response_body = raw.partition(b'\r\n\r\n')
    parsed_headers: dict[str, str] = {}
    for line in head.split(b'\r\n')[1:]:
        if b':' in line:
            key, value = line.split(b':', 1)
            parsed_headers[key.decode('latin1').lower()] = value.strip().decode('latin1')
    return parsed_headers, response_body


async def _release_generation_memory(label: str) -> None:
    import gc

    gc.collect()
    await asyncio.sleep(float(os.getenv('AUTOTD_SOURCE_QUEUE_PAUSE', '1.0')))
    log.info("[AutoTD] Source generation queue released after %s", label)


def _initial_source_status(asset_plan: AssetPlan) -> dict:
    return {
        'image': {
            'requested': bool(asset_plan.needs_image_asset),
            'status': 'requested' if asset_plan.needs_image_asset else 'not_requested',
            'prompt': asset_plan.image_prompt,
            'usage': asset_plan.image_usage,
            'path': '',
            'loaded': False,
            'error': '',
        },
        'object_3d': {
            'requested': bool(asset_plan.needs_3d_asset),
            'status': 'requested' if asset_plan.needs_3d_asset else 'not_requested',
            'prompt': asset_plan.trellis_prompt,
            'object_description': asset_plan.object_description,
            'usage': asset_plan.asset_usage,
            'path': '',
            'glb_path': '',
            'obj_path': '',
            'loaded': False,
            'error': '',
        },
    }


def _mvp_source_status() -> dict:
    return {
        'mode': 'procedural_recipe_mvp',
        'procedural_recipe': {
            'requested': True,
            'status': 'active',
            'label': 'Procedural TouchDesigner Recipe',
        },
        'image': {
            'requested': False,
            'status': 'experimental',
            'label': 'External Image Source',
            'tool': 'OpenAI Images API',
            'loaded': False,
            'path': '',
            'error': '',
        },
        'object_3d': {
            'requested': False,
            'status': 'experimental',
            'label': 'External 3D Source',
            'tool': 'Trellis',
            'loaded': False,
            'path': '',
            'obj_path': '',
            'glb_path': '',
            'point_count': 0,
            'primitive_count': 0,
            'error': '',
        },
        'source_options': {'generate_image': False, 'generate_3d': False},
        'source_generation_errors': [],
        'note': 'External OpenAI image/Trellis generation is disabled for the presentation MVP.',
    }


def _merge_td_source_status(source_status: dict, telemetry: dict) -> dict:
    image = source_status.setdefault('image', {})
    mesh = source_status.setdefault('object_3d', {})
    if image.get('requested'):
        image['loaded'] = bool(telemetry.get('td_image_loaded'))
        image['width'] = int(telemetry.get('image_width') or 0)
        image['height'] = int(telemetry.get('image_height') or 0)
        if image.get('status') == 'generated':
            image['status'] = 'loaded' if image['loaded'] else 'failed'
        if not image['loaded'] and not image.get('error'):
            image['error'] = 'TouchDesigner did not load the generated image source.'
    if mesh.get('requested'):
        mesh['loaded'] = bool(telemetry.get('td_model_loaded'))
        mesh['point_count'] = int(telemetry.get('point_count') or 0)
        mesh['primitive_count'] = int(telemetry.get('primitive_count') or 0)
        if mesh.get('status') == 'generated':
            mesh['status'] = 'loaded' if mesh['loaded'] else 'failed'
        if not mesh['loaded'] and not mesh.get('error'):
            mesh['error'] = 'TouchDesigner did not load the generated 3D source.'
    errors = telemetry.get('failed_nodes') or telemetry.get('cook_errors') or []
    if errors:
        source_status['touchdesigner_errors'] = errors
    return source_status


def _source_generation_errors(source_status: dict, telemetry: dict | None = None) -> list[str]:
    errors = []
    for key in ('image', 'object_3d'):
        entry = source_status.get(key, {}) if isinstance(source_status, dict) else {}
        if entry.get('requested') and entry.get('error'):
            errors.append(f"{key}: {entry['error']}")
        elif entry.get('requested') and entry.get('status') == 'failed':
            errors.append(f"{key}: requested source failed")

    if telemetry:
        for err in telemetry.get('failed_nodes') or telemetry.get('cook_errors') or []:
            if isinstance(err, dict):
                message = err.get('message') or err.get('reason') or str(err)
            else:
                message = str(err)
            if message and message not in errors:
                errors.append(f"touchdesigner: {message}")
    return errors


def _flat_source_status(req_source_options: SourceOptions, source_status: dict, telemetry: dict, params: TDParameters) -> dict:
    image = source_status.get('image', {}) if isinstance(source_status, dict) else {}
    mesh = source_status.get('object_3d', {}) if isinstance(source_status, dict) else {}
    image_requested = bool(req_source_options.generate_image)
    mesh_requested = bool(req_source_options.generate_3d)
    image_generated = bool(image.get('path') or params.asset_image_path)
    mesh_obj_path = mesh.get('obj_path') or (params.asset_3d_path if params.asset_3d_path.lower().endswith('.obj') else '')
    mesh_glb_path = mesh.get('glb_path') or params.asset_3d_glb_path
    mesh_generated = bool(mesh_obj_path or mesh_glb_path)
    image_loaded = bool(image_requested and telemetry.get('td_image_loaded'))
    mesh_loaded = bool(mesh_requested and telemetry.get('td_model_loaded'))
    errors = _source_generation_errors(source_status, telemetry)
    if image_requested and not image_generated:
        errors.append(image.get('error') or 'Image source was requested but OpenAI Images API did not generate an image.')
    elif image_requested and not image_loaded:
        errors.append(image.get('error') or 'Image source was generated but TouchDesigner did not load it.')
    if mesh_requested and not mesh_generated:
        errors.append(mesh.get('error') or '3D source was requested but Trellis did not generate an OBJ/GLB asset.')
    elif mesh_requested and not mesh_loaded:
        errors.append(mesh.get('error') or '3D source was generated but TouchDesigner did not load it.')
    errors = list(dict.fromkeys(str(error) for error in errors if error))

    return {
        'source_options': req_source_options.model_dump(),
        'image_asset_requested': image_requested,
        'image_asset_generated': image_generated,
        'image_asset_path': image.get('path') or params.asset_image_path,
        'image_loaded_in_td': image_loaded,
        'asset_3d_requested': mesh_requested,
        'asset_3d_generated': mesh_generated,
        'asset_3d_obj_path': mesh_obj_path,
        'asset_3d_glb_path': mesh_glb_path,
        'asset_3d_loaded_in_td': mesh_loaded,
        'point_count': int(telemetry.get('point_count') or 0),
        'primitive_count': int(telemetry.get('primitive_count') or 0),
        'source_generation_errors': errors,
    }


def _source_status_from_flat(flat: dict, source_status: dict) -> dict:
    source_status = dict(source_status or {})
    image = dict(source_status.get('image', {}))
    mesh = dict(source_status.get('object_3d', {}))
    image.update({
        'requested': bool(flat['image_asset_requested']),
        'generated': bool(flat['image_asset_generated']),
        'path': flat['image_asset_path'],
        'loaded': bool(flat['image_loaded_in_td']),
    })
    mesh.update({
        'requested': bool(flat['asset_3d_requested']),
        'generated': bool(flat['asset_3d_generated']),
        'path': flat['asset_3d_obj_path'] or flat['asset_3d_glb_path'],
        'obj_path': flat['asset_3d_obj_path'],
        'glb_path': flat['asset_3d_glb_path'],
        'loaded': bool(flat['asset_3d_loaded_in_td']),
        'point_count': flat['point_count'],
        'primitive_count': flat['primitive_count'],
    })
    if image['requested']:
        image['status'] = 'loaded' if image['loaded'] else 'generated' if image['generated'] else 'failed'
    else:
        image['status'] = 'not_requested'
    if mesh['requested']:
        mesh['status'] = 'loaded' if mesh['loaded'] else 'generated' if mesh['generated'] else 'failed'
    else:
        mesh['status'] = 'not_requested'
    source_status['image'] = image
    source_status['object_3d'] = mesh
    source_status['source_options'] = flat['source_options']
    source_status['source_generation_errors'] = flat['source_generation_errors']
    if image['requested'] and not image['loaded']:
        image['status'] = 'failed'
        if not image.get('error'):
            image['error'] = 'Image source was requested but was not loaded in TouchDesigner.'
    if mesh['requested'] and not mesh['loaded']:
        mesh['status'] = 'failed'
        if not mesh.get('error'):
            mesh['error'] = '3D source was requested but was not loaded in TouchDesigner.'
    return source_status


def _upsert_asset_node(plan: dict, node: dict, after_id: str | None = None) -> None:
    nodes = plan.setdefault('nodes', [])
    connections = plan.setdefault('connections', [])
    node_id = node['id']
    existing = next((item for item in nodes if item.get('id') == node_id or item.get('type') == node.get('type')), None)
    if existing:
        existing.update(node)
    else:
        insert_at = 0
        if after_id:
            for idx, item in enumerate(nodes):
                if item.get('id') == after_id:
                    insert_at = idx + 1
                    break
        nodes.insert(insert_at, node)

    if node.get('type') in {'source_image', 'source_3d'}:
        return

    if len(nodes) > 1:
        target = None
        for item in nodes:
            if item.get('id') != node_id and item.get('type') != 'final_output':
                target = item.get('id')
                break
        if target and not any(c.get('from') == node_id and c.get('to') == target for c in connections):
            connections.insert(0, {'from': node_id, 'to': target})


def _augment_td_plan_with_assets(asset_plan: AssetPlan, td_payload: dict) -> dict:
    plan = dict(asset_plan.td_mcp_plan or {})
    plan['source_options'] = {
        'generate_image': bool(asset_plan.needs_image_asset),
        'generate_3d': bool(asset_plan.needs_3d_asset),
    }
    if asset_plan.needs_image_asset:
        _upsert_asset_node(plan, {
            'id': 'image_asset_loader',
            'type': 'source_image',
            'name': 'image_asset_loader',
            'params': {},
            'asset_path': td_payload.get('image_asset_path', ''),
        })
    if asset_plan.needs_3d_asset:
        _upsert_asset_node(plan, {
            'id': 'asset_3d_loader',
            'type': 'source_3d',
            'name': 'asset_3d_loader',
            'params': {},
            'asset_path': td_payload.get('asset_3d_obj_path') or td_payload.get('asset_3d_path', ''),
            'asset_3d_glb_path': td_payload.get('asset_3d_glb_path', ''),
        })
    return plan


async def process_assets_and_send(params: TDParameters, asset_plan: AssetPlan) -> tuple[str, str, list[dict], dict, dict]:
    # Current MVP scope: Ollama + optional OpenAI 2D source + TouchDesigner recipes only.
    asset_plan.needs_3d_asset = False
    asset_plan.trellis_prompt = ''
    asset_plan.object_description = ''
    asset_plan.asset_usage = ''

    if not EXTERNAL_SOURCE_GENERATION_ENABLED:
        asset_plan.needs_image_asset = False
        asset_plan.needs_3d_asset = False
        asset_plan.image_prompt = ''
        asset_plan.trellis_prompt = ''
        asset_plan.object_description = ''
        params.asset_image_path = ''
        params.asset_3d_path = ''
        params.asset_3d_glb_path = ''

    image_timeout = float(os.getenv('AUTOTD_IMAGE_TIMEOUT', '90'))
    trellis_timeout = float(os.getenv('AUTOTD_TRELLIS_TIMEOUT', '75'))
    if asset_plan.needs_image_asset and not asset_plan.image_prompt:
        asset_plan.image_prompt = params.description or params.mood or 'abstract black and white media art source image'
    if asset_plan.needs_3d_asset and not asset_plan.trellis_prompt:
        asset_plan.trellis_prompt = asset_plan.object_description or params.description or 'abstract 3D media art object'
    source_status = _initial_source_status(asset_plan)
    if not EXTERNAL_SOURCE_GENERATION_ENABLED:
        source_status = _mvp_source_status()

    async with source_generation_lock:
        if asset_plan.needs_image_asset:
            try:
                source_status['image']['status'] = 'generating'
                log.info(f"Generating image asset: {asset_plan.image_prompt}")
                img_result = await asyncio.wait_for(
                    image_tool.generate_image(
                        prompt=asset_plan.image_prompt,
                        negative_prompt=asset_plan.image_negative_prompt
                    ),
                    timeout=image_timeout,
                )
                if not (img_result.get("success") and img_result.get("images")):
                    raise RuntimeError(str(img_result.get('error') or 'OpenAI Images API returned no image'))
                img_data = img_result["images"][0]
                img_filename = img_data.get("filename", "")
                img_url = img_data.get("url", "")
                img_bytes = None
                if img_data.get('b64_json'):
                    img_bytes = image_tool.decode_image_bytes(img_data)
                elif img_url:
                    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                        img_response = await client.get(img_url)
                    if img_response.status_code != 200:
                        raise RuntimeError(f"OpenAI image download failed: HTTP {img_response.status_code}")
                    img_bytes = img_response.content
                else:
                    raise RuntimeError('OpenAI Images API returned no b64_json or image URL.')
                assets_dir = BASE_DIR / "generated_assets"
                filename = img_filename or 'autotd_openai_image.png'
                try:
                    assets_dir.mkdir(exist_ok=True)
                    local_img_path = assets_dir / filename
                    local_img_path.write_bytes(img_bytes)
                except Exception as write_error:
                    TD_ASSET_DIR.mkdir(exist_ok=True)
                    local_img_path = TD_ASSET_DIR / filename
                    local_img_path.write_bytes(img_bytes)
                    log.warning(f"Project asset write failed; saved image to TD temp asset dir instead: {write_error}")
                params.asset_image_path = local_img_path.as_posix()
                source_status['image']['status'] = 'generated'
                source_status['image']['path'] = params.asset_image_path
                source_status['image']['provider'] = img_result.get('provider', '')
                if img_result.get('warning'):
                    source_status['image']['warning'] = img_result.get('warning')
                log.info(f"Image asset saved and path set to: {params.asset_image_path}")
            except Exception as e:
                source_status['image']['status'] = 'failed'
                source_status['image']['error'] = str(e) or f'{type(e).__name__}: OpenAI Images API did not return an image before the timeout.'
                log.error(f"Failed to generate image asset: {e}")
            finally:
                await _release_generation_memory('OpenAI image generation')

        if asset_plan.needs_3d_asset:
            try:
                source_status['object_3d']['status'] = 'generating'
                log.info(f"Generating 3D asset: {asset_plan.trellis_prompt}")
                trellis_img_path = params.asset_image_path
                if not trellis_img_path:
                    img_result = await asyncio.wait_for(
                        image_tool.generate_image(prompt=asset_plan.trellis_prompt),
                        timeout=image_timeout,
                    )
                    if not (img_result.get("success") and img_result.get("images")):
                        raise RuntimeError(str(img_result.get('error') or 'OpenAI Images API returned no Trellis support image'))
                    img_data = img_result["images"][0]
                    img_filename = img_data.get("filename", "")
                    img_url = img_data.get("url", "")
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        img_response = await client.get(img_url)
                    if img_response.status_code != 200:
                        raise RuntimeError(f"Trellis support image download failed: HTTP {img_response.status_code}")
                    assets_dir = BASE_DIR / "generated_assets"
                    assets_dir.mkdir(exist_ok=True)
                    local_img_path = assets_dir / f"trellis_source_{img_filename}"
                    local_img_path.write_bytes(img_response.content)
                    trellis_img_path = local_img_path.as_posix()

                mesh_result = await asyncio.wait_for(
                    trellis_tool.generate_mesh(
                        prompt=asset_plan.trellis_prompt,
                        image_path=trellis_img_path
                    ),
                    timeout=trellis_timeout,
                )
                if not (mesh_result.get("success") and mesh_result.get("glb_path")):
                    raise RuntimeError(str(mesh_result.get('error') or 'Trellis returned no GLB'))
                local_glb_path = mesh_result["glb_path"]
                assets_dir = BASE_DIR / "generated_assets"
                assets_dir.mkdir(exist_ok=True)
                dest_glb_path = assets_dir / Path(local_glb_path).name
                shutil.copy(local_glb_path, dest_glb_path)
                params.asset_3d_glb_path = dest_glb_path.as_posix()
                source_status['object_3d']['glb_path'] = params.asset_3d_glb_path
                converted_obj, obj_sidecars = _convert_glb_to_obj(dest_glb_path.as_posix())
                if converted_obj:
                    params.asset_3d_path = converted_obj
                    source_status['object_3d']['obj_path'] = converted_obj
                    if not obj_sidecars.get('has_texture') and params.asset_image_path:
                        log.info("[AutoTD] OBJ texture missing; OpenAI image will be available to TouchDesigner as material/background fallback.")
                else:
                    params.asset_3d_path = dest_glb_path.as_posix()
                    log.warning(f"[AutoTD] OBJ conversion failed. Using GLB path as primary fallback: {params.asset_3d_path}")
                source_status['object_3d']['path'] = params.asset_3d_path
                source_status['object_3d']['status'] = 'generated'
            except Exception as e:
                source_status['object_3d']['status'] = 'failed'
                source_status['object_3d']['error'] = str(e)
                log.error(f"Failed to generate 3D asset: {e}")
            finally:
                await _release_generation_memory('Trellis')

    td_payload = params.model_dump()
    td_payload['asset_base_dir'] = TD_ASSET_DIR.as_posix()
    td_payload['recipe_id'] = getattr(params, 'recipe_id', 'dreamy_particle_field')

    if td_payload.get('asset_image_path'):
        try:
            td_payload['asset_image_path'] = _mirror_asset_for_td(td_payload['asset_image_path'])
        except Exception as e:
            log.warning(f"Could not mirror asset_image_path for TouchDesigner: {e}")

    if td_payload.get('asset_3d_path'):
        try:
            td_payload['asset_3d_path'] = _mirror_asset_for_td(td_payload['asset_3d_path'])
        except Exception as e:
            log.warning(f"Could not mirror asset_3d_path for TouchDesigner: {e}")

    if td_payload.get('asset_3d_glb_path'):
        try:
            td_payload['asset_3d_glb_path'] = _mirror_asset_for_td(td_payload['asset_3d_glb_path'])
        except Exception as e:
            log.warning(f"Could not mirror asset_3d_glb_path for TouchDesigner: {e}")

    td_payload['source_options'] = {
        'generate_image': bool(asset_plan.needs_image_asset),
        'generate_3d': bool(asset_plan.needs_3d_asset),
    }
    td_payload['image_asset_requested'] = bool(asset_plan.needs_image_asset)
    td_payload['image_asset_path'] = td_payload.get('asset_image_path', '')
    td_payload['image_usage'] = asset_plan.image_usage
    td_payload['asset_3d_requested'] = bool(asset_plan.needs_3d_asset)
    td_payload['asset_3d_path'] = td_payload.get('asset_3d_path', '')
    td_payload['asset_3d_glb_path'] = td_payload.get('asset_3d_glb_path', '')
    td_payload['asset_3d_obj_path'] = td_payload.get('asset_3d_path', '') if str(td_payload.get('asset_3d_path', '')).lower().endswith('.obj') else ''
    td_payload['td_parameters'] = params.model_dump()
    asset_plan.td_mcp_plan = _augment_td_plan_with_assets(asset_plan, td_payload)
    td_payload['mcp_plan'] = asset_plan.td_mcp_plan
    td_payload['operator_brief'] = asset_plan.operator_brief
    td_status, td_message = await send_to_touchdesigner(td_payload)
    td_applied = _parse_td_message(td_message).get('applied', {})
    telemetry = _normalize_td_telemetry(params, td_applied)
    source_status = _merge_td_source_status(source_status, telemetry)
    return td_status, td_message, _collect_source_assets(params, asset_plan), td_applied, source_status


async def check_td_connection() -> str:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f'{TD_BASE_URL}/status')
            return 'connected' if response.status_code == 200 else f'error ({response.status_code})'
    except Exception as exc:
        return f'disconnected ({type(exc).__name__})'


async def _await_with_progress(
    ws: WebSocket,
    awaitable,
    messages: list[str],
    interval: float = 8.0,
    timeout: float | None = None,
):
    task = asyncio.create_task(awaitable)
    index = 0
    started = asyncio.get_running_loop().time()
    try:
        while not task.done():
            if timeout is not None:
                elapsed = asyncio.get_running_loop().time() - started
                if elapsed >= timeout:
                    task.cancel()
                    raise TimeoutError(f'Generation step timed out after {timeout:.0f}s')
                sleep_for = min(interval, max(0.1, timeout - elapsed))
            else:
                sleep_for = interval
            await asyncio.sleep(sleep_for)
            if task.done():
                break
            message = messages[index % len(messages)]
            index += 1
            await ws.send_json({'type': 'thinking_delta', 'delta': f'\n{message}'})
        return await task
    except Exception:
        if not task.done():
            task.cancel()
        raise


@app.get('/')
async def root():
    return FileResponse(BASE_DIR / 'index.html')


@app.get('/style.css')
async def style_css():
    return FileResponse(BASE_DIR / 'style.css')


@app.get('/script.js')
async def script_js():
    return FileResponse(BASE_DIR / 'script.js')


@app.get('/info.html')
async def info_html():
    return FileResponse(BASE_DIR / 'info.html')


@app.get('/info')
async def info_redirect():
    return FileResponse(BASE_DIR / 'info.html')


@app.get('/api/status', response_model=StatusResponse)
async def api_status():
    return StatusResponse(
        gemini='ollama mode' if llm_router.agent_mode in {'ollama', 'local_ollama'} else ('disabled (local MCP mode)' if llm_router.agent_mode in {'local', 'mcp', 'no_api'} else ('ready' if llm_router.gemini_client else 'fallback only')),
        touchdesigner=await check_td_connection(),
        td_url=TD_BASE_URL,
        llm_stack={
            **llm_router.status(),
            'ollama': await llm_router.ollama_status(),
            'image_tool': await image_tool.status(),
            'trellis_tool': {'enabled': False, 'ok': False, 'scope': 'disabled for current 2D MVP'},
        },
    )


@app.get('/api/tools/trellis/status')
async def trellis_status():
    return {'enabled': False, 'ok': False, 'scope': 'disabled for current 2D MVP'}


@app.post('/api/tools/trellis/generate')
async def trellis_generate(req: TrellisGenerateRequest):
    raise HTTPException(status_code=400, detail='Trellis is disabled for the current Ollama + OpenAI Images + TouchDesigner 2D MVP.')
    result = await trellis_tool.generate_mesh(
        prompt=req.prompt,
        image_path=req.image_path,
        seed=req.seed,
        simplify=req.simplify,
        texture_size=req.texture_size,
    )
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get('/api/tools/image/status')
async def image_status():
    return await image_tool.status()


@app.post('/api/tools/image/generate')
async def image_generate(req: ImageGenerateRequest):
    result = await image_tool.generate_image(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        workflow=req.workflow,
    )
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result)
    return result

@app.post('/api/generate', response_model=GenerateResponse)
async def api_generate(req: GenerateRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail='Please enter a prompt.')
    log.info(
        "source_options received: image=%s, 3d=%s (external sources enabled=%s)",
        req.source_options.generate_image,
        req.source_options.generate_3d,
        EXTERNAL_SOURCE_GENERATION_ENABLED,
    )
    if MVP_FAST_RECIPE_MODE:
        reasoning, orchestrator = llm_router._fallback_output(req, 'MVP fast recipe mode')
    else:
        try:
            reasoning, orchestrator = await asyncio.wait_for(
                llm_router.orchestrate(req),
                timeout=float(os.getenv('AUTOTD_HTTP_ORCHESTRATE_TIMEOUT', '180')),
            )
        except Exception as exc:
            reasoning, orchestrator = llm_router._fallback_output(req, f'HTTP orchestration fallback: {exc}')
    params = orchestrator.td_params
    effective_source_options = SourceOptions(
        generate_image=bool(orchestrator.asset_plan.needs_image_asset and EXTERNAL_SOURCE_GENERATION_ENABLED),
        generate_3d=False,
    )

    td_status = 'skipped'
    td_message = 'TouchDesigner not connected'
    preview_url = None

    try:
        td_status, td_message, source_assets, td_applied, source_status = await asyncio.wait_for(
            process_assets_and_send(params, orchestrator.asset_plan),
            timeout=float(os.getenv('AUTOTD_HTTP_ASSET_TIMEOUT', '180')),
        )
        if td_status == 'sent':
            preview_url = f'{TD_BASE_URL}/preview'
    except Exception as exc:
        td_status = 'error'
        td_message = str(exc)
        source_assets = _collect_source_assets(params, orchestrator.asset_plan)
        td_applied = {}
        source_status = _initial_source_status(orchestrator.asset_plan)
        source_status['error'] = str(exc)
    telemetry = _normalize_td_telemetry(params, td_applied)
    flat_source = _flat_source_status(effective_source_options, source_status, telemetry, params)
    source_status = _source_status_from_flat(flat_source, source_status)
    if not EXTERNAL_SOURCE_GENERATION_ENABLED:
        source_status.update(_mvp_source_status())
    telemetry['source_options'] = flat_source['source_options']
    telemetry['source_generation_errors'] = flat_source['source_generation_errors']
    telemetry['mvp_mode'] = 'procedural_recipe'
    telemetry['selected_recipe'] = params.recipe_id
    telemetry['generated_parameters'] = {
        'recipe_id': params.recipe_id,
        'noise_strength': params.noise_strength,
        'speed': params.speed,
        'feedback_opacity': params.feedback_opacity,
        'blur_amount': params.blur_amount,
        'displace_weight': params.displace_weight,
        'glow_intensity': params.glow_intensity,
        'particle_count': params.particle_count,
        'spread': params.spread,
        'color_1': params.color_1,
        'color_2': params.color_2,
        'color_3': params.color_3,
    }
    telemetry['planned_td_nodes'] = orchestrator.asset_plan.td_mcp_plan.get('nodes', [])
    telemetry.update({k: v for k, v in flat_source.items() if k != 'source_options'})

    return GenerateResponse(
        success=True,
        message=f'Procedural TouchDesigner recipe selected: {params.recipe_id}',
        source_options=effective_source_options,
        parameters=params,
        orchestrator=orchestrator,
        td_status=td_status,
        td_message=td_message,
        preview_url=preview_url,
        reasoning=reasoning,
        source_assets=source_assets,
        td_plan=orchestrator.asset_plan.td_mcp_plan,
        td_applied=td_applied,
        telemetry=telemetry,
        source_status=source_status,
        image_asset_requested=flat_source['image_asset_requested'],
        image_asset_generated=flat_source['image_asset_generated'],
        image_asset_path=flat_source['image_asset_path'],
        image_loaded_in_td=flat_source['image_loaded_in_td'],
        asset_3d_requested=flat_source['asset_3d_requested'],
        asset_3d_generated=flat_source['asset_3d_generated'],
        asset_3d_obj_path=flat_source['asset_3d_obj_path'],
        asset_3d_glb_path=flat_source['asset_3d_glb_path'],
        asset_3d_loaded_in_td=flat_source['asset_3d_loaded_in_td'],
        point_count=flat_source['point_count'],
        primitive_count=flat_source['primitive_count'],
        source_generation_errors=flat_source['source_generation_errors'],
        converted_obj_path=params.asset_3d_path if params.asset_3d_path.endswith('.obj') else None,
        converted_fbx_path=None,
        td_load_success=telemetry.get('td_model_loaded'),
        td_point_count=telemetry.get('point_count'),
        td_primitive_count=telemetry.get('primitive_count'),
        td_image_resolution=f"{telemetry.get('image_width', 0)}x{telemetry.get('image_height', 0)}",
        td_render_connection=telemetry.get('render_top'),
        td_composite_connection_status='connected' if telemetry.get('composite_connected') else 'failed',
        td_image_usage=telemetry.get('image_usage'),
        td_final_output_top=telemetry.get('final_output_top')
    )


@app.get('/api/td/preview')
async def td_preview():
    try:
        headers, content = await asyncio.to_thread(
            _raw_http_request,
            'GET',
            '/preview',
            b'',
            'application/json',
            6.0,
        )
        media_type = headers.get('content-type', 'image/png')
        if not media_type.startswith('image/'):
            raise HTTPException(status_code=502, detail=f'TD preview returned non-image content: {media_type}')
        if not content:
            raise HTTPException(status_code=502, detail='TD preview returned an empty image response')
        return Response(
            content=content,
            media_type=media_type,
            headers={'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'TD preview unavailable: {exc}')


@app.websocket('/ws')
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            if data.get('type') == 'ping':
                await ws.send_json({'type': 'pong'})
                continue
            if data.get('type') != 'generate':
                continue

            req = GenerateRequest(**data.get('payload', {}))
            log.info(
                "source_options received over websocket: image=%s, 3d=%s (external sources enabled=%s)",
                req.source_options.generate_image,
                req.source_options.generate_3d,
                EXTERNAL_SOURCE_GENERATION_ENABLED,
            )
            await ws.send_json({'type': 'thinking_start'})
            await ws.send_json({'type': 'step', 'step': 1, 'message': 'Analyzing prompt with Ollama...'})

            streamed = False
            async def on_chunk(chunk: str):
                nonlocal streamed
                streamed = True
                try:
                    if chunk:
                        await ws.send_json({'type': 'thinking_delta', 'delta': chunk})
                except Exception as ws_err:
                    log.warning(f"Error streaming token over WebSocket: {ws_err}")

            if MVP_FAST_RECIPE_MODE:
                reasoning, orchestrator = llm_router._fallback_output(req, 'MVP fast recipe mode')
                await ws.send_json({
                    'type': 'thinking_delta',
                    'delta': '\nMVP fast recipe mode selected a verified TouchDesigner recipe.\n',
                })
            else:
                try:
                    reasoning, orchestrator = await _await_with_progress(
                        ws,
                        llm_router.orchestrate(req, on_chunk=on_chunk),
                        [
                            'Ollama is loading qwen3:30b and preparing the media-art recipe...',
                            'Waiting for the first reasoning tokens from Ollama...',
                            'Still thinking - large local models can take a while on first run...',
                        ],
                        timeout=float(os.getenv('AUTOTD_WS_ORCHESTRATE_TIMEOUT', '180')),
                    )
                except Exception as exc:
                    reasoning, orchestrator = llm_router._fallback_output(req, f'WebSocket orchestration fallback: {exc}')
                    await ws.send_json({
                        'type': 'thinking_delta',
                        'delta': '\nOllama response was too slow, so AutoTD switched to the local recipe planner.\n',
                    })
            params = orchestrator.td_params
            effective_source_options = SourceOptions(
                generate_image=bool(orchestrator.asset_plan.needs_image_asset and EXTERNAL_SOURCE_GENERATION_ENABLED),
                generate_3d=False,
            )
            if not streamed:
                await ws.send_json({'type': 'thinking_delta', 'delta': reasoning})
            await ws.send_json({
                'type': 'agent_plan',
                'template': params.template,
                'td_plan': orchestrator.asset_plan.td_mcp_plan,
                'asset_plan': orchestrator.asset_plan.model_dump(),
                'parameters': params.model_dump(),
            })
            await ws.send_json({'type': 'step', 'step': 2, 'message': 'Recipe selected. Preparing verified TD chain...'})
            await ws.send_json({'type': 'step', 'step': 3, 'message': 'Generating optional OpenAI 2D source if the recipe requested it...'})
            
            try:
                td_status, td_message, source_assets, td_applied, source_status = await _await_with_progress(
                    ws,
                    process_assets_and_send(params, orchestrator.asset_plan),
                    [
                        'Generating or collecting source assets...',
                        'Sending the node plan and source paths to TouchDesigner...',
                        'Waiting for TouchDesigner to cook the output TOP...',
                    ],
                    timeout=float(os.getenv('AUTOTD_WS_ASSET_TIMEOUT', '220')),
                )
            except Exception as exc:
                td_status, td_message = 'error', str(exc)
                source_assets = _collect_source_assets(params, orchestrator.asset_plan)
                td_applied = {}
                source_status = _mvp_source_status() if not EXTERNAL_SOURCE_GENERATION_ENABLED else _initial_source_status(orchestrator.asset_plan)
                source_status['error'] = str(exc)
            telemetry = _normalize_td_telemetry(params, td_applied)
            flat_source = _flat_source_status(effective_source_options, source_status, telemetry, params)
            source_status = _source_status_from_flat(flat_source, source_status)
            if not EXTERNAL_SOURCE_GENERATION_ENABLED:
                source_status.update(_mvp_source_status())
            telemetry['source_options'] = flat_source['source_options']
            telemetry['source_generation_errors'] = flat_source['source_generation_errors']
            telemetry['mvp_mode'] = 'procedural_recipe'
            telemetry['selected_recipe'] = params.recipe_id
            telemetry['generated_parameters'] = {
                'recipe_id': params.recipe_id,
                'noise_strength': params.noise_strength,
                'speed': params.speed,
                'feedback_opacity': params.feedback_opacity,
                'blur_amount': params.blur_amount,
                'displace_weight': params.displace_weight,
                'glow_intensity': params.glow_intensity,
                'particle_count': params.particle_count,
                'spread': params.spread,
                'color_1': params.color_1,
                'color_2': params.color_2,
                'color_3': params.color_3,
            }
            telemetry['planned_td_nodes'] = orchestrator.asset_plan.td_mcp_plan.get('nodes', [])
            telemetry.update({k: v for k, v in flat_source.items() if k != 'source_options'})

            await ws.send_json({
                'type': 'source_assets',
                'assets': source_assets,
                'td_applied': td_applied,
                'source_status': source_status,
                **flat_source,
            })
            await ws.send_json({'type': 'step', 'step': 4, 'message': 'Sending recipe parameters to TouchDesigner...'})

            preview_url = f'{TD_BASE_URL}/preview' if td_status == 'sent' else None
            await ws.send_json({'type': 'step', 'step': 5, 'message': 'Finalizing preview...'})
            await ws.send_json({
                'type': 'complete',
                'parameters': params.model_dump(),
                'orchestrator': orchestrator.model_dump(),
                'td_status': td_status,
                'td_message': td_message,
                'preview_url': preview_url,
                'reasoning': reasoning,
                'source_assets': source_assets,
                'td_plan': orchestrator.asset_plan.td_mcp_plan,
                'td_applied': td_applied,
                'telemetry': telemetry,
                'source_status': source_status,
                'source_options': flat_source['source_options'],
                'image_asset_requested': flat_source['image_asset_requested'],
                'image_asset_generated': flat_source['image_asset_generated'],
                'image_asset_path': flat_source['image_asset_path'],
                'image_loaded_in_td': flat_source['image_loaded_in_td'],
                'asset_3d_requested': flat_source['asset_3d_requested'],
                'asset_3d_generated': flat_source['asset_3d_generated'],
                'asset_3d_obj_path': flat_source['asset_3d_obj_path'],
                'asset_3d_glb_path': flat_source['asset_3d_glb_path'],
                'asset_3d_loaded_in_td': flat_source['asset_3d_loaded_in_td'],
                'point_count': flat_source['point_count'],
                'primitive_count': flat_source['primitive_count'],
                'source_generation_errors': flat_source['source_generation_errors'],
            })
    except WebSocketDisconnect:
        log.info('WebSocket disconnected')
    except Exception as exc:
        log.error('WebSocket error: %s', exc)
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('server:app', host='127.0.0.1', port=int(os.getenv('SERVER_PORT', '8000')))


