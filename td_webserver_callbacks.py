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
        recipe_id = 'dreamy_particle_field'
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


def _recipe_params_from_payload(params):
    image_path = params.get('image_asset_path') or params.get('asset_image_path') or ''
    return {
        'speed': _clamp_float(params.get('speed', params.get('motion_speed', 0.5))),
        'noise_strength': _clamp_float(params.get('noise_strength', params.get('turbulence', 0.5))),
        'feedback_opacity': _clamp_float(params.get('feedback_opacity', 0.85)),
        'blur_amount': _clamp_float(params.get('blur_amount', 0.4)),
        'displace_weight': _clamp_float(params.get('displace_weight', 0.35)),
        'glow_intensity': _clamp_float(params.get('glow_intensity', params.get('glow', params.get('brightness', 0.6)))),
        'particle_count': int(_clamp_float(params.get('particle_count', 700), 50, 3000, 700)),
        'spread': _clamp_float(params.get('spread', 0.7)),
        'color_1': params.get('color_1', '#ffffff'),
        'color_2': params.get('color_2', '#9fb7ff'),
        'color_3': params.get('color_3', '#05070a'),
        'image_asset_path': image_path,
        'image_usage': params.get('image_usage', 'background_composite'),
    }


def _build_feedback_2d(scope, p):
    nodes = []
    image_top, image_info = _load_image_top(scope, p.get('image_asset_path', ''), -240, 170)
    if image_top:
        nodes.append(('source_image', 'moviefileinTOP'))

    noise = _make_top(scope, 'noiseTOP', 'base_noise', -240, 0)
    _set_resolution(noise)
    _set_noise_par(noise, 'period', 1.8 - p['speed'] * 0.6)
    _set_noise_par(noise, 'harmonics', 1 + int(p['noise_strength'] * 2))
    _set_noise_par(noise, 'amp', 0.08 + p['noise_strength'] * 0.16)
    nodes.append(('base_noise', 'noiseTOP'))

    ramp = _make_top(scope, 'rampTOP', 'color_ramp', -20, 0)
    _set_resolution(ramp)
    _apply_ramp_palette(ramp, p, 'feedback')
    _connect(noise, ramp)
    nodes.append(('color_ramp', 'rampTOP'))

    seed_mix = _make_top(scope, 'compositeTOP', 'seed_composite', 200, 0)
    _connect(ramp, seed_mix, 0)
    if image_top:
        _connect(image_top, seed_mix, 1)
    _set_par_safe(seed_mix, 'operand', 'over')
    nodes.append(('seed_composite', 'compositeTOP'))

    feedback = _make_top(scope, 'feedbackTOP', 'feedback_loop', 420, 0)
    _connect(seed_mix, feedback)
    _set_par_safe(feedback, 'opacity', min(0.97, max(0.82, p['feedback_opacity'] + 0.04)))
    nodes.append(('feedback_loop', 'feedbackTOP'))

    transform = _make_top(scope, 'transformTOP', 'feedback_transform', 640, 0)
    _connect(feedback, transform)
    _set_par_safe(transform, 'tx', (p['speed'] - 0.5) * 0.018)
    _set_par_safe(transform, 'ty', (0.5 - p['speed']) * 0.014)
    _set_par_safe(transform, 'rotate', p['speed'] * 1.15)
    _set_par_safe(transform, 'scale', 1.002 + p['speed'] * 0.01)
    nodes.append(('feedback_transform', 'transformTOP'))

    displace = _make_top(scope, 'displaceTOP', 'flow_displace', 860, 0)
    _connect(transform, displace, 0)
    _connect(noise, displace, 1)
    _set_par_safe(displace, 'weight', 0.006 + p['displace_weight'] * 0.055)
    nodes.append(('flow_displace', 'displaceTOP'))

    echo = _make_top(scope, 'feedbackTOP', 'feedback_echo', 1080, 0)
    _connect(displace, echo)
    _set_par_safe(echo, 'opacity', min(0.96, max(0.78, p['feedback_opacity'] * 0.92)))
    nodes.append(('feedback_echo', 'feedbackTOP'))

    echo_mix = _make_top(scope, 'compositeTOP', 'echo_composite', 1300, 0)
    _connect(echo, echo_mix, 0)
    _connect(seed_mix, echo_mix, 1)
    _set_par_safe(echo_mix, 'operand', 'screen')
    nodes.append(('echo_composite', 'compositeTOP'))

    blur = _make_top(scope, 'blurTOP', 'soft_blur', 1520, 0)
    _connect(echo_mix, blur)
    _set_par_safe(blur, 'sizex', 3 + p['blur_amount'] * 16)
    _set_par_safe(blur, 'sizey', 3 + p['blur_amount'] * 16)
    nodes.append(('soft_blur', 'blurTOP'))

    level = _make_top(scope, 'levelTOP', 'tone_level', 1740, 0)
    _connect(blur, level)
    _set_par_safe(level, 'brightness', 0.62 + p['glow_intensity'] * 0.3)
    _set_par_safe(level, 'contrast', 0.82 + p['glow_intensity'] * 0.22)
    nodes.append(('tone_level', 'levelTOP'))

    image_comp = _make_top(scope, 'compositeTOP', 'image_composite', 1960, 0)
    _connect(level, image_comp, 0)
    if image_top:
        _connect(image_top, image_comp, 1)
    _set_par_safe(image_comp, 'operand', 'screen')
    nodes.append(('image_composite', 'compositeTOP'))

    out = _make_top(scope, 'nullTOP', 'generated_out', 2180, 0)
    _connect(image_comp, out)
    nodes.append(('generated_out', 'nullTOP'))
    try:
        recipe_out = _make_top(scope, 'outTOP', 'recipe_out', 2400, 0)
        _connect(out, recipe_out)
        nodes.append(('recipe_out', 'outTOP'))
        return recipe_out, nodes, image_info
    except Exception:
        return out, nodes, image_info


