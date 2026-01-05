"""Tests for video template service.

Template types:
- INTRO: イントロ（コース開始）
- TOC: 目次（セクション一覧）
- TUTORIAL: チュートリアル（操作説明）
- CTA: Call to Action（行動喚起）
- OUTRO: アウトロ（コース終了）
"""

import json
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pytest

from src.services.template_service import (
    TemplateService,
    Template,
    TemplateType,
    TemplateConfig,
    TemplateSlot,
    SlotType,
    TemplateInstance,
)


class TestTemplateType:
    """Tests for TemplateType enum."""

    def test_template_types_exist(self):
        """Test that all 5 template types exist."""
        assert TemplateType.INTRO.value == "intro"
        assert TemplateType.TOC.value == "toc"
        assert TemplateType.TUTORIAL.value == "tutorial"
        assert TemplateType.CTA.value == "cta"
        assert TemplateType.OUTRO.value == "outro"


class TestSlotType:
    """Tests for SlotType enum."""

    def test_slot_types_exist(self):
        """Test that slot types exist."""
        assert SlotType.TEXT.value == "text"
        assert SlotType.IMAGE.value == "image"
        assert SlotType.VIDEO.value == "video"
        assert SlotType.AVATAR.value == "avatar"


class TestTemplateSlot:
    """Tests for TemplateSlot dataclass."""

    def test_slot_creation(self):
        """Test template slot creation."""
        slot = TemplateSlot(
            id="title",
            name="タイトル",
            slot_type=SlotType.TEXT,
            required=True,
            default_value="講座タイトル",
        )
        assert slot.id == "title"
        assert slot.name == "タイトル"
        assert slot.slot_type == SlotType.TEXT
        assert slot.required is True
        assert slot.default_value == "講座タイトル"

    def test_slot_with_constraints(self):
        """Test slot with constraints."""
        slot = TemplateSlot(
            id="subtitle",
            name="サブタイトル",
            slot_type=SlotType.TEXT,
            max_length=50,
            min_length=5,
        )
        assert slot.max_length == 50
        assert slot.min_length == 5

    def test_slot_video_with_duration(self):
        """Test video slot with duration constraints."""
        slot = TemplateSlot(
            id="background_video",
            name="背景動画",
            slot_type=SlotType.VIDEO,
            max_duration_ms=10000,
        )
        assert slot.max_duration_ms == 10000


class TestTemplateConfig:
    """Tests for TemplateConfig dataclass."""

    def test_config_defaults(self):
        """Test default template configuration."""
        config = TemplateConfig()
        assert config.width == 1920
        assert config.height == 1080
        assert config.fps == 30
        assert config.duration_ms == 5000

    def test_config_custom(self):
        """Test custom template configuration."""
        config = TemplateConfig(
            width=1280,
            height=720,
            fps=60,
            duration_ms=10000,
            background_color="#1a1a2e",
        )
        assert config.width == 1280
        assert config.background_color == "#1a1a2e"


class TestTemplate:
    """Tests for Template dataclass."""

    def test_template_creation(self):
        """Test template creation."""
        slots = [
            TemplateSlot(id="title", name="タイトル", slot_type=SlotType.TEXT),
        ]
        template = Template(
            id="intro_basic",
            name="ベーシックイントロ",
            template_type=TemplateType.INTRO,
            description="シンプルなイントロテンプレート",
            slots=slots,
            config=TemplateConfig(),
        )
        assert template.id == "intro_basic"
        assert template.template_type == TemplateType.INTRO
        assert len(template.slots) == 1

    def test_template_with_multiple_slots(self):
        """Test template with multiple slots."""
        slots = [
            TemplateSlot(id="title", name="タイトル", slot_type=SlotType.TEXT),
            TemplateSlot(id="subtitle", name="サブタイトル", slot_type=SlotType.TEXT),
            TemplateSlot(id="avatar", name="アバター", slot_type=SlotType.AVATAR),
            TemplateSlot(id="background", name="背景", slot_type=SlotType.VIDEO),
        ]
        template = Template(
            id="intro_full",
            name="フルイントロ",
            template_type=TemplateType.INTRO,
            slots=slots,
        )
        assert len(template.slots) == 4

    def test_template_to_dict(self):
        """Test template serialization."""
        template = Template(
            id="test",
            name="テスト",
            template_type=TemplateType.INTRO,
            slots=[],
        )
        data = template.to_dict()
        assert data["id"] == "test"
        assert data["template_type"] == "intro"


class TestTemplateInstance:
    """Tests for TemplateInstance dataclass."""

    def test_instance_creation(self):
        """Test template instance creation."""
        instance = TemplateInstance(
            template_id="intro_basic",
            slot_values={
                "title": "Pythonプログラミング入門",
                "subtitle": "初心者向け完全ガイド",
            },
        )
        assert instance.template_id == "intro_basic"
        assert instance.slot_values["title"] == "Pythonプログラミング入門"


