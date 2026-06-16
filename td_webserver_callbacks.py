import json
import os
import tempfile
from pathlib import Path


def _json_headers(response):
    response['headers'] = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
    }


def _find_preview_top():
    return op('out1') or op('/project1/out1') or op('motion_noise') or op('/project1/motion_noise')


def _save_preview_png():
    out_top = _find_preview_top()
    if not out_top:
        raise RuntimeError('No preview TOP found (expected out1 or motion_noise)')
    out_top.cook(force=True)
    preview_path = os.path.join(tempfile.gettempdir(), 'autotd_td_preview.png')
    out_top.save(preview_path)
    return preview_path


def _safe_export_name(name='autotd_export'):
    safe = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in str(name or 'autotd_export'))
    safe = safe.strip('_') or 'autotd_export'
    return safe[:80]


def _export_generated_tox(export_dir='', name='autotd_export'):
    target = op('/project1/autotd_generated') or op('/project1')
    if not target:
        raise RuntimeError('No TouchDesigner component available for TOX export')
    export_root = Path(export_dir or os.path.join(tempfile.gettempdir(), 'autotd_exports'))
    export_root.mkdir(parents=True, exist_ok=True)
    export_path = export_root / f'{_safe_export_name(name)}.tox'
    if export_path.exists():
        try:
            export_path.unlink()
        except Exception:
            pass
    saved = target.save(str(export_path))
    final_path = Path(saved) if saved else export_path
    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise RuntimeError(f'TOX export failed: {final_path}')
    return {
        'status': 'ok',
        'path': final_path.as_posix(),
        'filename': final_path.name,
        'bytes': final_path.stat().st_size,
        'source': target.path,
    }


# ── Last received plan debug storage ──
_last_debug = {}


def _set_par_safe(node, name, value):
    try:
        node.par[name] = value
        return True
    except Exception:
        try:
            getattr(node.par, name).val = value
            return True
        except Exception:
            return False


def _set_noise_par(node, name, value):
    aliases = {
        'harmonics': ('harmon', 'harmonics'),
        'resolutionw': ('resolutionw', 'resw'),
        'resolutionh': ('resolutionh', 'resh'),
    }
    for candidate in aliases.get(name, (name,)):
        if _set_par_safe(node, candidate, value):
            return True
    return False


def _reset_generated_scope(scope_path='/project1/autotd_generated'):
    parent = op('/project1') or op('/')
    existing = op(scope_path)
    if existing:
        existing.destroy()
    scope_name = scope_path.rsplit('/', 1)[-1] or 'autotd_generated'
    try:
        scope = parent.create('baseCOMP', scope_name)
    except Exception:
        scope = parent.create('containerCOMP', scope_name)
    try:
        scope.nodeX = 600
        scope.nodeY = -240
    except Exception:
        pass
    return scope


def _route_project_out(source_top):
    parent = op('/project1') or op('/')
    if not source_top:
        return ''
    old_out = parent.op('out1')
    if old_out:
        old_out.destroy()
    old_select = parent.op('autotd_generated_select')
    if old_select:
        old_select.destroy()
    selector = parent.create('selectTOP', 'autotd_generated_select')
    selector.par.top = source_top.path
    out = parent.create('nullTOP', 'out1')
    selector.outputConnectors[0].connect(out)
    try:
        selector.cook(force=True)
        out.cook(force=True)
    except Exception:
        pass
    return out.path


def _ensure_render_setup(scope):
    cam = scope.op('cam1') or scope.create('cameraCOMP', 'cam1')
    light = scope.op('light1') or scope.create('lightCOMP', 'light1')
    render_top = scope.op('render1') or scope.create('renderTOP', 'render1')
    try:
        render_top.par.camera = cam.path
        render_top.par.geometry = '*'
        render_top.par.lights = light.path
        render_top.par.resolutionw = 1280
        render_top.par.resolutionh = 720
        cam.nodeX = -200
        cam.nodeY = -150
        light.nodeX = -200
        light.nodeY = -300
        render_top.nodeX = 0
        render_top.nodeY = -150
    except Exception:
        pass
    return render_top


# ══════════════════════════════════════════════════════════════════════
#  RECIPE DEFINITIONS - Hardcoded verified node chains
# ══════════════════════════════════════════════════════════════════════

def _build_dreamy_particle_field(scope, p):
    """Soft flowing particles with ethereal glow and feedback trails."""
    nodes_created = []
    
    # 1. Primary noise field
    noise1 = scope.create('noiseTOP', 'field_noise')
    _set_par_safe(noise1, 'seed', 42)
    _set_noise_par(noise1, 'period', max(0.15, 1.8 - p['motion_speed'] * 1.4))
    _set_noise_par(noise1, 'harmonics', max(3, min(10, int(4 + p['particle_density'] * 6))))
    _set_noise_par(noise1, 'rough', max(0.2, min(1.0, 0.25 + p['noise_strength'] * 0.65)))
    _set_noise_par(noise1, 'amp', max(0.55, min(1.0, 0.45 + p['particle_density'] * 0.55)))
    _set_noise_par(noise1, 'mono', p['color_palette'] == 'monochrome')
    _set_noise_par(noise1, 'resolutionw', 1280)
    _set_noise_par(noise1, 'resolutionh', 720)
    noise1.nodeX = 0
    noise1.nodeY = 0
    nodes_created.append(('field_noise', 'noiseTOP'))
    
    # 2. Secondary flow noise
    noise2 = scope.create('noiseTOP', 'flow_noise')
    _set_par_safe(noise2, 'seed', 137)
    _set_noise_par(noise2, 'period', max(0.2, 1.2 - p['motion_speed'] * 0.8))
    _set_noise_par(noise2, 'harmonics', max(3, min(8, int(3 + p['noise_strength'] * 5))))
    _set_noise_par(noise2, 'rough', max(0.3, min(0.9, 0.35 + p['noise_strength'] * 0.5)))
    _set_noise_par(noise2, 'amp', 0.8)
    _set_noise_par(noise2, 'mono', True)
    _set_noise_par(noise2, 'resolutionw', 1280)
    _set_noise_par(noise2, 'resolutionh', 720)
    noise2.nodeX = 0
    noise2.nodeY = -150
    nodes_created.append(('flow_noise', 'noiseTOP'))
    
    # 3. Base grade (contrast + brightness)
    grade1 = scope.create('levelTOP', 'base_grade')
    _set_par_safe(grade1, 'brightness', max(0.5, min(1.0, p['glow'])))
    _set_par_safe(grade1, 'contrast', 1.2 + p['noise_strength'] * 0.3)
    _set_par_safe(grade1, 'gamma', 0.92)
    noise1.outputConnectors[0].connect(grade1.inputConnectors[0])
    grade1.nodeX = 200
    grade1.nodeY = 0
    nodes_created.append(('base_grade', 'levelTOP'))
    
    # 4. Soft blur
    blur1 = scope.create('blurTOP', 'soft_blur')
    _set_par_safe(blur1, 'sizex', 2.5 + p['glow'] * 3.0)
    _set_par_safe(blur1, 'sizey', 2.5 + p['glow'] * 3.0)
    grade1.outputConnectors[0].connect(blur1.inputConnectors[0])
    blur1.nodeX = 400
    blur1.nodeY = 0
    nodes_created.append(('soft_blur', 'blurTOP'))
    
    # 5. Edge extract from flow noise
    edge1 = scope.create('edgeTOP', 'edge_trace')
    noise2.outputConnectors[0].connect(edge1.inputConnectors[0])
    edge1.nodeX = 200
    edge1.nodeY = -150
    nodes_created.append(('edge_trace', 'edgeTOP'))
    
    # 6. Edge grade
    edge_grade = scope.create('levelTOP', 'edge_grade')
    _set_par_safe(edge_grade, 'brightness', 0.6 + p['glow'] * 0.3)
    _set_par_safe(edge_grade, 'contrast', 1.5 + p['noise_strength'] * 0.4)
    _set_par_safe(edge_grade, 'gamma', 0.85)
    edge1.outputConnectors[0].connect(edge_grade.inputConnectors[0])
    edge_grade.nodeX = 400
    edge_grade.nodeY = -150
    nodes_created.append(('edge_grade', 'levelTOP'))
    
    # 7. Glow blur on edges
    glow_blur = scope.create('blurTOP', 'edge_glow')
    _set_par_safe(glow_blur, 'sizex', 5 + p['glow'] * 6)
    _set_par_safe(glow_blur, 'sizey', 5 + p['glow'] * 6)
    edge_grade.outputConnectors[0].connect(glow_blur.inputConnectors[0])
    glow_blur.nodeX = 600
    glow_blur.nodeY = -150
    nodes_created.append(('edge_glow', 'blurTOP'))
    
    # 8. Screen composite (blur + glow)
    comp1 = scope.create('compositeTOP', 'screen_mix')
    _set_par_safe(comp1, 'operand', 'screen')
    blur1.outputConnectors[0].connect(comp1.inputConnectors[0])
    glow_blur.outputConnectors[0].connect(comp1.inputConnectors[1])
    comp1.nodeX = 800
    comp1.nodeY = 0
    nodes_created.append(('screen_mix', 'compositeTOP'))
    
    # 9. Feedback loop
    feedback = scope.create('feedbackTOP', 'motion_feedback')
    _set_par_safe(feedback, 'opacity', p['feedback_opacity'])
    comp1.outputConnectors[0].connect(feedback.inputConnectors[0])
    feedback.nodeX = 1000
    feedback.nodeY = 0
    nodes_created.append(('motion_feedback', 'feedbackTOP'))
    
    # 10. Drift transform (slow pan/rotate)
    drift = scope.create('transformTOP', 'drift_transform')
    _set_par_safe(drift, 'tx', round(p['motion_speed'] * 0.025, 4))
    _set_par_safe(drift, 'ty', round(p['noise_strength'] * 0.018, 4))
    _set_par_safe(drift, 'scale', max(0.97, min(1.03, 1.0 + p['noise_strength'] * 0.02)))
    _set_par_safe(drift, 'rotate', round((p['motion_speed'] - 0.5) * 3.0, 3))
    feedback.outputConnectors[0].connect(drift.inputConnectors[0])
    drift.nodeX = 1200
    drift.nodeY = 0
    nodes_created.append(('drift_transform', 'transformTOP'))
    
    # 11. Final grade
    final_grade = scope.create('levelTOP', 'final_grade')
    _set_par_safe(final_grade, 'brightness', min(1.0, p['glow'] + 0.1))
    _set_par_safe(final_grade, 'contrast', 1.25)
    _set_par_safe(final_grade, 'gamma', 0.9)
    drift.outputConnectors[0].connect(final_grade.inputConnectors[0])
    final_grade.nodeX = 1400
    final_grade.nodeY = 0
    nodes_created.append(('final_grade', 'levelTOP'))
    
    # 12. Output null
    out = scope.create('nullTOP', 'generated_out')
    final_grade.outputConnectors[0].connect(out.inputConnectors[0])
    out.nodeX = 1600
    out.nodeY = 0
    nodes_created.append(('generated_out', 'nullTOP'))
    
    return out, nodes_created


