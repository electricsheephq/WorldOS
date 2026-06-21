#!/usr/bin/env python3
"""bake_sprites.py — Blender-headless bake of a .glb into 8-facing dimetric sprite frames (#1062).

Stage 2 of the WorldOS GT2 final-art pipeline (meshy_gen.py -> THIS -> pack_sheet.py).

Run ONLY under Blender's bundled Python:

    blender --background --python bake_sprites.py -- \
        --model <model.glb> --out <frames_dir> [--cell 128]

What it does (all geometry obeys ISO-PROJECTION.md — the LOCKED dimetric 2:1 projection):

  1. Import the glb, recenter it on the origin, parent it to an Empty (the "turntable").
  2. Build an ORTHOGRAPHIC camera at yaw 45 deg, and EMPIRICALLY CALIBRATE its elevation:
     render a 1x1x1 reference cube and measure the alpha-bbox of its top face; tune the
     camera elevation until width:height of that diamond == 2.0 (+-0.06). This is the
     dimetric-2to1 lock VERIFIED from pixels, not assumed.  (~26.57deg => tan(elev)=0.5.)
  3. Add a simple 3-point-ish rig: a sun (key) + two fill area lights.
  4. For each of the 8 LOCKED facings (S,SE,E,NE,N,NW,W,SW) — rotate the turntable Empty
     by 0,45,90,...,315 deg so S faces the camera — and for each animation frame, apply a
     SYNTHESIZED procedural pose offset to the Empty (the imported model is static, so we
     fake life): idle = vertical breathing bob; walk = bob + slight lean cycle;
     attack = forward lunge along the facing then back; cast = raise + scale "glow".
  5. Render each (facing, anim, frame) to a CELL x CELL transparent PNG named
     "<facing>_<anim>_<i>.png" in <frames_dir>.
  6. Compute the FOOT-anchor pixel by projecting the model's lowest world point to screen
     at the rest pose (S facing, idle frame 0) -> <frames_dir>/anchor.json.

The frame naming + anim layout match gen_placeholder_sheet.py / CharacterToken.gd:
    idle(4) + walk(8) + attack(6) + cast(6) = 24 frames per facing.
"""

from __future__ import annotations

import json
import math
import os
import sys

import bpy
from mathutils import Vector

# ---------------------------------------------------------------------------
# Locked layout (mirrors gen_placeholder_sheet.py / ISO-PROJECTION.md).
# ---------------------------------------------------------------------------
FACING_ORDER = ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]
# (name, frame_count). Order defines column packing downstream.
ANIMS = [("idle", 4), ("walk", 8), ("attack", 6), ("cast", 6)]

# Camera yaw is LOCKED at 45 deg (diagonal view). Elevation is CALIBRATED at runtime
# to hit the 2:1 dimetric diamond; this is the starting guess (atan(0.5) ~= 26.57 deg).
CAMERA_YAW_DEG = 45.0
ELEV_GUESS_DEG = 26.57
DIMETRIC_TARGET_RATIO = 2.0
DIMETRIC_TOL = 0.06

# Camera distance / framing. The ortho camera "scale" is set from the model height so the
# character fills the cell with a little headroom.
CELL_DEFAULT = 128
FRAME_MARGIN = 1.25  # ortho_scale = model_height * this (some padding)


# ---------------------------------------------------------------------------
# Arg parsing (everything after the `--` in the blender command line).
# ---------------------------------------------------------------------------
def _parse_args() -> dict:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    out = {"model": None, "out": None, "cell": CELL_DEFAULT}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--model":
            out["model"] = argv[i + 1]; i += 2
        elif a == "--out":
            out["out"] = argv[i + 1]; i += 2
        elif a == "--cell":
            out["cell"] = int(argv[i + 1]); i += 2
        else:
            i += 1
    if not out["model"] or not out["out"]:
        sys.exit("[bake_sprites] ERROR: --model and --out are required")
    return out


