from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class SourceOptions(BaseModel):
    generate_image: bool = Field(False, description='Disabled in procedural-only mode')
    generate_3d: bool = Field(False, description='Disabled in procedural-only mode')


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description='User text prompt')
    source: str = Field('', description='Optional path or URL of a source image/3D asset')
    recipe_id: str = Field('auto', description='Forced recipe ID option (auto, feedback_2d, particle_field)')
    source_options: SourceOptions = Field(default_factory=SourceOptions)
    # Legacy fields kept for backward-compat; all are now inferred from the prompt by the LLM
    style: str = 'Ethereal'
    motion: str = 'Flowing'
    particles: str = 'High'
    color: str = 'Monochrome'
    export_type: str = 'TOX (TouchDesigner)'


class TDParameters(BaseModel):
    recipe_id: Literal['dreamy_particle_field', 'glitch_feedback_field', 'soft_3d_orb', 'feedback_2d', 'particle_field'] = 'dreamy_particle_field'
    template: Literal['particle', 'feedback', '3d'] = 'particle'
    concept_summary: str = ''
    visual_mood: str = ''
    use_comfyui_image: bool = False
    image_usage: Literal['none', 'background_composite', 'fog_overlay', 'particle_sprite'] = 'none'
    style: str = 'ethereal'
    motion: str = 'flowing'
    color_mode: str = 'monochrome'
    color_palette: str = 'monochrome'
    color_r: float = Field(1.0, ge=0, le=1)
    color_g: float = Field(1.0, ge=0, le=1)
    color_b: float = Field(1.0, ge=0, le=1)
    speed: float = Field(0.5, ge=0, le=1)
    density: float = Field(0.7, ge=0, le=1)
    scale: float = Field(1.0, ge=0.5, le=2.0)
    turbulence: float = Field(0.3, ge=0, le=1)
    brightness: float = Field(0.8, ge=0, le=1)
    noise_strength: float = Field(0.5, ge=0, le=1)
    motion_speed: float = Field(0.5, ge=0, le=1)
    feedback_opacity: float = Field(0.85, ge=0, le=1)
    glow: float = Field(0.5, ge=0, le=1)
    particle_density: float = Field(0.7, ge=0, le=1)
    particle_count: int = Field(400, ge=50, le=3000)
    spread: float = Field(0.7, ge=0, le=1)
    displace_weight: float = Field(0.35, ge=0, le=1)
    blur_amount: float = Field(0.4, ge=0, le=1)
    glow_intensity: float = Field(0.6, ge=0, le=1)
    color_1: str = '#ffffff'
    color_2: str = '#9fb7ff'
    color_3: str = '#111111'
    keywords: list[str] = Field(default_factory=list)
    mood: str = ''
    description: str = ''
    asset_image_path: str = ''
    asset_3d_path: str = ''
    asset_3d_glb_path: str = ''


class AssetPlan(BaseModel):
    needs_image_asset: bool = False
    needs_3d_asset: bool = False
    image_prompt: str = ''
    image_negative_prompt: str = ''
    image_usage: str = 'background_composite'
    trellis_prompt: str = ''
    object_description: str = ''
    asset_usage: str = 'geometry_source'
    operator_brief: str = ''
    refined_by: str = ''
    model_hint: str = ''
    td_mcp_plan: dict[str, Any] = Field(default_factory=dict)


class RoutingTrace(BaseModel):
    orchestrator: str = ''
    json_repair: str = ''
    prompt_refiner: str = ''
    comparator: str = ''
    used_fallback: bool = False


class OrchestratorOutput(BaseModel):
    template: Literal['particle', 'feedback', '3d'] = 'particle'
    td_params: TDParameters
    asset_plan: AssetPlan = Field(default_factory=AssetPlan)
    reasoning: str = ''
    routing: RoutingTrace = Field(default_factory=RoutingTrace)


class GenerateResponse(BaseModel):
    success: bool
    message: str
    parameters: Optional[TDParameters] = None
    orchestrator: Optional[OrchestratorOutput] = None
    source_options: SourceOptions = Field(default_factory=SourceOptions)
    td_status: Optional[str] = None
    td_message: Optional[str] = None
    preview_url: Optional[str] = None
    reasoning: Optional[str] = None
    source_assets: list[dict[str, Any]] = Field(default_factory=list)
    td_plan: dict[str, Any] = Field(default_factory=dict)
    td_applied: dict[str, Any] = Field(default_factory=dict)
    telemetry: dict[str, Any] = Field(default_factory=dict)
    source_status: dict[str, Any] = Field(default_factory=dict)
    image_asset_requested: bool = False
    image_asset_generated: bool = False
    image_asset_path: str = ''
    image_loaded_in_td: bool = False
    asset_3d_requested: bool = False
    asset_3d_generated: bool = False
    asset_3d_obj_path: str = ''
    asset_3d_glb_path: str = ''
    asset_3d_loaded_in_td: bool = False
    point_count: int = 0
    primitive_count: int = 0
    source_generation_errors: list[str] = Field(default_factory=list)
    converted_obj_path: Optional[str] = None
    converted_fbx_path: Optional[str] = None
    td_load_success: Optional[bool] = None
    td_point_count: Optional[int] = None
    td_primitive_count: Optional[int] = None
    td_image_resolution: Optional[str] = None
    td_render_connection: Optional[str] = None
    td_composite_connection_status: Optional[str] = None
    td_image_usage: Optional[str] = None
    td_final_output_top: Optional[str] = None


class StatusResponse(BaseModel):
    server: str = 'running'
    gemini: str
    touchdesigner: str
    td_url: str
    llm_stack: dict[str, Any] = Field(default_factory=dict)