def _build_glitch_feedback_field(scope, p):
    """Aggressive feedback loops with digital distortion."""
    nodes_created = []
    
    # 1. High-frequency noise
    noise1 = scope.create('noiseTOP', 'glitch_noise')
    _set_par_safe(noise1, 'seed', 666)
    _set_noise_par(noise1, 'period', max(0.08, 0.5 - p['noise_strength'] * 0.4))
    _set_noise_par(noise1, 'harmonics', max(5, min(10, int(6 + p['noise_strength'] * 4))))
    _set_noise_par(noise1, 'rough', max(0.5, min(1.0, 0.6 + p['noise_strength'] * 0.35)))
    _set_noise_par(noise1, 'amp', 1.0)
    _set_noise_par(noise1, 'mono', False)
    _set_noise_par(noise1, 'resolutionw', 1280)
    _set_noise_par(noise1, 'resolutionh', 720)
    noise1.nodeX = 0
    noise1.nodeY = 0
    nodes_created.append(('glitch_noise', 'noiseTOP'))
    
    # 2. Displacement noise
    disp_noise = scope.create('noiseTOP', 'displace_vector')
    _set_par_safe(disp_noise, 'seed', 999)
    _set_noise_par(disp_noise, 'period', max(0.1, 0.8 - p['motion_speed'] * 0.5))
    _set_noise_par(disp_noise, 'harmonics', 4)
    _set_noise_par(disp_noise, 'amp', 0.9)
    _set_noise_par(disp_noise, 'mono', True)
    _set_noise_par(disp_noise, 'resolutionw', 1280)
    _set_noise_par(disp_noise, 'resolutionh', 720)
    disp_noise.nodeX = 0
    disp_noise.nodeY = -150
    nodes_created.append(('displace_vector', 'noiseTOP'))
    
    # 3. Edge detection
    edge1 = scope.create('edgeTOP', 'edge_detect')
    noise1.outputConnectors[0].connect(edge1.inputConnectors[0])
    edge1.nodeX = 200
    edge1.nodeY = 0
    nodes_created.append(('edge_detect', 'edgeTOP'))
    
    # 4. Harsh contrast
    harsh_grade = scope.create('levelTOP', 'harsh_grade')
    _set_par_safe(harsh_grade, 'brightness', 0.4 + p['glow'] * 0.4)
    _set_par_safe(harsh_grade, 'contrast', 1.8 + p['noise_strength'] * 0.5)
    _set_par_safe(harsh_grade, 'gamma', 0.75)
    edge1.outputConnectors[0].connect(harsh_grade.inputConnectors[0])
    harsh_grade.nodeX = 400
    harsh_grade.nodeY = 0
    nodes_created.append(('harsh_grade', 'levelTOP'))
    
    # 5. Displacement
    displace = scope.create('displaceTOP', 'pixel_displace')
    _set_par_safe(displace, 'displaceweight', p['noise_strength'] * 0.6)
    harsh_grade.outputConnectors[0].connect(displace.inputConnectors[0])
    disp_noise.outputConnectors[0].connect(displace.inputConnectors[1])
    displace.nodeX = 600
    displace.nodeY = 0
    nodes_created.append(('pixel_displace', 'displaceTOP'))
    
    # 6. Threshold
    thresh = scope.create('thresholdTOP', 'threshold_cut')
    _set_par_safe(thresh, 'level', max(0.2, min(0.8, 0.5 - p['noise_strength'] * 0.2)))
    displace.outputConnectors[0].connect(thresh.inputConnectors[0])
    thresh.nodeX = 800
    thresh.nodeY = 0
    nodes_created.append(('threshold_cut', 'thresholdTOP'))
    
    # 7. Heavy feedback
    feedback = scope.create('feedbackTOP', 'glitch_feedback')
    _set_par_safe(feedback, 'opacity', min(0.98, p['feedback_opacity'] + 0.05))
    thresh.outputConnectors[0].connect(feedback.inputConnectors[0])
    feedback.nodeX = 1000
    feedback.nodeY = 0
    nodes_created.append(('glitch_feedback', 'feedbackTOP'))
    
    # 8. Aggressive transform
    drift = scope.create('transformTOP', 'scan_drift')
    _set_par_safe(drift, 'tx', round(p['motion_speed'] * 0.04, 4))
    _set_par_safe(drift, 'ty', round(p['noise_strength'] * -0.015, 4))
    _set_par_safe(drift, 'scale', max(0.98, min(1.02, 1.0 + p['motion_speed'] * 0.015)))
    _set_par_safe(drift, 'rotate', round(p['motion_speed'] * 2.5, 3))
    feedback.outputConnectors[0].connect(drift.inputConnectors[0])
    drift.nodeX = 1200
    drift.nodeY = 0
    nodes_created.append(('scan_drift', 'transformTOP'))
    
    # 9. Screen composite with original
    comp1 = scope.create('compositeTOP', 'feedback_mix')
    _set_par_safe(comp1, 'operand', 'screen')
    drift.outputConnectors[0].connect(comp1.inputConnectors[0])
    noise1.outputConnectors[0].connect(comp1.inputConnectors[1])
    comp1.nodeX = 1400
    comp1.nodeY = 0
    nodes_created.append(('feedback_mix', 'compositeTOP'))
    
    # 10. Signal limiter
    limiter = scope.create('limitTOP', 'signal_limit')
    comp1.outputConnectors[0].connect(limiter.inputConnectors[0])
    limiter.nodeX = 1600
    limiter.nodeY = 0
    nodes_created.append(('signal_limit', 'limitTOP'))
    
    # 11. Final glow
    final_glow = scope.create('blurTOP', 'final_glow')
    _set_par_safe(final_glow, 'sizex', 1.5 + p['glow'] * 2.0)
    _set_par_safe(final_glow, 'sizey', 1.5 + p['glow'] * 2.0)
    limiter.outputConnectors[0].connect(final_glow.inputConnectors[0])
    final_glow.nodeX = 1800
    final_glow.nodeY = 0
    nodes_created.append(('final_glow', 'blurTOP'))
    
    # 12. Output null
    out = scope.create('nullTOP', 'generated_out')
    final_glow.outputConnectors[0].connect(out.inputConnectors[0])
    out.nodeX = 2000
    out.nodeY = 0
    nodes_created.append(('generated_out', 'nullTOP'))
    
    return out, nodes_created


