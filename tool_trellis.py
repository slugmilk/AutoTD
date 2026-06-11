import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import httpx


class TrellisTool:
    """Small AutoTD wrapper around the official TRELLIS Gradio app."""

    def __init__(self) -> None:
        self.base_url = os.getenv('TRELLIS_URL', 'http://127.0.0.1:7860').rstrip('/')

    async def status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f'{self.base_url}/config')
                response.raise_for_status()
                config = response.json()
            api_names = sorted(
                dep.get('api_name')
                for dep in config.get('dependencies', [])
                if dep.get('api_name')
            )
            return {
                'enabled': True,
                'ok': 'image_to_3d' in api_names,
                'base_url': self.base_url,
                'api_names': api_names,
            }
        except Exception as exc:
            return {'enabled': False, 'ok': False, 'base_url': self.base_url, 'error': str(exc)}

    async def generate_mesh(
        self,
        prompt: str = '',
        image_path: str = '',
        seed: int = 0,
        simplify: float = 0.95,
        texture_size: int = 1024,
    ) -> dict[str, Any]:
        """
        Run TRELLIS image-to-3D and export GLB.

        TRELLIS is not text-to-3D by itself. AutoTD should pass an image generated
        by ComfyUI or selected by the user, then this wrapper converts it to 3D.
        """
        if not image_path:
            return {
                'success': False,
                'code': 'image_required',
                'message': 'TRELLIS needs an image_path. Generate/select an image first, then send it here.',
                'prompt': prompt,
            }

        is_remote_image = urlparse(image_path).scheme in {'http', 'https'}
        image_file = image_path if is_remote_image else Path(image_path).expanduser()
        if not is_remote_image and not image_file.exists():
            return {
                'success': False,
                'code': 'image_not_found',
                'message': f'Image not found: {image_file}',
                'prompt': prompt,
            }

        try:
            from gradio_client import Client, handle_file
        except Exception as exc:
            return {
                'success': False,
                'code': 'missing_dependency',
                'message': 'Install gradio_client to call TRELLIS from AutoTD.',
                'error': str(exc),
            }

        def run_trellis() -> dict[str, Any]:
            import urllib.request
            import tempfile
            import os

            def sanitize_gradio_files(obj, base_url):
                if isinstance(obj, dict):
                    if obj.get('meta', {}).get('_type') == 'gradio.FileData':
                        path = obj.get('path')
                        url = obj.get('url')
                        if path and not os.path.exists(path):
                            if not url and path.startswith('/'):
                                url = f"{base_url}/file={path}"
                            if url:
                                temp_dir = tempfile.gettempdir()
                                filename = os.path.basename(path)
                                local_path = os.path.join(temp_dir, filename).replace('\\', '/')
                                try:
                                    urllib.request.urlretrieve(url, local_path)
                                    obj['path'] = local_path
                                except Exception as e:
                                    pass
                    else:
                        for k, v in list(obj.items()):
                            obj[k] = sanitize_gradio_files(v, base_url)
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        obj[i] = sanitize_gradio_files(item, base_url)
                return obj

            client = Client(self.base_url)
            generated = client.predict(
                handle_file(str(image_file)),
                [],
                False,
                seed,
                7.5,
                12,
                3.0,
                12,
                'stochastic',
                api_name='/image_to_3d',
            )
            # Sanitize intermediate generated state (download state.bin to Windows if needed)
            generated = sanitize_gradio_files(generated, self.base_url)

            exported = client.predict(
                simplify,
                texture_size,
                api_name='/extract_glb',
            )
            # Sanitize final exported mesh (download GLB to Windows if needed)
            exported = sanitize_gradio_files(exported, self.base_url)

            # Explicitly download output files from WSL Gradio container if they do not exist locally
            glb_path = self._extract_path(exported, preferred_ext=('.glb',))
            preview_video = self._extract_path(generated, preferred_ext=('.mp4', '.webm'))

            import tempfile
            import urllib.request
            temp_dir = tempfile.gettempdir()

            if glb_path and (glb_path.startswith('/') or 'TRELLIS/tmp' in glb_path) and not os.path.exists(glb_path):
                filename = os.path.basename(glb_path)
                local_glb = os.path.join(temp_dir, filename).replace('\\', '/')
                url = f"{self.base_url}/file={glb_path}"
                try:
                    urllib.request.urlretrieve(url, local_glb)
                    glb_path = local_glb
                except Exception as e:
                    pass

            if preview_video and (preview_video.startswith('/') or 'TRELLIS/tmp' in preview_video) and not os.path.exists(preview_video):
                filename = os.path.basename(preview_video)
                local_video = os.path.join(temp_dir, filename).replace('\\', '/')
                url = f"{self.base_url}/file={preview_video}"
                try:
                    urllib.request.urlretrieve(url, local_video)
                    preview_video = local_video
                except Exception as e:
                    pass

            return {
                'success': True,
                'prompt': prompt,
                'image_path': str(image_file),
                'seed': seed,
                'preview_video': preview_video,
                'glb_path': glb_path,
                'raw_generated': self._compact_result(generated),
                'raw_exported': self._compact_result(exported),
            }

        return await asyncio.to_thread(run_trellis)

    @staticmethod
    def _compact_result(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: TrellisTool._compact_result(v) for k, v in value.items() if k not in {'data'}}
        if isinstance(value, (list, tuple)):
            return [TrellisTool._compact_result(v) for v in value]
        if hasattr(value, 'path'):
            return str(value.path)
        return value

    @staticmethod
    def _extract_path(value: Any, preferred_ext: tuple[str, ...]) -> str | None:
        if isinstance(value, dict):
            for key in ('path', 'name', 'value'):
                found = value.get(key)
                if isinstance(found, str) and found.lower().endswith(preferred_ext):
                    return found
            for nested in value.values():
                found = TrellisTool._extract_path(nested, preferred_ext)
                if found:
                    return found
        if isinstance(value, (list, tuple)):
            for item in value:
                found = TrellisTool._extract_path(item, preferred_ext)
                if found:
                    return found
        if hasattr(value, 'path'):
            found = str(value.path)
            if found.lower().endswith(preferred_ext):
                return found
        if isinstance(value, str) and value.lower().endswith(preferred_ext):
            return value
        return None

