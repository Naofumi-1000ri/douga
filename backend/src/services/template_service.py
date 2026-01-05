"""Video template service for Udemy course production.

Template types:
- INTRO: イントロ（コース開始）
- TOC: 目次（セクション一覧）
- TUTORIAL: チュートリアル（操作説明）
- CTA: Call to Action（行動喚起）
- OUTRO: アウトロ（コース終了）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TemplateType(Enum):
    """Types of video templates."""

    INTRO = "intro"
    TOC = "toc"
    TUTORIAL = "tutorial"
    CTA = "cta"
    OUTRO = "outro"


class SlotType(Enum):
    """Types of template slots."""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AVATAR = "avatar"


@dataclass
class TemplateSlot:
    """A customizable slot in a template."""

    id: str
    name: str
    slot_type: SlotType
    required: bool = False
    default_value: Optional[str] = None
    max_length: Optional[int] = None
    min_length: Optional[int] = None
    max_duration_ms: Optional[int] = None


@dataclass
class TemplateConfig:
    """Configuration for template rendering."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration_ms: int = 5000
    background_color: str = "#000000"


@dataclass
class Template:
    """A video template definition."""

    id: str
    name: str
    template_type: TemplateType
    slots: list[TemplateSlot] = field(default_factory=list)
    config: TemplateConfig = field(default_factory=TemplateConfig)
    description: str = ""
    thumbnail_url: Optional[str] = None
    is_preset: bool = True

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "template_type": self.template_type.value,
            "description": self.description,
            "is_preset": self.is_preset,
            "slots": [
                {
                    "id": s.id,
                    "name": s.name,
                    "slot_type": s.slot_type.value,
                    "required": s.required,
                    "default_value": s.default_value,
                }
                for s in self.slots
            ],
            "config": {
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "duration_ms": self.config.duration_ms,
            },
        }


@dataclass
class TemplateInstance:
    """An instance of a template with filled slot values."""

    template_id: str
    slot_values: dict[str, Any] = field(default_factory=dict)