def _build_soft_3d_orb(scope, p):
    """Rotating 3D geometry with ambient lighting and post-processing."""
    nodes_created = []
    
    # 1. Geometry COMP with a sphere/torus
    geo = scope.create('geometryCOMP', 'orb_geo')
    geo.nodeX = 0
    geo.nodeY = 0
    nodes_created.append(('orb_geo', 'geometryCOMP'))
    
    # Remove default nodes inside geo
    try:
        for child in list(geo.children):
            child.destroy()
    except Exception:
        pass
    
    # Create shape inside geo
    shape_sop = geo.create('sphereSOP', 'orb_shape')
    try:
        shape_sop.par.rad = [0.6 + p['particle_density'] * 0.3] * 3
    except Exception:
        try:
            shape_sop.par.radx = 0.6 + p['particle_density'] * 0.3
            shape_sop.par.rady = 0.6 + p['particle_density'] * 0.3
            shape_sop.par.radz = 0.6 + p['particle_density'] * 0.3
        except Exception:
            pass
    nodes_created.append(('orb_shape', 'sphereSOP'))
    
    # Noise deform
    noise_sop = geo.create('noiseSOP', 'orb_deform')
    try:
        noise_sop.par.amp = p['noise_strength'] * 0.3
    except Exception:
        pass
    try:
        noise_sop.par.period = max(0.5, 2.0 - p['noise_strength'])
    except Exception:
        pass
    shape_sop.outputConnectors[0].connect(noise_sop.inputConnectors[0])
    nodes_created.append(('orb_deform', 'noiseSOP'))
    
    # Transform (rotation)
    xform = geo.create('transformSOP', 'orb_rotate')
    try:
        xform.par.rx = p['motion_speed'] * 45
        xform.par.ry = p['motion_speed'] * 90
    except Exception:
        pass
    noise_sop.outputConnectors[0].connect(xform.inputConnectors[0])
    xform.display = True
    xform.render = True
    nodes_created.append(('orb_rotate', 'transformSOP'))
    
    # 2. Camera
    cam = scope.create('cameraCOMP', 'cam1')
    try:
        cam.par.tz = -(1.8 + p['particle_density'] * 0.8)
    except Exception:
        pass
    cam.nodeX = -200
    cam.nodeY = -100
    nodes_created.append(('cam1', 'cameraCOMP'))
    
    # 3. Light
    light = scope.create('lightCOMP', 'light1')
    try:
        light.par.dimmer = 0.8 + p['glow'] * 0.2
    except Exception:
        pass
    light.nodeX = -200
    light.nodeY = -250
    nodes_created.append(('light1', 'lightCOMP'))
    
    # 4. Render TOP
    render = scope.create('renderTOP', 'render1')
    try:
        render.par.camera = cam.path
        render.par.geometry = geo.path
        render.par.lights = light.path
        render.par.resolutionw = 1280
        render.par.resolutionh = 720
    except Exception:
        pass
    render.nodeX = 200
    render.nodeY = 0
    nodes_created.append(('render1', 'renderTOP'))
    
    # 5. Post blur / glow
    post_blur = scope.create('blurTOP', 'post_glow')
    _set_par_safe(post_blur, 'sizex', 3 + p['glow'] * 5)
    _set_par_safe(post_blur, 'sizey', 3 + p['glow'] * 5)
    render.outputConnectors[0].connect(post_blur.inputConnectors[0])
    post_blur.nodeX = 400
    post_blur.nodeY = 0
    nodes_created.append(('post_glow', 'blurTOP'))
    
    # 6. Screen composite (render + glow)
    comp = scope.create('compositeTOP', 'render_glow_mix')
    _set_par_safe(comp, 'operand', 'screen')
    render.outputConnectors[0].connect(comp.inputConnectors[0])
    post_blur.outputConnectors[0].connect(comp.inputConnectors[1])
    comp.nodeX = 600
    comp.nodeY = 0
    nodes_created.append(('render_glow_mix', 'compositeTOP'))
    
    # 7. Level grade
    grade = scope.create('levelTOP', 'render_grade')
    _set_par_safe(grade, 'brightness', p['glow'])
    _set_par_safe(grade, 'contrast', 1.2 + p['noise_strength'] * 0.2)
    _set_par_safe(grade, 'gamma', 0.9)
    comp.outputConnectors[0].connect(grade.inputConnectors[0])
    grade.nodeX = 800
    grade.nodeY = 0
    nodes_created.append(('render_grade', 'levelTOP'))
    
    # 8. Feedback (subtle)
    feedback = scope.create('feedbackTOP', 'orb_feedback')
    _set_par_safe(feedback, 'opacity', p['feedback_opacity'] * 0.7)
    grade.outputConnectors[0].connect(feedback.inputConnectors[0])
    feedback.nodeX = 1000
    feedback.nodeY = 0
    nodes_created.append(('orb_feedback', 'feedbackTOP'))
    
    # 9. Final soften
    final_blur = scope.create('blurTOP', 'final_soften')
    _set_par_safe(final_blur, 'sizex', 1.0 + p['glow'] * 1.5)
    _set_par_safe(final_blur, 'sizey', 1.0 + p['glow'] * 1.5)
    feedback.outputConnectors[0].connect(final_blur.inputConnectors[0])
    final_blur.nodeX = 1200
    final_blur.nodeY = 0
    nodes_created.append(('final_soften', 'blurTOP'))
    
    # 10. Output null
    out = scope.create('nullTOP', 'generated_out')
    final_blur.outputConnectors[0].connect(out.inputConnectors[0])
    out.nodeX = 1400
    out.nodeY = 0
    nodes_created.append(('generated_out', 'nullTOP'))
    
    return out, nodes_created


RECIPE_BUILDERS = {
    'dreamy_particle_field': _build_dreamy_particle_field,
    'glitch_feedback_field': _build_glitch_feedback_field,
    'soft_3d_orb': _build_soft_3d_orb,
}


