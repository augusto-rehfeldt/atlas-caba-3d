# Atlas CABA 3D

A seamless isometric viewer with saved full renders for CABA and all 48 barrios, built from official Buenos Aires building footprints, heights, streets, parks, and 2021 aerial photography.

## Open

Double-click `index.html`. It opens the saved full-CABA render by default; the navbar can switch to any barrio. Everything is bundled locally, so the map also works from `file://` without a server.

Normal page loads use saved WebP renders and lightweight boundary/label shells, so every building is present without browser-side geometry generation. The cached GCBA aerial photography is warped onto the ground plane, preserving real vegetation, paving, road markings, and block texture; vector overlays retain clean geometry. Every building also carries a compact aerial-derived texture value encoding local roof contrast and roughness, used for deterministic roof detail without separate image files. Barrio renders use twice CABA's pixel density, satellite-derived roof colours, material-tinted façades, roof outlines, and resolution-aware windows on tall buildings. Eleven zoom levels reach 2.4×, and the navbar offers real, vivid, warm, monochrome, futuristic, and animated masks.

The compact navbar includes street labels, 360° barrio rotation, day/night modes, and area controls. Rotation lazily activates each barrio's original geometry for correct 3D depth and façade direction. The full-CABA raster stays north-up because true rotation would require loading all 293 MB of barrio geometry. The default geographic orientation keeps north toward the top and east toward the right. Facade materials are stylized from aerial roof classes because vertical surfaces are not visible in an orthophoto.

## High-resolution merged export

```powershell
python export_highres.py
```

This renders six seamless tiles and merges them into `flores-highres.png` at 6144 × 3456. Width, height, and tile size can be changed with command-line options.
Add `--night` for the night palette and illuminated windows.

## Colours from real imagery

The included official route samples the GCBA IDECABA 2021 aerial photography at about 1 m/pixel for Flores; the all-barrios build defaults to about 2 m/pixel:

```powershell
python apply_imagery_colors.py --gcba-aerial
```

To use a different licensed north-up image that covers the Flores bounding box exactly:

```powershell
python apply_imagery_colors.py vista-aerea-flores.png
```

Otherwise add `--bbox west,south,east,north`. Each building colour is sampled at its real geographic centroid. Google Maps imagery is intentionally unsupported. Sentinel-2 can also be used, but its 10 m pixels give neighbourhood colour tendencies rather than reliable roof-level detail.

## Rebuild the data

```powershell
python -m pip install -r requirements.txt
python build_flores.py
python build_flores.py --check
```

The first build downloads the official source files into `.cache/`; later builds reuse them.

Build the lazy-loaded datasets for all 48 barrios, then save the full CABA render used automatically by future page loads:

```powershell
python build_caba.py
python apply_building_textures.py
python cache_aerial_coverage.py
python preprocess_caba.py
python preprocess_areas.py
python validate_data.py
```

Only the selected saved image and its lightweight shell are loaded. CABA completa contains all 1.39 million barrio volumes; each barrio has its own higher-density render.

Data attribution: Gobierno de la Ciudad de Buenos Aires, Buenos Aires Data. Building fabric, streets, parks, and neighborhood boundaries are published under CC BY 2.5 AR / CC BY 4.0 as identified by their source datasets.
