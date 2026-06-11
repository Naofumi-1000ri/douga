"""Tests for Issue #320: AI DX 小改善3点。

1. capabilities の audio 系 operation_details が non-null
2. イベント説明にアセット名が含まれる（IDの頭8桁ではなく）
3. 同一 group_id の重なりが warning にならず、異 group_id は warning になる

DBなしで実行できるユニットテスト（requires_db マーク不要）。
"""

from src.services.composition_validator import CompositionValidator
from src.services.event_detector import EventDetector

# =============================================================================
# 1. capabilities の audio 系 operation_details — 静的な辞書テスト
# =============================================================================


class TestCapabilitiesAudioOperationDetails:
    """capabilities 辞書の audio 系 operation_details が non-null であることを検証する。

    DBなし: OPERATION_DETAILS モジュール定数を直接インポートして検証する。
    """

    def _get_operation_details(self) -> dict:
        """OPERATION_DETAILS 定数を返す。DB不要。"""
        from src.api.ai_v1 import OPERATION_DETAILS

        return OPERATION_DETAILS

    def test_add_audio_clip_operation_details_not_null(self):
        """add_audio_clip の operation_details が存在し、必要なキーを持つ。"""
        op_details = self._get_operation_details()

        assert "add_audio_clip" in op_details, "OPERATION_DETAILS に add_audio_clip がない"
        detail = op_details["add_audio_clip"]
        assert detail is not None, "add_audio_clip の operation_details が null"

        # 必須フィールドが文書化されている
        assert "description" in detail
        assert "required_fields" in detail
        assert "track_id" in detail["required_fields"]
        assert "asset_id" in detail["required_fields"]
        assert "start_ms" in detail["required_fields"]
        assert "duration_ms" in detail["required_fields"]

        # volume / fade_in_ms / fade_out_ms の範囲説明がある
        optional = detail.get("optional_fields", {})
        assert "volume" in optional
        assert "fade_in_ms" in optional
        assert "fade_out_ms" in optional

        # 重複音声警告が含まれる
        assert "IMPORTANT_duplicate_audio_warning" in detail

    def test_add_audio_track_operation_details_not_null(self):
        """add_audio_track の operation_details が存在し、必要なキーを持つ。"""
        op_details = self._get_operation_details()

        assert "add_audio_track" in op_details, "OPERATION_DETAILS に add_audio_track がない"
        detail = op_details["add_audio_track"]
        assert detail is not None, "add_audio_track の operation_details が null"

        assert "description" in detail
        # track_id を返すことが説明に含まれる
        assert "track" in detail["description"].lower() or "id" in detail["description"].lower()

        assert "required_fields" in detail
        assert "name" in detail["required_fields"]

        optional = detail.get("optional_fields", {})
        assert "type" in optional
        assert "volume" in optional

    def test_add_clip_operation_details_still_present(self):
        """既存の add_clip の operation_details が引き続き存在する（回帰テスト）。"""
        op_details = self._get_operation_details()

        assert "add_clip" in op_details
        assert op_details["add_clip"] is not None
        assert "IMPORTANT_duplicate_audio_warning" in op_details["add_clip"]


# =============================================================================
# 2. イベント説明にアセット名が含まれる
# =============================================================================