def _execute_recipe(recipe_id, params):
    """Execute a hardcoded recipe and return telemetry."""
    global _last_debug
    
    recipe_params = {
        'color_palette': params.get('color_palette', params.get('color_mode', 'monochrome')),
        'noise_strength': float(params.get('noise_strength', params.get('turbulence', 0.5))),
        'motion_speed': float(params.get('motion_speed', params.get('speed', 0.5))),
        'feedback_opacity': float(params.get('feedback_opacity', 0.85)),
        'glow': float(params.get('glow', params.get('brightness', 0.5))),
        'particle_density': float(params.get('particle_density', params.get('density', 0.7))),
    }
    
    builder = RECIPE_BUILDERS.get(recipe_id)
    if not builder:
        recipe_id = 'feedback_2d'
        builder = RECIPE_BUILDERS[recipe_id]
    
    scope = _reset_generated_scope('/project1/autotd_generated')
    print(f'[AutoTD] Scope reset: {scope.path}')
    print(f'[AutoTD] Executing recipe: {recipe_id}')
    print(f'[AutoTD] Recipe params: {json.dumps(recipe_params, indent=2)}')
    
    try:
        output_node, nodes_created = builder(scope, recipe_params)
    except Exception as e:
        print(f'[AutoTD] RECIPE ERROR: {e}')
        _last_debug = {'error': str(e), 'recipe_id': recipe_id}
        return {
            'scope': scope.path if scope else '',
            'error': str(e),
            'recipe_id': recipe_id,
        }
    
    # Route to project out1
    out1_path = _route_project_out(output_node)
    
    _last_debug = {
        'recipe_id': recipe_id,
        'recipe_params': recipe_params,
        'nodes_created': nodes_created,
        'node_count': len(nodes_created),
        'output_node': output_node.path if output_node else '',
        'out1': out1_path,
    }
    
    # Print telemetry
    print('\n========= AutoTD Recipe Telemetry =========')
    print(f'Recipe: {recipe_id}')
    print(f'Nodes created: {len(nodes_created)}')
    for name, ntype in nodes_created:
        print(f'  - {name} ({ntype})')
    print(f'Output: {output_node.path if output_node else "NONE"}')
    print(f'out1: {out1_path}')
    print('============================================\n')
    
    return {
        'scope': scope.path,
        'recipe_id': recipe_id,
        'recipe_params': recipe_params,
        'node_count': len(nodes_created),
        'nodes_created': [{'name': n, 'type': t} for n, t in nodes_created],
        'output': output_node.path if output_node else '',
        'out1': out1_path,
        'final_output_top': out1_path,
        'output_top': output_node.path if output_node else '',
    }


# ══════════════════════════════════════════════════════════════════════
#  2D MVP recipe override: feedback_2d + particle_field only
# ══════════════════════════════════════════════════════════════════════

def _clamp_float(value, lo=0.0, hi=1.0, default=0.5):
    try:
        value = float(value)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _hex_to_rgb(color, fallback=(1.0, 1.0, 1.0)):
    try:
        value = str(color or '').strip()
        if value.startswith('#'):
            value = value[1:]
        if len(value) == 3:
            value = ''.join(ch * 2 for ch in value)
        if len(value) != 6:
            return fallback
        return (
            int(value[0:2], 16) / 255.0,
            int(value[2:4], 16) / 255.0,
            int(value[4:6], 16) / 255.0,
        )
    except Exception:
        return fallback


def _set_rgb_candidates(node, prefixes, rgb, alpha=1.0):
    for prefix in prefixes:
        _set_par_safe(node, prefix, (*rgb, alpha))
        _set_par_safe(node, prefix, rgb)
        _set_par_safe(node, f'{prefix}r', rgb[0])
        _set_par_safe(node, f'{prefix}g', rgb[1])
        _set_par_safe(node, f'{prefix}b', rgb[2])
        _set_par_safe(node, f'{prefix}a', alpha)


def _apply_ramp_palette(ramp, p, mode='feedback'):
    c1 = _hex_to_rgb(p.get('color_1'), (0.85, 0.94, 1.0))
    c2 = _hex_to_rgb(p.get('color_2'), (0.38, 0.56, 0.9))
    c3 = _hex_to_rgb(p.get('color_3'), (0.03, 0.04, 0.08))
    _set_rgb_candidates(ramp, ('color1', 'colora', 'colorstart', 'startcolor'), c3)
    _set_rgb_candidates(ramp, ('color2', 'colorb', 'colorend', 'endcolor'), c2)
    _set_rgb_candidates(ramp, ('color3', 'colorc', 'midcolor'), c1)
    _set_par_safe(ramp, 'type', 'radial' if mode == 'particle' else 'circular')
    _set_par_safe(ramp, 'phase', p.get('speed', 0.5) * 0.2)
    _set_par_safe(ramp, 'period', 0.75)
    return {'color_1_rgb': c1, 'color_2_rgb': c2, 'color_3_rgb': c3}


def _connect(source, target, index=0):
    if not source or not target:
        return False
    try:
        source.outputConnectors[0].connect(target.inputConnectors[index])
        return True
    except Exception:
        try:
            target.inputConnectors[index].connect(source)
            return True
        except Exception:
            return False


def _make_top(scope, op_type, name, x, y):
    node = scope.create(op_type, name)
    try:
        node.nodeX = x
        node.nodeY = y
    except Exception:
        pass
    return node


def _make_op(scope, op_type, name, x, y):
    node = scope.create(op_type, name)
    try:
        node.nodeX = x
        node.nodeY = y
    except Exception:
        pass
    return node


def _set_first_par(node, names, value):
    for name in names:
        if _set_par_safe(node, name, value):
            return True
    return False


def _set_expr_safe(node, name, expression):
    try:
        par = getattr(node.par, name)
        par.expr = expression
        return True
    except Exception:
        return False


def _set_resolution(top, w=960, h=540):
    _set_par_safe(top, 'resolutionw', w)
    _set_par_safe(top, 'resolutionh', h)
    _set_noise_par(top, 'resolutionw', w)
    _set_noise_par(top, 'resolutionh', h)


def _load_image_top(scope, image_path, x=-240, y=130):
    if not image_path:
        return None, {'requested': False, 'loaded': False, 'path': '', 'width': 0, 'height': 0, 'error': ''}
    movie = _make_top(scope, 'moviefileinTOP', 'source_image', x, y)
    ok = _set_par_safe(movie, 'file', image_path)
    info = {'requested': True, 'loaded': False, 'path': image_path, 'width': 0, 'height': 0, 'error': ''}
    try:
        movie.cook(force=True)
        info['width'] = int(getattr(movie, 'width', 0) or 0)
        info['height'] = int(getattr(movie, 'height', 0) or 0)
        info['loaded'] = bool(ok and info['width'] > 0 and info['height'] > 0)
        if not info['loaded']:
            info['error'] = 'moviefileinTOP did not report a valid image size'
    except Exception as e:
        info['error'] = str(e)
    return movie, info


