#!/usr/bin/env python3
"""extract_glb_albedo.py — pull the baseColor (albedo) texture out of a binary glTF (.glb) file.

#1423: the fighter cast member (Assets/cast/fighter/fighter.fbx) had a NULL albedo_ref in
registry.json — not an intentional "use the model's own material" convention (as a stale comment
claimed), but a real gap: fighter.fbx's own imported Material_1 has no texture bound at all
(Unity's FBX ModelImporter left mainTexture null), and the renderer's registry-miss fallback was
silently substituting the DEFAULT TEMPLATE's hero_albedo.png (a different mesh's UVs) onto it,
producing a garbled "camo" read. The mage/innkeeper/patron_commoner cast members already had a
real Assets/chars_v2/<name>/albedo.jpg extracted this same way from their Meshy model.glb (per
their registry.json gen_recipe: "albedo from model.glb embedded PBR") — this script is that same
extraction, generalized and committed so it does not need re-deriving ad hoc next time.

Why this works with no external deps: a .glb container is documented, simple binary framing (a
12-byte header + a JSON chunk + an optional BIN chunk); an embedded PBR material's baseColorTexture
points at an image, and an image with no external URI stores its raw bytes (PNG/JPEG-encoded) as a
byteRange inside the BIN chunk via a bufferView. No glTF library needed — just struct + json.

Usage:
    python3 extract_glb_albedo.py <model.glb> <out_image_path_without_ext>
    # writes <out_image_path>.jpg or .png depending on the embedded image's mimeType

The mesh generated FROM this same model.glb (e.g. Meshy's rigged.fbx output) preserves the
original UVs, so the extracted image is a correct match for that mesh's texture coordinates —
unlike substituting an unrelated default template's texture.
"""
import json
import struct
import sys


def read_glb(path: str):
    with open(path, "rb") as f:
        data = f.read()
    magic, _version, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF", "not a glb file"
    off = 12
    json_chunk = None
    bin_chunk = None
    while off < length:
        chunk_len, chunk_type = struct.unpack_from("<II", data, off)
        off += 8
        chunk_data = data[off:off + chunk_len]
        off += chunk_len
        if chunk_type == 0x4E4F534A:  # 'JSON'
            json_chunk = chunk_data
        elif chunk_type == 0x004E4942:  # 'BIN\0'
            bin_chunk = chunk_data
    assert json_chunk is not None, "no JSON chunk found in glb"
    gltf = json.loads(json_chunk.decode("utf-8"))
    return gltf, bin_chunk


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: extract_glb_albedo.py <model.glb> <out_image_path_without_ext>", file=sys.stderr)
        sys.exit(2)
    src, out = sys.argv[1], sys.argv[2]
    gltf, binchunk = read_glb(src)
    images = gltf.get("images", [])
    materials = gltf.get("materials", [])
    textures = gltf.get("textures", [])
    bufferviews = gltf.get("bufferViews", [])

    base_img_idx = None
    for m in materials:
        pbr = m.get("pbrMetallicRoughness", {})
        bct = pbr.get("baseColorTexture")
        if bct is not None:
            tex_idx = bct["index"]
            base_img_idx = textures[tex_idx].get("source")
            print(f"material '{m.get('name')}' baseColorTexture -> texture[{tex_idx}] -> image[{base_img_idx}]")
            break
    if base_img_idx is None and images:
        print("WARNING: no baseColorTexture found on any material; falling back to image[0]")
        base_img_idx = 0
    if base_img_idx is None:
        print("NO IMAGES EMBEDDED IN GLB")
        sys.exit(1)

    img = images[base_img_idx]
    print("image entry:", img)
    if "bufferView" not in img:
        print("image has no bufferView (external URI?):", img)
        sys.exit(1)

    bv = bufferviews[img["bufferView"]]
    offset = bv.get("byteOffset", 0)
    length = bv["byteLength"]
    img_bytes = binchunk[offset:offset + length]
    mime = img.get("mimeType", "image/png")
    ext = ".jpg" if "jpeg" in mime else ".png"
    out_path = out if out.endswith(ext) else out + ext
    with open(out_path, "wb") as f:
        f.write(img_bytes)
    print(f"wrote {len(img_bytes)} bytes -> {out_path}")

    print(f"\ntotal images in glb: {len(images)}")
    for i, im in enumerate(images):
        bv2 = bufferviews[im["bufferView"]] if "bufferView" in im else None
        print(f"  [{i}] {im.get('name', '')} mime={im.get('mimeType')} bytes={bv2['byteLength'] if bv2 else '?'}")


if __name__ == "__main__":
    main()