class TestEventDetectorAssetName:
    """EventDetector._describe_clip がアセット名を使うことを検証する。"""

    def _make_timeline(self, asset_id: str) -> dict:
        return {
            "duration_ms": 10000,
            "layers": [
                {
                    "type": "content",
                    "name": "Slide",
                    "visible": True,
                    "clips": [
                        {
                            "id": "clip-001",
                            "asset_id": asset_id,
                            "start_ms": 0,
                            "duration_ms": 5000,
                        }
                    ],
                }
            ],
            "audio_tracks": [],
        }

    def test_description_uses_asset_name_when_map_provided(self):
        """asset_name_map があれば description にアセット名が入る。"""
        asset_id = "2d4c307b-0000-0000-0000-000000000000"
        timeline = self._make_timeline(asset_id)
        asset_name_map = {asset_id: "feature_slide_bg"}

        detector = EventDetector(timeline, asset_name_map=asset_name_map)
        events = detector.detect_all(include_audio=False)

        start_events = [e for e in events if "starts" in e.description]
        assert start_events, "start イベントが検出されていない"

        desc = start_events[0].description
        assert "feature_slide_bg" in desc, (
            f"アセット名 'feature_slide_bg' が description に含まれない: {desc!r}"
        )
        # ID の頭8桁は出ないこと
        assert "2d4c307b" not in desc, (
            f"アセットIDの頭8桁 '2d4c307b' が description に残っている: {desc!r}"
        )

    def test_description_falls_back_to_id_prefix_when_name_not_in_map(self):
        """asset_name_map にない場合は ID の頭8桁にフォールバックする。"""
        asset_id = "2d4c307b-0000-0000-0000-000000000000"
        timeline = self._make_timeline(asset_id)
        # 別の asset_id のマップ（このIDは含まれない）
        asset_name_map = {"aaaaaaaa-0000-0000-0000-000000000000": "other_asset"}

        detector = EventDetector(timeline, asset_name_map=asset_name_map)
        events = detector.detect_all(include_audio=False)

        start_events = [e for e in events if "starts" in e.description]
        assert start_events

        desc = start_events[0].description
        assert "2d4c307b" in desc, (
            f"フォールバック: IDの頭8桁 '2d4c307b' が description に含まれない: {desc!r}"
        )

    def test_description_falls_back_to_id_prefix_when_no_map(self):
        """asset_name_map なしの場合は従来どおり ID の頭8桁を使う。"""
        asset_id = "2d4c307b-0000-0000-0000-000000000000"
        timeline = self._make_timeline(asset_id)

        detector = EventDetector(timeline)  # asset_name_map なし
        events = detector.detect_all(include_audio=False)

        start_events = [e for e in events if "starts" in e.description]
        assert start_events

        desc = start_events[0].description
        assert "2d4c307b" in desc

    def test_describe_clip_static_method_with_name_map(self):
        """_describe_clip 静的メソッドを直接検証する。"""
        clip = {"asset_id": "abcd1234-0000-0000-0000-000000000000"}
        name_map = {"abcd1234-0000-0000-0000-000000000000": "my_slide_asset"}

        result = EventDetector._describe_clip(clip, "content", name_map)
        assert result == "my_slide_asset"

    def test_describe_clip_no_asset_id(self):
        """asset_id がないクリップはレイヤー種別だけ返る。"""
        clip = {}
        result = EventDetector._describe_clip(clip, "content", {})
        assert result == "content clip"

    def test_describe_clip_text_content_takes_priority(self):
        """text_content がある場合はアセット名より優先される。"""
        clip = {
            "text_content": "Hello world",
            "asset_id": "abcd1234-0000-0000-0000-000000000000",
        }
        name_map = {"abcd1234-0000-0000-0000-000000000000": "some_asset"}
        result = EventDetector._describe_clip(clip, "text", name_map)
        assert result.startswith("Text: ")
        assert "Hello world" in result


# =============================================================================
# 3. overlapping_clips: 同一 group_id は info、異 group_id は warning
# =============================================================================