def _build_image_source_showcase(scope, p):
    nodes = []
    image_top, image_info = _load_image_top(scope, p.get('image_asset_path', ''), -260, 120)
    if not image_top or not image_info.get('loaded'):
        return None, nodes, image_info
    nodes.append(('source_image', 'moviefileinTOP'))

    image_level = _make_top(scope, 'levelTOP', 'image_source_level', -20, 120)
    _connect(image_top, image_level)
    _set_par_safe(image_level, 'brightness', 0.62 + p['glow_intensity'] * 0.18)
    _set_par_safe(image_level, 'contrast', 1.35 + p['glow_intensity'] * 0.5)
    nodes.append(('image_source_level', 'levelTOP'))

    edge = _make_top(scope, 'edgeTOP', 'image_displace_map', 220, -80)
    _connect(image_level, edge)
    nodes.append(('image_displace_map', 'edgeTOP'))

    drift = _make_top(scope, 'transformTOP', 'image_slow_drift', 220, 120)
    _connect(image_level, drift)
    _set_par_safe(drift, 'scale', 1.018 + p['speed'] * 0.022)
    _set_par_safe(drift, 'rotate', (p['speed'] - 0.5) * 1.8)
    nodes.append(('image_slow_drift', 'transformTOP'))

    feedback = _make_top(scope, 'feedbackTOP', 'image_soft_feedback', 460, 120)
    _connect(drift, feedback)
    _set_par_safe(feedback, 'opacity', min(0.995, max(0.91, p['feedback_opacity'] + 0.12)))
    nodes.append(('image_soft_feedback', 'feedbackTOP'))

    displace = _make_top(scope, 'displaceTOP', 'image_flow_displace', 700, 120)
    _connect(feedback, displace, 0)
    _connect(edge, displace, 1)
    _set_par_safe(displace, 'weight', 0.025 + p['displace_weight'] * 0.09)
    nodes.append(('image_flow_displace', 'displaceTOP'))

    echo = _make_top(scope, 'feedbackTOP', 'image_echo_feedback', 940, 120)
    _connect(displace, echo)
    _set_par_safe(echo, 'opacity', min(0.985, max(0.88, p['feedback_opacity'] + 0.04)))
    nodes.append(('image_echo_feedback', 'feedbackTOP'))

    echo_drift = _make_top(scope, 'transformTOP', 'image_echo_drift', 1180, 120)
    _connect(echo, echo_drift)
    _set_par_safe(echo_drift, 'tx', (p['speed'] - 0.5) * 0.018)
    _set_par_safe(echo_drift, 'ty', 0.006 + p['speed'] * 0.014)
    _set_par_safe(echo_drift, 'rotate', (p['speed'] - 0.5) * 2.6)
    _set_par_safe(echo_drift, 'scale', 1.006 + p['speed'] * 0.018)
    nodes.append(('image_echo_drift', 'transformTOP'))

    color_ramp = _make_top(scope, 'rampTOP', 'image_color_filter', 1180, -120)
    _set_resolution(color_ramp, image_info.get('width', 1024), image_info.get('height', 1024))
    _apply_ramp_palette(color_ramp, p, 'feedback')
    nodes.append(('image_color_filter', 'rampTOP'))

    color_mix = _make_top(scope, 'compositeTOP', 'image_color_mix', 1420, 120)
    _connect(echo_drift, color_mix, 0)
    _connect(color_ramp, color_mix, 1)
    _set_par_safe(color_mix, 'operand', 'multiply')
    nodes.append(('image_color_mix', 'compositeTOP'))

    glow = _make_top(scope, 'blurTOP', 'image_soft_glow', 1660, 120)
    _connect(color_mix, glow)
    _set_par_safe(glow, 'sizex', 4 + p['blur_amount'] * 18)
    _set_par_safe(glow, 'sizey', 4 + p['blur_amount'] * 18)
    nodes.append(('image_soft_glow', 'blurTOP'))

    glow_level = _make_top(scope, 'levelTOP', 'image_glow_level', 1900, 120)
    _connect(glow, glow_level)
    _set_par_safe(glow_level, 'brightness', 0.92 + p['glow_intensity'] * 0.62)
    _set_par_safe(glow_level, 'contrast', 1.16 + p['glow_intensity'] * 0.42)
    nodes.append(('image_glow_level', 'levelTOP'))

    final_grade = _make_top(scope, 'levelTOP', 'image_final_grade', 2140, 120)
    _connect(glow_level, final_grade)
    _set_par_safe(final_grade, 'brightness', 0.92 + p['glow_intensity'] * 0.22)
    _set_par_safe(final_grade, 'contrast', 1.12 + p['glow_intensity'] * 0.28)
    nodes.append(('image_final_grade', 'levelTOP'))

    out = _make_top(scope, 'nullTOP', 'generated_out', 2380, 120)
    _connect(final_grade, out)
    nodes.append(('generated_out', 'nullTOP'))

    image_info.update({
        'image_first_pipeline': True,
        'image_pipeline': 'moviefileinTOP_level_edge_displace_feedback_color_glow',
        'image_usage': p.get('image_usage', 'background_composite'),
        'particle_engine': 'disabled_for_image_source',
        'particle_background': '',
        'pop_nodes_created': [],
        'particle_count': 0,
        'trail_enabled': False,
        'fallback_used': False,
        'fallback_reason': '',
    })
    try:
        recipe_out = _make_top(scope, 'outTOP', 'recipe_out', 2620, 120)
        _connect(out, recipe_out)
        nodes.append(('recipe_out', 'outTOP'))
        return recipe_out, nodes, image_info
    except Exception:
        return out, nodes, image_info


def _recipe_params_from_payload(params):
    image_path = params.get('image_asset_path') or params.get('asset_image_path') or ''
    color_palette = params.get('color_palette') or params.get('color_mode') or 'monochrome'
    noise_strength = _clamp_float(params.get('noise_strength', params.get('turbulence', 0.5)))
    motion_speed = _clamp_float(params.get('motion_speed', params.get('speed', 0.5)))
    feedback_opacity = _clamp_float(params.get('feedback_opacity', 0.85))
    glow = _clamp_float(params.get('glow', params.get('brightness', 0.6)))
    particle_density = _clamp_float(params.get('particle_density', params.get('density', 0.7)))
    
    blur_amount = _clamp_float(params.get('blur_amount', 0.4))
    displace_weight = _clamp_float(params.get('displace_weight', 0.35))
    glow_intensity = _clamp_float(params.get('glow_intensity', glow))
    particle_count = int(_clamp_float(params.get('particle_count', 700), 50, 3000, 700))
    spread = _clamp_float(params.get('spread', 0.7))
    color_1 = params.get('color_1', '#ffffff')
    color_2 = params.get('color_2', '#9fb7ff')
    color_3 = params.get('color_3', '#05070a')
    
    return {
        'color_palette': color_palette,
        'noise_strength': noise_strength,
        'motion_speed': motion_speed,
        'feedback_opacity': feedback_opacity,
        'glow': glow,
        'particle_density': particle_density,
        'speed': motion_speed,
        'blur_amount': blur_amount,
        'displace_weight': displace_weight,
        'glow_intensity': glow_intensity,
        'particle_count': particle_count,
        'spread': spread,
        'color_1': color_1,
        'color_2': color_2,
        'color_3': color_3,
        'image_asset_path': image_path,
        'asset_image_path': image_path,
        'asset_3d_path': params.get('asset_3d_path', ''),
        'image_usage': params.get('image_usage', 'background_composite' if image_path else 'none'),
    }


