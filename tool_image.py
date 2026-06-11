import base64
import hashlib
import io
import math
import os
import random
import time
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFilter


class LocalImageTool:
    """OpenAI Images API wrapper used by AutoTD's 2D source generation path."""

    def __init__(self) -> None:
        self.api_key = os.getenv('OPENAI_API_KEY', '')
        self.model = os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-1')
        self.size = os.getenv('OPENAI_IMAGE_SIZE', '1024x1024')
        self.quality = os.getenv('OPENAI_IMAGE_QUALITY', 'low')
        self.timeout = float(os.getenv('AUTOTD_IMAGE_TIMEOUT', '90'))
        self.fallback_enabled = os.getenv('AUTOTD_IMAGE_FALLBACK', '1').lower() in {'1', 'true', 'yes', 'on'}

    async def status(self) -> dict[str, Any]:
        return {
            'enabled': bool(self.api_key),
            'ok': bool(self.api_key),
            'provider': 'openai',
            'model': self.model,
            'size': self.size,
            'quality': self.quality,
            'fallback_enabled': self.fallback_enabled,
            'error': '' if self.api_key else 'OPENAI_API_KEY is not set',
        }

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = '',
        workflow: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            return {
                'success': False,
                'provider': 'openai',
                'code': 'openai_api_key_required',
                'error': 'OPENAI_API_KEY is not set.',
                'prompt': prompt,
            }

        final_prompt = self._compose_prompt(prompt, negative_prompt)
        payload: dict[str, Any] = {
            'model': self.model,
            'prompt': final_prompt,
            'size': self.size,
            'n': 1,
        }
        if self.quality:
            payload['quality'] = self.quality

        timeout = httpx.Timeout(self.timeout, connect=15.0, read=self.timeout, write=20.0, pool=15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(
                    'https://api.openai.com/v1/images/generations',
                    headers={
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type': 'application/json',
                    },
                    json=payload,
                )
                if response.status_code >= 400:
                    if self.fallback_enabled:
                        return self._fallback_result(prompt, negative_prompt, f'OpenAI HTTP {response.status_code}: {response.text}')
                    return {
                        'success': False,
                        'provider': 'openai',
                        'status_code': response.status_code,
                        'error': response.text,
                        'prompt': prompt,
                    }
                data = response.json()
        except Exception as exc:
            if self.fallback_enabled:
                return self._fallback_result(prompt, negative_prompt, str(exc) or type(exc).__name__)
            return {
                'success': False,
                'provider': 'openai',
                'code': type(exc).__name__,
                'error': str(exc),
                'prompt': prompt,
            }

        images: list[dict[str, str]] = []
        for index, item in enumerate(data.get('data', [])):
            filename = f"autotd_openai_{int(time.time())}_{index}.png"
            if item.get('b64_json'):
                images.append({
                    'filename': filename,
                    'type': 'output',
                    'b64_json': item['b64_json'],
                })
            elif item.get('url'):
                images.append({
                    'filename': filename,
                    'type': 'output',
                    'url': item['url'],
                })

        return {
            'success': bool(images),
            'provider': 'openai',
            'model': self.model,
            'prompt': prompt,
            'negative_prompt': negative_prompt,
            'images': images,
            'error': '' if images else 'OpenAI Images API returned no image data.',
        }

    def _fallback_result(self, prompt: str, negative_prompt: str, reason: str) -> dict[str, Any]:
        image_bytes = self._make_local_source_image(prompt)
        b64_image = base64.b64encode(image_bytes).decode('ascii')
        return {
            'success': True,
            'provider': 'local_fallback',
            'model': 'procedural-pillow-source',
            'prompt': prompt,
            'negative_prompt': negative_prompt,
            'images': [{
                'filename': f"autotd_local_source_{int(time.time())}.png",
                'type': 'output',
                'b64_json': b64_image,
            }],
            'warning': f'OpenAI image generation failed; used local procedural source image. Reason: {reason}',
            'openai_error': reason,
        }

    def _make_local_source_image(self, prompt: str) -> bytes:
        width, height = self._parse_size()
        width = min(width, 1024)
        height = min(height, 1024)
        seed = int(hashlib.sha256((prompt or 'autotd').encode('utf-8')).hexdigest()[:12], 16)
        rng = random.Random(seed)
        c1, c2, c3 = self._palette(prompt)

        img = Image.new('RGB', (width, height), c3)
        pixels = img.load()
        for y in range(height):
            yy = y / max(1, height - 1)
            for x in range(width):
                xx = x / max(1, width - 1)
                wave = 0.5 + 0.5 * math.sin((xx * 7.0 + yy * 4.0 + rng.random() * 0.02) * math.pi)
                fog = 0.5 + 0.5 * math.sin((xx - yy) * math.pi * 3.0 + seed % 97)
                mix = min(1.0, max(0.0, yy * 0.45 + wave * 0.35 + fog * 0.2))
                r = int(c1[0] * mix + c2[0] * (1 - mix))
                g = int(c1[1] * mix + c2[1] * (1 - mix))
                b = int(c1[2] * mix + c2[2] * (1 - mix))
                pixels[x, y] = (r, g, b)

        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for _ in range(90):
            cx = rng.randint(-width // 4, width + width // 4)
            cy = rng.randint(-height // 4, height + height // 4)
            radius = rng.randint(max(12, width // 35), max(24, width // 8))
            color = (*rng.choice([c1, c2]), rng.randint(18, 70))
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(8, width // 45)))
        img = Image.alpha_composite(img.convert('RGBA'), overlay).filter(ImageFilter.GaussianBlur(radius=1.1))

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    def _parse_size(self) -> tuple[int, int]:
        try:
            left, right = self.size.lower().split('x', 1)
            return int(left), int(right)
        except Exception:
            return 1024, 1024

    @staticmethod
    def _palette(prompt: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
        text = (prompt or '').lower()
        if any(word in text for word in ('pink', 'rose', 'magenta', '핑크', '분홍')):
            return (255, 95, 215), (255, 179, 230), (19, 4, 15)
        if any(word in text for word in ('blue', 'cyan', 'aqua', 'underwater', 'fog', 'water', '파랑', '청록', '수중', '안개')):
            return (79, 215, 255), (36, 107, 255), (2, 11, 24)
        if any(word in text for word in ('gold', 'yellow', 'orange', 'amber', '금색', '노랑', '주황')):
            return (255, 209, 102), (255, 122, 24), (18, 8, 4)
        if any(word in text for word in ('green', 'emerald', 'lime', '초록')):
            return (109, 255, 143), (0, 212, 166), (3, 19, 11)
        if any(word in text for word in ('purple', 'violet', '보라')):
            return (182, 108, 255), (110, 231, 255), (9, 4, 22)
        return (88, 230, 255), (111, 125, 255), (3, 16, 24)

    @staticmethod
    def decode_image_bytes(image_data: dict[str, str]) -> bytes:
        if image_data.get('b64_json'):
            return base64.b64decode(image_data['b64_json'])
        raise ValueError('No b64_json field found in OpenAI image response.')

    @staticmethod
    def _compose_prompt(prompt: str, negative_prompt: str) -> str:
        prompt = (prompt or '').strip()
        negative_prompt = (negative_prompt or '').strip()
        if not negative_prompt:
            return prompt
        return (
            f"{prompt}\n\n"
            f"Avoid these traits: {negative_prompt}."
        )

    async def enqueue_prompt(
        self,
        prompt: str,
        negative_prompt: str = '',
        workflow: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.generate_image(prompt, negative_prompt, workflow)