def _build_particle_field(scope, p):
    nodes = []
    particle_info = {
        'particle_engine': 'pop',
        'particle_background': 'black',
        'pop_nodes_created': [],
        'particle_count': int(p.get('particle_count', 700) or 700),
        'trail_enabled': False,
        'fallback_used': False,
        'fallback_reason': '',
    }
    image_top, image_info = _load_image_top(scope, p.get('image_asset_path', ''), -260, 170)
    if image_top:
        nodes.append(('source_image', 'moviefileinTOP'))

    black = _make_top(scope, 'constantTOP', 'black_background', -300, -20)
    _set_resolution(black)
    _set_rgb_candidates(black, ('color', 'colorrgba', 'bgcolor'), (0.0, 0.0, 0.0), 1.0)
    nodes.append(('black_background', 'constantTOP'))

    particle_source = None
    try:
        geo = _make_op(scope, 'geoCOMP', 'particle_geo', -80, 190)
        cam = _make_op(scope, 'cameraCOMP', 'particle_cam', -80, -210)
        light = _make_op(scope, 'lightCOMP', 'particle_light', 120, -210)
        mat = _make_op(scope, 'constantMAT', 'particle_material', 120, 190)
        nodes.extend([
            ('particle_geo', 'geoCOMP'),
            ('particle_cam', 'cameraCOMP'),
            ('particle_light', 'lightCOMP'),
            ('particle_material', 'constantMAT'),
        ])

        _set_rgb_candidates(mat, ('color', 'constant', 'emitcolor'), _hex_to_rgb(p.get('color_1'), (1.0, 0.92, 0.66)), 1.0)
        _set_first_par(mat, ('emit', 'emitcolor'), 1.0)
        _set_first_par(geo, ('material', 'mat'), mat.path)

        volume = _make_op(geo, 'spherePOP', 'particle_volume', -420, 0)
        source = _make_op(geo, 'sprinklePOP', 'source_particles', -200, 0)
        drift = _make_op(geo, 'transformPOP', 'particle_motion', 20, 0)
        trail = _make_op(geo, 'trailPOP', 'particle_trails', 240, 0)
        particle_source = trail

        _connect(volume, source)
        _connect(source, drift)
        _connect(drift, trail)
        for pop_node in (volume, source, drift, trail):
            try:
                pop_node.display = True
                pop_node.render = True
            except Exception:
                pass

        _set_first_par(volume, ('radius', 'rad', 'sizex', 'scale'), 1.5 + p['spread'] * 2.0)
        _set_first_par(source, ('numpoints', 'pointcount', 'points', 'count', 'numpts'), particle_info['particle_count'])
        _set_first_par(source, ('seed', 'randomseed'), int(abs(hash(str(p.get('color_1', 'white')))) % 10000))
        _set_first_par(drift, ('rx', 'rotx'), p['speed'] * 6.0)
        _set_first_par(drift, ('ry', 'roty'), p['speed'] * 11.0)
        _set_first_par(drift, ('rz', 'rotz'), p['speed'] * 4.0)
        _set_first_par(drift, ('sx', 'scalex'), 1.0 + p['spread'] * 0.08)
        _set_first_par(drift, ('sy', 'scaley'), 1.0 + p['spread'] * 0.08)
        _set_first_par(drift, ('sz', 'scalez'), 1.0 + p['spread'] * 0.08)
        _set_first_par(trail, ('length', 'trailength', 'trailengthframes', 'frames'), 8 + int(p['blur_amount'] * 34))

        pop_pairs = [
            ('particle_volume', 'spherePOP'),
            ('source_particles', 'sprinklePOP'),
            ('particle_motion', 'transformPOP'),
            ('particle_trails', 'trailPOP'),
        ]
        nodes.extend(pop_pairs)
        particle_info['pop_nodes_created'] = [name for name, _type in pop_pairs]
        particle_info['trail_enabled'] = True

        try:
            cam.par.tz = 5.0 + p['spread'] * 2.2
            cam.par.ty = 0.25
            light.par.tz = 3.0
            light.par.ty = 2.5
        except Exception:
            pass

        pop_render = _make_top(scope, 'renderTOP', 'pop_render', 180, -20)
        _set_resolution(pop_render)
        _set_first_par(pop_render, ('camera', 'cam'), cam.path)
        _set_first_par(pop_render, ('geometry', 'geo', 'geometries'), geo.path)
        _set_first_par(pop_render, ('lights', 'light'), light.path)
        nodes.append(('pop_render', 'renderTOP'))
        particle_base = pop_render
    except Exception as e:
        particle_info['particle_engine'] = 'top_fallback'
        particle_info['fallback_used'] = True
        particle_info['fallback_reason'] = str(e)
        print(f'[AutoTD] POP particle chain failed, using black particle fallback: {e}')

        seed = _make_top(scope, 'noiseTOP', 'particle_point_seed', -80, -20)
        _set_resolution(seed)
        _set_noise_par(seed, 'period', 0.16 + (1.0 - p['speed']) * 0.42)
        _set_noise_par(seed, 'harmonics', 1)
        _set_noise_par(seed, 'amp', 0.08 + p['noise_strength'] * 0.08)
        nodes.append(('particle_point_seed', 'noiseTOP'))

        mask = _make_top(scope, 'thresholdTOP', 'particle_point_mask', 140, -20)
        _connect(seed, mask)
        _set_par_safe(mask, 'threshold', 0.78 - min(0.18, particle_info['particle_count'] / 18000.0))
        nodes.append(('particle_point_mask', 'thresholdTOP'))
        particle_base = mask

    particle_level = _make_top(scope, 'levelTOP', 'particle_level', 400, -20)
    _connect(particle_base, particle_level)
    _set_par_safe(particle_level, 'brightness', 0.92 + p['glow_intensity'] * 0.55)
    _set_par_safe(particle_level, 'contrast', 1.25 + p['glow_intensity'] * 0.9)
    nodes.append(('particle_level', 'levelTOP'))

    feedback = _make_top(scope, 'feedbackTOP', 'particle_feedback', 620, -20)
    _connect(particle_level, feedback)
    _set_par_safe(feedback, 'opacity', min(0.96, max(0.78, p['feedback_opacity'] + 0.08)))
    nodes.append(('particle_feedback', 'feedbackTOP'))

    drift = _make_top(scope, 'transformTOP', 'particle_drift', 840, -20)
    _connect(feedback, drift)
    _set_par_safe(drift, 'tx', (p['speed'] - 0.5) * 0.006)
    _set_par_safe(drift, 'ty', 0.002 + p['speed'] * 0.006)
    _set_par_safe(drift, 'rotate', p['speed'] * 0.28)
    _set_par_safe(drift, 'scale', 1.001 + p['spread'] * 0.004)
    nodes.append(('particle_drift', 'transformTOP'))

    echo = _make_top(scope, 'feedbackTOP', 'echo_feedback', 1060, -20)
    _connect(drift, echo)
    _set_par_safe(echo, 'opacity', min(0.94, max(0.72, p['feedback_opacity'] * 0.88)))
    nodes.append(('echo_feedback', 'feedbackTOP'))

    glow = _make_top(scope, 'blurTOP', 'particle_glow', 1280, -20)
    _connect(echo, glow)
    _set_par_safe(glow, 'sizex', 2 + p['blur_amount'] * 14)
    _set_par_safe(glow, 'sizey', 2 + p['blur_amount'] * 14)
    nodes.append(('particle_glow', 'blurTOP'))

    black_mix = _make_top(scope, 'compositeTOP', 'black_particle_composite', 1500, -20)
    _connect(black, black_mix, 0)
    _connect(glow, black_mix, 1)
    _set_par_safe(black_mix, 'operand', 'screen')
    nodes.append(('black_particle_composite', 'compositeTOP'))

    composite = _make_top(scope, 'compositeTOP', 'particle_composite', 1720, -20)
    _connect(black_mix, composite, 0)
    if image_top:
        image_fog = _make_top(scope, 'levelTOP', 'image_fog_hint', 1500, 170)
        _connect(image_top, image_fog)
        _set_par_safe(image_fog, 'brightness', 0.08)
        _set_par_safe(image_fog, 'opacity', 0.10)
        nodes.append(('image_fog_hint', 'levelTOP'))
        _connect(image_fog, composite, 1)
    _set_par_safe(composite, 'operand', 'screen')
    nodes.append(('particle_composite', 'compositeTOP'))

    out = _make_top(scope, 'nullTOP', 'generated_out', 1940, -20)
    _connect(composite, out)
    nodes.append(('generated_out', 'nullTOP'))
    image_info.update(particle_info)
    try:
        recipe_out = _make_top(scope, 'outTOP', 'recipe_out', 2160, -20)
        _connect(out, recipe_out)
        nodes.append(('recipe_out', 'outTOP'))
        return recipe_out, nodes, image_info
    except Exception:
        return out, nodes, image_info