def _build_feedback_2d(scope, p):
    nodes = []
    image_info = {'requested': False, 'loaded': False, 'path': '', 'width': 0, 'height': 0, 'error': ''}

    notes = _make_op(scope, 'textDAT', 'recipe_notes', -520, 240)
    try:
        notes.text = 'AutoTD procedural-only feedback_2d: layered noise seeds, dual feedback loops, displacement, glow, final grade.'
    except Exception:
        pass
    nodes.append(('recipe_notes', 'textDAT'))

    lfo = _make_op(scope, 'lfoCHOP', 'control_lfo', -520, 80)
    _set_first_par(lfo, ('freq', 'frequency'), 0.03 + p['speed'] * 0.11)
    nodes.append(('control_lfo', 'lfoCHOP'))

    base_a = _make_top(scope, 'noiseTOP', 'base_noise_a', -300, 0)
    _set_resolution(base_a)
    _set_par_safe(base_a, 'seed', 17)
    _set_noise_par(base_a, 'period', 1.5 - p['speed'] * 0.45)
    _set_noise_par(base_a, 'harmonics', 3 + int(p['noise_strength'] * 5))
    _set_noise_par(base_a, 'amp', 0.32 + p['noise_strength'] * 0.42)
    nodes.append(('base_noise_a', 'noiseTOP'))

    base_b = _make_top(scope, 'noiseTOP', 'base_noise_b', -300, -170)
    _set_resolution(base_b)
    _set_par_safe(base_b, 'seed', 131)
    _set_noise_par(base_b, 'period', 0.62 + (1.0 - p['speed']) * 0.55)
    _set_noise_par(base_b, 'harmonics', 2 + int(p['noise_strength'] * 4))
    _set_noise_par(base_b, 'amp', 0.18 + p['displace_weight'] * 0.45)
    nodes.append(('base_noise_b', 'noiseTOP'))

    ramp_primary = _make_top(scope, 'rampTOP', 'color_ramp_primary', -80, 0)
    _set_resolution(ramp_primary)
    _apply_ramp_palette(ramp_primary, p, 'feedback')
    nodes.append(('color_ramp_primary', 'rampTOP'))

    seed_mix = _make_top(scope, 'compositeTOP', 'seed_composite', 140, 0)
    _connect(base_a, seed_mix, 0)
    _connect(ramp_primary, seed_mix, 1)
    _set_par_safe(seed_mix, 'operand', 'multiply')
    nodes.append(('seed_composite', 'compositeTOP'))

    loop_a = _make_top(scope, 'feedbackTOP', 'feedback_loop_a', 360, 0)
    _connect(seed_mix, loop_a)
    _set_par_safe(loop_a, 'opacity', min(0.985, max(0.84, p['feedback_opacity'] + 0.045)))
    nodes.append(('feedback_loop_a', 'feedbackTOP'))

    transform_a = _make_top(scope, 'transformTOP', 'feedback_transform_a', 580, 0)
    _connect(loop_a, transform_a)
    _set_par_safe(transform_a, 'tx', (p['speed'] - 0.5) * 0.012)
    _set_par_safe(transform_a, 'ty', (0.5 - p['speed']) * 0.009)
    _set_par_safe(transform_a, 'rotate', p['speed'] * 0.72)
    _set_par_safe(transform_a, 'scale', 1.004 + p['speed'] * 0.010)
    nodes.append(('feedback_transform_a', 'transformTOP'))

    displace_a = _make_top(scope, 'displaceTOP', 'flow_displace_a', 800, 0)
    _connect(transform_a, displace_a, 0)
    _connect(base_b, displace_a, 1)
    _set_par_safe(displace_a, 'weight', 0.012 + p['displace_weight'] * 0.085)
    nodes.append(('flow_displace_a', 'displaceTOP'))

    loop_b = _make_top(scope, 'feedbackTOP', 'feedback_loop_b', 1020, 0)
    _connect(displace_a, loop_b)
    _set_par_safe(loop_b, 'opacity', min(0.965, max(0.80, p['feedback_opacity'] * 0.94)))
    nodes.append(('feedback_loop_b', 'feedbackTOP'))

    transform_b = _make_top(scope, 'transformTOP', 'feedback_transform_b', 1240, 0)
    _connect(loop_b, transform_b)
    _set_par_safe(transform_b, 'tx', (0.5 - p['speed']) * 0.007)
    _set_par_safe(transform_b, 'ty', 0.003 + p['speed'] * 0.008)
    _set_par_safe(transform_b, 'rotate', -0.38 - p['speed'] * 0.55)
    _set_par_safe(transform_b, 'scale', 1.001 + p['displace_weight'] * 0.014)
    nodes.append(('feedback_transform_b', 'transformTOP'))

    echo_mix = _make_top(scope, 'compositeTOP', 'echo_composite', 1460, 0)
    _connect(transform_b, echo_mix, 0)
    _connect(seed_mix, echo_mix, 1)
    _set_par_safe(echo_mix, 'operand', 'screen')
    nodes.append(('echo_composite', 'compositeTOP'))

    mist_blur = _make_top(scope, 'blurTOP', 'mist_blur', 1680, 0)
    _connect(echo_mix, mist_blur)
    _set_par_safe(mist_blur, 'sizex', 4 + p['blur_amount'] * 18)
    _set_par_safe(mist_blur, 'sizey', 4 + p['blur_amount'] * 18)
    nodes.append(('mist_blur', 'blurTOP'))

    glow_blur = _make_top(scope, 'blurTOP', 'glow_blur', 1680, -160)
    _connect(echo_mix, glow_blur)
    _set_par_safe(glow_blur, 'sizex', 12 + p['glow_intensity'] * 32)
    _set_par_safe(glow_blur, 'sizey', 12 + p['glow_intensity'] * 32)
    nodes.append(('glow_blur', 'blurTOP'))

    final_mix = _make_top(scope, 'compositeTOP', 'final_composite', 1900, 0)
    _connect(mist_blur, final_mix, 0)
    _connect(glow_blur, final_mix, 1)
    _set_par_safe(final_mix, 'operand', 'screen')
    nodes.append(('final_composite', 'compositeTOP'))

    level = _make_top(scope, 'levelTOP', 'color_filter_level', 2120, 0)
    _connect(final_mix, level)
    _set_par_safe(level, 'brightness', 0.68 + p['glow_intensity'] * 0.24)
    _set_par_safe(level, 'contrast', 1.02 + p['glow_intensity'] * 0.34)
    _set_par_safe(level, 'gamma', 0.88 + (1.0 - p['glow_intensity']) * 0.16)
    nodes.append(('color_filter_level', 'levelTOP'))

    out = _make_top(scope, 'nullTOP', 'generated_out', 2340, 0)
    _connect(level, out)
    nodes.append(('generated_out', 'nullTOP'))
    try:
        recipe_out = _make_top(scope, 'outTOP', 'recipe_out', 2560, 0)
        _connect(out, recipe_out)
        nodes.append(('recipe_out', 'outTOP'))
        return recipe_out, nodes, image_info
    except Exception:
        return out, nodes, image_info