# ---------------------------------------------------------------------------
# Scene setup.
# ---------------------------------------------------------------------------
def _reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if _has_eevee_next() else "BLENDER_EEVEE"
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    # --- COLOR MANAGEMENT (the #1 brightness fix) ---------------------------------
    # Blender 4.x defaults the view transform to AgX (older builds: Filmic), which
    # heavily desaturates + crushes midtones — that is what turned the old fighter bake
    # into a murky grey blob. Force the STANDARD (linear-sRGB) transform so the model's
    # albedo reads bright + saturated, then add a touch of exposure + contrast so the
    # character pops off the transparent background instead of sinking into shadow.
    try:
        vs = scene.view_settings
        vs.view_transform = "Standard"
        vs.look = "None"
        vs.exposure = 0.35       # ~+0.35 stop — lift the whole frame out of the murk
        vs.gamma = 1.0
    except Exception as e:
        print("[bake_sprites] WARN: could not set view transform: %s" % e)

    # --- WORLD AMBIENT (lift the shadow side off near-black) -----------------------
    # A much brighter, slightly cool ambient so the facing AWAY from the key light still
    # reads as lit armor, not a silhouette. EEVEE uses the world as flat ambient fill.
    world = bpy.data.worlds.new("W")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.45, 0.47, 0.52, 1.0)  # bright neutral-cool ambient
        bg.inputs[1].default_value = 1.0

    # EEVEE quality: ambient occlusion for grounded contact + more TAA samples for clean
    # edges. Both are best-effort across the EEVEE / EEVEE-Next property split.
    _boost_eevee_quality(scene)


def _boost_eevee_quality(scene) -> None:
    """Best-effort EEVEE quality knobs. Property names differ across Blender versions:
    Blender 4.2+/5.x EEVEE (a.k.a. "EEVEE Next") replaced the old screen-space GTAO with
    Fast GI (`use_fast_gi`/`fast_gi_*`); pre-4.2 legacy EEVEE used `use_gtao`. We set
    whichever exists so the bake gets soft contact AO + clean AA on any CI Blender."""
    ee = getattr(scene, "eevee", None)
    if ee is None:
        return
    # More TAA samples -> cleaner anti-aliased silhouette edges.
    _try_set(ee, "taa_render_samples", 64)
    # Ambient occlusion: prefer the modern Fast-GI path, fall back to legacy GTAO.
    if hasattr(ee, "use_fast_gi"):
        _try_set(ee, "use_fast_gi", True)
        _try_set(ee, "fast_gi_method", "AMBIENT_OCCLUSION_ONLY")
        _try_set(ee, "fast_gi_distance", 0.5)
    else:
        _try_set(ee, "use_gtao", True)
        _try_set(ee, "gtao_distance", 0.4)


def _try_set(obj, attr: str, val) -> None:
    """Set obj.attr = val, swallowing AttributeError/TypeError for cross-version safety."""
    try:
        setattr(obj, attr, val)
    except Exception:
        pass


def _has_eevee_next() -> bool:
    try:
        # Blender 4.2+ renamed EEVEE to EEVEE Next.
        return "BLENDER_EEVEE_NEXT" in [
            e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
        ]
    except Exception:
        return False