class TestTemplateService:
    """Tests for TemplateService class."""

    def test_list_templates(self):
        """Test listing all templates."""
        service = TemplateService()
        templates = service.list_templates()

        assert len(templates) >= 5  # At least 5 preset templates
        # Should have all template types
        types = {t.template_type for t in templates}
        assert TemplateType.INTRO in types
        assert TemplateType.TOC in types
        assert TemplateType.TUTORIAL in types
        assert TemplateType.CTA in types
        assert TemplateType.OUTRO in types

    def test_get_template_by_id(self):
        """Test getting template by ID."""
        service = TemplateService()
        template = service.get_template("intro_basic")

        assert template is not None
        assert template.id == "intro_basic"
        assert template.template_type == TemplateType.INTRO

    def test_get_nonexistent_template(self):
        """Test getting nonexistent template returns None."""
        service = TemplateService()
        template = service.get_template("nonexistent")

        assert template is None

    def test_list_templates_by_type(self):
        """Test filtering templates by type."""
        service = TemplateService()
        intro_templates = service.list_templates(template_type=TemplateType.INTRO)

        assert len(intro_templates) >= 1
        for t in intro_templates:
            assert t.template_type == TemplateType.INTRO

    def test_create_custom_template(self):
        """Test creating custom template."""
        service = TemplateService()
        template = Template(
            id=f"custom_{uuid4().hex[:8]}",
            name="カスタムテンプレート",
            template_type=TemplateType.TUTORIAL,
            slots=[
                TemplateSlot(id="title", name="タイトル", slot_type=SlotType.TEXT),
            ],
            is_preset=False,
        )

        result = service.create_template(template)

        assert result.id == template.id
        # Should be retrievable
        retrieved = service.get_template(template.id)
        assert retrieved is not None

    def test_delete_custom_template(self):
        """Test deleting custom template."""
        service = TemplateService()
        template = Template(
            id=f"delete_test_{uuid4().hex[:8]}",
            name="削除テスト",
            template_type=TemplateType.CTA,
            slots=[],
            is_preset=False,
        )
        service.create_template(template)

        result = service.delete_template(template.id)

        assert result is True
        assert service.get_template(template.id) is None

    def test_cannot_delete_preset_template(self):
        """Test that preset templates cannot be deleted."""
        service = TemplateService()

        result = service.delete_template("intro_basic")

        assert result is False  # Cannot delete preset
        assert service.get_template("intro_basic") is not None

    def test_validate_slot_values(self):
        """Test validation of slot values."""
        service = TemplateService()
        template = service.get_template("intro_basic")

        # Valid values
        valid, errors = service.validate_slot_values(
            template,
            {"title": "有効なタイトル"},
        )
        assert valid is True
        assert len(errors) == 0

    def test_validate_missing_required_slot(self):
        """Test validation fails for missing required slot."""
        service = TemplateService()

        # Create template with required slot
        template = Template(
            id="test_required",
            name="必須テスト",
            template_type=TemplateType.INTRO,
            slots=[
                TemplateSlot(
                    id="title",
                    name="タイトル",
                    slot_type=SlotType.TEXT,
                    required=True,
                ),
            ],
        )

        # Missing required slot
        valid, errors = service.validate_slot_values(template, {})

        assert valid is False
        assert len(errors) > 0
        assert "title" in str(errors)

    def test_validate_text_length(self):
        """Test validation of text length constraints."""
        service = TemplateService()

        template = Template(
            id="test_length",
            name="長さテスト",
            template_type=TemplateType.INTRO,
            slots=[
                TemplateSlot(
                    id="title",
                    name="タイトル",
                    slot_type=SlotType.TEXT,
                    max_length=10,
                    min_length=2,
                ),
            ],
        )

        # Too long
        valid, errors = service.validate_slot_values(
            template,
            {"title": "これは長すぎるタイトルです"},
        )
        assert valid is False

        # Too short
        valid, errors = service.validate_slot_values(
            template,
            {"title": "短"},
        )
        assert valid is False

        # Valid length
        valid, errors = service.validate_slot_values(
            template,
            {"title": "ちょうど良い"},
        )
        assert valid is True


class TestPresetTemplates:
    """Tests for preset templates."""

    def test_intro_template_structure(self):
        """Test intro template has correct structure."""
        service = TemplateService()
        template = service.get_template("intro_basic")

        assert template is not None
        assert template.template_type == TemplateType.INTRO
        assert template.is_preset is True

        # Should have title slot at minimum
        slot_ids = [s.id for s in template.slots]
        assert "title" in slot_ids

    def test_toc_template_structure(self):
        """Test TOC template has correct structure."""
        service = TemplateService()
        templates = service.list_templates(template_type=TemplateType.TOC)

        assert len(templates) >= 1
        toc = templates[0]

        # Should have items slot for list of sections
        slot_ids = [s.id for s in toc.slots]
        assert "title" in slot_ids or "items" in slot_ids

    def test_tutorial_template_structure(self):
        """Test tutorial template has correct structure."""
        service = TemplateService()
        templates = service.list_templates(template_type=TemplateType.TUTORIAL)

        assert len(templates) >= 1
        tutorial = templates[0]
        assert tutorial.template_type == TemplateType.TUTORIAL

    def test_cta_template_structure(self):
        """Test CTA template has correct structure."""
        service = TemplateService()
        templates = service.list_templates(template_type=TemplateType.CTA)

        assert len(templates) >= 1
        cta = templates[0]

        # CTA should have action-oriented slots
        slot_ids = [s.id for s in cta.slots]
        # Should have button or action text
        assert any(
            "button" in s or "action" in s or "title" in s
            for s in slot_ids
        )

    def test_outro_template_structure(self):
        """Test outro template has correct structure."""
        service = TemplateService()
        templates = service.list_templates(template_type=TemplateType.OUTRO)

        assert len(templates) >= 1
        outro = templates[0]
        assert outro.template_type == TemplateType.OUTRO