def _build_particle_field(scope, p):
    nodes = []
    particle_info = {
        'particle_engine': 'top_procedural',
        'particle_background': 'black',
        'pop_nodes_created': [],
        'particle_count': int(p.get('particle_count', 700) or 700),
        'trail_enabled': True,
        'fallback_used': False,
        'fallback_reason': '',
    }
    image_info = {'requested': False, 'loaded': False, 'path': '', 'width': 0, 'height': 0, 'error': ''}

    black = _make_top(scope, 'constantTOP', 'black_background', -300, -20)
    _set_resolution(black)
    _set_rgb_candidates(black, ('color', 'colorrgba', 'bgcolor'), (0.0, 0.0, 0.0), 1.0)
    nodes.append(('black_background', 'constantTOP'))

    notes = _make_op(scope, 'textDAT', 'recipe_notes', -300, 190)
    try:
        notes.text = 'AutoTD procedural-only particle_field: thresholded sparkle seeds, dual feedback trails, glow blur, black composite.'
    except Exception:
        pass
    nodes.append(('recipe_notes', 'textDAT'))

    lfo = _make_op(scope, 'lfoCHOP', 'particle_lfo', -80, 190)
    _set_first_par(lfo, ('freq', 'frequency'), 0.04 + p['speed'] * 0.08)
    nodes.append(('particle_lfo', 'lfoCHOP'))

    seed = _make_top(scope, 'noiseTOP', 'particle_seed_noise', -80, -20)
    _set_resolution(seed)
    _set_par_safe(seed, 'seed', int(200 + particle_info['particle_count']) % 10000)
    _set_noise_par(seed, 'period', 0.07 + (1.0 - p['speed']) * 0.18)
    _set_noise_par(seed, 'harmonics', 6 + int(p['noise_strength'] * 4))
    _set_noise_par(seed, 'rough', 0.72 + p['noise_strength'] * 0.2)
    _set_noise_par(seed, 'amp', 0.42 + p['noise_strength'] * 0.22)
    _set_noise_par(seed, 'mono', True)
    nodes.append(('particle_seed_noise', 'noiseTOP'))

    mask = _make_top(scope, 'thresholdTOP', 'particle_mask_threshold', 140, -20)
    _connect(seed, mask)
    threshold = 0.94 - min(0.08, particle_info['particle_count'] / 45000.0)
    _set_par_safe(mask, 'threshold', threshold)
    nodes.append(('particle_mask_threshold', 'thresholdTOP'))

    sharpen = _make_top(scope, 'levelTOP', 'particle_sharpen_level', 360, -20)
    _connect(mask, sharpen)
    _set_par_safe(sharpen, 'brightness', 0.95 + p['glow_intensity'] * 0.35)
    _set_par_safe(sharpen, 'contrast', 1.8 + p['glow_intensity'] * 1.1)
    _set_par_safe(sharpen, 'gamma', 0.62)
    nodes.append(('particle_sharpen_level', 'levelTOP'))

    particle_level = _make_top(scope, 'levelTOP', 'particle_level', 400, -20)
    _connect(sharpen, particle_level)
    _set_par_safe(particle_level, 'brightness', 0.92 + p['glow_intensity'] * 0.55)
    _set_par_safe(particle_level, 'contrast', 1.25 + p['glow_intensity'] * 0.9)
    nodes.append(('particle_level', 'levelTOP'))

    color_wash = _make_top(scope, 'constantTOP', 'particle_color_wash', 400, -170)
    _set_resolution(color_wash)
    _set_rgb_candidates(color_wash, ('color', 'colorrgba', 'bgcolor'), _hex_to_rgb(p.get('color_1'), (1.0, 0.94, 0.65)), 1.0)
    nodes.append(('particle_color_wash', 'constantTOP'))

    colorize = _make_top(scope, 'compositeTOP', 'particle_colorize', 560, -20)
    _connect(particle_level, colorize, 0)
    _connect(color_wash, colorize, 1)
    _set_par_safe(colorize, 'operand', 'multiply')
    nodes.append(('particle_colorize', 'compositeTOP'))

    feedback = _make_top(scope, 'feedbackTOP', 'particle_feedback_a', 620, -20)
    _connect(colorize, feedback)
    _set_par_safe(feedback, 'opacity', min(0.975, max(0.80, p['feedback_opacity'] + 0.06)))
    nodes.append(('particle_feedback_a', 'feedbackTOP'))

    drift = _make_top(scope, 'transformTOP', 'particle_drift_transform', 840, -20)
    _connect(feedback, drift)
    _set_par_safe(drift, 'tx', (p['speed'] - 0.5) * 0.006)
    _set_par_safe(drift, 'ty', 0.002 + p['speed'] * 0.006)
    _set_par_safe(drift, 'rotate', p['speed'] * 0.28)
    _set_par_safe(drift, 'scale', 1.001 + p['spread'] * 0.004)
    nodes.append(('particle_drift_transform', 'transformTOP'))

    echo = _make_top(scope, 'feedbackTOP', 'particle_feedback_b', 1060, -20)
    _connect(drift, echo)
    _set_par_safe(echo, 'opacity', min(0.94, max(0.72, p['feedback_opacity'] * 0.88)))
    nodes.append(('particle_feedback_b', 'feedbackTOP'))

    trail_small = _make_top(scope, 'blurTOP', 'trail_blur_small', 1280, -20)
    _connect(echo, trail_small)
    _set_par_safe(trail_small, 'sizex', 1.5 + p['blur_amount'] * 8)
    _set_par_safe(trail_small, 'sizey', 1.5 + p['blur_amount'] * 8)
    nodes.append(('trail_blur_small', 'blurTOP'))

    trail_wide = _make_top(scope, 'blurTOP', 'trail_blur_wide', 1280, -170)
    _connect(echo, trail_wide)
    _set_par_safe(trail_wide, 'sizex', 8 + p['glow_intensity'] * 28)
    _set_par_safe(trail_wide, 'sizey', 8 + p['glow_intensity'] * 28)
    nodes.append(('trail_blur_wide', 'blurTOP'))

    sparkle_mix = _make_top(scope, 'compositeTOP', 'sparkle_composite', 1500, -20)
    _connect(trail_small, sparkle_mix, 0)
    _connect(colorize, sparkle_mix, 1)
    _set_par_safe(sparkle_mix, 'operand', 'screen')
    nodes.append(('sparkle_composite', 'compositeTOP'))

    tone = _make_top(scope, 'levelTOP', 'particle_color_level', 1720, -20)
    _connect(sparkle_mix, tone)
    _set_par_safe(tone, 'brightness', 0.82 + p['glow_intensity'] * 0.34)
    _set_par_safe(tone, 'contrast', 1.2 + p['glow_intensity'] * 0.42)
    _set_par_safe(tone, 'gamma', 0.72)
    nodes.append(('particle_color_level', 'levelTOP'))

    black_mix = _make_top(scope, 'compositeTOP', 'black_screen_composite', 1940, -20)
    _connect(black, black_mix, 0)
    _connect(tone, black_mix, 1)
    _set_par_safe(black_mix, 'operand', 'screen')
    nodes.append(('black_screen_composite', 'compositeTOP'))

    glow_mix = _make_top(scope, 'compositeTOP', 'wide_glow_composite', 2160, -20)
    _connect(black_mix, glow_mix, 0)
    _connect(trail_wide, glow_mix, 1)
    _set_par_safe(glow_mix, 'operand', 'screen')
    nodes.append(('wide_glow_composite', 'compositeTOP'))

    out = _make_top(scope, 'nullTOP', 'generated_out', 2380, -20)
    _connect(glow_mix, out)
    nodes.append(('generated_out', 'nullTOP'))
    image_info.update(particle_info)
    try:
        recipe_out = _make_top(scope, 'outTOP', 'recipe_out', 2600, -20)
        _connect(out, recipe_out)
        nodes.append(('recipe_out', 'outTOP'))
        return recipe_out, nodes, image_info
    except Exception:
        return out, nodes, image_info

RECIPE_BUILDERS = {
    'dreamy_particle_field': _build_dreamy_particle_field,
    'glitch_feedback_field': _build_glitch_feedback_field,
    'soft_3d_orb': _build_soft_3d_orb,
    'feedback_2d': _build_feedback_2d,
    'particle_field': _build_particle_field,
}


