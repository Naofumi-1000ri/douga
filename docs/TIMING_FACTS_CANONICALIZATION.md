# Timing Facts Canonicalization

Issue: `#73`  
Status: initial read-only observability slice

## Current timing fact inventory

| Surface | Current fact source | Notes |
| --- | --- | --- |
| Browser upload registration | `frontend/src/api/assets.ts` local media inspection | Browser-probed `duration_ms` / dimensions are sent to `POST /projects/{id}/assets`. Failures are logged client-side and upload continues without canonical facts. |
| Persisted asset facts | `backend/src/api/assets.py::register_asset` | `Asset.duration_ms`, `sample_rate`, `channels`, `width`, `height` are accepted as-is from the client on first write. |
| Background storage probe | `backend/src/api/assets.py::_probe_media_metadata_background` + `backend/src/utils/media_info.py` | Downloads the stored file and uses `ffprobe` to derive `duration_ms`, `sample_rate`, `channels`, `width`, `height`. This is the closest thing to raw media truth today. |
| Waveform artifact | `backend/src/api/assets.py::_generate_waveform_background` + `backend/src/services/preview_service.py` | Generates `waveforms/{project}/{asset}.json` with `peaks`, `duration_ms`, `sample_rate`. This is a derived artifact, not a raw fact, but today it can overwrite `asset.duration_ms`. |
| Editor waveform view | `frontend/src/utils/audioNormalization.ts` | Prefers `waveform.duration_ms` for visible peak slicing and falls back to `asset.duration_ms`. |
| Editor clip creation | `frontend/src/components/editor/Timeline.tsx` | Missing `asset.duration_ms` often falls back to `5000ms`. This creates a UI-local timing truth. |
| Preview playback | `frontend/src/pages/Editor.tsx` | Uses clip-local `start_ms`, `duration_ms`, `in_point_ms`, `speed` and implicit fallbacks when `out_point_ms` is missing. |
| Export audio mix | `backend/src/render/audio_mixer.py` | Uses clip-local trim math plus forced render resample (`render_audio_sample_rate`, currently 48000 Hz). |
| Export/video render | `backend/src/render/pipeline.py` and `backend/src/render/package_builder.py` | Re-derives effective in/out points from timeline clip fields and asset facts. |

## Current fallback paths

| Path | Current behavior | Risk |
| --- | --- | --- |
| Browser metadata detection failure | `frontend/src/api/assets.ts` logs the error and still registers the asset without `duration_ms`. | Wrong or missing duration can become visible before server probe finishes. |
| Asset registration with incomplete facts | `backend/src/api/assets.py::register_asset` commits the asset first, then schedules background probe. | Preview/editor can observe incomplete facts during the gap. |
| Extracted audio metadata probe failure | `backend/src/api/assets.py::_select_audio_asset_metadata` falls back to `duration_ms=None`, `sample_rate=44100`, `channels=2`. | Derived audio assets can start life with guessed facts. |
| Waveform endpoint fallback | `backend/src/api/assets.py::get_waveform` falls back to on-demand generation when the JSON artifact is missing. | Preview can observe fresh waveform facts that are different from stored asset facts. |
| Waveform sync writes asset duration | `backend/src/api/assets.py::_sync_asset_duration_from_waveform` updates `asset.duration_ms` from waveform output. | A derived artifact can overwrite the raw asset fact owner. |
| Editor missing-duration fallback | `frontend/src/components/editor/Timeline.tsx` frequently uses `asset.duration_ms || 5000`. | UI can build clip timing from a local default instead of canonical facts. |
| Derived asset creation fallback | `backend/src/api/ai_v1.py` and `backend/src/api/assets.py` sometimes use `media_info.duration_ms or asset.duration_ms`. | Derived exports and helper assets may inherit stale duration. |

## Canonical owner decision

The owner should be split by fact type:

1. Raw asset timing facts
   Owner: persisted storage-probe result on `Asset`
   Fields: `duration_ms`, `sample_rate`, `channels`, plus video dimensions

2. Waveform facts
   Owner: waveform artifact JSON
   Constraint: must be validated against raw asset facts and treated as derived, never authoritative

3. Derived clip timing
   Owner: timeline clip contract
   Fields: `start_ms`, `duration_ms`, `in_point_ms`, `out_point_ms`, `speed`
   Constraint: preview, editor, and export must all consume the same normalized interpretation

4. Export/render timing facts
   Owner: render plan generated from canonical clip timing + canonical raw asset facts
   Constraint: render-only resampling must not become a new user-visible timing truth

## Initial observability shipped in this slice

- `GET /projects/{project_id}/asset-timing-audit`
  - inventories current `asset_record`, `waveform_artifact`, and optional `storage_probe` facts
  - reports pairwise drift for `duration_ms`, `sample_rate`, and `channels`
  - flags visible fallback risks such as missing duration or missing waveform artifact
- Background ingest/waveform paths now log:
  - storage-probe drift before asset metadata is updated
  - waveform-vs-asset drift before waveform duration sync
  - extracted-audio default fallbacks
  - waveform on-demand fallback usage

## Follow-up implementation split

1. Stop letting waveform writes own `asset.duration_ms`.
2. Persist explicit provenance for asset facts (`client`, `storage_probe`, `waveform`, `derived_asset`, etc.).
3. Remove editor-side `5000ms` fallback by blocking clip creation until canonical duration exists or by surfacing a pending state.
4. Normalize preview/export clip timing math behind one shared contract.
5. Regenerate waveforms and derived audio assets where the audit shows stored drift.
