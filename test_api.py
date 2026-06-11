import json
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "generated_assets"
TD_URL = "http://127.0.0.1:9980/generate"
RESULT_PATH = BASE_DIR / "test_api_result.json"


SCENARIOS = [
    {"name": "none", "generate_image": False, "generate_3d": False},
    {"name": "image_only", "generate_image": True, "generate_3d": False},
    {"name": "object_3d_only", "generate_image": False, "generate_3d": True},
    {"name": "image_and_object_3d", "generate_image": True, "generate_3d": True},
]


def _latest_image() -> Path:
    candidates = sorted(
        ASSETS_DIR.glob("AutoTD_*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No generated AutoTD image found in generated_assets")
    return candidates[0]


def _existing_mesh() -> tuple[Path, Path]:
    obj_path = ASSETS_DIR / "sample.obj"
    glb_path = ASSETS_DIR / "sample.glb"
    if not glb_path.exists():
        raise FileNotFoundError(f"Missing GLB asset: {glb_path}")
    if not obj_path.exists():
        from server import _convert_glb_to_obj

        converted, _sidecars = _convert_glb_to_obj(glb_path.as_posix())
        if not converted:
            raise RuntimeError("GLB to OBJ conversion failed")
        obj_path = Path(converted)
    return obj_path, glb_path


def _asset_paths(options: dict) -> dict:
    from server import _mirror_asset_for_td

    paths = {}
    if options["generate_image"]:
        paths["image"] = _mirror_asset_for_td(_latest_image().as_posix())
    if options["generate_3d"]:
        obj_path, glb_path = _existing_mesh()
        paths["obj"] = _mirror_asset_for_td(obj_path.as_posix())
        paths["glb"] = _mirror_asset_for_td(glb_path.as_posix())
    return paths


def _build_payload(options: dict) -> dict:
    from server import TD_ASSET_DIR

    paths = _asset_paths(options)
    td_parameters = {
        "template": "3d" if options["generate_3d"] else "particle",
        "mood": "test",
        "speed": 0.55,
        "density": 0.72,
        "scale": 1.0,
        "turbulence": 0.48,
        "brightness": 0.82,
        "color_mode": "monochrome",
    }
    nodes = []
    connections = []
    if options["generate_image"]:
        nodes.append({"id": "image_asset_loader", "type": "source_image", "name": "image_asset_loader", "params": {}, "asset_path": paths["image"]})
    if options["generate_3d"]:
        nodes.append({"id": "asset_3d_loader", "type": "source_3d", "name": "asset_3d_loader", "params": {}, "asset_path": paths["obj"], "asset_3d_glb_path": paths["glb"]})
    nodes.extend([
        {"id": "noise", "type": "procedural_noise", "name": "field_noise", "params": {"seed": 1234, "period": 0.5, "harmon": 5, "amp": 0.7}},
        {"id": "blur", "type": "spatial_filter", "name": "soft_blur", "params": {"sizex": 2, "sizey": 2}},
        {"id": "grade", "type": "color_grade", "name": "contrast_grade", "params": {"brightness": 0.8, "contrast": 1.25}},
        {"id": "out", "type": "final_output", "name": "generated_out", "params": {}},
    ])
    for left, right in zip(nodes, nodes[1:]):
        connections.append({"from": left["id"], "to": right["id"]})

    return {
        **td_parameters,
        "motion": "flowing",
        "source_options": {
            "generate_image": options["generate_image"],
            "generate_3d": options["generate_3d"],
        },
        "asset_base_dir": TD_ASSET_DIR.as_posix(),
        "asset_image_path": paths.get("image", ""),
        "asset_3d_path": paths.get("obj", ""),
        "asset_3d_glb_path": paths.get("glb", ""),
        "image_asset_requested": options["generate_image"],
        "image_asset_path": paths.get("image", ""),
        "image_usage": "background_composite",
        "asset_3d_requested": options["generate_3d"],
        "asset_3d_obj_path": paths.get("obj", ""),
        "td_parameters": td_parameters,
        "operator_brief": "Verification run for source_options-aware TD loading.",
        "mcp_plan": {
            "version": "autotd-plan-v1",
            "intent": f"Verify source_options scenario: {options['name']}",
            "reset_scope": "/project1/autotd_generated",
            "output": "generated_out",
            "source_options": {
                "generate_image": options["generate_image"],
                "generate_3d": options["generate_3d"],
            },
            "nodes": nodes,
            "connections": connections,
            "notes": "Direct TD source-loading verification payload.",
        },
    }


def _extract_telemetry(data: dict) -> dict:
    applied = data.get("applied", {})
    telemetry = applied.get("telemetry") or applied.get("mcp_plan", {}).get("telemetry") or {}
    if telemetry:
        return telemetry
    return {
        key: applied.get(key)
        for key in (
            "obj_path",
            "glb_path",
            "image_path",
            "td_model_loaded",
            "td_image_loaded",
            "point_count",
            "primitive_count",
            "image_width",
            "image_height",
            "composite_connected",
            "final_output_top",
            "failed_nodes",
            "cook_errors",
            "image_usage",
        )
    }


def _success_criteria(options: dict, telemetry: dict) -> dict:
    final_output_top = telemetry.get("final_output_top") or ""
    criteria = {
        "final_output_top_exists": bool(final_output_top),
        "composite_connected_when_needed": True,
    }
    if options["generate_image"]:
        criteria.update({
            "td_image_loaded": telemetry.get("td_image_loaded") is True,
            "image_width_gt_zero": int(telemetry.get("image_width") or 0) > 0,
            "image_height_gt_zero": int(telemetry.get("image_height") or 0) > 0,
        })
    if options["generate_3d"]:
        obj_path = telemetry.get("obj_path") or ""
        criteria.update({
            "obj_path_exists": bool(obj_path and Path(obj_path).exists()),
            "td_model_loaded": telemetry.get("td_model_loaded") is True,
            "point_count_gt_zero": int(telemetry.get("point_count") or 0) > 0,
            "primitive_count_gt_zero": int(telemetry.get("primitive_count") or 0) > 0,
        })
    if options["generate_image"] and options["generate_3d"]:
        criteria["composite_connected_when_needed"] = telemetry.get("composite_connected") is True
    return criteria


def _run_scenario(options: dict) -> dict:
    payload = _build_payload(options)
    response = requests.post(TD_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    telemetry = _extract_telemetry(data)
    criteria = _success_criteria(options, telemetry)
    return {
        "name": options["name"],
        "source_options": {
            "generate_image": options["generate_image"],
            "generate_3d": options["generate_3d"],
        },
        "success": all(criteria.values()),
        "criteria": criteria,
        "telemetry": telemetry,
        "response": data,
    }


def main() -> int:
    results = [_run_scenario(options) for options in SCENARIOS]
    output = {
        "success": all(item["success"] for item in results),
        "results": results,
    }
    RESULT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