def _execute_recipe(recipe_id, params):
    """Execute the recipe and return telemetry with full backward compatibility."""
    global _last_debug
    
    # Standardize recipe_id
    if recipe_id not in RECIPE_BUILDERS:
        recipe_id = 'feedback_2d'
        
    recipe_params = _recipe_params_from_payload(params)
    scope = _reset_generated_scope('/project1/autotd_generated')
    print(f'[AutoTD] Scope reset: {scope.path}')
    print(f'[AutoTD] Executing recipe: {recipe_id}')
    print(f'[AutoTD] Recipe params: {json.dumps(recipe_params, indent=2)}')
    
    try:
        if recipe_params.get('image_asset_path'):
            res = _build_image_source_showcase(scope, recipe_params)
            if res[0]:
                recipe_id = 'image_source_showcase'
            else:
                builder = RECIPE_BUILDERS[recipe_id]
                res = builder(scope, recipe_params)
        else:
            builder = RECIPE_BUILDERS[recipe_id]
            res = builder(scope, recipe_params)
        if len(res) == 3:
            output_node, nodes_created, image_info = res
        else:
            output_node, nodes_created = res
            image_info = {}
    except Exception as e:
        print(f'[AutoTD] RECIPE ERROR: {e}')
        _last_debug = {'error': str(e), 'recipe_id': recipe_id, 'recipe_params': recipe_params}
        return {'scope': scope.path if scope else '', 'error': str(e), 'recipe_id': recipe_id}

    out1_path = _route_project_out(output_node)
    
    # Read optional geometry/3D telemetry from 3D templates or parameters
    point_count = 0
    primitive_count = 0
    td_model_loaded = False
    asset_3d_loaded_in_td = False
    
    # If this is soft_3d_orb or contains a 3D asset, inspect the fileSOP or geometry node
    if recipe_id == 'soft_3d_orb' or recipe_params.get('asset_3d_path'):
        try:
            # Try to query the SOP node inside our generated scope
            file_sop = scope.op('file1') or scope.op('file_sop')
            if not file_sop:
                # search recursively in generated scope
                for child in scope.children:
                    if child.type == 'fileSOP':
                        file_sop = child
                        break
            if file_sop:
                point_count = getattr(file_sop, 'numPoints', 0)
                primitive_count = getattr(file_sop, 'numPrims', 0)
                if point_count > 0:
                    td_model_loaded = True
                    asset_3d_loaded_in_td = True
        except Exception as exc:
            print(f'[AutoTD] Failed to retrieve 3D model telemetry: {exc}')

    _last_debug = {
        'recipe_id': recipe_id,
        'recipe_params': recipe_params,
        'nodes_created': nodes_created,
        'node_count': len(nodes_created),
        'output_node': output_node.path if output_node else '',
        'out1': out1_path,
        'image_asset_path': image_info.get('path', recipe_params.get('asset_image_path', '')),
        'image_usage': recipe_params.get('image_usage', 'background' if recipe_params.get('asset_image_path') else 'none'),
        'image_loaded_in_td': bool(image_info.get('loaded')),
        'image_width': image_info.get('width', 0),
        'image_height': image_info.get('height', 0),
        'image_error': image_info.get('error', ''),
        'image_first_pipeline': bool(image_info.get('image_first_pipeline')),
        'image_pipeline': image_info.get('image_pipeline', ''),
        'td_model_loaded': td_model_loaded,
        'asset_3d_loaded_in_td': asset_3d_loaded_in_td,
        'point_count': point_count,
        'primitive_count': primitive_count,
        'particle_engine': image_info.get('particle_engine', 'pop' if recipe_id in ('dreamy_particle_field', 'particle_field') else ''),
        'particle_background': image_info.get('particle_background', ''),
        'pop_nodes_created': image_info.get('pop_nodes_created', []),
        'particle_count': image_info.get('particle_count', 0),
        'trail_enabled': bool(image_info.get('trail_enabled')),
        'fallback_used': bool(image_info.get('fallback_used')),
        'fallback_reason': image_info.get('fallback_reason', ''),
    }
    
    print('\n========= AutoTD Recipe Telemetry =========')
    print(f'Recipe: {recipe_id}')
    print(f'Nodes created: {len(nodes_created)}')
    print(f'Image loaded: {_last_debug["image_loaded_in_td"]} {_last_debug["image_asset_path"]}')
    print(f'3D Model loaded: {asset_3d_loaded_in_td} (points: {point_count}, primitives: {primitive_count})')
    print(f'Output: {output_node.path if output_node else "NONE"}')
    print(f'out1: {out1_path}')
    print('============================================\n')
    
    return {
        'scope': scope.path,
        'recipe_id': recipe_id,
        'recipe_params': recipe_params,
        'node_count': len(nodes_created),
        'nodes_created': [{'name': n, 'type': t} for n, t in nodes_created],
        'output': output_node.path if output_node else '',
        'out1': out1_path,
        'final_output_top': out1_path,
        'output_top': output_node.path if output_node else '',
        'render_top': output_node.path if output_node else '',
        'image_asset_path': _last_debug['image_asset_path'],
        'image_usage': _last_debug['image_usage'],
        'image_loaded_in_td': _last_debug['image_loaded_in_td'],
        'td_image_loaded': _last_debug['image_loaded_in_td'],
        'image_width': _last_debug['image_width'],
        'image_height': _last_debug['image_height'],
        'image_error': _last_debug['image_error'],
        'image_first_pipeline': _last_debug['image_first_pipeline'],
        'image_pipeline': _last_debug['image_pipeline'],
        'td_model_loaded': td_model_loaded,
        'asset_3d_loaded_in_td': asset_3d_loaded_in_td,
        'point_count': point_count,
        'primitive_count': primitive_count,
        'particle_engine': _last_debug['particle_engine'],
        'particle_background': _last_debug['particle_background'],
        'pop_nodes_created': _last_debug['pop_nodes_created'],
        'particle_count': _last_debug['particle_count'],
        'trail_enabled': _last_debug['trail_enabled'],
        'fallback_used': _last_debug['fallback_used'],
        'fallback_reason': _last_debug['fallback_reason'],
        'composite_connected': True,
        'window_comp_disabled': True,
        'fullscreen_disabled': True,
    }


# ══════════════════════════════════════════════════════════════════════
#  HTTP HANDLERS
# ══════════════════════════════════════════════════════════════════════

def onHTTPRequest(*args):
    if len(args) == 3:
        _webServerDAT, request, response = args
    elif len(args) == 2:
        request, response = args
    else:
        raise TypeError('onHTTPRequest expects (request, response) or (webServerDAT, request, response)')

    uri = request.get('uri', '').rstrip('/')
    method = request.get('method', 'GET').upper()

    if method == 'OPTIONS':
        response['statusCode'] = 204
        _json_headers(response)
        response['statusReason'] = 'No Content'
        return response

    if uri == '/status':
        response['statusCode'] = 200
        response['statusReason'] = 'OK'
        _json_headers(response)
        response['data'] = json.dumps({'status': 'connected', 'server': 'TouchDesigner WebServer'})
        return response

    if uri == '/preview':
        try:
            path = _save_preview_png()
            data = Path(path).read_bytes()
            response['statusCode'] = 200
            response['statusReason'] = 'OK'
            response['headers'] = {
                'Content-Type': 'image/png',
                'Content-Length': str(len(data)),
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Access-Control-Allow-Origin': '*',
            }
            response['data'] = data
        except Exception as e:
            response['statusCode'] = 500
            response['statusReason'] = 'Internal Server Error'
            _json_headers(response)
            response['data'] = json.dumps({'error': str(e)})
        return response

    if uri == '/debug':
        response['statusCode'] = 200
        response['statusReason'] = 'OK'
        _json_headers(response)
        response['data'] = json.dumps(_last_debug, default=str)
        return response

    if uri in ('/export/tox', '/export_tox') and method == 'POST':
        response['statusCode'] = 200
        response['statusReason'] = 'OK'
        _json_headers(response)
        try:
            body = request.get('data', '{}')
            if isinstance(body, bytes):
                body = body.decode('utf-8')
            params = json.loads(body) if isinstance(body, str) else (body if isinstance(body, dict) else {})
            result = _export_generated_tox(params.get('export_dir', ''), params.get('name', 'autotd_export'))
            response['data'] = json.dumps(result)
        except Exception as e:
            response['statusCode'] = 500
            response['statusReason'] = 'Internal Server Error'
            response['data'] = json.dumps({'status': 'error', 'error': str(e)})
        return response

    if uri == '/generate' and method == 'POST':
        response['statusCode'] = 200
        response['statusReason'] = 'OK'
        _json_headers(response)

        try:
            body = request.get('data', '{}')
            if isinstance(body, bytes):
                body = body.decode('utf-8')
            params = json.loads(body) if isinstance(body, str) else (body if isinstance(body, dict) else {})
        except Exception as e:
            response['data'] = json.dumps({'error': f'JSON parse error: {e}'})
            return response

        recipe_id = params.get('recipe_id', params.get('template', 'feedback_2d'))
        # Map legacy template names to the current 2D MVP recipes.
        template_to_recipe = {
            'particle': 'particle_field',
            'feedback': 'feedback_2d',
            '3d': 'feedback_2d',
        }
        if recipe_id in template_to_recipe:
            recipe_id = template_to_recipe[recipe_id]
        if recipe_id not in RECIPE_BUILDERS:
            recipe_id = 'feedback_2d'

        try:
            result = _execute_recipe(recipe_id, params)
            response['data'] = json.dumps({'applied': result})
        except Exception as e:
            print(f'[AutoTD] Generate failed: {e}')
            response['data'] = json.dumps({'error': str(e)})

        return response

    response['statusCode'] = 404
    response['statusReason'] = 'Not Found'
    _json_headers(response)
    response['data'] = json.dumps({'error': f'Unknown endpoint: {uri}'})
    return response