RECIPE_BUILDERS = {
    'feedback_2d': _build_feedback_2d,
    'particle_field': _build_particle_field,
}


def _execute_recipe(recipe_id, params):
    """Execute the current 2D MVP recipe and return telemetry."""
    global _last_debug
    if recipe_id not in RECIPE_BUILDERS:
        recipe_id = 'feedback_2d'
    recipe_params = _recipe_params_from_payload(params)
    scope = _reset_generated_scope('/project1/autotd_generated')
    print(f'[AutoTD] Scope reset: {scope.path}')
    print(f'[AutoTD] Executing 2D recipe: {recipe_id}')
    print(f'[AutoTD] Recipe params: {json.dumps(recipe_params, indent=2)}')
    try:
        output_node, nodes_created, image_info = RECIPE_BUILDERS[recipe_id](scope, recipe_params)
    except Exception as e:
        print(f'[AutoTD] RECIPE ERROR: {e}')
        _last_debug = {'error': str(e), 'recipe_id': recipe_id, 'recipe_params': recipe_params}
        return {'scope': scope.path if scope else '', 'error': str(e), 'recipe_id': recipe_id}

    out1_path = _route_project_out(output_node)
    _last_debug = {
        'recipe_id': recipe_id,
        'recipe_params': recipe_params,
        'nodes_created': nodes_created,
        'node_count': len(nodes_created),
        'output_node': output_node.path if output_node else '',
        'out1': out1_path,
        'image_asset_path': image_info.get('path', ''),
        'image_usage': recipe_params.get('image_usage', ''),
        'image_loaded_in_td': bool(image_info.get('loaded')),
        'image_width': image_info.get('width', 0),
        'image_height': image_info.get('height', 0),
        'image_error': image_info.get('error', ''),
        'td_model_loaded': False,
        'asset_3d_loaded_in_td': False,
        'point_count': 0,
        'primitive_count': 0,
        'particle_engine': image_info.get('particle_engine', ''),
        'particle_background': image_info.get('particle_background', ''),
        'pop_nodes_created': image_info.get('pop_nodes_created', []),
        'particle_count': image_info.get('particle_count', 0),
        'trail_enabled': bool(image_info.get('trail_enabled')),
        'fallback_used': bool(image_info.get('fallback_used')),
        'fallback_reason': image_info.get('fallback_reason', ''),
    }
    print('\n========= AutoTD 2D Recipe Telemetry =========')
    print(f'Recipe: {recipe_id}')
    print(f'Nodes created: {len(nodes_created)}')
    print(f'Image loaded: {_last_debug["image_loaded_in_td"]} {_last_debug["image_asset_path"]}')
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
        'image_asset_path': image_info.get('path', ''),
        'image_usage': recipe_params.get('image_usage', ''),
        'image_loaded_in_td': bool(image_info.get('loaded')),
        'td_image_loaded': bool(image_info.get('loaded')),
        'image_width': image_info.get('width', 0),
        'image_height': image_info.get('height', 0),
        'image_error': image_info.get('error', ''),
        'td_model_loaded': False,
        'asset_3d_loaded_in_td': False,
        'point_count': 0,
        'primitive_count': 0,
        'particle_engine': image_info.get('particle_engine', ''),
        'particle_background': image_info.get('particle_background', ''),
        'pop_nodes_created': image_info.get('pop_nodes_created', []),
        'particle_count': image_info.get('particle_count', 0),
        'trail_enabled': bool(image_info.get('trail_enabled')),
        'fallback_used': bool(image_info.get('fallback_used')),
        'fallback_reason': image_info.get('fallback_reason', ''),
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
