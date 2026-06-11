from __future__ import annotations

from dataclasses import dataclass

from schema import AssetPlan, GenerateRequest, OrchestratorOutput, RoutingTrace, TDParameters


@dataclass(frozen=True)
class ColorPreset:
    mode: str
    rgb: tuple[float, float, float]


COLOR_PRESETS = {
    'monochrome': ColorPreset('monochrome', (1.0, 1.0, 1.0)),
    'neon': ColorPreset('neon', (0.2, 1.0, 0.8)),
    'warm': ColorPreset('warm', (1.0, 0.42, 0.12)),
    'cool': ColorPreset('cool', (0.2, 0.52, 1.0)),
    'pastel': ColorPreset('pastel', (0.9, 0.82, 1.0)),
    'red': ColorPreset('warm', (1.0, 0.15, 0.08)),
    'blue': ColorPreset('cool', (0.15, 0.38, 1.0)),
    'green': ColorPreset('neon', (0.18, 1.0, 0.45)),
    'pink': ColorPreset('pastel', (1.0, 0.55, 0.88)),
    'white': ColorPreset('monochrome', (1.0, 1.0, 1.0)),
}


class LocalMCPAgent:
    """No-API prompt mapper for the MCP-only prototype path."""

    def orchestrate(self, req: GenerateRequest) -> tuple[str, OrchestratorOutput]:
        prompt = req.prompt.strip()
        text = f'{prompt} {req.style} {req.motion} {req.color}'.lower()

        template = self._template(text, req)
        color = self._color(text, req)
        motion = self._motion(text, req)
        speed = self._speed(text, req)
        density = self._density(text, req)
        turbulence = self._turbulence(text, req)
        mood = self._mood(text, req)
        scale = 1.2 if template == '3d' else 1.0
        brightness = self._brightness(text, req)

        keywords = self._keywords(text, req, template, color.mode, mood)
        td_params = TDParameters(
            template=template,
            style=req.style.lower(),
            motion=motion,
            color_mode=color.mode,
            color_r=color.rgb[0],
            color_g=color.rgb[1],
            color_b=color.rgb[2],
            speed=speed,
            density=density,
            scale=scale,
            turbulence=turbulence,
            brightness=brightness,
            keywords=keywords,
            mood=mood,
            description=prompt,
        )
        needs_image = any(k in text for k in ['image', 'photo', 'texture', 'source', 'reference', '이미지', '사진', '텍스처', '소스'])
        needs_3d = (template == '3d')
        asset_plan = AssetPlan(
            needs_image_asset=needs_image,
            needs_3d_asset=needs_3d,
            image_prompt=prompt if needs_image else '',
            trellis_prompt=prompt if needs_3d else '',
            model_hint='mcp-only procedural TouchDesigner output',
            td_mcp_plan=self._default_mcp_plan(req, template, color.mode, motion, density, turbulence, speed, brightness),
        )
        reasoning = (
            f'로컬 MCP 모드로 프롬프트를 해석했습니다. '
            f'{template} 템플릿에 {color.mode} 팔레트, {motion} 모션, '
            f'밀도 {density:.2f}, 난류 {turbulence:.2f}를 적용합니다.'
        )
        output = OrchestratorOutput(
            template=template,
            td_params=td_params,
            asset_plan=asset_plan,
            reasoning=reasoning,
            routing=RoutingTrace(
                orchestrator='local-mcp-agent',
                json_repair='not-needed',
                prompt_refiner='not-needed',
                comparator='not-used',
                used_fallback=False,
            ),
        )
        return reasoning, output


    def _default_mcp_plan(
        self,
        req: GenerateRequest,
        template: str,
        color_mode: str,
        motion: str,
        density: float,
        turbulence: float,
        speed: float,
        brightness: float,
    ) -> dict:
        return {
            'version': 'autotd-plan-v1',
            'intent': req.prompt,
            'reset_scope': '/project1/autotd_generated',
            'output': 'generated_out',
            'nodes': [
                {'id': 'field', 'type': 'noiseTOP', 'name': 'field_noise', 'params': {
                    'seed': 0, 'period': max(0.12, 2.0 - speed * 1.6),
                    'harmon': max(1, min(8, int(round(1 + density * 7)))),
                    'rough': max(0.05, min(1.0, 0.2 + turbulence * 0.7)),
                    'amp': max(0.05, min(1.0, 0.2 + density * 0.8)),
                    'mono': color_mode == 'monochrome', 'resolutionw': 1280, 'resolutionh': 720,
                }},
                {'id': 'flow', 'type': 'noiseTOP', 'name': 'flow_noise', 'params': {
                    'seed': 37, 'period': max(0.18, 1.35 - speed * 0.75),
                    'harmon': max(3, min(10, int(round(4 + turbulence * 6)))),
                    'rough': max(0.15, min(1.0, 0.35 + turbulence * 0.55)),
                    'amp': max(0.25, min(1.0, 0.45 + density * 0.45)),
                    'mono': True, 'resolutionw': 1280, 'resolutionh': 720,
                }},
                {'id': 'contrast', 'type': 'levelTOP', 'name': 'contrast_cut', 'params': {
                    'brightness': brightness, 'contrast': 1.25, 'gamma': 0.9,
                }},
                {'id': 'base_blur', 'type': 'blurTOP', 'name': 'base_soft_blur', 'params': {'sizex': 2.5, 'sizey': 2.5}},
                {'id': 'edge_trace', 'type': 'edgeTOP', 'name': 'edge_trace', 'params': {}},
                {'id': 'edge_grade', 'type': 'levelTOP', 'name': 'edge_grade', 'params': {'brightness': 0.72, 'contrast': 1.65, 'gamma': 0.86}},
                {'id': 'edge_glow', 'type': 'blurTOP', 'name': 'edge_glow', 'params': {'sizex': 6, 'sizey': 6}},
                {'id': 'comp', 'type': 'compositeTOP', 'name': 'screen_mix', 'params': {'operand': 'screen'}},
                {'id': 'feedback', 'type': 'feedbackTOP', 'name': 'motion_feedback', 'params': {'opacity': 0.88}},
                {'id': 'drift', 'type': 'transformTOP', 'name': 'drift_transform', 'params': {
                    'tx': round(speed * 0.035, 4), 'ty': round(turbulence * 0.025, 4),
                    'scale': max(0.96, min(1.04, 1.0 + turbulence * 0.025)),
                    'rotate': round((speed - 0.5) * 4.0, 3),
                }},
                {'id': 'final_grade', 'type': 'levelTOP', 'name': 'final_gallery_grade', 'params': {'brightness': min(1.0, brightness + 0.08), 'contrast': 1.28, 'gamma': 0.9}},
                {'id': 'final_soften', 'type': 'blurTOP', 'name': 'final_soften', 'params': {'sizex': 1.2, 'sizey': 1.2}},
                {'id': 'out', 'type': 'nullTOP', 'name': 'generated_out', 'params': {}},
            ],
            'connections': [
                {'from': 'field', 'to': 'contrast'},
                {'from': 'contrast', 'to': 'base_blur'},
                {'from': 'flow', 'to': 'edge_trace'},
                {'from': 'edge_trace', 'to': 'edge_grade'},
                {'from': 'edge_grade', 'to': 'edge_glow'},
                {'from': 'base_blur', 'to': 'comp', 'to_input': 0},
                {'from': 'edge_glow', 'to': 'comp', 'to_input': 1},
                {'from': 'comp', 'to': 'feedback'},
                {'from': 'feedback', 'to': 'drift'},
                {'from': 'drift', 'to': 'final_grade'},
                {'from': 'final_grade', 'to': 'final_soften'},
                {'from': 'final_soften', 'to': 'out'},
            ],
            'notes': f'Procedural {template} plan generated without a fixed template.',
        }

    def _template(self, text: str, req: GenerateRequest) -> str:
        if any(k in text for k in ['3d', 'object', 'mesh', 'sculpture', 'geometry', '입체', '구조', '오브젝트', '조형', '공간']):
            return '3d'
        if any(k in text for k in ['feedback', 'trail', 'glitch', 'distortion', 'image', 'texture', '잔상', '반복', '왜곡', '이미지', '텍스처', '흐림']):
            return 'feedback'
        if req.style == 'Glitch':
            return 'feedback'
        return 'particle'

    def _color(self, text: str, req: GenerateRequest) -> ColorPreset:
        prompt_colors = {
            'red': ['red', 'crimson', 'scarlet', '빨강', '붉', '적색'],
            'blue': ['blue', 'cyan', 'azure', '파랑', '푸른', '청색'],
            'green': ['green', 'lime', '초록', '녹색'],
            'pink': ['pink', 'magenta', '분홍', '핑크'],
            'white': ['white', 'silver', '흰', '하얀', '은색'],
            'warm': ['warm', 'orange', 'gold', '따뜻', '노을', '금빛'],
            'cool': ['cool', 'cold', 'ice', '차가운', '얼음'],
            'neon': ['neon', 'cyber', 'electric', '네온', '사이버'],
            'pastel': ['pastel', 'soft', '파스텔', '부드러운'],
        }
        for key, tokens in prompt_colors.items():
            if any(token in text for token in tokens):
                return COLOR_PRESETS[key]
        return COLOR_PRESETS.get(req.color.lower(), COLOR_PRESETS['monochrome'])

    def _motion(self, text: str, req: GenerateRequest) -> str:
        if any(k in text for k in ['static', 'still', '정지', '고요', '멈춘']):
            return 'static'
        if any(k in text for k in ['pulse', 'beat', 'heartbeat', '맥박', '박동', '깜빡']):
            return 'pulsing'
        if any(k in text for k in ['chaos', 'storm', 'explosion', 'turbulent', '혼돈', '폭발', '격렬', '소용돌이']):
            return 'turbulent'
        return req.motion.lower()

    def _speed(self, text: str, req: GenerateRequest) -> float:
        base = {'Static': 0.05, 'Flowing': 0.5, 'Pulsing': 0.65, 'Turbulent': 0.9}.get(req.motion, 0.5)
        if any(k in text for k in ['slow', 'gentle', 'calm', '천천히', '느린', '잔잔']):
            return min(base, 0.32)
        if any(k in text for k in ['fast', 'rapid', 'wild', '빠른', '급격', '격렬']):
            return max(base, 0.86)
        return base

    def _density(self, text: str, req: GenerateRequest) -> float:
        base = {'Low': 0.25, 'Medium': 0.5, 'High': 0.75, 'Ultra': 1.0}.get(req.particles, 0.75)
        if any(k in text for k in ['sparse', 'empty', 'few', '성긴', '비어', '희박']):
            return min(base, 0.32)
        if any(k in text for k in ['dense', 'crowded', 'many', '많은', '가득', '촘촘']):
            return max(base, 0.88)
        return base

    def _turbulence(self, text: str, req: GenerateRequest) -> float:
        if any(k in text for k in ['smooth', 'soft', 'calm', '부드러운', '잔잔', '고요']):
            return 0.2
        if any(k in text for k in ['chaos', 'glitch', 'storm', 'noise', '혼돈', '왜곡', '폭풍', '난류']):
            return 0.85
        return 0.45 if req.motion == 'Pulsing' else 0.3

    def _brightness(self, text: str, req: GenerateRequest) -> float:
        if any(k in text for k in ['dark', 'black', 'shadow', '어두운', '검은', '그림자']):
            return 0.55
        if any(k in text for k in ['bright', 'light', 'glow', '빛', '밝은', '발광']):
            return 0.95
        return 0.8

    def _mood(self, text: str, req: GenerateRequest) -> str:
        mood_tokens = {
            'calm': ['calm', 'quiet', '고요', '잔잔', '평온'],
            'ethereal': ['ethereal', 'dream', '몽환', '꿈', '환상'],
            'tense': ['tense', 'anxiety', '긴장', '불안'],
            'chaotic': ['chaos', 'wild', '혼돈', '격렬'],
            'minimal': ['minimal', 'simple', '미니멀', '단순'],
        }
        for mood, tokens in mood_tokens.items():
            if any(token in text for token in tokens):
                return mood
        return req.style.lower()

    def _keywords(self, text: str, req: GenerateRequest, template: str, color_mode: str, mood: str) -> list[str]:
        words = [req.style, req.motion, req.color, template, color_mode, mood]
        extras = ['particle', 'flow', 'noise', 'feedback', 'trail', '3d', 'geometry', 'glow']
        words.extend(token for token in extras if token in text)
        return list(dict.fromkeys(words))