class TemplateService:
    """Service for managing video templates."""

    def __init__(self):
        self._templates: dict[str, Template] = {}
        self._load_preset_templates()

    def _load_preset_templates(self):
        """Load preset templates."""
        presets = [
            # INTRO templates
            Template(
                id="intro_basic",
                name="ベーシックイントロ",
                template_type=TemplateType.INTRO,
                description="シンプルなタイトル表示のイントロ",
                slots=[
                    TemplateSlot(
                        id="title",
                        name="タイトル",
                        slot_type=SlotType.TEXT,
                        required=True,
                        max_length=50,
                    ),
                    TemplateSlot(
                        id="subtitle",
                        name="サブタイトル",
                        slot_type=SlotType.TEXT,
                        required=False,
                        max_length=100,
                    ),
                ],
                config=TemplateConfig(duration_ms=5000),
            ),
            Template(
                id="intro_avatar",
                name="アバターイントロ",
                template_type=TemplateType.INTRO,
                description="アバター付きの挨拶イントロ",
                slots=[
                    TemplateSlot(
                        id="title",
                        name="タイトル",
                        slot_type=SlotType.TEXT,
                        required=True,
                    ),
                    TemplateSlot(
                        id="avatar",
                        name="アバター動画",
                        slot_type=SlotType.AVATAR,
                        required=False,
                    ),
                    TemplateSlot(
                        id="background",
                        name="背景動画",
                        slot_type=SlotType.VIDEO,
                        required=False,
                    ),
                ],
                config=TemplateConfig(duration_ms=8000),
            ),
            # TOC templates
            Template(
                id="toc_basic",
                name="ベーシック目次",
                template_type=TemplateType.TOC,
                description="セクション一覧を表示",
                slots=[
                    TemplateSlot(
                        id="title",
                        name="目次タイトル",
                        slot_type=SlotType.TEXT,
                        required=True,
                        default_value="このセクションの内容",
                    ),
                    TemplateSlot(
                        id="items",
                        name="項目リスト",
                        slot_type=SlotType.TEXT,
                        required=True,
                    ),
                ],
                config=TemplateConfig(duration_ms=10000),
            ),
            # TUTORIAL templates
            Template(
                id="tutorial_screen",
                name="スクリーンチュートリアル",
                template_type=TemplateType.TUTORIAL,
                description="画面操作の説明用テンプレート",
                slots=[
                    TemplateSlot(
                        id="title",
                        name="手順タイトル",
                        slot_type=SlotType.TEXT,
                        required=True,
                    ),
                    TemplateSlot(
                        id="screen",
                        name="操作画面",
                        slot_type=SlotType.VIDEO,
                        required=True,
                    ),
                    TemplateSlot(
                        id="avatar",
                        name="解説アバター",
                        slot_type=SlotType.AVATAR,
                        required=False,
                    ),
                ],
                config=TemplateConfig(duration_ms=30000),
            ),
            # CTA templates
            Template(
                id="cta_subscribe",
                name="チャンネル登録CTA",
                template_type=TemplateType.CTA,
                description="チャンネル登録を促すCTA",
                slots=[
                    TemplateSlot(
                        id="title",
                        name="メッセージ",
                        slot_type=SlotType.TEXT,
                        required=True,
                        default_value="チャンネル登録お願いします！",
                    ),
                    TemplateSlot(
                        id="button_text",
                        name="ボタンテキスト",
                        slot_type=SlotType.TEXT,
                        required=False,
                        default_value="登録する",
                    ),
                ],
                config=TemplateConfig(duration_ms=5000),
            ),
            Template(
                id="cta_action",
                name="アクションCTA",
                template_type=TemplateType.CTA,
                description="カスタムアクションを促すCTA",
                slots=[
                    TemplateSlot(
                        id="title",
                        name="行動喚起メッセージ",
                        slot_type=SlotType.TEXT,
                        required=True,
                    ),
                    TemplateSlot(
                        id="action_text",
                        name="アクションテキスト",
                        slot_type=SlotType.TEXT,
                        required=True,
                    ),
                ],
                config=TemplateConfig(duration_ms=5000),
            ),
            # OUTRO templates
            Template(
                id="outro_basic",
                name="ベーシックアウトロ",
                template_type=TemplateType.OUTRO,
                description="シンプルな終了画面",
                slots=[
                    TemplateSlot(
                        id="title",
                        name="終了メッセージ",
                        slot_type=SlotType.TEXT,
                        required=True,
                        default_value="ご視聴ありがとうございました",
                    ),
                    TemplateSlot(
                        id="next_video",
                        name="次の動画",
                        slot_type=SlotType.TEXT,
                        required=False,
                    ),
                ],
                config=TemplateConfig(duration_ms=8000),
            ),
        ]

        for template in presets:
            self._templates[template.id] = template

    def list_templates(
        self,
        template_type: Optional[TemplateType] = None,
    ) -> list[Template]:
        """List all templates, optionally filtered by type.

        Args:
            template_type: Optional filter by template type

        Returns:
            List of templates
        """
        templates = list(self._templates.values())

        if template_type:
            templates = [t for t in templates if t.template_type == template_type]

        return templates

    def get_template(self, template_id: str) -> Optional[Template]:
        """Get template by ID.

        Args:
            template_id: Template ID

        Returns:
            Template or None if not found
        """
        return self._templates.get(template_id)

    def create_template(self, template: Template) -> Template:
        """Create a new custom template.

        Args:
            template: Template to create

        Returns:
            Created template
        """
        template.is_preset = False
        self._templates[template.id] = template
        return template

    def delete_template(self, template_id: str) -> bool:
        """Delete a custom template.

        Args:
            template_id: Template ID to delete

        Returns:
            True if deleted, False if preset or not found
        """
        template = self._templates.get(template_id)
        if not template:
            return False

        if template.is_preset:
            return False  # Cannot delete preset templates

        del self._templates[template_id]
        return True

    def validate_slot_values(
        self,
        template: Template,
        slot_values: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """Validate slot values against template definition.

        Args:
            template: Template to validate against
            slot_values: Slot values to validate

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors = []

        for slot in template.slots:
            value = slot_values.get(slot.id)

            # Check required
            if slot.required and not value:
                errors.append(f"Required slot '{slot.id}' ({slot.name}) is missing")
                continue

            if value is None:
                continue

            # Check text constraints
            if slot.slot_type == SlotType.TEXT and isinstance(value, str):
                if slot.max_length and len(value) > slot.max_length:
                    errors.append(
                        f"Slot '{slot.id}' exceeds max length of {slot.max_length}"
                    )
                if slot.min_length and len(value) < slot.min_length:
                    errors.append(
                        f"Slot '{slot.id}' is shorter than min length of {slot.min_length}"
                    )

        return (len(errors) == 0, errors)

    def update_template(
        self,
        template_id: str,
        updates: dict[str, Any],
    ) -> Optional[Template]:
        """Update a custom template.

        Args:
            template_id: Template ID to update
            updates: Fields to update

        Returns:
            Updated template or None if not found/preset
        """
        template = self._templates.get(template_id)
        if not template or template.is_preset:
            return None

        # Apply updates
        if "name" in updates:
            template.name = updates["name"]
        if "description" in updates:
            template.description = updates["description"]
        if "config" in updates:
            config_updates = updates["config"]
            if "duration_ms" in config_updates:
                template.config.duration_ms = config_updates["duration_ms"]
            if "background_color" in config_updates:
                template.config.background_color = config_updates["background_color"]

        return template
