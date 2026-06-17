import asyncio
import json
import logging
import os
import re
from typing import Any

import httpx
from pydantic import ValidationError

from local_agent import LocalMCPAgent
from schema import AssetPlan, GenerateRequest, OrchestratorOutput, RoutingTrace, TDParameters

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None


log = logging.getLogger('autotd.llm')


class LLMRouter:
    def __init__(self) -> None:
        self.gemini_api_key = os.getenv('GEMINI_API_KEY', '')
        self.openai_api_key = os.getenv('OPENAI_API_KEY', '')
        self.anthropic_api_key = os.getenv('ANTHROPIC_API_KEY', '')
        self.openrouter_api_key = os.getenv('OPENROUTER_API_KEY', '')
        self.agent_mode = os.getenv('AUTOTD_AGENT_MODE', 'api').lower()

        self.gemini_model = os.getenv('GEMINI_MAIN_MODEL', 'gemini-2.5-flash-lite')
        self.openai_repair_model = os.getenv('OPENAI_REPAIR_MODEL', 'gpt-5.4-mini')
        self.prompt_refiner_model = os.getenv('PROMPT_REFINER_MODEL', 'claude-3-5-haiku-latest')
        self.openrouter_refine_model = os.getenv('OPENROUTER_REFINE_MODEL', 'anthropic/claude-3.5-haiku')
        self.ollama_base_url = os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/')
        self.ollama_model = os.getenv('OLLAMA_MODEL', 'qwen3:30b')
        self.ollama_timeout = float(os.getenv('OLLAMA_TIMEOUT', '180'))
        self.ollama_think = os.getenv('OLLAMA_THINK', '1').lower() in {'1', 'true', 'yes', 'on'}

        self.local_agent = LocalMCPAgent()
        self.gemini_client = None
        if self.gemini_api_key and genai:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_api_key)
            except Exception as exc:
                log.warning('Gemini init failed: %s', exc)

    def status(self) -> dict[str, Any]:
        return {
            'orchestrator': {
                'provider': self._orchestrator_provider(),
                'model': self._orchestrator_model_name(),
                'enabled': self._orchestrator_enabled(),
                'mode': self.agent_mode,
            },
            'json_repair': {
                'provider': 'openai',
                'model': self.openai_repair_model,
                'enabled': bool(self.openai_api_key),
            },
            'prompt_refiner': {
                'provider': self._prompt_refiner_provider(),
                'model': self._prompt_refiner_model_name(),
                'enabled': bool(self.anthropic_api_key or self.openai_api_key or self.openrouter_api_key),
            },
            'compare_router': {
                'provider': 'openrouter',
                'model': self.openrouter_refine_model,
                'enabled': bool(self.openrouter_api_key),
            },
        }

    async def orchestrate(self, req: GenerateRequest, on_chunk=None) -> tuple[str, OrchestratorOutput]:
        if self.agent_mode in {'local', 'mcp', 'no_api'}:
            return self.local_agent.orchestrate(req)

        if self.agent_mode in {'ollama', 'local_ollama'}:
            try:
                raw_text = await self._ollama_orchestrate(req, on_chunk=on_chunk)
                reasoning, output = await self._parse_or_repair(raw_text, req)
                output.reasoning = reasoning
                output.routing.orchestrator = f'ollama:{self.ollama_model}'
                await self._refine_asset_prompts(output, req)
                return reasoning, output
            except Exception as exc:
                reasoning, output = self._fallback_output(req, f'Ollama failed: {exc}')
                return reasoning, output

        if not self.gemini_client or not genai_types:
            reasoning, output = self._fallback_output(req, 'Gemini not configured')
            return reasoning, output

        try:
            raw_text = await self._gemini_orchestrate(req)
            reasoning, output = await self._parse_or_repair(raw_text, req)
            output.reasoning = reasoning
            output.routing.orchestrator = f'gemini:{self.gemini_model}'
            await self._refine_asset_prompts(output, req)
            return reasoning, output
        except Exception as exc:
            reasoning, output = self._fallback_output(req, str(exc))
            return reasoning, output

    def _detect_operator_family(self, prompt: str) -> str:
        """Detect the primary TouchDesigner operator family from the prompt.
        Returns 'sop' for 3-D/geometry prompts, 'pop' for particle/fluid prompts, 'top' otherwise."""
        text = prompt.lower()
        sop_keywords = [
            '3d', '3-d', 'mesh', 'object', 'geometry', 'sculpture', 'space', 'depth', 'volume',
            'extrude', 'polygon', 'solid', 'model', 'shape', 'primitive', 'sphere', 'cube', 'torus',
            '입체', '오브젝트', '조형', '공간', '기하', '도형', '메쉬', '모델', '입체물', '조각',
        ]
        pop_keywords = [
            'particle', 'particles', 'fluid', 'fluid sim', 'simulation', 'emitter', 'force',
            'gravity', 'swarm', 'flock', 'spray', 'smoke', 'fire', 'explosion', 'pop',
            '파티클', '입자', '유체', '시뮬레이션', '폭발', '연기', '불꽃', '분수', '군집',
        ]
        for kw in sop_keywords:
            if kw in text:
                return 'sop'
        for kw in pop_keywords:
            if kw in text:
                return 'pop'
        return 'top'

    def _orchestrator_system_prompt(self, operator_family: str = 'top') -> str:
        return (
            'You are an AutoTD procedural TouchDesigner media-art director. '
            'External image generation, 3D generation, ComfyUI, OpenAI Images, and Trellis are disabled. '
            'Your job is to design a rich procedural TouchDesigner network plan using internal operators only.\n\n'
            'AVAILABLE RECIPES:\n'
            '- feedback_2d: layered 2D feedback, liquid flow, dream haze, soft displacement, color filtering.\n'
            '- particle_field: black-background particle art, stardust, floating sparkles, glowing trails.\n\n'
            'Recipe selection rules:\n'
            '- water, sea, fog, dream, flow, soft, underwater -> feedback_2d\n'
            '- particle, starlight, dust, light, floating, sparkles -> particle_field\n\n'
            'Planning rules:\n'
            '- Reusing the same role is allowed and encouraged when it improves the image. Multiple feedback, blur, noise, transform, composite, level, and displace nodes are valid.\n'
            '- Think in layers: seed layer, motion/control layer, feedback loop, color grade, glow/trail post-process, final output.\n'
            '- TouchDesigner can use TOP, SOP, POP, CHOP, COMP, DAT, MAT, and helper Python DAT scripts when structurally useful.\n'
            '- The final result must be procedural-only and should look like media art, not a single default operator.\n'
            '- Include concise visible planning text before JSON using labels: planning, node design, connection proposal, repair/safety.\n\n'
            'Return short Korean planning notes, then a single JSON block in ```json fences.\n'
            'The JSON must match this shape exactly:\n'
            '{"recipe_id":"feedback_2d","concept_summary":"...","visual_mood":"...",'
            '"use_comfyui_image":false,"comfyui_prompt":"","comfyui_negative_prompt":"",'
            '"image_usage":"none","parameters":{'
            '"speed":0.45,"noise_strength":0.6,"feedback_opacity":0.86,"blur_amount":0.45,'
            '"displace_weight":0.35,"glow_intensity":0.7,"particle_count":600,"spread":0.7,'
            '"color_1":"#dfefff","color_2":"#6a8fbf","color_3":"#05070a"}}\n\n'
            'image_usage must be "none" and use_comfyui_image must be false.\n'
            'All numeric values except particle_count must be between 0.0 and 1.0. particle_count must be 50-3000.'
        )

    def _orchestrator_user_message(self, req: GenerateRequest) -> str:
        return (
            f'Prompt: {req.prompt}\n'
            'Pick feedback_2d or particle_field. Build a rich procedural TouchDesigner direction without external image or 3D assets.\n'
            'Tune parameters for color, feedback depth, motion, displacement, glow, density, and spread. '
            'Never request ComfyUI, OpenAI Images, Trellis, or imported files.'
        )

    async def _gemini_orchestrate(self, req: GenerateRequest) -> str:
        family = self._detect_operator_family(req.prompt)
        system_prompt = self._orchestrator_system_prompt(family)
        user_message = self._orchestrator_user_message(req)
        response = await asyncio.to_thread(
            self.gemini_client.models.generate_content,
            model=self.gemini_model,
            contents=user_message,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.35,
            ),
        )
        return response.text or ''

    async def _ollama_orchestrate(self, req: GenerateRequest, on_chunk=None) -> str:
        family = self._detect_operator_family(req.prompt)
        payload = {
            'model': self.ollama_model,
            'stream': True if on_chunk else False,
            'think': self.ollama_think,
            'messages': [
                {'role': 'system', 'content': self._orchestrator_system_prompt(family)},
                {'role': 'user', 'content': self._orchestrator_user_message(req)},
            ],
            'options': {
                'temperature': 0.35,
                'num_predict': int(os.getenv('OLLAMA_NUM_PREDICT', '900')),
                'num_ctx': int(os.getenv('OLLAMA_NUM_CTX', '16384')),
            },
        }
        timeout = httpx.Timeout(self.ollama_timeout, connect=10.0, read=self.ollama_timeout, write=10.0, pool=10.0)
        
        if on_chunk:
            full_content = []
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream('POST', f'{self.ollama_base_url}/api/chat', json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            message = chunk.get('message', {})
                            thinking = message.get('thinking', '') or chunk.get('thinking', '')
                            content = message.get('content', '')
                            if thinking:
                                await on_chunk(thinking)
                            if content:
                                full_content.append(content)
                                await on_chunk(content)
                        except Exception:
                            pass
            return ''.join(full_content)
        else:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f'{self.ollama_base_url}/api/chat', json=payload)
                response.raise_for_status()
                data = response.json()
            return data.get('message', {}).get('content', '') or data.get('response', '') or ''

    async def _parse_or_repair(self, raw_text: str, req: GenerateRequest) -> tuple[str, OrchestratorOutput]:
        reasoning = ""
        if '```json' in raw_text:
            reasoning = raw_text.split('```json', 1)[0].strip()

        try:
            payload = self._extract_payload(raw_text, req)
            output = self._payload_to_output(payload, req)
            if not reasoning:
                reasoning = payload.get('reasoning', '') or ''
            output.reasoning = reasoning or 'No reasoning returned.'
            return reasoning or 'No reasoning returned.', output
        except Exception as exc:
            log.warning('Recipe parse failed: %s – using fallback', exc)
            reasoning_text, output = self._fallback_output(req, f'Parse error: {exc}')
            if reasoning:
                output.reasoning = reasoning
            return output.reasoning, output

    def _extract_payload(self, raw_text: str, req: GenerateRequest) -> dict[str, Any]:
        fenced = re.search(r'```json\s*([\s\S]*?)\s*```', raw_text or '', re.IGNORECASE)
        if fenced:
            candidate = fenced.group(1).strip()
        else:
            start = (raw_text or '').find('{')
            end = (raw_text or '').rfind('}')
            if start < 0 or end <= start:
                raise ValueError('No JSON block found in orchestrator response')
            candidate = raw_text[start:end + 1]
        return json.loads(candidate)

    def _upgrade_flat_payload(self, data: dict[str, Any], req: GenerateRequest) -> dict[str, Any]:
        td_keys = {
            'template', 'style', 'motion', 'color_mode', 'color_r', 'color_g', 'color_b',
            'speed', 'density', 'scale', 'turbulence', 'brightness', 'keywords', 'mood', 'description',
            'asset_image_path', 'asset_3d_path'
        }
        td_params = {k: v for k, v in data.items() if k in td_keys}
        asset_plan = {k: v for k, v in data.items() if k not in td_keys and k != 'template'}
        td_params.setdefault('template', data.get('template', 'particle'))
        asset_plan.setdefault('needs_image_asset', bool(req.source_options.generate_image))
        asset_plan.setdefault('needs_3d_asset', bool(req.source_options.generate_3d))
        asset_plan.setdefault('image_prompt', '')
        asset_plan.setdefault('image_negative_prompt', '')
        asset_plan.setdefault('image_usage', 'background_composite')
        asset_plan.setdefault('trellis_prompt', req.prompt if req.source_options.generate_3d else '')
        asset_plan.setdefault('object_description', req.prompt if req.source_options.generate_3d else '')
        asset_plan.setdefault('asset_usage', 'geometry_source')
        asset_plan.setdefault('operator_brief', data.get('operator_brief', ''))
        asset_plan.setdefault('model_hint', '')
        asset_plan.setdefault('td_mcp_plan', self._default_mcp_plan(req, td_params))
        return {
            'template': td_params.get('template', 'particle'),
            'td_params': td_params,
            'asset_plan': asset_plan,
        }

    def _recipe_override_from_prompt(self, prompt: str) -> str:
        """Use deterministic recipe routing so a weak LLM choice cannot derail the demo."""
        text = (prompt or '').lower()
        particle_hint_terms = [
            'particle', 'particles', 'starlight', 'starfield', 'dust', 'sparkle',
            'sparkles', 'glitter', 'firefly', 'mote', 'motes', 'black background',
            '입자', '파티클', '별가루', '별빛', '먼지', '반짝', '반짝임',
            '검은 배경', '검정 배경',
        ]
        if any(term in text for term in particle_hint_terms):
            return 'particle_field'
        feedback_terms = [
            'water', 'sea', 'ocean', 'fog', 'mist', 'dream', 'flow', 'flowing',
            'soft', 'underwater', 'liquid', 'wave', 'fluid', 'haze',
            '물', '바다', '수중', '안개', '꿈', '흐름', '부드러운', '물결', '유체',
        ]
        particle_terms = [
            'particle', 'particles', 'starlight', 'starfield', 'dust', 'sparkle',
            'sparkles', 'glitter', 'firefly', 'mote', 'motes',
            '입자', '별빛', '별가루', '먼지', '반짝', '반딧불',
        ]
        if any(term in text for term in feedback_terms):
            return 'feedback_2d'
        if any(term in text for term in particle_terms):
            return 'particle_field'
        return 'feedback_2d'

    def _palette_from_prompt(self, prompt: str, recipe_id: str) -> dict[str, str]:
        text = (prompt or '').lower()
        palettes = [
            (('pink', 'rose', 'magenta', '핑크', '분홍', '장미'), ('#ff5fd7', '#ffb3e6', '#13040f')),
            (('purple', 'violet', 'lavender', '보라', '라벤더'), ('#b66cff', '#6ee7ff', '#090416')),
            (('blue', 'cyan', 'aqua', 'teal', '파랑', '푸른', '청록'), ('#4fd7ff', '#246bff', '#020b18')),
            (('red', 'crimson', 'scarlet', '빨강', '붉은'), ('#ff3b4f', '#ff9a3d', '#160304')),
            (('orange', 'amber', 'gold', 'yellow', '주황', '노랑', '금색'), ('#ffd166', '#ff7a18', '#120804')),
            (('green', 'lime', 'emerald', '초록', '녹색'), ('#6dff8f', '#00d4a6', '#03130b')),
            (('neon', 'cyber', 'laser', '네온'), ('#00fff0', '#ff4fd8', '#050014')),
        ]
        for tokens, colors in palettes:
            if any(token in text for token in tokens):
                return {'color_1': colors[0], 'color_2': colors[1], 'color_3': colors[2]}
        if recipe_id == 'particle_field':
            return {'color_1': '#ffd166', 'color_2': '#48e5ff', 'color_3': '#07020f'}
        return {'color_1': '#58e6ff', 'color_2': '#6f7dff', 'color_3': '#031018'}

    def _payload_to_output(self, payload: dict[str, Any], req: GenerateRequest) -> OrchestratorOutput:
        if not isinstance(payload, dict):
            payload = {}

        if req.recipe_id in ('feedback_2d', 'particle_field'):
            recipe_id = req.recipe_id
        else:
            recipe_id = str(payload.get('recipe_id') or self._select_recipe_from_prompt(req.prompt))
            if recipe_id not in ('feedback_2d', 'particle_field'):
                recipe_id = self._select_recipe_from_prompt(req.prompt)

        rp = payload.get('parameters') or payload.get('recipe_params') or payload.get('td_params') or {}
        if not isinstance(rp, dict):
            rp = {}

        template_map = {
            'feedback_2d': 'feedback',
            'particle_field': 'particle',
        }
        template = template_map.get(recipe_id, 'particle')

        heuristic = self._heuristic_recipe_params(req.prompt, recipe_id)
        heuristic.update(self._palette_from_prompt(req.prompt, recipe_id))
        for key, value in heuristic.items():
            rp.setdefault(key, value)
        rp.update(self._palette_from_prompt(req.prompt, recipe_id))

        color_1 = str(rp.get('color_1') or '#ffffff')
        color_2 = str(rp.get('color_2') or '#9fb7ff')
        color_3 = str(rp.get('color_3') or '#05070a')
        cr, cg, cb = self._hex_to_rgb01(color_1)
        image_usage = 'none'
        use_image = False
        concept_summary = str(payload.get('concept_summary') or heuristic.get('concept_summary') or req.prompt)
        visual_mood = str(payload.get('visual_mood') or heuristic.get('visual_mood') or 'atmospheric')
        comfy_prompt = ''
        comfy_negative = ''

        td_params = TDParameters(
            recipe_id=recipe_id,
            template=template,
            style=str(rp.get('style', req.style)).lower(),
            motion=str(rp.get('motion', req.motion)).lower(),
            concept_summary=concept_summary,
            visual_mood=visual_mood,
            use_comfyui_image=use_image,
            image_usage=image_usage,
            color_mode='custom',
            color_palette='custom',
            color_r=cr,
            color_g=cg,
            color_b=cb,
            speed=self._clamp_float(rp.get('speed', rp.get('motion_speed', 0.5)), 0.0, 1.0, 0.5),
            density=self._clamp_float(rp.get('particle_count', 600), 50, 3000, 600) / 3000.0,
            scale=1.0,
            turbulence=self._clamp_float(rp.get('noise_strength', 0.3), 0.0, 1.0, 0.3),
            brightness=self._clamp_float(rp.get('glow_intensity', rp.get('glow', 0.7)), 0.0, 1.0, 0.7),
            noise_strength=self._clamp_float(rp.get('noise_strength', 0.5), 0.0, 1.0, 0.5),
            motion_speed=self._clamp_float(rp.get('speed', rp.get('motion_speed', 0.5)), 0.0, 1.0, 0.5),
            feedback_opacity=self._clamp_float(rp.get('feedback_opacity', 0.85), 0.0, 1.0, 0.85),
            glow=self._clamp_float(rp.get('glow_intensity', rp.get('glow', 0.6)), 0.0, 1.0, 0.6),
            particle_density=self._clamp_float(rp.get('particle_count', 600), 50, 3000, 600) / 3000.0,
            particle_count=int(self._clamp_float(rp.get('particle_count', 600), 50, 3000, 600)),
            spread=self._clamp_float(rp.get('spread', 0.7), 0.0, 1.0, 0.7),
            displace_weight=self._clamp_float(rp.get('displace_weight', 0.35), 0.0, 1.0, 0.35),
            blur_amount=self._clamp_float(rp.get('blur_amount', 0.4), 0.0, 1.0, 0.4),
            glow_intensity=self._clamp_float(rp.get('glow_intensity', rp.get('glow', 0.6)), 0.0, 1.0, 0.6),
            color_1=color_1,
            color_2=color_2,
            color_3=color_3,
            keywords=[req.style, req.motion, req.color],
            mood=visual_mood,
            description=req.prompt,
        )

        asset_plan = AssetPlan(
            needs_image_asset=use_image,
            needs_3d_asset=False,
            image_prompt='',
            image_negative_prompt=comfy_negative,
            image_usage=image_usage,
            operator_brief=f'Procedural-only recipe: {recipe_id}. {concept_summary}',
            td_mcp_plan=self._recipe_mcp_plan(recipe_id, req, td_params.model_dump()),
        )

        return OrchestratorOutput(
            template=template,
            td_params=td_params,
            asset_plan=asset_plan,
            routing=RoutingTrace(),
        )

    def _select_recipe_from_prompt(self, prompt: str) -> str:
        text = prompt.lower()
        glitch_kw = ['glitch', 'cyber', 'neon', 'digital', 'distort', 'chaos', 'pixel', 'scan',
                     '글리치', '사이버', '네온', '디지털', '왜곡', '혼돈', '픽셀']
        orb_kw = ['3d', 'sphere', 'cube', 'object', 'sculpture', 'geometry', 'orb', 'minimal',
                  '입체', '오브젝트', '조형', '기하', '구', '큐브', '조각']
        for kw in glitch_kw:
            if kw in text:
                return 'glitch_feedback_field'
        for kw in orb_kw:
            if kw in text:
                return 'soft_3d_orb'
        return 'dreamy_particle_field'

    def _heuristic_recipe_params(self, prompt: str, recipe_id: str) -> dict[str, Any]:
        text = prompt.lower()

        def has(*tokens: str) -> bool:
            return any(token in text for token in tokens)

        if has('neon', 'cyber', 'glitch', 'digital', 'laser', '네온', '사이버', '글리치'):
            palette = 'neon'
        elif has('warm', 'sun', 'fire', 'gold', 'orange', '노을', '불', '따뜻'):
            palette = 'warm'
        elif has('ocean', 'water', 'ice', 'blue', 'underwater', '바다', '물', '푸른', '차가운'):
            palette = 'cool'
        elif has('dream', 'soft', 'pastel', 'cloud', '몽환', '부드러운', '파스텔'):
            palette = 'pastel'
        else:
            palette = 'monochrome'

        energetic = has('fast', 'storm', 'chaos', 'explosion', 'aggressive', '빠른', '폭발', '혼돈')
        calm = has('slow', 'calm', 'quiet', 'gentle', '명상', '고요', '느린')
        dense = has('dense', 'many', 'swarm', 'particles', '많은', '밀도', '입자', '군집')
        strong_noise = has('turbulent', 'rough', 'distort', 'wave', '왜곡', '거친', '파동')
        bright = has('glow', 'light', 'bright', 'shine', '빛', '발광', '반짝')

        params = {
            'color_palette': palette,
            'noise_strength': 0.42 if strong_noise else 0.22 if calm else 0.3,
            'motion_speed': 0.78 if energetic else 0.25 if calm else 0.48,
            'feedback_opacity': 0.92 if recipe_id == 'glitch_feedback_field' else 0.82,
            'glow': 0.86 if bright or recipe_id != 'glitch_feedback_field' else 0.58,
            'particle_density': 0.86 if dense else 0.62,
        }
        if recipe_id == 'soft_3d_orb':
            params['feedback_opacity'] = 0.58
            params['particle_density'] = 0.5 if calm else params['particle_density']
        return params

    def _recipe_mcp_plan(self, recipe_id: str, req: GenerateRequest, td_params: dict[str, Any]) -> dict[str, Any]:
        recipes = {
            'dreamy_particle_field': [
                ('field_noise', 'noiseTOP'),
                ('flow_noise', 'noiseTOP'),
                ('base_grade', 'levelTOP'),
                ('soft_blur', 'blurTOP'),
                ('edge_trace', 'edgeTOP'),
                ('edge_grade', 'levelTOP'),
                ('edge_glow', 'blurTOP'),
                ('screen_mix', 'compositeTOP'),
                ('motion_feedback', 'feedbackTOP'),
                ('drift_transform', 'transformTOP'),
                ('final_grade', 'levelTOP'),
                ('generated_out', 'nullTOP'),
            ],
            'glitch_feedback_field': [
                ('glitch_noise', 'noiseTOP'),
                ('displace_vector', 'noiseTOP'),
                ('edge_detect', 'edgeTOP'),
                ('harsh_grade', 'levelTOP'),
                ('pixel_displace', 'displaceTOP'),
                ('threshold_cut', 'thresholdTOP'),
                ('glitch_feedback', 'feedbackTOP'),
                ('scan_drift', 'transformTOP'),
                ('feedback_mix', 'compositeTOP'),
                ('signal_limit', 'limitTOP'),
                ('final_glow', 'blurTOP'),
                ('generated_out', 'nullTOP'),
            ],
            'soft_3d_orb': [
                ('orb_geo', 'geometryCOMP'),
                ('orb_shape', 'sphereSOP'),
                ('orb_deform', 'noiseSOP'),
                ('orb_rotate', 'transformSOP'),
                ('cam1', 'cameraCOMP'),
                ('light1', 'lightCOMP'),
                ('render1', 'renderTOP'),
                ('post_glow', 'blurTOP'),
                ('render_glow_mix', 'compositeTOP'),
                ('render_grade', 'levelTOP'),
                ('orb_feedback', 'feedbackTOP'),
                ('final_soften', 'blurTOP'),
                ('generated_out', 'nullTOP'),
            ],
        }
        node_pairs = recipes.get(recipe_id, recipes['dreamy_particle_field'])
        nodes = [
            {'id': name, 'type': op_type, 'name': name, 'params': {}}
            for name, op_type in node_pairs
        ]
        connections = [
            {'from': nodes[index]['id'], 'to': nodes[index + 1]['id']}
            for index in range(len(nodes) - 1)
        ]
        return {
            'version': 'autotd-recipe-v1',
            'recipe_id': recipe_id,
            'intent': req.prompt,
            'source_options': {'generate_image': False, 'generate_3d': False},
            'reset_scope': '/project1/autotd_generated',
            'output': 'generated_out',
            'nodes': nodes,
            'connections': connections,
            'recipe_params': {
                'color_palette': td_params.get('color_palette'),
                'noise_strength': td_params.get('noise_strength'),
                'motion_speed': td_params.get('motion_speed'),
                'feedback_opacity': td_params.get('feedback_opacity'),
                'glow': td_params.get('glow'),
                'particle_density': td_params.get('particle_density'),
            },
            'notes': 'MVP uses a verified hardcoded TouchDesigner recipe; Ollama only selects recipe_id and parameters.',
        }

    def _select_recipe_from_prompt(self, prompt: str) -> str:
        text = prompt.lower()
        particle_kw = [
            'particle', 'particles', 'starlight', 'star', 'dust', 'light', 'floating',
            'sparkle', 'sparkles', 'glitter', 'firefly', '입자', '별빛', '별가루',
            '먼지', '빛', '부유', '반짝', '가루',
        ]
        feedback_kw = [
            'water', 'sea', 'ocean', 'fog', 'mist', 'dream', 'flow', 'soft',
            'underwater', 'liquid', 'wave', '물', '바다', '안개', '꿈', '흐름',
            '부드러운', '수중', '액체', '파도',
        ]
        for kw in particle_kw:
            if kw in text:
                return 'particle_field'
        for kw in feedback_kw:
            if kw in text:
                return 'feedback_2d'
        return 'feedback_2d'

    def _heuristic_recipe_params(self, prompt: str, recipe_id: str) -> dict[str, Any]:
        text = prompt.lower()

        def has(*tokens: str) -> bool:
            return any(token in text for token in tokens)

        energetic = has('fast', 'storm', 'chaos', 'explosion', 'aggressive', '빠른', '폭발', '혼돈')
        calm = has('slow', 'calm', 'quiet', 'gentle', '명상', '고요', '느린')
        dense = has('dense', 'many', 'swarm', 'particles', '많은', '밀도', '입자', '군집', '가득')
        strong_noise = has('turbulent', 'rough', 'distort', 'wave', '왜곡', '거친', '파동')
        bright = has('glow', 'light', 'bright', 'shine', '빛', '발광', '반짝')
        water = has('ocean', 'water', 'sea', 'underwater', 'fog', 'mist', '바다', '물', '수중', '안개')

        if recipe_id == 'particle_field':
            return {
                'concept_summary': '작은 빛 입자와 먼지가 어두운 공간에 부유하는 파티클 필드',
                'visual_mood': 'floating luminous particles',
                'use_comfyui_image': True,
                'image_usage': 'particle_sprite' if bright else 'background_composite',
                'comfyui_prompt': 'small glowing particle sprite texture, dust motes, starry haze, dark background, no text',
                'comfyui_negative_prompt': 'text, watermark, logo, low quality, hard edges, ugly artifacts',
                'speed': 0.72 if energetic else 0.32 if calm else 0.5,
                'noise_strength': 0.38 if strong_noise else 0.24,
                'feedback_opacity': 0.76,
                'blur_amount': 0.34,
                'displace_weight': 0.18,
                'glow_intensity': 0.88 if bright else 0.72,
                'particle_count': 1400 if dense else 800,
                'spread': 0.86,
                'color_1': '#ffffff',
                'color_2': '#b8d8ff',
                'color_3': '#050507',
            }

        return {
            'concept_summary': '안개와 흐름이 겹쳐지는 부드러운 2D 피드백 미디어아트',
            'visual_mood': 'soft flowing fog',
            'use_comfyui_image': bool(water or strong_noise),
            'image_usage': 'fog_overlay' if water else 'background_composite',
            'comfyui_prompt': 'abstract soft fog texture, underwater haze, flowing translucent gradients, no text',
            'comfyui_negative_prompt': 'text, watermark, logo, low quality, hard edges, ugly artifacts',
            'speed': 0.68 if energetic else 0.24 if calm else 0.45,
            'noise_strength': 0.42 if strong_noise else 0.22 if calm else 0.3,
            'feedback_opacity': 0.9,
            'blur_amount': 0.62,
            'displace_weight': 0.28 if strong_noise else 0.16,
            'glow_intensity': 0.76 if bright else 0.62,
            'particle_count': 350,
            'spread': 0.55,
            'color_1': '#dfefff' if water else '#f3f0ff',
            'color_2': '#5f789b' if water else '#9aa0c8',
            'color_3': '#05070a',
        }

    def _recipe_mcp_plan(self, recipe_id: str, req: GenerateRequest, td_params: dict[str, Any]) -> dict[str, Any]:
        recipes = {
            'feedback_2d': [
                ('source_image', 'moviefileinTOP'),
                ('base_noise', 'noiseTOP'),
                ('color_ramp', 'rampTOP'),
                ('seed_composite', 'compositeTOP'),
                ('feedback_loop', 'feedbackTOP'),
                ('feedback_transform', 'transformTOP'),
                ('flow_displace', 'displaceTOP'),
                ('feedback_echo', 'feedbackTOP'),
                ('echo_composite', 'compositeTOP'),
                ('soft_blur', 'blurTOP'),
                ('tone_level', 'levelTOP'),
                ('image_composite', 'compositeTOP'),
                ('generated_out', 'nullTOP'),
                ('out1', 'outTOP'),
            ],
            'particle_field': [
                ('source_image', 'moviefileinTOP'),
                ('black_background', 'constantTOP'),
                ('particle_geo', 'geometryCOMP'),
                ('particle_volume', 'spherePOP'),
                ('source_particles', 'sprinklePOP'),
                ('particle_motion', 'transformPOP'),
                ('particle_trails', 'trailPOP'),
                ('particle_material', 'constantMAT'),
                ('particle_cam', 'cameraCOMP'),
                ('particle_light', 'lightCOMP'),
                ('pop_render', 'renderTOP'),
                ('particle_level', 'levelTOP'),
                ('particle_feedback', 'feedbackTOP'),
                ('particle_drift', 'transformTOP'),
                ('echo_feedback', 'feedbackTOP'),
                ('particle_glow', 'blurTOP'),
                ('black_particle_composite', 'compositeTOP'),
                ('particle_composite', 'compositeTOP'),
                ('generated_out', 'nullTOP'),
                ('out1', 'outTOP'),
            ],
        }
        node_pairs = recipes.get(recipe_id, recipes['feedback_2d'])
        nodes = [{'id': name, 'type': op_type, 'name': name, 'params': {}} for name, op_type in node_pairs]
        connections = [{'from': nodes[index]['id'], 'to': nodes[index + 1]['id']} for index in range(len(nodes) - 1)]
        return {
            'version': 'autotd-2d-recipe-v1',
            'recipe_id': recipe_id,
            'intent': req.prompt,
            'reset_scope': '/project1/autotd_generated',
            'output': 'generated_out',
            'nodes': nodes,
            'connections': connections,
            'parameters': {
                'speed': td_params.get('speed'),
                'noise_strength': td_params.get('noise_strength'),
                'feedback_opacity': td_params.get('feedback_opacity'),
                'blur_amount': td_params.get('blur_amount'),
                'displace_weight': td_params.get('displace_weight'),
                'glow_intensity': td_params.get('glow_intensity'),
                'particle_count': td_params.get('particle_count'),
                'spread': td_params.get('spread'),
                'color_1': td_params.get('color_1'),
                'color_2': td_params.get('color_2'),
                'color_3': td_params.get('color_3'),
            },
            'notes': 'Ollama selects a stable 2D recipe and parameters; TouchDesigner builds the verified chain.',
        }

    def _select_recipe_from_prompt(self, prompt: str) -> str:
        text = prompt.lower()
        particle_kw = [
            'particle', 'particles', 'starlight', 'star', 'dust', 'light', 'floating',
            'sparkle', 'sparkles', 'glitter', 'firefly',
            '입자', '파티클', '별빛', '별가루', '먼지', '빛', '부유', '반짝', '반짝임',
        ]
        feedback_kw = [
            'water', 'sea', 'ocean', 'fog', 'mist', 'dream', 'flow', 'soft',
            'underwater', 'liquid', 'wave',
            '물', '바다', '수중', '안개', '꿈', '흐름', '흐르는', '부드러운', '왜곡',
        ]
        if any(token in text for token in particle_kw):
            return 'particle_field'
        if any(token in text for token in feedback_kw):
            return 'feedback_2d'
        return 'feedback_2d'

    def _heuristic_recipe_params(self, prompt: str, recipe_id: str) -> dict[str, Any]:
        text = prompt.lower()

        def has(*tokens: str) -> bool:
            return any(token in text for token in tokens)

        energetic = has('fast', 'storm', 'chaos', 'explosion', 'aggressive', '빠른', '폭발', '격렬')
        calm = has('slow', 'calm', 'quiet', 'gentle', '느린', '고요', '명상', '잔잔')
        dense = has('dense', 'many', 'swarm', 'particles', '가득', '많은', '입자', '파티클', '별가루')
        strong_noise = has('turbulent', 'rough', 'distort', 'wave', '거친', '왜곡', '파동')
        bright = has('glow', 'light', 'bright', 'shine', '빛', '발광', '반짝')
        water = has('ocean', 'water', 'sea', 'underwater', 'fog', 'mist', '바다', '물', '수중', '안개')

        if recipe_id == 'particle_field':
            return {
                'concept_summary': '작은 빛 입자와 별가루가 어두운 공간에 부유하는 파티클 필드',
                'visual_mood': 'floating luminous particles',
                'use_comfyui_image': True,
                'image_usage': 'particle_sprite' if bright else 'background_composite',
                'comfyui_prompt': 'small glowing particle sprite texture, dust motes, starry haze, dark background, no text',
                'comfyui_negative_prompt': 'text, watermark, logo, low quality, hard edges, ugly artifacts',
                'speed': 0.72 if energetic else 0.32 if calm else 0.5,
                'noise_strength': 0.38 if strong_noise else 0.24,
                'feedback_opacity': 0.76,
                'blur_amount': 0.34,
                'displace_weight': 0.18,
                'glow_intensity': 0.88 if bright else 0.72,
                'particle_count': 1400 if dense else 800,
                'spread': 0.86,
                'color_1': '#ffffff',
                'color_2': '#b8d8ff',
                'color_3': '#050507',
            }

        return {
            'concept_summary': '안개와 흐름이 겹쳐지는 부드러운 2D 피드백 미디어아트',
            'visual_mood': 'soft flowing fog',
            'use_comfyui_image': bool(water or strong_noise),
            'image_usage': 'fog_overlay' if water else 'background_composite',
            'comfyui_prompt': 'abstract soft fog texture, underwater haze, flowing translucent gradients, no text',
            'comfyui_negative_prompt': 'text, watermark, logo, low quality, hard edges, ugly artifacts',
            'speed': 0.68 if energetic else 0.24 if calm else 0.45,
            'noise_strength': 0.42 if strong_noise else 0.22 if calm else 0.3,
            'feedback_opacity': 0.9,
            'blur_amount': 0.62,
            'displace_weight': 0.28 if strong_noise else 0.16,
            'glow_intensity': 0.76 if bright else 0.62,
            'particle_count': 350,
            'spread': 0.55,
            'color_1': '#dfefff' if water else '#f3f0ff',
            'color_2': '#5f789b' if water else '#9aa0c8',
            'color_3': '#05070a',
        }

    def _heuristic_recipe_params(self, prompt: str, recipe_id: str) -> dict[str, Any]:
        """Procedural-only fallback parameters used when the LLM is slow or malformed."""
        text = (prompt or '').lower()

        def has(*tokens: str) -> bool:
            return any(token in text for token in tokens)

        energetic = has('fast', 'storm', 'chaos', 'explosion', 'aggressive', 'rapid', 'intense', '빠른', '폭발', '격렬')
        calm = has('slow', 'calm', 'quiet', 'gentle', 'soft', 'dream', '느린', '고요', '명상', '부드러운')
        dense = has('dense', 'many', 'swarm', 'particles', 'dust', 'stars', 'sparkles', '많은', '입자', '별가루', '먼지')
        turbulent = has('turbulent', 'rough', 'distort', 'wave', 'glitch', '거친', '왜곡', '파동')
        bright = has('glow', 'light', 'bright', 'shine', 'neon', '빛', '발광', '반짝')
        water = has('ocean', 'water', 'sea', 'underwater', 'fog', 'mist', 'liquid', '바다', '물', '수중', '안개')

        if recipe_id == 'particle_field':
            return {
                'concept_summary': 'Black-space particle field with layered glow trails and slow procedural drift.',
                'visual_mood': 'floating luminous stardust',
                'use_comfyui_image': False,
                'image_usage': 'none',
                'comfyui_prompt': '',
                'comfyui_negative_prompt': '',
                'speed': 0.72 if energetic else 0.28 if calm else 0.48,
                'noise_strength': 0.46 if turbulent else 0.26,
                'feedback_opacity': 0.86,
                'blur_amount': 0.48 if bright else 0.34,
                'displace_weight': 0.14,
                'glow_intensity': 0.94 if bright else 0.78,
                'particle_count': 1800 if dense else 950,
                'spread': 0.88,
                'color_1': '#fff7d6',
                'color_2': '#48e5ff',
                'color_3': '#020207',
            }

        return {
            'concept_summary': 'Layered 2D feedback field built from internal noise, ramps, displacement, echo loops, and color grading.',
            'visual_mood': 'liquid dream feedback' if water else 'procedural feedback haze',
            'use_comfyui_image': False,
            'image_usage': 'none',
            'comfyui_prompt': '',
            'comfyui_negative_prompt': '',
            'speed': 0.68 if energetic else 0.22 if calm else 0.44,
            'noise_strength': 0.58 if turbulent else 0.34 if water else 0.42,
            'feedback_opacity': 0.93,
            'blur_amount': 0.66 if calm or water else 0.52,
            'displace_weight': 0.42 if turbulent or water else 0.26,
            'glow_intensity': 0.82 if bright else 0.68,
            'particle_count': 420,
            'spread': 0.58,
            'color_1': '#58e6ff' if water else '#ff7adf',
            'color_2': '#6f7dff' if water else '#6ee7ff',
            'color_3': '#031018' if water else '#090416',
        }

    def _recipe_mcp_plan(self, recipe_id: str, req: GenerateRequest, td_params: dict[str, Any]) -> dict[str, Any]:
        recipes = {
            'feedback_2d': [
                ('base_noise_a', 'noiseTOP'),
                ('base_noise_b', 'noiseTOP'),
                ('control_lfo', 'lfoCHOP'),
                ('color_ramp_primary', 'rampTOP'),
                ('color_ramp_secondary', 'rampTOP'),
                ('seed_composite', 'compositeTOP'),
                ('feedback_loop_a', 'feedbackTOP'),
                ('feedback_transform_a', 'transformTOP'),
                ('flow_displace_a', 'displaceTOP'),
                ('feedback_loop_b', 'feedbackTOP'),
                ('feedback_transform_b', 'transformTOP'),
                ('echo_composite', 'compositeTOP'),
                ('mist_blur', 'blurTOP'),
                ('glow_blur', 'blurTOP'),
                ('color_filter_level', 'levelTOP'),
                ('final_composite', 'compositeTOP'),
                ('generated_out', 'nullTOP'),
                ('recipe_out', 'outTOP'),
            ],
            'particle_field': [
                ('black_background', 'constantTOP'),
                ('particle_seed_noise', 'noiseTOP'),
                ('particle_mask_threshold', 'thresholdTOP'),
                ('particle_sharpen_level', 'levelTOP'),
                ('particle_feedback_a', 'feedbackTOP'),
                ('particle_drift_transform', 'transformTOP'),
                ('particle_feedback_b', 'feedbackTOP'),
                ('trail_blur_small', 'blurTOP'),
                ('trail_blur_wide', 'blurTOP'),
                ('particle_color_level', 'levelTOP'),
                ('sparkle_composite', 'compositeTOP'),
                ('black_screen_composite', 'compositeTOP'),
                ('generated_out', 'nullTOP'),
                ('recipe_out', 'outTOP'),
            ],
        }
        node_pairs = recipes.get(recipe_id, recipes['feedback_2d'])
        nodes = [{'id': name, 'type': op_type, 'name': name, 'params': {}} for name, op_type in node_pairs]
        connections = []
        for index in range(len(nodes) - 1):
            connections.append({'from': nodes[index]['id'], 'to': nodes[index + 1]['id']})
        if recipe_id == 'feedback_2d':
            connections.extend([
                {'from': 'base_noise_b', 'to': 'flow_displace_a', 'to_input': 1},
                {'from': 'color_ramp_secondary', 'to': 'echo_composite', 'to_input': 1},
                {'from': 'mist_blur', 'to': 'final_composite', 'to_input': 1},
                {'from': 'glow_blur', 'to': 'final_composite', 'to_input': 2},
            ])
        else:
            connections.extend([
                {'from': 'black_background', 'to': 'black_screen_composite', 'to_input': 0},
                {'from': 'trail_blur_small', 'to': 'sparkle_composite', 'to_input': 1},
                {'from': 'trail_blur_wide', 'to': 'black_screen_composite', 'to_input': 1},
            ])
        return {
            'version': 'autotd-procedural-rich-v2',
            'recipe_id': recipe_id,
            'intent': req.prompt,
            'source_options': {'generate_image': False, 'generate_3d': False},
            'reset_scope': '/project1/autotd_generated',
            'output': 'generated_out',
            'nodes': nodes,
            'connections': connections,
            'parameters': {
                'speed': td_params.get('speed'),
                'noise_strength': td_params.get('noise_strength'),
                'feedback_opacity': td_params.get('feedback_opacity'),
                'blur_amount': td_params.get('blur_amount'),
                'displace_weight': td_params.get('displace_weight'),
                'glow_intensity': td_params.get('glow_intensity'),
                'particle_count': td_params.get('particle_count'),
                'spread': td_params.get('spread'),
                'color_1': td_params.get('color_1'),
                'color_2': td_params.get('color_2'),
                'color_3': td_params.get('color_3'),
            },
            'operator_policy': {
                'same_role_nodes_allowed': True,
                'families_allowed': ['TOP', 'SOP', 'POP', 'CHOP', 'COMP', 'DAT', 'MAT'],
                'python_dat_helpers_allowed': True,
                'external_assets_enabled': False,
            },
            'notes': 'Procedural-only rich TD plan: repeated feedback/blur/transform/composite nodes are intentional.',
        }

    def _coerce_payload_shape(self, payload: Any, req: GenerateRequest) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return self._upgrade_flat_payload({}, req)
        if 'td_params' not in payload:
            return self._upgrade_flat_payload(payload, req)
        td_params = payload.get('td_params')
        asset_plan = payload.get('asset_plan')
        if not isinstance(td_params, dict):
            td_params = {}
        if not isinstance(asset_plan, dict):
            asset_plan = {}
        if 'td_mcp_plan' not in asset_plan and isinstance(payload.get('td_mcp_plan'), dict):
            asset_plan['td_mcp_plan'] = payload['td_mcp_plan']
        return {
            'template': payload.get('template', td_params.get('template', 'particle')),
            'td_params': td_params,
            'asset_plan': asset_plan,
        }

    def _sanitize_td_params(self, data: dict[str, Any], req: GenerateRequest) -> dict[str, Any]:
        clean = dict(data)
        clean['template'] = self._coerce_template(clean.get('template'), req.prompt)
        clean['style'] = str(clean.get('style') or req.style).lower()
        clean['motion'] = str(clean.get('motion') or req.motion).lower()
        clean['color_mode'] = str(clean.get('color_mode') or req.color).lower()
        keywords = clean.get('keywords', [req.style, req.motion, req.color])
        if isinstance(keywords, str):
            keywords = [part.strip() for part in re.split(r'[,/]', keywords) if part.strip()]
        clean['keywords'] = [str(item) for item in keywords] if isinstance(keywords, list) else [req.style, req.motion, req.color]
        clean['description'] = str(clean.get('description') or req.prompt)
        clean['mood'] = str(clean.get('mood') or clean['style'])
        for key, default in {
            'color_r': 1.0, 'color_g': 1.0, 'color_b': 1.0,
            'speed': 0.5, 'density': 0.7, 'turbulence': 0.3, 'brightness': 0.8,
        }.items():
            clean[key] = self._clamp_float(clean.get(key, default), 0.0, 1.0, default)
        clean['scale'] = self._clamp_float(clean.get('scale', 1.0), 0.5, 2.0, 1.0)
        clean['asset_image_path'] = str(clean.get('asset_image_path') or '')
        clean['asset_3d_path'] = str(clean.get('asset_3d_path') or '')
        return clean

    def _sanitize_asset_plan(
        self,
        data: dict[str, Any],
        req: GenerateRequest,
        td_params: dict[str, Any],
    ) -> dict[str, Any]:
        clean = dict(data)
        prompt_lower = req.prompt.lower()
        wants_image_source = any(token in prompt_lower for token in [
            'image', 'source', 'texture', 'reference', 'photo',
            '이미지', '소스', '텍스처', '레퍼런스', '사진', '자산',
        ])
        clean['needs_image_asset'] = bool(clean.get('needs_image_asset', False) or wants_image_source)
        clean['needs_3d_asset'] = bool(clean.get('needs_3d_asset', False) or td_params.get('template') == '3d')
        clean['image_prompt'] = str(clean.get('image_prompt') or (req.prompt if clean['needs_image_asset'] else ''))
        clean['image_negative_prompt'] = str(clean.get('image_negative_prompt') or '')
        clean['trellis_prompt'] = str(clean.get('trellis_prompt') or (req.prompt if clean['needs_3d_asset'] else ''))
        clean['operator_brief'] = str(clean.get('operator_brief') or td_params.get('description') or req.prompt)
        clean['model_hint'] = str(clean.get('model_hint') or '')
        # Procedural-only mode: external image/3D assets are intentionally disabled.
        clean['needs_image_asset'] = False
        clean['needs_3d_asset'] = False
        clean['image_prompt'] = ''
        clean['image_negative_prompt'] = str(clean.get('image_negative_prompt') or '')
        clean['image_usage'] = 'none'
        clean['trellis_prompt'] = ''
        clean['object_description'] = ''
        clean['asset_usage'] = 'none'
        plan = self._sanitize_mcp_plan(clean.get('td_mcp_plan'), req, td_params)
        clean['td_mcp_plan'] = plan or self._default_mcp_plan(req, td_params)
        clean['td_mcp_plan']['operator_brief'] = clean['operator_brief'][:1800]
        return clean

    def _sanitize_mcp_plan(
        self,
        plan: Any,
        req: GenerateRequest,
        td_params: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(plan, dict):
            return None
        allowed = {
            # TOP operators (2-D compositing)
            'noiseTOP', 'levelTOP', 'blurTOP', 'edgeTOP', 'transformTOP',
            'feedbackTOP', 'compositeTOP', 'nullTOP', 'selectTOP',
            'constantTOP', 'rampTOP', 'displaceTOP', 'lookupTOP',
            'monochromeTOP', 'thresholdTOP', 'cacheTOP', 'limitTOP',
            'moviefileinTOP',
            # SOP operators (3-D geometry)
            'sopCreate', 'sopTransform', 'sopNoise', 'sopMerge', 'sopSelect',
            'sopSprite', 'sopLimit', 'sopPoint', 'sopCache', 'sopCarve',
            'sopFacet', 'sopExtrude', 'sopPolyExtrude',
            # POP operators (particles)
            'popNet', 'popEmitter', 'popForce', 'popGravity', 'popWind',
            'popVortex', 'popColor', 'popKill', 'popSprite', 'popMerge',
            'popSpeed', 'popSOPSolve', 'popStream',
        }
        safe_params = {
            'seed', 'period', 'harmon', 'harmonics', 'rough', 'amp', 'mono',
            'brightness', 'contrast', 'gamma', 'blacklevel',
            'sizex', 'sizey', 'tx', 'ty', 'tz', 'scale', 'rotate',
            'operand', 'outputresolution', 'resolutionw', 'resolutionh', 'top', 'opacity',
            'level', 'multiply', 'displaceweight',
        }
        nodes: list[dict[str, Any]] = []
        name_map: dict[str, str] = {}
        for index, node in enumerate(plan.get('nodes') or []):
            if not isinstance(node, dict):
                continue
            original_name = str(node.get('id') or node.get('name') or f'node_{index}')
            node_type = self._coerce_node_type(str(node.get('type') or ''), original_name)
            if node_type not in allowed:
                continue
            name = self._safe_node_name(str(node.get('name') or original_name or node_type), index)
            params = self._sanitize_node_params(node_type, node.get('params') or {}, safe_params)
            self._apply_node_defaults(node_type, params)
            clean_node = {
                'id': self._safe_node_name(str(node.get('id') or name), index),
                'type': node_type,
                'name': name,
                'params': params,
            }
            nodes.append(clean_node)
            name_map[original_name] = clean_node['id']
            name_map[clean_node['name']] = clean_node['id']
            name_map[clean_node['id']] = clean_node['id']
        if not nodes:
            return None
        if all(node['type'] != 'nullTOP' for node in nodes):
            nodes.append({'id': 'out', 'type': 'nullTOP', 'name': 'generated_out', 'params': {}})
        else:
            nodes[-1]['name'] = 'generated_out'
        connections: list[dict[str, Any]] = []
        for conn in plan.get('connections') or []:
            if not isinstance(conn, dict):
                continue
            src = conn.get('from') or conn.get('source')
            dst = conn.get('to') or conn.get('target')
            if src in name_map and dst in name_map:
                clean_conn = {'from': name_map[src], 'to': name_map[dst]}
                if isinstance(conn.get('to_input'), int):
                    clean_conn['to_input'] = conn['to_input']
                connections.append(clean_conn)
        if not connections and len(nodes) > 1:
            connections = [{'from': nodes[i]['id'], 'to': nodes[i + 1]['id']} for i in range(len(nodes) - 1)]
        if len(nodes) < 10:
            fallback = self._analysis_mcp_plan(
                req,
                td_params,
                str(plan.get('operator_brief') or plan.get('notes') or req.prompt),
            )
            fallback['notes'] = (
                'Ollama returned fewer than 10 usable nodes, so AutoTD rebuilt the plan '
                'from the operator brief and prompt analysis.'
            )
            return fallback

        return {
            'version': 'autotd-plan-v1',
            'intent': str(plan.get('intent') or req.prompt),
            'reset_scope': '/project1/autotd_generated',
            'output': 'generated_out',
            'nodes': nodes[:16],
            'connections': connections,
            'notes': str(plan.get('notes') or 'Ollama-designed plan sanitized for TouchDesigner MCP execution.'),
            'operator_brief': str(plan.get('operator_brief') or plan.get('notes') or req.prompt)[:1800],
        }

    def _coerce_template(self, value: Any, prompt: str) -> str:
        text = str(value or '').lower()
        if text in {'particle', 'feedback', '3d'}:
            return text
        if '3d' in text or '3d' in prompt.lower():
            return '3d'
        if 'feedback' in text or 'feedback' in prompt.lower():
            return 'feedback'
        return 'particle'

    def _coerce_node_type(self, node_type: str, name: str) -> str:
        text = f'{node_type} {name}'.lower()
        # SOP candidates – checked first so 'noise' in a SOP context stays as sopNoise
        sop_candidates = [
            ('sopcreate', 'sopCreate'), ('soptransform', 'sopTransform'),
            ('sopnoise', 'sopNoise'), ('sopmerge', 'sopMerge'),
            ('sopselect', 'sopSelect'), ('sopsprite', 'sopSprite'),
            ('soplimit', 'sopLimit'), ('soppoint', 'sopPoint'),
            ('sopcache', 'sopCache'), ('sopcarve', 'sopCarve'),
            ('sopfacet', 'sopFacet'), ('sopextrude', 'sopExtrude'),
            ('soppolyextrude', 'sopPolyExtrude'),
        ]
        for key, canonical in sop_candidates:
            if key in text:
                return canonical
        # POP candidates
        pop_candidates = [
            ('popnet', 'popNet'), ('popemitter', 'popEmitter'),
            ('popforce', 'popForce'), ('popgravity', 'popGravity'),
            ('popwind', 'popWind'), ('popvortex', 'popVortex'),
            ('popcolor', 'popColor'), ('popkill', 'popKill'),
            ('popsprite', 'popSprite'), ('popmerge', 'popMerge'),
            ('popspeed', 'popSpeed'), ('popsopsolve', 'popSOPSolve'),
            ('popstream', 'popStream'),
        ]
        for key, canonical in pop_candidates:
            if key in text:
                return canonical
        # TOP candidates (fallback)
        for candidate in (
            'noise', 'level', 'blur', 'edge', 'transform', 'feedback',
            'composite', 'null', 'select', 'constant', 'ramp', 'displace',
            'lookup', 'monochrome', 'threshold', 'cache', 'limit', 'moviefilein',
        ):
            if candidate in text and 'sop' not in text and 'pop' not in text:
                return f'{candidate}TOP'
        return node_type

    def _sanitize_node_params(
        self,
        node_type: str,
        params: dict[str, Any],
        safe_params: set[str],
    ) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in params.items():
            normalized = str(key).lower()
            if normalized == 'size' and node_type == 'blurTOP':
                clean['sizex'] = self._clamp_float(value, 0.0, 20.0, 3.0)
                clean['sizey'] = self._clamp_float(value, 0.0, 20.0, 3.0)
                continue
            if normalized == 'blend' and node_type == 'compositeTOP':
                normalized = 'operand'
            if normalized not in safe_params:
                continue
            clean[normalized] = value
        return clean

    def _apply_node_defaults(self, node_type: str, params: dict[str, Any]) -> None:
        if node_type == 'noiseTOP':
            params.setdefault('seed', 0)
            params['period'] = max(0.12, self._clamp_float(params.get('period', 0.65), 0.12, 4.0, 0.65))
            params['harmon'] = max(3, int(self._clamp_float(params.get('harmon', 6), 3, 10, 6)))
            params['rough'] = max(0.2, self._clamp_float(params.get('rough', 0.55), 0.2, 1.0, 0.55))
            params['amp'] = max(0.65, self._clamp_float(params.get('amp', 1.0), 0.65, 1.0, 1.0))
            params.setdefault('mono', True)
            params.setdefault('outputresolution', 'custom')
            params.setdefault('resolutionw', 1280)
            params.setdefault('resolutionh', 720)
        elif node_type == 'levelTOP':
            params.setdefault('brightness', 0.78)
            params.setdefault('contrast', 1.35)
            params.setdefault('gamma', 0.9)
        elif node_type == 'blurTOP':
            params.setdefault('sizex', 2.5)
            params.setdefault('sizey', 2.5)
        elif node_type == 'compositeTOP':
            params.setdefault('operand', 'screen')
        elif node_type == 'displaceTOP':
            params.setdefault('displaceweight', 0.35)
        elif node_type == 'thresholdTOP':
            params.setdefault('level', 0.45)

    def _safe_node_name(self, value: str, index: int) -> str:
        safe = re.sub(r'[^A-Za-z0-9_]', '_', value.strip())[:40].strip('_')
        return safe or f'node_{index}'

    def _clamp_float(self, value: Any, low: float, high: float, default: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = default
        return max(low, min(high, num))

    def _hex_to_rgb01(self, value: str) -> tuple[float, float, float]:
        text = str(value or '').strip().lstrip('#')
        if len(text) != 6:
            return 1.0, 1.0, 1.0
        try:
            return tuple(int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4))  # type: ignore[return-value]
        except Exception:
            return 1.0, 1.0, 1.0

    def _default_mcp_plan(self, req: GenerateRequest, td_params: dict[str, Any]) -> dict[str, Any]:
        speed = float(td_params.get('speed', 0.5))
        density = float(td_params.get('density', 0.7))
        turbulence = float(td_params.get('turbulence', 0.3))
        brightness = float(td_params.get('brightness', 0.8))
        color_mode = str(td_params.get('color_mode', 'monochrome'))
        return {
            'version': 'autotd-plan-v1',
            'intent': req.prompt,
            'source_options': req.source_options.model_dump(),
            'reset_scope': '/project1/autotd_generated',
            'output': 'generated_out',
            'nodes': [
                {'id': 'field', 'type': 'noiseTOP', 'name': 'field_noise', 'params': {
                    'seed': 0,
                    'period': max(0.12, 2.0 - speed * 1.6),
                    'harmon': max(1, min(8, int(round(1 + density * 7)))),
                    'rough': max(0.05, min(1.0, 0.2 + turbulence * 0.7)),
                    'amp': max(0.05, min(1.0, 0.2 + density * 0.8)),
                    'mono': color_mode == 'monochrome',
                    'resolutionw': 1280,
                    'resolutionh': 720,
                }},
                {'id': 'flow', 'type': 'noiseTOP', 'name': 'flow_noise', 'params': {
                    'seed': 37,
                    'period': max(0.18, 1.35 - speed * 0.75),
                    'harmon': max(3, min(10, int(round(4 + turbulence * 6)))),
                    'rough': max(0.15, min(1.0, 0.35 + turbulence * 0.55)),
                    'amp': max(0.25, min(1.0, 0.45 + density * 0.45)),
                    'mono': True,
                    'resolutionw': 1280,
                    'resolutionh': 720,
                }},
                {'id': 'base_grade', 'type': 'levelTOP', 'name': 'base_grade', 'params': {
                    'brightness': brightness,
                    'contrast': 1.18,
                    'gamma': 0.92,
                }},
                {'id': 'base_blur', 'type': 'blurTOP', 'name': 'base_soft_blur', 'params': {'sizex': 2.5, 'sizey': 2.5}},
                {'id': 'edge_trace', 'type': 'edgeTOP', 'name': 'edge_trace', 'params': {}},
                {'id': 'edge_grade', 'type': 'levelTOP', 'name': 'edge_grade', 'params': {
                    'brightness': 0.72,
                    'contrast': 1.65,
                    'gamma': 0.86,
                }},
                {'id': 'edge_glow', 'type': 'blurTOP', 'name': 'edge_glow', 'params': {'sizex': 6, 'sizey': 6}},
                {'id': 'detail_mix', 'type': 'compositeTOP', 'name': 'detail_screen_mix', 'params': {'operand': 'screen'}},
                {'id': 'feedback', 'type': 'feedbackTOP', 'name': 'motion_feedback', 'params': {'opacity': 0.88}},
                {'id': 'drift', 'type': 'transformTOP', 'name': 'drift_transform', 'params': {
                    'tx': round(speed * 0.035, 4),
                    'ty': round(turbulence * 0.025, 4),
                    'scale': max(0.96, min(1.04, 1.0 + turbulence * 0.025)),
                    'rotate': round((speed - 0.5) * 4.0, 3),
                }},
                {'id': 'final_grade', 'type': 'levelTOP', 'name': 'final_gallery_grade', 'params': {
                    'brightness': min(1.0, brightness + 0.08),
                    'contrast': 1.28,
                    'gamma': 0.9,
                }},
                {'id': 'final_soften', 'type': 'blurTOP', 'name': 'final_soften', 'params': {'sizex': 1.2, 'sizey': 1.2}},
                {'id': 'out', 'type': 'nullTOP', 'name': 'generated_out', 'params': {}},
            ],
            'connections': [
                {'from': 'field', 'to': 'base_grade'},
                {'from': 'base_grade', 'to': 'base_blur'},
                {'from': 'flow', 'to': 'edge_trace'},
                {'from': 'edge_trace', 'to': 'edge_grade'},
                {'from': 'edge_grade', 'to': 'edge_glow'},
                {'from': 'base_blur', 'to': 'detail_mix', 'to_input': 0},
                {'from': 'edge_glow', 'to': 'detail_mix', 'to_input': 1},
                {'from': 'detail_mix', 'to': 'feedback'},
                {'from': 'feedback', 'to': 'drift'},
                {'from': 'drift', 'to': 'final_grade'},
                {'from': 'final_grade', 'to': 'final_soften'},
                {'from': 'final_soften', 'to': 'out'},
            ],
            'notes': 'Deep fallback procedural graph; generated scope is reset on every prompt.',
        }

    def _analysis_payload(self, raw_text: str, req: GenerateRequest, error: str = '') -> dict[str, Any]:
        brief = self._compact_operator_brief(raw_text, req, error)
        td_params = self._heuristic_td_params(req, brief)
        plan = self._analysis_mcp_plan(req, td_params, brief)
        prompt_text = f'{req.prompt}\n\nTouchDesigner operator brief:\n{brief}'
        text = f'{req.prompt} {brief}'.lower()
        needs_image = any(token in text for token in [
            'image', 'photo', 'texture', 'source', 'reference',
            '이미지', '사진', '텍스처', '소스', '레퍼런스',
        ])
        needs_image = bool(req.source_options.generate_image)
        needs_3d = bool(req.source_options.generate_3d)
        return {
            'template': td_params['template'],
            'td_params': td_params,
            'asset_plan': {
                'needs_image_asset': bool(needs_image),
                'needs_3d_asset': bool(needs_3d),
                'image_prompt': prompt_text if needs_image else '',
                'image_negative_prompt': 'low quality, blurry, watermark, text',
                'image_usage': 'background_composite',
                'trellis_prompt': prompt_text if needs_3d else '',
                'object_description': prompt_text if needs_3d else '',
                'asset_usage': 'geometry_source',
                'operator_brief': brief,
                'model_hint': 'analysis-derived-touchdesigner-plan',
                'td_mcp_plan': plan,
            },
        }

    def _compact_operator_brief(self, raw_text: str, req: GenerateRequest, error: str = '') -> str:
        text = re.sub(r'```json[\s\S]*?```', '', raw_text or '').strip()
        if not text:
            text = req.prompt
        text = re.sub(r'\s+', ' ', text)
        prefix = (
            f'Prompt: {req.prompt}. '
            'Use this analysis as the TouchDesigner operator-selection brief. '
        )
        suffix = f' JSON handoff issue: {error}.' if error else ''
        return (prefix + text + suffix)[:1800]

    def _heuristic_td_params(self, req: GenerateRequest, brief: str) -> dict[str, Any]:
        text = f'{req.prompt} {brief}'.lower()

        def has(*tokens: str) -> bool:
            return any(token in text for token in tokens)

        if has('3d', 'mesh', 'object', 'sculpture', 'geometry', '입체', '오브젝트', '조형', '공간', '메쉬'):
            template = '3d'
        elif has('feedback', 'loop', 'trail', 'afterimage', 'glitch', '잔상', '피드백', '루프', '글리치'):
            template = 'feedback'
        else:
            template = 'particle'

        if has('glitch', 'pixel', 'scan', 'datamosh', 'broken', '왜곡', '글리치', '픽셀', '스캔'):
            style = 'glitch'
        elif has('grid', 'line', 'polygon', 'cube', 'geometry', '기하', '격자', '선', '다면체'):
            style = 'geometric'
        elif has('smoke', 'fluid', 'organic', 'water', 'cloud', '연기', '유체', '물결', '구름', '유기'):
            style = 'organic'
        else:
            style = 'ethereal'

        if has('explosive', 'chaos', 'storm', 'turbulent', '폭발', '폭풍', '혼돈', '격렬'):
            motion = 'turbulent'
            speed = 0.9
            turbulence = 0.9
        elif has('pulse', 'beat', 'heartbeat', 'pulsing', '맥박', '박동', '비트'):
            motion = 'pulsing'
            speed = 0.68
            turbulence = 0.55
        elif has('still', 'minimal', 'quiet', '정적', '고요', '미니멀'):
            motion = 'static'
            speed = 0.12
            turbulence = 0.22
        else:
            motion = 'flowing'
            speed = 0.52
            turbulence = 0.52 if style in {'organic', 'glitch'} else 0.35

        if has('neon', 'cyber', 'electric', '네온', '사이버', '전기'):
            color_mode, color = 'neon', (0.2, 1.0, 0.82)
        elif has('warm', 'fire', 'sun', 'gold', 'orange', '따뜻', '불', '금색', '주황'):
            color_mode, color = 'warm', (1.0, 0.48, 0.16)
        elif has('cool', 'ice', 'blue', 'silver', 'cold', '차가', '푸른', '은색', '얼음'):
            color_mode, color = 'cool', (0.58, 0.72, 1.0)
        elif has('pastel', 'soft', 'dream', '파스텔', '부드러운', '몽환'):
            color_mode, color = 'pastel', (0.95, 0.82, 1.0)
        else:
            color_mode, color = 'monochrome', (1.0, 1.0, 1.0)

        density = 0.9 if has('many', 'dense', 'swarm', 'particle', '입자', '많은', '밀도', '군집') else 0.68
        brightness = 0.92 if has('bright', 'glow', 'light', '빛', '발광', '반짝') else 0.78
        keywords = [word for word in re.split(r'[\s,./]+', req.prompt) if word][:8]
        return {
            'template': template,
            'style': style,
            'motion': motion,
            'color_mode': color_mode,
            'color_r': color[0],
            'color_g': color[1],
            'color_b': color[2],
            'speed': speed,
            'density': density,
            'scale': 1.0,
            'turbulence': turbulence,
            'brightness': brightness,
            'keywords': keywords,
            'mood': style,
            'description': brief,
            'asset_image_path': '',
            'asset_3d_path': '',
        }

    def _analysis_mcp_plan(self, req: GenerateRequest, td_params: dict[str, Any], brief: str) -> dict[str, Any]:
        text = f'{req.prompt} {brief}'.lower()
        style = str(td_params.get('style', 'ethereal'))
        speed = float(td_params.get('speed', 0.5))
        density = float(td_params.get('density', 0.7))
        turbulence = float(td_params.get('turbulence', 0.4))
        brightness = float(td_params.get('brightness', 0.8))

        family = self._detect_operator_family(req.prompt)
        if req.source_options.generate_3d:
            family = 'sop'

        def mod(node_id, module_type, name, params=None, asset_path=None):
            return {
                'id': node_id,
                'type': module_type,
                'name': name,
                'params': params or {},
                'asset_path': asset_path or ''
            }

        nodes = []
        connections = []

        # 1. 3D Geometry family (SOP)
        if family == 'sop':
            shape_type = 'torus'
            if any(k in text for k in ['sphere', '구', '공']):
                shape_type = 'sphere'
            elif any(k in text for k in ['box', 'cube', '상자', '큐브']):
                shape_type = 'box'
            elif any(k in text for k in ['grid', 'plane', '격자', '평면']):
                shape_type = 'grid'

            if req.source_options.generate_3d:
                nodes.append(mod('asset_3d_loader', 'source_3d', 'asset_3d_loader'))
            else:
                nodes.append(mod('geo_src', 'geometry_generator', 'base_shape', {'type': shape_type, 'rad': 0.75}))

            if any(k in text for k in ['image', 'photo', 'texture', '3d asset', 'mesh', 'glb', 'obj', '메쉬', '모델']):
                nodes.append(mod('glb_src', 'source_3d', 'imported_mesh'))

            nodes.append(mod('geo_deform', 'geometry_deform', 'noise_deformer', {
                'amp2': turbulence * 0.8,
                'freq': 1.0 + turbulence
            }))

            nodes.append(mod('geo_trans', 'geometry_transform', 'transform_deformer', {
                'rx': speed * 45.0,
                'ry': speed * 90.0,
                'scale': 1.0 + turbulence * 0.1
            }))

            proc_type = 'facet'
            if any(k in text for k in ['extrude', 'polyextrude', '돌출', '두께']):
                proc_type = 'extrude'
            nodes.append(mod('geo_proc', 'geometry_process', 'faceting_polish', {'type': proc_type}))

            nodes.append(mod('post_blur', 'spatial_filter', 'render_micro_blur', {'sizex': 1.5, 'sizey': 1.5}))
            nodes.append(mod('post_grade', 'color_grade', 'contrast_grade', {'brightness': brightness, 'contrast': 1.25}))

            if speed > 0.4:
                nodes.append(mod('post_feed', 'feedback_loop', 'trails', {'opacity': 0.8 + speed * 0.15}))

            nodes.append(mod('post_comp', 'compositor', 'screen_mix', {'operand': 'screen'}))

            if turbulence > 0.6:
                nodes.append(mod('post_displace', 'displace_effect', 'noise_displacement', {'displaceweight': turbulence * 0.5}))

            nodes.append(mod('post_lim', 'signal_limiter', 'limit_safety'))
            nodes.append(mod('out', 'final_output', 'generated_out'))

        # 2. Particle simulation family (POP)
        elif family == 'pop':
            nodes.append(mod('pop_sim', 'particle_simulation', 'pop_simulation_net'))
            emit_rate = 200.0 + density * 600.0
            nodes.append(mod('pop_emit', 'particle_emitter', 'emitter_source', {'rate': emit_rate, 'life': 2.5}))
            nodes.append(mod('pop_grav', 'particle_force', 'gravity_force', {'forcey': -1.2}))
            nodes.append(mod('pop_wind', 'particle_force', 'turbulent_wind', {
                'forcex': speed * 3.0,
                'speed': turbulence * 2.5
            }))
            nodes.append(mod('post_blur', 'spatial_filter', 'particle_glow_blur', {'sizex': 3.5, 'sizey': 3.5}))
            nodes.append(mod('post_grade', 'color_grade', 'particle_grading', {'brightness': brightness, 'contrast': 1.3}))
            nodes.append(mod('post_feed', 'feedback_loop', 'motion_buffer', {'opacity': 0.88}))
            nodes.append(mod('post_comp', 'compositor', 'glow_multiply', {'operand': 'screen'}))
            nodes.append(mod('post_lim', 'signal_limiter', 'output_limit'))
            nodes.append(mod('out', 'final_output', 'generated_out'))

        # 3. 2D Texture filters (TOP)
        else:
            nodes.append(mod('noise_gen', 'procedural_noise', 'field_noise', {
                'seed': abs(hash(req.prompt)) % 10000,
                'period': max(0.12, 1.8 - speed * 1.25),
                'harmon': max(3, min(10, int(round(4 + density * 6)))),
                'rough': max(0.2, min(1.0, 0.25 + turbulence * 0.65)),
                'amp': max(0.55, min(1.0, 0.45 + density * 0.55))
            }))

            if any(k in text for k in ['image', 'photo', 'texture', 'source', '이미지', '사진', '소스']):
                nodes.append(mod('img_src', 'source_image', 'imported_source_image'))

            if req.source_options.generate_image and not any(node.get('type') == 'source_image' for node in nodes):
                nodes.insert(0, mod('image_asset_loader', 'source_image', 'image_asset_loader'))

            nodes.append(mod('base_grade', 'color_grade', 'contrast_grade', {
                'brightness': brightness,
                'contrast': 1.15 + turbulence * 0.2
            }))

            if style == 'glitch' or any(k in text for k in ['glitch', 'pixel', 'scan', '글리치', '픽셀']):
                nodes.append(mod('edge_ext', 'spatial_filter', 'edge_extract', {'type': 'edge'}))
                nodes.append(mod('edge_grade', 'color_grade', 'edge_boost', {'contrast': 1.7}))
                nodes.append(mod('soft_blur', 'spatial_filter', 'micro_soften', {'sizex': 1.2, 'sizey': 1.2}))
                nodes.append(mod('feed_trails', 'feedback_loop', 'afterimage_feedback', {'opacity': 0.86}))
                nodes.append(mod('feed_mix', 'compositor', 'feedback_merge', {'operand': 'screen'}))
                nodes.append(mod('post_lim', 'signal_limiter', 'limit_safety'))
                nodes.append(mod('out', 'final_output', 'generated_out'))

            elif style == 'geometric' or any(k in text for k in ['grid', 'polygon', 'geometric', '도형', '기하', '격자']):
                nodes.append(mod('edge_ext', 'spatial_filter', 'edge_extract', {'type': 'edge'}))
                nodes.append(mod('edge_grade', 'color_grade', 'edge_boost', {'contrast': 1.8}))
                nodes.append(mod('displace', 'displace_effect', 'vector_displace', {'displaceweight': turbulence * 0.7}))
                nodes.append(mod('flow_vector', 'procedural_noise', 'flow_vector_noise', {
                    'seed': (abs(hash(brief)) + 43) % 10000,
                    'period': 1.2
                }))
                nodes.append(mod('feed_trails', 'feedback_loop', 'motion_trails', {'opacity': 0.8}))
                nodes.append(mod('feed_mix', 'compositor', 'trails_merge', {'operand': 'screen'}))
                nodes.append(mod('post_lim', 'signal_limiter', 'limit_safety'))
                nodes.append(mod('out', 'final_output', 'generated_out'))

            else:
                nodes.append(mod('soft_blur', 'spatial_filter', 'soften_filter', {'sizex': 3.0, 'sizey': 3.0}))
                nodes.append(mod('feed_trails', 'feedback_loop', 'atmosphere_trails', {'opacity': 0.88}))
                nodes.append(mod('feed_mix', 'compositor', 'bloom_screen', {'operand': 'screen'}))
                nodes.append(mod('post_lim', 'signal_limiter', 'signal_limit'))
                nodes.append(mod('out', 'final_output', 'generated_out'))

        # Guarantee >= 10 nodes count using padding blurs/grades
        idx = 0
        while len(nodes) < 10:
            pad_id = f'pad_{idx}'
            nodes.insert(-2, mod(pad_id, 'color_grade', f'aesthetic_adjustment_{idx}', {'brightness': 1.0, 'contrast': 1.02}))
            idx += 1

        # Re-build sequential connections safely honoring composition bypass lines
        node_ids = [n['id'] for n in nodes]
        for i in range(len(node_ids) - 1):
            if node_ids[i] == 'flow_vector':
                connections.append({'from': 'flow_vector', 'to': 'displace', 'to_input': 1})
            elif node_ids[i] == 'img_src':
                connections.append({'from': 'img_src', 'to': 'base_grade'})
            elif node_ids[i] == 'glb_src':
                connections.append({'from': 'glb_src', 'to': 'geo_deform'})
            else:
                connections.append({'from': node_ids[i], 'to': node_ids[i+1]})

        if 'post_comp' in node_ids:
            connections.append({'from': 'post_blur', 'to': 'post_comp', 'to_input': 1})
        elif 'feed_mix' in node_ids:
            if 'soft_blur' in node_ids:
                connections.append({'from': 'soft_blur', 'to': 'feed_mix', 'to_input': 1})
            elif 'displace' in node_ids:
                connections.append({'from': 'displace', 'to': 'feed_mix', 'to_input': 1})

        return {
            'version': 'autotd-plan-v1',
            'intent': req.prompt,
            'source_options': req.source_options.model_dump(),
            'reset_scope': '/project1/autotd_generated',
            'output': 'generated_out',
            'nodes': nodes[:16],
            'connections': connections,
            'notes': f'Dynamic fallback composition for {family}/{style} art style.',
            'operator_brief': brief[:1800],
        }

    async def _repair_payload(self, raw_text: str, req: GenerateRequest, error: str) -> dict[str, Any]:
        if self.openai_api_key:
            try:
                return await self._repair_with_openai(raw_text, req, error)
            except Exception as exc:
                log.warning('OpenAI repair failed: %s', exc)
        return self._analysis_payload(raw_text, req, error)

    async def _repair_with_openai(self, raw_text: str, req: GenerateRequest, error: str) -> dict[str, Any]:
        system = (
            'Repair the malformed orchestration payload into valid JSON only. '
            'Return exactly one JSON object with keys template, td_params, asset_plan. '
            'Do not include markdown fences or commentary.'
        )
        user = (
            f'Validation error: {error}\n'
            f'Original prompt: {req.prompt}\n'
            f'Broken payload:\n{raw_text}'
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                'https://api.openai.com/v1/responses',
                headers={
                    'Authorization': f'Bearer {self.openai_api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.openai_repair_model,
                    'input': [
                        {
                            'role': 'system',
                            'content': [{'type': 'input_text', 'text': system}],
                        },
                        {
                            'role': 'user',
                            'content': [{'type': 'input_text', 'text': user}],
                        },
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
        text = self._extract_openai_text(data)
        return json.loads(text)

    def _extract_openai_text(self, payload: dict[str, Any]) -> str:
        if payload.get('output_text'):
            return payload['output_text']
        chunks: list[str] = []
        for item in payload.get('output', []):
            for content in item.get('content', []):
                if content.get('type') in {'output_text', 'text'} and content.get('text'):
                    chunks.append(content['text'])
        if not chunks:
            raise ValueError('No OpenAI output text found')
        return ''.join(chunks)

    async def _refine_asset_prompts(self, output: OrchestratorOutput, req: GenerateRequest) -> None:
        plan = output.asset_plan
        if plan.needs_image_asset and plan.image_prompt:
            refined, provider = await self._refine_prompt(plan.image_prompt, 'image')
            if refined:
                plan.image_prompt = refined
                plan.refined_by = provider
                output.routing.prompt_refiner = provider
        if plan.needs_3d_asset and plan.trellis_prompt:
            refined, provider = await self._refine_prompt(plan.trellis_prompt, '3d')
            if refined:
                plan.trellis_prompt = refined
                plan.refined_by = provider
                output.routing.prompt_refiner = provider

    async def _refine_prompt(self, prompt: str, kind: str) -> tuple[str, str]:
        if self.anthropic_api_key:
            try:
                return await self._refine_with_anthropic(prompt, kind), f'anthropic:{self.prompt_refiner_model}'
            except Exception as exc:
                log.warning('Anthropic refine failed: %s', exc)
        if self.openrouter_api_key:
            try:
                return await self._refine_with_openrouter(prompt, kind), f'openrouter:{self.openrouter_refine_model}'
            except Exception as exc:
                log.warning('OpenRouter refine failed: %s', exc)
        if self.openai_api_key:
            return prompt, f'openai:{self.openai_repair_model}'
        return prompt, ''

    async def _refine_with_anthropic(self, prompt: str, kind: str) -> str:
        instruction = f'Rewrite this {kind} generation prompt to be vivid, concise, and production-ready. Return only the rewritten prompt.'
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': self.anthropic_api_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': self.prompt_refiner_model,
                    'max_tokens': 220,
                    'messages': [
                        {'role': 'user', 'content': f'{instruction}\n\n{prompt}'},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
        texts = [block.get('text', '') for block in data.get('content', []) if block.get('type') == 'text']
        return ''.join(texts).strip() or prompt

    async def _refine_with_openrouter(self, prompt: str, kind: str) -> str:
        instruction = f'Rewrite this {kind} generation prompt to be vivid, concise, and production-ready. Return only the rewritten prompt.'
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.openrouter_api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.openrouter_refine_model,
                    'messages': [
                        {'role': 'system', 'content': 'You refine asset-generation prompts.'},
                        {'role': 'user', 'content': f'{instruction}\n\n{prompt}'},
                    ],
                    'temperature': 0.4,
                },
            )
            response.raise_for_status()
            data = response.json()
        return data['choices'][0]['message']['content'].strip()

    def _fallback_output(self, req: GenerateRequest, reason: str) -> tuple[str, OrchestratorOutput]:
        if req.recipe_id in ('feedback_2d', 'particle_field'):
            recipe_id = req.recipe_id
        else:
            recipe_id = self._select_recipe_from_prompt(req.prompt)
            
        recipe_params = self._heuristic_recipe_params(req.prompt, recipe_id)
        recipe_params.update(self._palette_from_prompt(req.prompt, recipe_id))
        
        template_map = {
            'feedback_2d': 'feedback',
            'particle_field': 'particle',
        }
        template = template_map.get(recipe_id, 'particle')
        color_1 = str(recipe_params.get('color_1') or '#ffffff')
        color_2 = str(recipe_params.get('color_2') or '#9fb7ff')
        color_3 = str(recipe_params.get('color_3') or '#05070a')
        cr, cg, cb = self._hex_to_rgb01(color_1)
        use_image = False
        image_usage = 'none'

        td_params = TDParameters(
            recipe_id=recipe_id,
            template=template,
            style=req.style.lower(),
            motion=req.motion.lower(),
            concept_summary=str(recipe_params.get('concept_summary') or req.prompt),
            visual_mood=str(recipe_params.get('visual_mood') or 'atmospheric 2D media art'),
            use_comfyui_image=use_image,
            image_usage=image_usage,
            color_mode='custom',
            color_palette='custom',
            color_r=cr,
            color_g=cg,
            color_b=cb,
            speed=self._clamp_float(recipe_params.get('speed', 0.5), 0.0, 1.0, 0.5),
            density=self._clamp_float(recipe_params.get('particle_count', 600), 50, 3000, 600) / 3000.0,
            turbulence=self._clamp_float(recipe_params.get('noise_strength', 0.5), 0.0, 1.0, 0.5),
            brightness=self._clamp_float(recipe_params.get('glow_intensity', 0.6), 0.0, 1.0, 0.6),
            noise_strength=self._clamp_float(recipe_params.get('noise_strength', 0.5), 0.0, 1.0, 0.5),
            motion_speed=self._clamp_float(recipe_params.get('speed', 0.5), 0.0, 1.0, 0.5),
            feedback_opacity=self._clamp_float(recipe_params.get('feedback_opacity', 0.85), 0.0, 1.0, 0.85),
            glow=self._clamp_float(recipe_params.get('glow_intensity', 0.6), 0.0, 1.0, 0.6),
            particle_density=self._clamp_float(recipe_params.get('particle_count', 600), 50, 3000, 600) / 3000.0,
            particle_count=int(self._clamp_float(recipe_params.get('particle_count', 600), 50, 3000, 600)),
            spread=self._clamp_float(recipe_params.get('spread', 0.7), 0.0, 1.0, 0.7),
            displace_weight=self._clamp_float(recipe_params.get('displace_weight', 0.35), 0.0, 1.0, 0.35),
            blur_amount=self._clamp_float(recipe_params.get('blur_amount', 0.4), 0.0, 1.0, 0.4),
            glow_intensity=self._clamp_float(recipe_params.get('glow_intensity', 0.6), 0.0, 1.0, 0.6),
            color_1=color_1,
            color_2=color_2,
            color_3=color_3,
            keywords=[req.style, req.motion, req.color],
            mood=str(recipe_params.get('visual_mood') or 'atmospheric'),
            description=req.prompt,
        )
        asset_plan = AssetPlan(
            needs_image_asset=use_image,
            needs_3d_asset=False,
            image_prompt='',
            image_negative_prompt='',
            image_usage=image_usage,
            operator_brief=f'Procedural-only recipe: {recipe_id} (fallback). {td_params.concept_summary}',
            td_mcp_plan=self._recipe_mcp_plan(recipe_id, req, td_params.model_dump()),
        )
        output = OrchestratorOutput(
            template=template,
            td_params=td_params,
            asset_plan=asset_plan,
            reasoning=f'Fallback: {reason}. Selected recipe: {recipe_id}',
            routing=RoutingTrace(
                orchestrator=f'fallback:{self.gemini_model}',
                used_fallback=True,
            ),
        )
        return output.reasoning, output

    def _fallback_json_text(self, req: GenerateRequest) -> str:
        reasoning, output = self._fallback_output(req, 'repair fallback')
        payload = {
            'template': output.template,
            'td_params': output.td_params.model_dump(),
            'asset_plan': output.asset_plan.model_dump(),
        }
        return reasoning + '\n```json\n' + json.dumps(payload) + '\n```'

    def _repair_label(self) -> str:
        if self.openai_api_key:
            return f'openai:{self.openai_repair_model}'
        return 'code-fallback'

    async def ollama_status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f'{self.ollama_base_url}/api/tags')
                response.raise_for_status()
                data = response.json()
            models = [m.get('name', '') for m in data.get('models', [])]
            return {
                'enabled': self.agent_mode in {'ollama', 'local_ollama'},
                'ok': True,
                'base_url': self.ollama_base_url,
                'model': self.ollama_model,
                'model_installed': self.ollama_model in models,
                'models': models,
            }
        except Exception as exc:
            return {
                'enabled': self.agent_mode in {'ollama', 'local_ollama'},
                'ok': False,
                'base_url': self.ollama_base_url,
                'model': self.ollama_model,
                'model_installed': False,
                'error': str(exc),
            }

    def _orchestrator_provider(self) -> str:
        if self.agent_mode in {'local', 'mcp', 'no_api'}:
            return 'local-mcp'
        if self.agent_mode in {'ollama', 'local_ollama'}:
            return 'ollama'
        return 'gemini'

    def _orchestrator_model_name(self) -> str:
        if self.agent_mode in {'local', 'mcp', 'no_api'}:
            return 'rule-based TouchDesigner mapper'
        if self.agent_mode in {'ollama', 'local_ollama'}:
            return self.ollama_model
        return self.gemini_model

    def _orchestrator_enabled(self) -> bool:
        if self.agent_mode in {'local', 'mcp', 'no_api', 'ollama', 'local_ollama'}:
            return True
        return bool(self.gemini_client)

    def _prompt_refiner_provider(self) -> str:
        if self.anthropic_api_key:
            return 'anthropic'
        if self.openrouter_api_key:
            return 'openrouter'
        if self.openai_api_key:
            return 'openai'
        return 'none'

    def _prompt_refiner_model_name(self) -> str:
        if self.anthropic_api_key:
            return self.prompt_refiner_model
        if self.openrouter_api_key:
            return self.openrouter_refine_model
        if self.openai_api_key:
            return self.openai_repair_model
        return ''
