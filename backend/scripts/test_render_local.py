"""
ローカルレンダリングテストスクリプト

Cloud Run APIからプロジェクトデータを取得し、ローカルでレンダリングパイプラインを実行する。
出力動画を5秒間隔でフレーム抽出・音声チェックし、レポートを生成する。

使い方:
    cd /Users/hgs/devel/douga_root/main/backend
    source .venv/bin/activate
    python scripts/test_render_local.py

環境変数（.envから自動読み込み）:
    DOUGA_API_URL  - APIベースURL（省略時はデフォルト値を使用）
    DOUGA_API_KEY  - APIキー（省略時はdev-tokenを使用）
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

# プロジェクトルートをsys.pathに追加（src.render.pipeline等をimportするため）
BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# .envを読み込む（pydantic-settingsを直接使わずdotenvで読む）
def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(BACKEND_DIR / ".env")

# サービスアカウントキーではなく gcloud ADC を使う
# .envで設定された古いGOOGLE_APPLICATION_CREDENTIALSを除去
if "GOOGLE_APPLICATION_CREDENTIALS" in os.environ:
    del os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================
# 設定値
# ============================================================

PROJECT_ID = "0f384115-0797-4e12-b790-c7f7f9ebb203"
# 特定シーケンスIDを指定（Noneならdefault sequence）
SEQUENCE_ID: str | None = "fa7b3ae5-c390-435f-9d6e-63ba173cd3c3"  # 動画3_セクション1
API_BASE_URL = os.environ.get(
    "DOUGA_API_URL",
    "https://douga-api-344056413972.asia-northeast1.run.app",
).rstrip("/")

# 認証トークン: APIキー優先 → dev-token フォールバック
_api_key = os.environ.get("DOUGA_API_KEY") or os.environ.get("API_KEY")
AUTH_TOKEN = _api_key if _api_key else "dev-token"

# フレーム抽出間隔（秒）
CHECK_INTERVAL_S = 5

# 出力ディレクトリ（None = 一時ディレクトリ）
OUTPUT_DIR: str | None = None

# ============================================================
# API クライアント
# ============================================================

def _make_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _api_get(path: str, params: dict | None = None) -> Any:
    """同期HTTPリクエストでAPIを叩く。"""
    url = f"{API_BASE_URL}{path}"
    logger.info(f"GET {url}")
    resp = httpx.get(url, headers=_make_headers(), params=params or {}, timeout=60)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# Step 1: プロジェクト・タイムラインデータ取得
# ============================================================

def _get_db_connection():
    """Cloud SQL Python Connector経由でDB接続を取得する。"""
    from google.cloud.sql.connector import Connector

    # Cloud Runの実際の接続情報を使用
    # gcloud run services describe で確認済み
    connector = Connector()
    conn = connector.connect(
        "douga-2f6f8:asia-northeast1:douga-db",
        "pg8000",
        user="postgres",
        password="douga2024secure",
        db="douga",
    )
    return conn


def fetch_timeline_data(project_id: str) -> tuple[dict, dict]:
    """
    Cloud SQL Python Connector経由でプロジェクト情報とタイムラインデータを取得する。

    Returns:
        (project_info, timeline_data)
    """
    logger.info("DB接続中... (Cloud SQL Python Connector)")

    conn = _get_db_connection()
    cur = conn.cursor()

    # プロジェクト情報
    cur.execute(
        "SELECT name, width, height, fps, timeline_data FROM projects WHERE id = %s",
        (project_id,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"プロジェクトが見つかりません: {project_id}")

    project_info = {
        "name": row[0],
        "width": row[1] or 1920,
        "height": row[2] or 1080,
        "fps": row[3] or 30,
    }

    # シーケンスからtimeline_dataを取得
    if SEQUENCE_ID:
        cur.execute(
            "SELECT timeline_data FROM sequences WHERE id = %s LIMIT 1",
            (SEQUENCE_ID,),
        )
    else:
        cur.execute(
            "SELECT timeline_data FROM sequences WHERE project_id = %s AND is_default = true LIMIT 1",
            (project_id,),
        )
    seq_row = cur.fetchone()
    if seq_row and seq_row[0]:
        timeline_data = seq_row[0] if isinstance(seq_row[0], dict) else json.loads(seq_row[0])
    else:
        timeline_data = row[4] if isinstance(row[4], dict) else json.loads(row[4])

    cur.close()
    conn.close()

    duration_ms = timeline_data.get("duration_ms", 0)
    layers = timeline_data.get("layers", [])
    audio_tracks = timeline_data.get("audio_tracks", [])
    logger.info(f"プロジェクト: {project_info['name']} "
                f"({project_info['width']}x{project_info['height']}, {project_info['fps']}fps)")
    logger.info(f"タイムライン: {duration_ms}ms, {len(layers)}レイヤー, {len(audio_tracks)}音声トラック")

    return project_info, timeline_data


# ============================================================
# Step 2: アセットダウンロード
# ============================================================

def collect_asset_ids(timeline_data: dict) -> set[str]:
    """タイムラインデータからasset_idを全収集する。"""
    ids: set[str] = set()
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            aid = clip.get("asset_id")
            if aid:
                ids.add(str(aid))
    for track in timeline_data.get("audio_tracks", []):
        for clip in track.get("clips", []):
            aid = clip.get("asset_id")
            if aid:
                ids.add(str(aid))
    return ids


def download_assets(project_id: str, asset_ids: set[str], assets_dir: str) -> dict[str, str]:
    """
    DBからstorage_keyを取得し、GCSから直接ダウンロードする。

    Returns:
        {asset_id: local_file_path}
    """
    if not asset_ids:
        logger.info("ダウンロードするアセットがありません")
        return {}

    from google.cloud import storage as gcs_storage

    conn = _get_db_connection()
    cur = conn.cursor()

    # アセット情報をDBから一括取得
    placeholders = ",".join(["%s"] * len(asset_ids))
    cur.execute(
        f"SELECT id, name, type, storage_key FROM assets WHERE id IN ({placeholders})",
        list(asset_ids),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    asset_map = {str(row[0]): {"name": row[1], "type": row[2], "storage_key": row[3]} for row in rows}
    logger.info(f"ダウンロード対象: {len(asset_map)}/{len(asset_ids)} アセット")

    os.makedirs(assets_dir, exist_ok=True)
    local_paths: dict[str, str] = {}

    # GCSクライアント
    gcs_client = gcs_storage.Client()
    bucket = gcs_client.bucket("douga-assets")

    for asset_id in asset_ids:
        asset = asset_map.get(asset_id)
        if asset is None:
            logger.warning(f"アセットが見つかりません: {asset_id}")
            continue

        storage_key = asset.get("storage_key", "")
        if not storage_key:
            logger.warning(f"  storage_keyなし: {asset_id}")
            continue

        ext = storage_key.rsplit(".", 1)[-1] if "." in storage_key else "bin"
        local_path = os.path.join(assets_dir, f"{asset_id}.{ext}")

        if os.path.exists(local_path):
            logger.info(f"  スキップ（キャッシュ済み）: {asset.get('name')} -> {local_path}")
            local_paths[asset_id] = local_path
            continue

        logger.info(f"  ダウンロード中: {asset.get('name')} ({asset.get('type')}) from gs://douga-assets/{storage_key}")
        try:
            blob = bucket.blob(storage_key)
            blob.download_to_filename(local_path)
            size_mb = os.path.getsize(local_path) / (1024 * 1024)
            logger.info(f"    -> {local_path} ({size_mb:.1f} MB)")
            local_paths[asset_id] = local_path
        except Exception as e:
            logger.error(f"    ダウンロード失敗: {e}")

    return local_paths


# ============================================================
# Step 3: ローカルレンダリング
# ============================================================

async def render_locally(
    timeline_data: dict,
    assets_local: dict[str, str],
    output_path: str,
    project_info: dict,
) -> str:
    """
    RenderPipelineを使ってローカルでレンダリングを実行する。

    Returns:
        output_path（完了後）
    """
    # パイプライン内部でsrc.configが読まれるためここでimport
    from src.render.pipeline import RenderPipeline

    width = project_info.get("width", 1920)
    height = project_info.get("height", 1080)
    fps = project_info.get("fps", 30)

    job_id = f"local_test_{PROJECT_ID[:8]}"

    pipeline = RenderPipeline(
        job_id=job_id,
        project_id=PROJECT_ID,
        width=width,
        height=height,
        fps=fps,
    )

    def _progress(percent: int, stage: str) -> None:
        print(f"  [{percent:3d}%] {stage}", flush=True)

    pipeline.set_progress_callback(_progress)

    logger.info(f"レンダリング開始: {output_path}")
    result = await pipeline.render(
        timeline_data=timeline_data,
        assets=assets_local,
        output_path=output_path,
    )
    logger.info(f"レンダリング完了: {result}")
    return result


# ============================================================
# Step 4: フレーム抽出
# ============================================================

def extract_frames(video_path: str, frames_dir: str, interval_s: int = 5) -> list[dict]:
    """
    5秒間隔でフレームを抽出し、黒フレーム判定を行う。

    Returns:
        [{"time_s": float, "frame_path": str, "is_black": bool, "mean_brightness": float}]
    """
    os.makedirs(frames_dir, exist_ok=True)

    # 動画の長さを取得（ffprobe）
    probe_cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path,
    ]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
    if probe_result.returncode != 0:
        logger.error(f"ffprobe 失敗: {probe_result.stderr}")
        return []

    probe_data = json.loads(probe_result.stdout)
    duration_s = float(probe_data.get("format", {}).get("duration", 0))
    logger.info(f"動画の長さ: {duration_s:.1f}s")

    # 5秒間隔のタイムスタンプリスト
    timestamps = list(range(0, int(duration_s), interval_s))
    if not timestamps:
        timestamps = [0]

    results = []
    for ts in timestamps:
        frame_path = os.path.join(frames_dir, f"frame_{ts:05d}s.png")

        # ffmpegでフレーム抽出
        extract_cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(ts),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            frame_path,
        ]
        r = subprocess.run(extract_cmd, capture_output=True)
        if r.returncode != 0 or not os.path.exists(frame_path):
            logger.warning(f"フレーム抽出失敗: {ts}s")
            results.append({
                "time_s": ts,
                "frame_path": None,
                "is_black": True,
                "mean_brightness": 0.0,
                "error": "extraction_failed",
            })
            continue

        # 平均輝度をPython PILで計算（signalstatsのパースが不安定なため）
        mean_brightness = 0.0
        try:
            from PIL import Image
            import numpy as np
            img = Image.open(frame_path)
            arr = np.array(img)
            mean_brightness = float(arr.mean())
        except Exception as e:
            logger.warning(f"輝度計算失敗: {e}")

        # 輝度10未満を「黒フレーム」と判定（0-255スケール）
        is_black = mean_brightness < 10.0

        results.append({
            "time_s": ts,
            "frame_path": frame_path,
            "is_black": is_black,
            "mean_brightness": mean_brightness,
        })

    return results


# ============================================================
# Step 5: 音声チェック
# ============================================================

def check_audio_at_timestamps(
    video_path: str,
    timestamps_s: list[int],
    window_s: float = 1.0,
) -> dict[int, dict]:
    """
    各タイムスタンプの前後1秒の音声レベルをチェックする。

    Returns:
        {time_s: {"rms_db": float, "is_silent": bool}}
    """
    results: dict[int, dict] = {}

    for ts in timestamps_s:
        # ffmpegでastatisticsフィルタを使ってRMSを計算
        start = max(0, ts - window_s / 2)
        cmd = [
            "ffmpeg",
            "-ss", str(start),
            "-t", str(window_s),
            "-i", video_path,
            "-vn",
            "-af", "astats=metadata=1:reset=1",
            "-f", "null",
            "-",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)

        rms_db = -100.0  # デフォルト（無音扱い）
        for line in r.stderr.splitlines():
            # RMS level dB: -23.45 のような行を探す
            if "RMS level dB:" in line:
                try:
                    val_str = line.split("RMS level dB:")[-1].strip()
                    # "inf" や "-inf" の場合は無音
                    if "inf" in val_str.lower():
                        rms_db = -100.0
                    else:
                        rms_db = float(val_str)
                except (ValueError, IndexError):
                    pass
                break

        # -60dB未満を「無音」と判定
        is_silent = rms_db < -60.0

        results[ts] = {
            "rms_db": rms_db,
            "is_silent": is_silent,
        }

    return results


# ============================================================
# Step 6: タイムライン期待値との照合
# ============================================================

def get_expected_audio_at(timeline_data: dict, time_ms: int) -> dict:
    """
    指定タイムスタンプに音声クリップがあるか調べる。

    Returns:
        {"has_clip": bool, "clips": list[dict]}
    """
    clips_at: list[dict] = []
    for track in timeline_data.get("audio_tracks", []):
        if track.get("muted", False):
            continue
        for clip in track.get("clips", []):
            start = clip.get("start_ms", 0)
            end = start + clip.get("duration_ms", 0)
            if start <= time_ms < end:
                clips_at.append({
                    "track_type": track.get("type", "unknown"),
                    "start_ms": start,
                    "duration_ms": clip.get("duration_ms", 0),
                    "asset_id": clip.get("asset_id"),
                })
    return {
        "has_clip": len(clips_at) > 0,
        "clips": clips_at,
    }


# ============================================================
# メインフロー
# ============================================================

async def main() -> None:
    print("=" * 60)
    print("=== Douga ローカルレンダリングテスト ===")
    print(f"=== Project ID: {PROJECT_ID}")
    print(f"=== API URL: {API_BASE_URL}")
    print("=" * 60)
    print()

    # 作業ディレクトリ
    if OUTPUT_DIR:
        work_dir = OUTPUT_DIR
        os.makedirs(work_dir, exist_ok=True)
    else:
        work_dir = tempfile.mkdtemp(prefix="douga_local_render_")

    assets_dir = os.path.join(work_dir, "assets")
    frames_dir = os.path.join(work_dir, "frames")
    output_video = os.path.join(work_dir, "output.mp4")
    report_path = os.path.join(work_dir, "report.txt")

    logger.info(f"作業ディレクトリ: {work_dir}")

    # -------------------------------------------------------
    # 1. APIからタイムラインデータ取得
    # -------------------------------------------------------
    print("\n[Step 1] タイムラインデータを取得中...")
    try:
        project_info, timeline_data = fetch_timeline_data(PROJECT_ID)
    except httpx.HTTPStatusError as e:
        print(f"  ERROR: APIアクセス失敗: {e}")
        print(f"  ステータス: {e.response.status_code}")
        print(f"  レスポンス: {e.response.text[:500]}")
        sys.exit(1)
    except Exception as e:
        print(f"  ERROR: {e}")
        raise

    # タイムラインデータをファイルに保存
    timeline_dump_path = os.path.join(work_dir, "timeline_data.json")
    with open(timeline_dump_path, "w", encoding="utf-8") as f:
        json.dump(timeline_data, f, ensure_ascii=False, indent=2)
    print(f"  タイムラインデータ保存: {timeline_dump_path}")

    duration_ms = timeline_data.get("duration_ms", 0)
    duration_s = duration_ms / 1000
    print(f"  動画の長さ: {duration_s:.1f}s ({duration_ms}ms)")

    # -------------------------------------------------------
    # 2. アセットダウンロード
    # -------------------------------------------------------
    print("\n[Step 2] アセットをダウンロード中...")
    asset_ids = collect_asset_ids(timeline_data)
    print(f"  必要アセット数: {len(asset_ids)}")

    assets_local = download_assets(PROJECT_ID, asset_ids, assets_dir)
    print(f"  ダウンロード完了: {len(assets_local)}/{len(asset_ids)} アセット")

    if not assets_local and asset_ids:
        print("  WARNING: アセットのダウンロードに失敗しました。レンダリングを継続しますが映像が空になる可能性があります。")

    # -------------------------------------------------------
    # 3. ローカルレンダリング
    # -------------------------------------------------------
    print("\n[Step 3] ローカルレンダリング中...")
    print(f"  出力先: {output_video}")
    try:
        await render_locally(
            timeline_data=timeline_data,
            assets_local=assets_local,
            output_path=output_video,
            project_info=project_info,
        )
    except Exception as e:
        print(f"  ERROR: レンダリング失敗: {e}")
        logger.exception("レンダリングエラー")
        sys.exit(1)

    if not os.path.exists(output_video):
        print("  ERROR: 出力ファイルが生成されませんでした")
        sys.exit(1)

    output_size_mb = os.path.getsize(output_video) / (1024 * 1024)
    print(f"  完了: {output_video} ({output_size_mb:.1f} MB)")

    # -------------------------------------------------------
    # 4. フレーム抽出
    # -------------------------------------------------------
    print(f"\n[Step 4] フレームを{CHECK_INTERVAL_S}秒間隔で抽出中...")
    frame_results = extract_frames(output_video, frames_dir, interval_s=CHECK_INTERVAL_S)
    print(f"  {len(frame_results)} フレーム抽出完了")

    # -------------------------------------------------------
    # 5. 音声チェック
    # -------------------------------------------------------
    print(f"\n[Step 5] {CHECK_INTERVAL_S}秒間隔で音声チェック中...")
    check_timestamps = [r["time_s"] for r in frame_results]
    audio_results = check_audio_at_timestamps(output_video, check_timestamps)
    print(f"  {len(audio_results)} ポイント音声チェック完了")

    # -------------------------------------------------------
    # 6. レポート生成
    # -------------------------------------------------------
    print("\n[Step 6] レポート生成中...")

    report_lines = [
        "=" * 60,
        "=== Render Test Report ===",
        f"Project ID : {PROJECT_ID}",
        f"Duration   : {duration_s:.1f}s ({duration_ms}ms)",
        f"Output     : {output_video}",
        f"Work dir   : {work_dir}",
        "=" * 60,
        "",
    ]

    # ヘッダー行
    report_lines.append(
        f"{'Time':>6s}  {'Video':^18s}  {'Audio':^30s}  Expected"
    )
    report_lines.append("-" * 80)

    all_ok = True
    for frame_r in frame_results:
        ts = frame_r["time_s"]
        time_ms = ts * 1000

        # ビデオ判定
        if frame_r.get("error") == "extraction_failed":
            video_status = "ERROR (extract failed)"
            video_ok = False
        elif frame_r["is_black"]:
            video_status = f"BLACK (brightness={frame_r['mean_brightness']:.1f})"
            video_ok = False
        else:
            video_status = f"OK (brightness={frame_r['mean_brightness']:.1f})"
            video_ok = True

        # 音声判定
        audio_r = audio_results.get(ts, {"rms_db": -100.0, "is_silent": True})
        if audio_r["is_silent"]:
            audio_status = f"silent  (RMS={audio_r['rms_db']:.1f}dB)"
        else:
            audio_status = f"playing (RMS={audio_r['rms_db']:.1f}dB)"

        # タイムラインとの照合
        expected = get_expected_audio_at(timeline_data, int(time_ms))
        if expected["has_clip"]:
            clip_info = expected["clips"][0]
            expected_str = (
                f"clip@{clip_info['start_ms']}ms "
                f"({clip_info['track_type']})"
            )
            # 音声があるべきなのに無音 → 問題あり
            if audio_r["is_silent"]:
                audio_status = "!SILENT (expected audio)"
                all_ok = False
        else:
            expected_str = "no clip"
            # 音声がないはずなのに音声あり → 要確認（エラーではないがログに残す）
            if not audio_r["is_silent"]:
                expected_str = "?unexpected audio"

        report_lines.append(
            f"[{ts:4.0f}s]  {video_status:<18s}  "
            f"{audio_status:<30s}  {expected_str}"
        )

        if not video_ok:
            all_ok = False

    report_lines.append("")
    report_lines.append("-" * 80)

    # サマリー
    total = len(frame_results)
    black_count = sum(1 for r in frame_results if r.get("is_black") or r.get("error"))
    silent_count = sum(
        1 for ts, ar in audio_results.items()
        if ar["is_silent"] and get_expected_audio_at(timeline_data, ts * 1000)["has_clip"]
    )

    report_lines.append(f"サマリー:")
    report_lines.append(f"  チェックポイント数: {total}")
    report_lines.append(f"  黒フレーム数      : {black_count}")
    report_lines.append(f"  音声欠落箇所数    : {silent_count} (音声あるべきなのに無音)")
    report_lines.append(f"  総合判定          : {'OK' if all_ok else 'NG - 問題あり'}")
    report_lines.append("")

    # クリップ情報サマリー
    report_lines.append("=== 音声トラック サマリー ===")
    for track in timeline_data.get("audio_tracks", []):
        track_type = track.get("type", "unknown")
        clips = track.get("clips", [])
        muted = track.get("muted", False)
        report_lines.append(
            f"  Track [{track_type}]: {len(clips)} clips"
            + (" (MUTED)" if muted else "")
        )
        for clip in clips:
            start = clip.get("start_ms", 0)
            dur = clip.get("duration_ms", 0)
            report_lines.append(
                f"    - start={start}ms ({start/1000:.1f}s), "
                f"dur={dur}ms ({dur/1000:.1f}s), "
                f"asset={clip.get('asset_id', 'N/A')}"
            )
    report_lines.append("")

    # レポート出力
    report_text = "\n".join(report_lines)
    print()
    print(report_text)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\nレポート保存: {report_path}")
    print(f"フレーム保存: {frames_dir}")
    print(f"出力動画   : {output_video}")
    print()

    if not all_ok:
        print("WARNING: 問題が検出されました。レポートを確認してください。")
        sys.exit(1)
    else:
        print("すべてのチェックが通過しました。")


if __name__ == "__main__":
    asyncio.run(main())