class TestOverlappingClipsGroupId:
    """CompositionValidator の _check_overlapping_clips が group_id を考慮することを検証する。"""

    def _make_timeline_with_clips(self, clips: list[dict]) -> dict:
        return {
            "duration_ms": 30000,
            "layers": [
                {
                    "type": "effects",
                    "name": "Effects",
                    "visible": True,
                    "clips": clips,
                }
            ],
            "audio_tracks": [],
        }

    def test_same_group_id_overlap_is_info_not_warning(self):
        """同一 group_id の重なりは severity='info' であること。"""
        clips = [
            {"id": "c1", "start_ms": 0, "duration_ms": 3000, "group_id": "ai-click-highlight"},
            {"id": "c2", "start_ms": 1000, "duration_ms": 3000, "group_id": "ai-click-highlight"},
            {"id": "c3", "start_ms": 2000, "duration_ms": 3000, "group_id": "ai-click-highlight"},
        ]
        timeline = self._make_timeline_with_clips(clips)
        validator = CompositionValidator(timeline)
        issues = validator._check_overlapping_clips()

        overlap_issues = [i for i in issues if i.rule == "overlapping_clips"]
        assert len(overlap_issues) > 0, "重なりが検出されていない"

        # すべて info であること（warning は0件）
        warnings = [i for i in overlap_issues if i.severity == "warning"]
        infos = [i for i in overlap_issues if i.severity == "info"]
        assert len(warnings) == 0, f"同一 group_id の重なりに warning が {len(warnings)} 件出ている"
        assert len(infos) > 0

    def test_different_group_id_overlap_is_warning(self):
        """異なる group_id の重なりは severity='warning' であること。"""
        clips = [
            {"id": "c1", "start_ms": 0, "duration_ms": 3000, "group_id": "group-A"},
            {"id": "c2", "start_ms": 1000, "duration_ms": 3000, "group_id": "group-B"},
        ]
        timeline = self._make_timeline_with_clips(clips)
        validator = CompositionValidator(timeline)
        issues = validator._check_overlapping_clips()

        warnings = [i for i in issues if i.rule == "overlapping_clips" and i.severity == "warning"]
        assert len(warnings) == 1, (
            f"異 group_id の重なりに warning が {len(warnings)} 件（期待: 1）"
        )

    def test_no_group_id_overlap_is_warning(self):
        """group_id なしの重なりは severity='warning' であること。"""
        clips = [
            {"id": "c1", "start_ms": 0, "duration_ms": 3000},  # group_id なし
            {"id": "c2", "start_ms": 1000, "duration_ms": 3000},  # group_id なし
        ]
        timeline = self._make_timeline_with_clips(clips)
        validator = CompositionValidator(timeline)
        issues = validator._check_overlapping_clips()

        warnings = [i for i in issues if i.rule == "overlapping_clips" and i.severity == "warning"]
        assert len(warnings) == 1

    def test_one_clip_no_group_one_with_group_is_warning(self):
        """片方だけ group_id がある重なりは warning になること。"""
        clips = [
            {"id": "c1", "start_ms": 0, "duration_ms": 3000, "group_id": "group-A"},
            {"id": "c2", "start_ms": 1000, "duration_ms": 3000},  # group_id なし
        ]
        timeline = self._make_timeline_with_clips(clips)
        validator = CompositionValidator(timeline)
        issues = validator._check_overlapping_clips()

        warnings = [i for i in issues if i.rule == "overlapping_clips" and i.severity == "warning"]
        assert len(warnings) == 1

    def test_no_overlap_produces_no_issues(self):
        """重ならないクリップには issue が出ないこと。"""
        clips = [
            {"id": "c1", "start_ms": 0, "duration_ms": 1000, "group_id": "group-A"},
            {"id": "c2", "start_ms": 2000, "duration_ms": 1000, "group_id": "group-A"},
        ]
        timeline = self._make_timeline_with_clips(clips)
        validator = CompositionValidator(timeline)
        issues = validator._check_overlapping_clips()

        assert len(issues) == 0

    def test_same_group_info_message_contains_group_name(self):
        """同一 group_id の info メッセージにグループ名が含まれること。"""
        clips = [
            {"id": "c1", "start_ms": 0, "duration_ms": 3000, "group_id": "ai-click-highlight"},
            {"id": "c2", "start_ms": 1000, "duration_ms": 3000, "group_id": "ai-click-highlight"},
        ]
        timeline = self._make_timeline_with_clips(clips)
        validator = CompositionValidator(timeline)
        issues = validator._check_overlapping_clips()

        info = [i for i in issues if i.severity == "info"]
        assert info
        assert "ai-click-highlight" in info[0].message