def _import_glb(path: str):
    """Import the glb, return the list of imported mesh-bearing objects."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new_objs = [o for o in bpy.data.objects if o not in before]
    if not new_objs:
        sys.exit("[bake_sprites] ERROR: glb import produced no objects: %s" % path)
    return new_objs


def _world_bbox(objs) -> tuple:
    """World-space (min, max) Vector over all object bounding boxes."""
    mins = Vector((math.inf, math.inf, math.inf))
    maxs = Vector((-math.inf, -math.inf, -math.inf))
    found = False
    for o in objs:
        if o.type not in ("MESH",):
            continue
        for corner in o.bound_box:
            wc = o.matrix_world @ Vector(corner)
            mins.x = min(mins.x, wc.x); mins.y = min(mins.y, wc.y); mins.z = min(mins.z, wc.z)
            maxs.x = max(maxs.x, wc.x); maxs.y = max(maxs.y, wc.y); maxs.z = max(maxs.z, wc.z)
            found = True
    if not found:
        sys.exit("[bake_sprites] ERROR: no mesh geometry to bound")
    return mins, maxs


def _setup_model(objs):
    """Recenter the model on XY origin with feet at z=0, parent it to a turntable Empty."""
    bpy.context.view_layer.update()
    mins, maxs = _world_bbox(objs)
    center_x = (mins.x + maxs.x) * 0.5
    center_y = (mins.y + maxs.y) * 0.5
    # Move so the model is centered on (0,0) in XY and its feet sit at z=0.
    shift = Vector((-center_x, -center_y, -mins.z))

    empty = bpy.data.objects.new("Turntable", None)
    bpy.context.scene.collection.objects.link(empty)
    empty.location = (0.0, 0.0, 0.0)

    for o in objs:
        if o.parent is None:
            o.parent = empty
            o.matrix_parent_inverse = empty.matrix_world.inverted()
            o.location = o.location + shift

    bpy.context.view_layer.update()
    mins, maxs = _world_bbox(objs)
    height = max(maxs.z - mins.z, 1e-4)
    return empty, height, mins, maxs


def _add_lights() -> None:
    """Bright, even 3-point + rim rig.

    The old rig (sun energy 3 + two weak fills) left the bake murky. This one keeps the
    KEY from the camera-front-upper but raises it, adds a STRONG broad FILL on the shadow
    side so no facing goes dark, a soft TOP fill for even coverage across all 8 yaws, and a
    cool RIM/back light to separate the silhouette from the (transparent) background. Sun
    lights are direction-only (distance-independent), so the rig reads the same at every
    facing — important because the turntable spins the model through all 8 directions.
    """
    # KEY (sun) — warm, strong, from front-upper-right (camera side).
    key = bpy.data.lights.new("Key", type="SUN")
    key.energy = 5.0
    key.color = (1.0, 0.97, 0.92)          # subtly warm
    key.angle = math.radians(3.0)          # soft-ish shadow edge
    ko = bpy.data.objects.new("Key", key)
    bpy.context.scene.collection.objects.link(ko)
    ko.rotation_euler = (math.radians(52), 0.0, math.radians(35))

    # FILL (sun) — broad, cool, from the opposite side to open up the shadows on every
    # facing. Direction-only so it lifts whichever side the turntable rotates into shadow.
    fill = bpy.data.lights.new("Fill", type="SUN")
    fill.energy = 2.6
    fill.color = (0.90, 0.94, 1.0)         # cool
    fo = bpy.data.objects.new("Fill", fill)
    bpy.context.scene.collection.objects.link(fo)
    fo.rotation_euler = (math.radians(60), 0.0, math.radians(-130))

    # TOP fill (sun) — gentle straight-down wash for even coverage across all yaws.
    top = bpy.data.lights.new("Top", type="SUN")
    top.energy = 1.6
    top.color = (1.0, 1.0, 1.0)
    to = bpy.data.objects.new("Top", top)
    bpy.context.scene.collection.objects.link(to)
    to.rotation_euler = (math.radians(10), 0.0, math.radians(10))

    # RIM / back light (sun) — bright, slightly cool, from behind-above to pop the edge of
    # the silhouette so the hero reads cleanly over the painterly backdrop in-game.
    rim = bpy.data.lights.new("Rim", type="SUN")
    rim.energy = 3.0
    rim.color = (0.95, 0.97, 1.0)
    ro = bpy.data.objects.new("Rim", rim)
    bpy.context.scene.collection.objects.link(ro)
    ro.rotation_euler = (math.radians(115), 0.0, math.radians(200))


def _aim_at(obj, target: Vector) -> None:
    direction = (target - Vector(obj.location))
    if direction.length < 1e-6:
        return
    rot = direction.to_track_quat("-Z", "Y").to_euler()
    obj.rotation_euler = rot


def _make_camera(model_height: float, elev_deg: float, dist: float):
    cam_data = bpy.data.cameras.new("Cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = model_height * FRAME_MARGIN
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    _place_camera(cam, elev_deg, dist, model_height)
    return cam


def _place_camera(cam, elev_deg: float, dist: float, model_height: float) -> None:
    """Place the ortho camera at yaw 45deg + elev_deg, looking at the model mid-height."""
    yaw = math.radians(CAMERA_YAW_DEG)
    elev = math.radians(elev_deg)
    look = Vector((0.0, 0.0, model_height * 0.5))
    # Spherical position around the look point.
    horiz = math.cos(elev) * dist
    cam.location = (
        look.x + horiz * math.sin(yaw),
        look.y - horiz * math.cos(yaw),
        look.z + math.sin(elev) * dist,
    )
    _aim_at(cam, look)


# ---------------------------------------------------------------------------
# Dimetric calibration: render the FLAT TOP FACE of a unit cube as a diamond and
# measure its alpha-bbox width:height. We render a single 1x1 horizontal PLANE (the
# cube's top face in isolation) so the side faces of a solid cube can't pollute the
# silhouette — the plane's alpha-bbox IS the top-face diamond. Geometrically, a flat
# square seen at yaw 45 + elevation t projects to a diamond of width = diag and
# height = diag*sin(t), so width:height = 1/sin(t); 2:1 => t = 30deg (verified here,
# not assumed). This is the dimetric "floor tile" the renderer's zone geometry shares.
# ---------------------------------------------------------------------------
def _measure_cube_ratio(elev_deg: float, cell: int) -> float:
    """Render a flat 1x1 top-face plane at the given elevation; return diamond w:h."""
    # Build a throwaway scene with just a flat plane (the cube's top face) + camera.
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, 0.0))
    plane = bpy.context.active_object

    cam = bpy.context.scene.camera
    _place_camera(cam, elev_deg, dist=10.0, model_height=0.0)
    cam.data.ortho_scale = 1.7

    scene = bpy.context.scene
    scene.render.resolution_x = cell
    scene.render.resolution_y = cell
    tmp = os.path.join(bpy.app.tempdir, "tile_calib.png")
    scene.render.filepath = tmp
    bpy.ops.render.render(write_still=True)

    ratio = _alpha_bbox_ratio(tmp)
    # cleanup the plane
    bpy.data.objects.remove(plane, do_unlink=True)
    return ratio


def _alpha_bbox_ratio(png_path: str) -> float:
    """Width:height of the opaque-alpha bounding box of a rendered PNG."""
    img = bpy.data.images.load(png_path)
    w, h = img.size
    px = list(img.pixels)  # flat RGBA, row-major bottom-up
    min_x, max_x, min_y, max_y = w, -1, h, -1
    for y in range(h):
        row = y * w * 4
        for x in range(w):
            a = px[row + x * 4 + 3]
            if a > 0.5:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
    bpy.data.images.remove(img)
    if max_x < 0 or max_y < 0:
        return 0.0
    bw = (max_x - min_x + 1)
    bh = (max_y - min_y + 1)
    return bw / max(bh, 1)


def _calibrate_elevation(cell: int) -> tuple:
    """Binary-search the camera elevation so the cube top-face diamond is 2:1 wide:tall.

    A HIGHER elevation flattens the top face (taller diamond, smaller ratio); a LOWER
    elevation makes the top face more head-on (wider, larger ratio). We bisect on elevation.
    Returns (chosen_elev_deg, measured_ratio).
    """
    print("[bake_sprites] calibrating dimetric elevation (target w:h = %.2f +-%.2f)..." % (
        DIMETRIC_TARGET_RATIO, DIMETRIC_TOL))
    lo, hi = 10.0, 60.0  # elevation bounds in degrees
    best = (ELEV_GUESS_DEG, 0.0)
    best_err = math.inf
    for it in range(14):
        mid = (lo + hi) * 0.5
        ratio = _measure_cube_ratio(mid, cell)
        err = abs(ratio - DIMETRIC_TARGET_RATIO)
        print("[bake_sprites]   elev=%.3f deg -> top-face ratio=%.4f (err=%.4f)" % (mid, ratio, err))
        if err < best_err:
            best_err = err
            best = (mid, ratio)
        if err <= DIMETRIC_TOL:
            print("[bake_sprites] CALIBRATED elevation=%.3f deg ratio=%.4f (within tol)" % (mid, ratio))
            return mid, ratio
        # ratio too LARGE (diamond too wide) => raise elevation; too SMALL => lower it.
        if ratio > DIMETRIC_TARGET_RATIO:
            lo = mid
        else:
            hi = mid
    print("[bake_sprites] WARNING: did not converge within tol; using best elev=%.3f ratio=%.4f" % best)
    return best


# ---------------------------------------------------------------------------
# Synthesized procedural motion. Applies a transient transform to the turntable
# Empty (on TOP of its facing yaw). Returns nothing; mutates the empty.
# ---------------------------------------------------------------------------
def _apply_pose(empty, base_yaw: float, anim: str, frame: int, count: int,
                model_height: float, facing_screen_dir) -> None:
    phase = (frame / count) if count > 0 else 0.0
    mid = 1.0 - abs(2.0 * phase - 1.0)  # 0->1->0 triangle

    # Reset to facing pose first.
    empty.location = (0.0, 0.0, 0.0)
    empty.rotation_euler = (0.0, 0.0, base_yaw)
    empty.scale = (1.0, 1.0, 1.0)

    bob = 0.0
    lean = 0.0      # extra rotation (radians) about a horizontal axis
    lunge = 0.0     # forward translation along the facing in WORLD xy
    rise = 0.0
    glow_scale = 1.0

    amp = model_height  # scale motion to the model size

    if anim == "idle":
        bob = 0.015 * amp * math.sin(phase * math.tau)
    elif anim == "walk":
        bob = 0.025 * amp * abs(math.sin(phase * math.tau))
        lean = math.radians(4.0) * math.sin(phase * math.tau)
    elif anim == "attack":
        lunge = 0.18 * amp * mid
        lean = math.radians(8.0) * mid
    elif anim == "cast":
        rise = 0.05 * amp * mid
        glow_scale = 1.0 + 0.04 * mid

    # Forward direction of THIS facing in world XY (the facing's "away from camera->toward"
    # is along the rotated +Y of the empty; lunge pushes the model toward the camera-front).
    fx, fy = facing_screen_dir
    empty.location = (fx * lunge, fy * lunge, bob + rise)
    # lean as a small pitch about the world X axis, composed after the yaw.
    empty.rotation_euler = (lean, 0.0, base_yaw)
    if glow_scale != 1.0:
        empty.scale = (glow_scale, glow_scale, glow_scale)


# Per-facing world XY direction the model "faces" for the lunge. The turntable yaw rotates
# the model; at yaw=0 the model's front points toward the camera (-Y in world, since the
# camera sits at -Y). Index = facing index.
def _facing_world_dir(facing_index: int):
    # The empty yaw for facing i is i*45deg. The model-forward in world after that yaw:
    yaw = math.radians(facing_index * 45.0)
    # base forward (toward camera) is -Y; rotate by yaw about Z.
    fx = -math.sin(yaw) * 1.0  # x' = -sin
    fy = -math.cos(yaw) * 1.0  # y' = -cos
    return (fx, fy)


# ---------------------------------------------------------------------------
# Foot-anchor projection: world point -> render pixel (top-left origin).
# ---------------------------------------------------------------------------
def _project_to_pixel(cam, scene, world_pt: Vector) -> tuple:
    from bpy_extras.object_utils import world_to_camera_view
    co = world_to_camera_view(scene, cam, world_pt)  # normalized 0..1, y up
    rw = scene.render.resolution_x
    rh = scene.render.resolution_y
    px = co.x * rw
    py = (1.0 - co.y) * rh  # flip to top-left origin
    return int(round(px)), int(round(py))


# ---------------------------------------------------------------------------
# Main bake.
# ---------------------------------------------------------------------------
def main() -> None:
    args = _parse_args()
    model_path = os.path.abspath(args["model"])
    out_dir = os.path.abspath(args["out"])
    cell = int(args["cell"])
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.isfile(model_path):
        sys.exit("[bake_sprites] ERROR: model not found: %s" % model_path)

    print("[bake_sprites] model=%s out=%s cell=%d" % (model_path, out_dir, cell))

    # --- calibration pass (fresh scene, just a cube + camera) ---
    _reset_scene()
    _make_camera(model_height=1.0, elev_deg=ELEV_GUESS_DEG, dist=10.0)
    elev_deg, cube_ratio = _calibrate_elevation(cell)

    # --- real scene: import model, light, camera at the calibrated elevation ---
    _reset_scene()
    objs = _import_glb(model_path)
    empty, model_height, mins, maxs = _setup_model(objs)
    _add_lights()
    cam_dist = max(model_height * 4.0, 6.0)
    cam = _make_camera(model_height, elev_deg, cam_dist)

    scene = bpy.context.scene
    scene.render.resolution_x = cell
    scene.render.resolution_y = cell
    scene.render.resolution_percentage = 100

    # The lowest world point of the model (its feet) — projected at the rest pose to get the
    # foot anchor pixel. Use the foot point on the model's vertical axis (x=y=0, z=mins.z).
    foot_world = Vector((0.0, 0.0, max(mins.z, 0.0)))

    anchor_px = None
    rendered = 0
    for fi, facing in enumerate(FACING_ORDER):
        base_yaw = math.radians(fi * 45.0)
        facing_dir = _facing_world_dir(fi)
        for anim, count in ANIMS:
            for frame in range(count):
                _apply_pose(empty, base_yaw, anim, frame, count, model_height, facing_dir)
                bpy.context.view_layer.update()
                fname = "%s_%s_%d.png" % (facing, anim, frame)
                scene.render.filepath = os.path.join(out_dir, fname)
                bpy.ops.render.render(write_still=True)
                rendered += 1
                # Capture the anchor at the canonical rest pose: S facing, idle frame 0.
                if facing == "S" and anim == "idle" and frame == 0:
                    anchor_px = _project_to_pixel(cam, scene, foot_world)

    if anchor_px is None:
        anchor_px = (cell // 2, int(cell * 0.9))

    # Clamp anchor into the cell.
    ax = max(0, min(cell - 1, anchor_px[0]))
    ay = max(0, min(cell - 1, anchor_px[1]))

    anchor_doc = {
        "x": ax,
        "y": ay,
        "cell": cell,
        "calibration": {
            "camera_yaw_deg": CAMERA_YAW_DEG,
            "camera_elevation_deg": round(elev_deg, 4),
            "cube_top_face_ratio": round(cube_ratio, 4),
            "target_ratio": DIMETRIC_TARGET_RATIO,
            "tolerance": DIMETRIC_TOL,
            "projection": "dimetric-2to1",
        },
        "facing_order": FACING_ORDER,
        "anims": {name: count for name, count in ANIMS},
        "frames_rendered": rendered,
    }
    with open(os.path.join(out_dir, "anchor.json"), "w") as f:
        json.dump(anchor_doc, f, indent=2)
        f.write("\n")

    print("[bake_sprites] rendered %d frames -> %s" % (rendered, out_dir))
    print("[bake_sprites] foot anchor px=(%d,%d) cell=%d" % (ax, ay, cell))
    print("[bake_sprites] dimetric: elev=%.3f deg cube_top_ratio=%.4f (target %.2f)" % (
        elev_deg, cube_ratio, DIMETRIC_TARGET_RATIO))
    print("[bake_sprites] OK")


if __name__ == "__main__":
    main()
