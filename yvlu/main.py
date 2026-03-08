# --- Stdlib ---
import asyncio
import importlib.metadata
import math
import re as _re
import unicodedata as _ud
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache, partial
from io import BytesIO
from os import makedirs, remove
from os.path import basename, exists
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

# --- Third-party ---
import httpx
from PIL import Image, ImageDraw, ImageFont
from telethon import functions, types, utils
from telethon.tl.types import (
    MessageEntityCustomEmoji, MessageMediaPhoto,
    InputPeerSelf, InputStickerSetShortName,
)
from telethon.errors.rpcerrorlist import StickersetInvalidError

# --- PagerMaid ---
from pagermaid.listener import listener
from pagermaid.services import sqlite
from pagermaid.utils import alias_command, pip_install

# --- Pillow compatibility ---
try:
    # PIL/Pillow 9.1.0+
    from PIL.Image import Resampling
    BICUBIC = Resampling.BICUBIC
except ImportError:
    # Older versions
    BICUBIC = Image.BICUBIC

# --- Entity styles ---
ENTITY_STYLE_MAPPING = {
    # Direct mappings
    "bold": "bold",
    "italic": "italic",
    "underline": "underline",
    "strikethrough": "strikethrough",
    "spoiler": "spoiler",
    "blockquote": "blockquote",

    # Name normalization (e.g., 'strike' becomes 'strikethrough')
    "strike": "strikethrough",

    # Grouped mappings to 'monospace' style
    "code": "monospace",
    "pre": "monospace",
    "precode": "monospace",

    # Grouped mappings to 'mention' style (for consistent coloring)
    "url": "mention",
    "text_link": "mention",
    "texturl": "mention",
    "mention": "mention",
    "hashtag": "mention",
    "email": "mention",
    "phone_number": "mention",
    "bot_command": "mention",
    "text_mention": "mention",
}

# --- Dependencies ---
def _check_and_install(package: str):
    """Checks if a package is installed and installs it if not."""
    try:
        importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        pip_install(package)


_check_and_install("emoji")
import emoji

# Extra grapheme support
_check_and_install("regex")
import regex

try:
    _check_and_install("fonttools")
    from fontTools.ttLib import TTFont
    _FT_AVAILABLE = True
except ImportError:
    TTFont = None
    _FT_AVAILABLE = False

# --- Paths and cache ---
PLUGIN_PATH = 'data/yvlu/'
CUSTOM_EMOJI_CACHE_PATH = f'{PLUGIN_PATH}emoji_custom/'
CUSTOM_EMOJI_MEMORY_CACHE_SIZE = 256
_custom_emoji_memory_cache: OrderedDict[int, Image.Image] = OrderedDict()
_custom_emoji_inflight: Dict[int, asyncio.Future] = {}
_custom_emoji_inflight_lock: Optional[asyncio.Lock] = None

# --- Commands ---
yvlu_cmd = alias_command('yvlu')


# --- Font config ---
class FontConfig:
    """
    Stores font URLs, names, and weight settings.
    """
    BOLD_WEIGHT = 600
    NORMAL_WEIGHT = 350
    DEFAULT_URL = 'https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/Variable/TTF/SourceHanSansSC-VF.ttf'
    FALLBACK_MATH_URL = 'https://raw.githubusercontent.com/notofonts/notofonts.github.io/refs/heads/main/fonts/NotoSansMath/googlefonts/ttf/NotoSansMath-Regular.ttf'
    FALLBACK_SYMBOLS2_URL = 'https://raw.githubusercontent.com/notofonts/notofonts.github.io/refs/heads/main/fonts/NotoSansSymbols2/googlefonts/ttf/NotoSansSymbols2-Regular.ttf'
    FALLBACK_UNIVERSAL_URL = 'https://raw.githubusercontent.com/googlefonts/roboto-flex/refs/heads/main/fonts/RobotoFlex%5BGRAD%2CXOPQ%2CXTRA%2CYOPQ%2CYTAS%2CYTDE%2CYTFI%2CYTLC%2CYTUC%2Copsz%2Cslnt%2Cwdth%2Cwght%5D.ttf'
    FALLBACK_NOTOSANS_URL = 'https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans-Regular.ttf'
    FALLBACK_EMOJI_URL = 'https://raw.githubusercontent.com/googlefonts/noto-emoji/refs/heads/main/fonts/NotoColorEmoji.ttf'

    @staticmethod
    def _get_name_from_url(url: str, default: str) -> str:
        """
        Extracts and correctly decodes the filename from a URL.
        """
        try:
            path_segment = url.split('/')[-1]
            filename_encoded = path_segment.split('?')[0]
            filename_decoded = unquote(filename_encoded)
            return filename_decoded or default
        except Exception:
            return default

    DEFAULT_NAME = _get_name_from_url(DEFAULT_URL, 'SourceHanSansSC-VF.ttf')
    FALLBACK_MATH_NAME = _get_name_from_url(
        FALLBACK_MATH_URL, 'NotoSansMath-Regular.ttf')
    FALLBACK_SYMBOLS2_NAME = _get_name_from_url(
        FALLBACK_SYMBOLS2_URL, 'NotoSansSymbols2-Regular.ttf')
    FALLBACK_UNIVERSAL_NAME = _get_name_from_url(
        FALLBACK_UNIVERSAL_URL, 'RobotoFlex[GRAD,XOPQ,XTRA,YOPQ,YTAS,YTDE,YTFI,YTLC,YTUC,opsz,slnt,wdth,wght].ttf')
    FALLBACK_NOTOSANS_NAME = _get_name_from_url(
        FALLBACK_NOTOSANS_URL, 'NotoSans-Regular.ttf')
    FALLBACK_EMOJI_NAME = _get_name_from_url(
        FALLBACK_EMOJI_URL, 'NotoColorEmoji.ttf')

# --- Render config ---
class RenderConfig:
    """
    Holds constants for rendering logic to avoid 'magic numbers'.
    """
    # Supersampling factor for anti-aliasing
    SUPERSAMPLE_FACTOR = 2
    # Base scale factor applied to all elements
    BASE_SCALE_FACTOR = 2.0
    
    # Sizing of core elements
    BASE_AVATAR_SIZE = 50
    BASE_INDENT_SIZE = 14
    BASE_BUBBLE_RADIUS = 25
    BASE_MAX_CONTENT_WIDTH = 400
    AVATAR_TO_BUBBLE_GAP = 10
    BASE_REPLY_LINE_WIDTH = 3
    GAP_FACTOR_OF_INDENT = 0.35
    
    # Font sizes
    BASE_NAME_FONT_SIZE = 24
    BASE_TEXT_FONT_SIZE = 24
    BASE_REPLY_NAME_FONT_SIZE = 21
    BASE_REPLY_TEXT_FONT_SIZE = 21
    BASE_FORWARD_FONT_SIZE = 21
    AVATAR_INITIAL_FONT_SIZE = 300
    
    # Text rendering properties
    LINE_HEIGHT_MULTIPLIER = 1.55
    BREAK_WORD_THRESHOLD_MARGIN_FACTOR = 0.05
    LINE_WRAP_MARGIN_FACTOR = 0.4
    
    # Emoji properties
    EMOJI_MIN_PADDING = 2
    EMOJI_PADDING_DIVISOR = 6
    EMOJI_MIN_SPACING = 4
    EMOJI_SPACING_DIVISOR = 5
    STANDALONE_EMOJI_SCALE_FACTOR = 3.0
    
    # Blockquote properties
    QUOTE_LINE_MIN_WIDTH = 2
    QUOTE_LINE_WIDTH_FACTOR = 0.12
    QUOTE_MIN_PADDING = 6
    QUOTE_PADDING_FACTOR = 0.35
    
    # Decoration line (underline/strikethrough) properties
    STRIKE_LINE_HEIGHT_DIVISOR = 12
    UNDERLINE_HEIGHT_DIVISOR = 10
    DECORATION_LINE_MIN_HEIGHT = 1
    
    # Media and layout constraints
    MAX_MEDIA_HEIGHT = 512
    MAX_TEXT_BLOCK_HEIGHT = 1024
    COMBINED_IMAGE_GAP = 10
    STICKER_THUMBNAIL_SIZE = 512
    MEDIA_WIDTH_SCALE_FACTOR = 0.6

# --- Settings keys ---
DB_CUSTOM_FONT_NAME = 'custom.yvlu_font_name'
DB_FONT_SCALE = 'custom.yvlu_font_scale'
DB_STICKER_PACK_NAME = 'custom.yvlu_sticker_pack_name'
DB_AUTO_ADD_STICKER = 'custom.yvlu_auto_add_sticker'


@dataclass(frozen=True)
class ThemeConfigData:
    background_rgb: Tuple[int, int, int]
    text_rgb: Tuple[int, int, int]
    name_rgb: Tuple[int, int, int]
    palette: List[str]
    accent_rgb: Tuple[int, int, int]


@dataclass(frozen=True)
class RenderScaleProfile:
    supersample: int
    scale: float
    avatar_size: int
    indent: int
    rect_round_radius: int
    gap: int
    max_content_width: int
    text_font_size: int
    reply_name_font_size: int
    reply_text_font_size: int
    forward_font_size: int


@dataclass
class ReplyPreview:
    name: Optional[str]
    text: Optional[str]
    entities: Optional[List[Dict[str, Any]]]
    chat_id: Optional[int]


@dataclass(frozen=True)
class MessageIdentity:
    display_name: str
    color_id: Optional[int]


@dataclass
class SenderAssets:
    display_name: str
    avatar_bytes: Optional[BytesIO]
    badge_img: Optional[Image.Image]
    name_img: Optional[Image.Image] = None


@dataclass
class MessageRenderData:
    text: str
    sender_name: str
    avatar_bytes: Optional[BytesIO]
    media_bytes: Optional[BytesIO]
    entities: List[Dict[str, Any]]
    reply_preview: ReplyPreview
    user_id_for_color: int
    custom_emoji_images: Dict[int, Image.Image]
    suppress_sender: bool
    theme: str
    name_img: Optional[Image.Image]
    forward_info_img: Optional[Image.Image]


@dataclass
class RichTextRuntime:
    font_base: ImageFont.FreeTypeFont
    font_size: int
    font_color: Tuple[int, int, int]
    accent_color: Tuple[int, int, int]
    max_width: int
    line_height: int
    primary_ascent: int
    primary_font_path: Optional[str]
    char_font_resolver: Optional[Callable[[str, bool, bool], Optional[ImageFont.FreeTypeFont]]]
    noto_emoji_font_large: Optional[ImageFont.FreeTypeFont]
    custom_emoji_images: Dict[int, Image.Image]


@dataclass
class ReplyRenderBlock:
    name_img: Image.Image
    text_img: Image.Image
    color: Tuple[int, int, int]


@dataclass
class BubbleElement:
    kind: str
    img: Optional[Image.Image] = None
    reply_block: Optional[ReplyRenderBlock] = None


@dataclass(frozen=True)
class BubbleLayoutMetrics:
    should_reserve_avatar_space: bool
    layout_avatar_space: int
    layout_gap: int
    calculated_max_width: int
    max_width: int
    content_height: int
    final_height: int
    final_width: int
    bubble_x: int
    bubble_width: int


@dataclass(frozen=True)
class ParsedCommand:
    raw_arg: str
    theme: str
    subcommand: str
    subcommand_arg: str


@dataclass(frozen=True)
class ReplyCommandOptions:
    count: int
    force_add: bool


@dataclass(frozen=True)
class MessageHandlingContext:
    parsed: ParsedCommand
    reply_message: Any


class Settings:
    """Typed wrapper around sqlite-backed plugin settings."""

    @staticmethod
    def get_custom_font_name() -> str:
        return sqlite.get(DB_CUSTOM_FONT_NAME, '')

    @staticmethod
    def set_custom_font_name(font_name: str) -> None:
        sqlite[DB_CUSTOM_FONT_NAME] = font_name

    @staticmethod
    def reset_custom_font_name() -> None:
        if sqlite.get(DB_CUSTOM_FONT_NAME):
            del sqlite[DB_CUSTOM_FONT_NAME]

    @staticmethod
    def get_sticker_pack_name() -> Optional[str]:
        return sqlite.get(DB_STICKER_PACK_NAME)

    @staticmethod
    def set_sticker_pack_name(pack_name: str) -> None:
        sqlite[DB_STICKER_PACK_NAME] = pack_name

    @staticmethod
    def is_auto_add_sticker_enabled() -> bool:
        return sqlite.get(DB_AUTO_ADD_STICKER) == 'true'

    @staticmethod
    def set_auto_add_sticker(enabled: bool) -> None:
        sqlite[DB_AUTO_ADD_STICKER] = 'true' if enabled else 'false'


class FontService:
    """Single-file service wrapper for font discovery, download, and resolution."""

    @staticmethod
    def get_font_path(base_path: str, font_name: str) -> str:
        return f'{base_path}{font_name}'

    @staticmethod
    def get_default_font_path() -> str:
        return f"{PLUGIN_PATH}{FontConfig.DEFAULT_NAME}"

    @staticmethod
    def get_fallback_math_path() -> str:
        return f"{PLUGIN_PATH}{FontConfig.FALLBACK_MATH_NAME}"

    @staticmethod
    def get_fallback_symbols2_path() -> str:
        return f"{PLUGIN_PATH}{FontConfig.FALLBACK_SYMBOLS2_NAME}"

    @staticmethod
    def get_fallback_universal_path() -> str:
        return f"{PLUGIN_PATH}{FontConfig.FALLBACK_UNIVERSAL_NAME}"

    @staticmethod
    def get_fallback_notosans_path() -> str:
        return f"{PLUGIN_PATH}{FontConfig.FALLBACK_NOTOSANS_NAME}"

    @staticmethod
    def get_fallback_emoji_path() -> str:
        return f"{PLUGIN_PATH}{FontConfig.FALLBACK_EMOJI_NAME}"

    @staticmethod
    async def download_file(url: str, path: str, timeout: int = 20):
        return await _download_file_async(url, path, timeout=timeout)

    @staticmethod
    async def ensure_fallback_fonts() -> None:
        await _ensure_fallback_fonts_async()

    @staticmethod
    def determine_primary_font_path() -> str | None:
        return _determine_primary_font_path()

    @staticmethod
    def make_fallback_resolver(primary_font_path: str | None, font_size: int):
        return _make_fallback_resolver(primary_font_path, font_size)

    @staticmethod
    def get_font_object(font_path: str, size: int, use_bold: bool, use_italic: bool) -> Optional[ImageFont.FreeTypeFont]:
        return get_font_object(font_path, size, use_bold, use_italic)

    @staticmethod
    def font(base_path: str, size: int, bold: bool = False, italic: bool = False):
        custom_name = Settings.get_custom_font_name()
        font_file = FontService.get_font_path(base_path, custom_name) if custom_name and exists(
            FontService.get_font_path(base_path, custom_name)
        ) else FontService.get_font_path(base_path, FontConfig.DEFAULT_NAME)
        font_obj = FontService.get_font_object(font_file, size, bold, italic)
        return font_obj if font_obj else ImageFont.load_default()


class MediaService:
    """Single-file service wrapper for media preview, emoji cache, and sender assets."""

    @staticmethod
    async def fetch_and_cache_custom_emojis(client, message, offset_units: int = 0) -> Dict[int, Image.Image]:
        return await _fetch_and_cache_custom_emojis(client, message, offset_units)

    @staticmethod
    async def load_custom_emoji_images_by_ids(client, document_ids: Set[int] | List[int]) -> Dict[int, Image.Image]:
        return await _load_custom_emoji_images_by_ids(client, document_ids)

    @staticmethod
    async def load_user_badge_image(client, user: Any) -> Optional[Image.Image]:
        return await _load_user_badge_image(client, user)

    @staticmethod
    async def download_static_media_preview(client, media_obj) -> BytesIO | None:
        return await _download_static_media_preview(client, media_obj)

    @staticmethod
    async def fetch_sender_assets(client, entity: Any, theme: str, user_id_for_color: int, include_avatar: bool = True) -> SenderAssets:
        return await _build_sender_visuals(client, entity, theme, user_id_for_color, include_avatar=include_avatar)


def _clear_font_related_caches() -> None:
    """Clears font-related caches after font configuration changes."""
    get_font_object.cache_clear()
    get_font_cmap.cache_clear()
    _get_char_font_resolver.cache_clear()
    _measure_text_size_with_fallback.cache_clear()


@lru_cache(maxsize=32)
def get_font_cmap(font_path: str) -> Set[int]:
    """
    Retrieves the character map (codepoints) for a given font file.
    The result is cached using @lru_cache for performance.
    """
    codepoints: Set[int] = set()

    # Return an empty cached result when the font cannot be inspected.
    if not _FT_AVAILABLE or not exists(font_path):
        return codepoints

    try:
        with TTFont(font_path, lazy=True) as tt:
            for table in tt['cmap'].tables:
                if hasattr(table, 'cmap'):
                    codepoints.update(table.cmap.keys())
    except Exception as e:
        # Keep the empty cached result to avoid repeated work on broken files.
        print(f"ERROR: Failed to read cmap from {basename(font_path)}: {e}")

    return codepoints

    
@lru_cache(maxsize=32)
def is_variable_font(font_path: str) -> bool:
    """
    Checks whether a font exposes a variable-font `fvar` table.
    """
    if not _FT_AVAILABLE or not exists(font_path):
        return False
    try:
        with TTFont(font_path, lazy=True) as font:
            return 'fvar' in font
    except Exception:
        return False


def set_font_variation(font, variations: dict):
    """
    Robust variable-font setter.
    - Handles different axis representations from font.get_variation_axes()
    - Tries numeric-list form first (most Pillow builds), falls back to (tag,value) pairs,
      then falls back to set_variation(dict).
    - Accepts bytes tags and common long names (e.g. 'Slant' -> 'slnt').
    """
    def _norm_tag(t):
        if t is None:
            return None
        if isinstance(t, bytes):
            try:
                t = t.decode('ascii')
            except Exception:
                t = str(t)
        t = str(t)
        return t

    def _parse_axis(axis):
        # return (tag_str_or_none, default_value_or_none)
        if isinstance(axis, dict):
            tag = axis.get("tag") or axis.get("name") or axis.get("axis")
            default = axis.get("default") or axis.get("def") or axis.get("value")
            return _norm_tag(tag), default
        # try tuple-like (tag, min, default, max)
        try:
            tag, _min, default, _max = axis
            return _norm_tag(tag), default
        except Exception:
            # object with attributes?
            tag = getattr(axis, "tag", None) or getattr(axis, "name", None)
            default = getattr(axis, "default", None)
            return _norm_tag(tag), default

    # helper to match variation keys provided by user with axis tag/name
    def _find_variation_for_tag(tag, default):
        if tag is None:
            return default
        # normalized forms to try (prefer canonical 4-letter tags)
        tag_lower = tag.lower()
        # map some common long names to 4-letter tags
        name_map = {
            "slant": "slnt", "slantangle": "slnt", "italic": "slnt",
            "weight": "wght", "width": "wdth", "grade": "grad",
            "opticalsize": "opsz", "opsz": "opsz"
        }
        candidates = [
            tag, tag_lower, tag.upper(),
            (tag_lower[:4] if len(tag_lower) >= 4 else tag_lower),
            (tag_upper := tag.upper()[:4])
        ]
        # inject canonical mapping if applicable
        if tag_lower in name_map:
            candidates.insert(0, name_map[tag_lower])

        # also try keys that user likely used: exact 4-letter keys
        candidates += [c for c in [tag_lower[:4], tag_upper[:4]] if c]

        for k in candidates:
            if k in variations:
                return variations[k]
        # not found -> fallback default
        return default

    # gather axes info in order
    axes = []
    if hasattr(font, "get_variation_axes"):
        try:
            raw_axes = font.get_variation_axes()
        except Exception:
            raw_axes = []
        for ax in raw_axes:
            tag, default = _parse_axis(ax)
            axes.append((tag, default))
    else:
        axes = []

    # build numeric list (in axis order) and tuple list [(tag,value),...]
    numeric_list = []
    tuple_list = []
    for tag, default in axes:
        val = _find_variation_for_tag(tag, default)
        # coerce to number; if impossible, fall back to default or 0
        num = None
        try:
            # allow ints/floats/strings that represent numbers
            if isinstance(val, (int, float)):
                num = val
            else:
                # try convert strings like "-10" or "350.0"
                num = float(str(val))
                # if it's an integer-like float, keep as int for readability
                if num.is_integer():
                    num = int(num)
        except Exception:
            # last-resort: try default, then 0
            try:
                if isinstance(default, (int, float)):
                    num = default
                else:
                    num = float(str(default))
                    if num.is_integer():
                        num = int(num)
            except Exception:
                num = 0
        numeric_list.append(num)
        # only include tag in tuple if tag is not None
        if tag is not None:
            tuple_list.append((tag, num))
        else:
            tuple_list.append((None, num))

    # Try calling set_variation_by_axes with numeric list first (most common)
    if hasattr(font, "set_variation_by_axes"):
        try:
            font.set_variation_by_axes(numeric_list)
            return
        except Exception:
            try:
                # Some builds expect (tag, value) pairs instead
                # filter out None tags if they break the API
                safe_tuple_list = [(t, v) for (t, v) in tuple_list if t is not None]
                font.set_variation_by_axes(safe_tuple_list)
                return
            except Exception:
                # fall through to dict-based fallback
                pass

    # Fallback to dict-based API if available
    if hasattr(font, "set_variation"):
        try:
            font.set_variation(variations)
            return
        except Exception:
            pass


@lru_cache(maxsize=128)
def get_font_object(font_path: str, size: int, use_bold: bool, use_italic: bool) -> Optional[ImageFont.FreeTypeFont]:
    """
    Builds and caches a Pillow font object.
    """
    if not exists(font_path):
        return None

    try:
        font = ImageFont.truetype(font_path, size=size, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        return None

    is_var = is_variable_font(font_path)

    if not is_var:
        return font

    # Apply supported variable-font axes.
    variations = {}
    fallback_universal = FontService.get_fallback_universal_path()

    if font_path == fallback_universal:
        variations['wght'] = FontConfig.BOLD_WEIGHT if use_bold else FontConfig.NORMAL_WEIGHT
        variations['slnt'] = -10 if use_italic else 0
    else:
        # Default fonts only use the weight axis.
        variations['wght'] = FontConfig.BOLD_WEIGHT if use_bold else FontConfig.NORMAL_WEIGHT

    if not variations:
        return font

    try:
        set_font_variation(font, variations)
    except Exception:
        # swallow unexpected errors here; caller receives font (possibly unmodified)
        pass

    return font


def _ensure_dir(path: str):
    """Ensures that a directory exists."""
    if not exists(path):
        makedirs(path, exist_ok=True)


def _get_command_arg(context, cmd_alias_obj, base_cmd_str: str) -> str:
    """
    Extracts the argument from a command message.
    """
    raw_text = (getattr(context, 'raw_text', None) or getattr(
        context.message, 'raw_text', '') or '').strip()
    cmd_alias = (str(cmd_alias_obj) or base_cmd_str).lower()
    # Check for various command prefixes
    for prefix in (f'.{cmd_alias}', f'/{cmd_alias}', f'!{cmd_alias}', f'-{cmd_alias}', f'{cmd_alias}'):
        if raw_text.lower().startswith(prefix):
            return raw_text[len(prefix):].strip()
    # Handle cases with spaces between command and argument
    m = _re.split(r"\s+", raw_text, maxsplit=1)
    if raw_text.lower().startswith(base_cmd_str):
        return raw_text[len(base_cmd_str):].strip()
    if len(m) > 1 and m[0].lower() in (f'.{cmd_alias}', f'/{cmd_alias}', f'!{cmd_alias}', f'-{cmd_alias}', cmd_alias, base_cmd_str):
        return m[1].strip()
    return ''


async def _fetch_and_cache_custom_emojis(client, message, offset_units: int = 0) -> Dict[int, Image.Image]:
    """
    Fetches custom emoji images from a message and caches them locally.
    Returns a dictionary mapping document IDs to Pillow Image objects.
    """
    custom_emoji_images: Dict[int, Image.Image] = {}
    try:
        # Collect unique custom emoji IDs from message entities
        custom_ids_set: Set[int] = set()
        if getattr(message, 'entities', None):
            for ent in message.entities:
                if isinstance(ent, MessageEntityCustomEmoji):
                    # Skip emojis before a certain offset (used for direct messages)
                    if offset_units > 0 and (getattr(ent, 'offset', 0) + getattr(ent, 'length', 0)) <= offset_units:
                        continue
                    custom_ids_set.add(int(ent.document_id))
        if not custom_ids_set:
            return {}
        custom_emoji_images.update(await _load_custom_emoji_images_by_ids(client, custom_ids_set))
    except Exception:
        pass
    return custom_emoji_images


def _get_custom_emoji_inflight_lock() -> asyncio.Lock:
    """Lazily creates the async lock used to deduplicate concurrent emoji downloads."""
    global _custom_emoji_inflight_lock
    if _custom_emoji_inflight_lock is None:
        _custom_emoji_inflight_lock = asyncio.Lock()
    return _custom_emoji_inflight_lock


def _get_custom_emoji_memory_cache(doc_id: int) -> Optional[Image.Image]:
    """Returns a copy of the cached in-memory emoji image to avoid accidental mutation."""
    cached = _custom_emoji_memory_cache.get(doc_id)
    if not cached:
        return None
    _custom_emoji_memory_cache.move_to_end(doc_id)
    return cached.copy()


def _store_custom_emoji_memory_cache(doc_id: int, image: Image.Image) -> None:
    """Stores a copy of the emoji image in the in-memory LRU cache."""
    _custom_emoji_memory_cache[doc_id] = image.copy()
    _custom_emoji_memory_cache.move_to_end(doc_id)
    while len(_custom_emoji_memory_cache) > CUSTOM_EMOJI_MEMORY_CACHE_SIZE:
        _custom_emoji_memory_cache.popitem(last=False)


async def _load_custom_emoji_images_by_ids(client, document_ids: Set[int] | List[int]) -> Dict[int, Image.Image]:
    """Loads Telegram custom emoji images by document ID, using the local cache when possible."""
    custom_emoji_images: Dict[int, Image.Image] = {}
    custom_ids_set = {int(doc_id) for doc_id in document_ids if doc_id}
    if not custom_ids_set:
        return custom_emoji_images

    owned_ids: Set[int] = set()
    try:
        _ensure_dir(CUSTOM_EMOJI_CACHE_PATH)
        needed_ids = []
        for doc_id in custom_ids_set:
            cached = _get_custom_emoji_memory_cache(doc_id)
            if cached:
                custom_emoji_images[doc_id] = cached
                continue
            cache_path = f"{CUSTOM_EMOJI_CACHE_PATH}{doc_id}.png"
            if exists(cache_path):
                try:
                    with Image.open(cache_path) as img:
                        loaded = img.convert('RGBA')
                    custom_emoji_images[doc_id] = loaded.copy()
                    _store_custom_emoji_memory_cache(doc_id, loaded)
                    continue
                except Exception:
                    pass
            needed_ids.append(doc_id)

        if not needed_ids:
            return custom_emoji_images

        lock = _get_custom_emoji_inflight_lock()
        loop = asyncio.get_running_loop()
        wait_futures: Dict[int, asyncio.Future] = {}
        async with lock:
            for doc_id in needed_ids:
                inflight = _custom_emoji_inflight.get(doc_id)
                if inflight:
                    wait_futures[doc_id] = inflight
                else:
                    future = loop.create_future()
                    _custom_emoji_inflight[doc_id] = future
                    wait_futures[doc_id] = future
                    owned_ids.add(doc_id)

        if owned_ids:
            owned_results: Dict[int, Optional[Image.Image]] = {doc_id: None for doc_id in owned_ids}
            try:
                docs = await client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=list(owned_ids)))
            except Exception:
                docs = []

            async def download_and_process(doc):
                doc_id = int(getattr(doc, 'id', 0))
                if not doc_id:
                    return None, None

                thumbs = getattr(doc, 'thumbs', []) or []
                best_thumb = max(thumbs, key=lambda t: getattr(t, 'w', 0) * getattr(t, 'h', 0), default=None)
                if not best_thumb:
                    return doc_id, None

                try:
                    output = BytesIO()
                    await client.download_media(doc, file=output, thumb=best_thumb)
                    output.seek(0)
                    if output.getbuffer().nbytes <= 0:
                        return doc_id, None
                    with Image.open(output) as img:
                        loaded = img.convert('RGBA')
                    cache_path = f"{CUSTOM_EMOJI_CACHE_PATH}{doc_id}.png"
                    loaded.save(cache_path, 'PNG')
                    _store_custom_emoji_memory_cache(doc_id, loaded)
                    return doc_id, loaded.copy()
                except Exception:
                    return doc_id, None

            tasks = [download_and_process(doc) for doc in docs]
            if tasks:
                results = await asyncio.gather(*tasks)
                for doc_id, img_obj in results:
                    if doc_id in owned_results:
                        owned_results[doc_id] = img_obj
            async with lock:
                for doc_id in owned_ids:
                    future = _custom_emoji_inflight.pop(doc_id, None)
                    if future and not future.done():
                        future.set_result(owned_results.get(doc_id))

        if wait_futures:
            resolved_images = await asyncio.gather(*wait_futures.values())
            for doc_id, img_obj in zip(wait_futures.keys(), resolved_images):
                if img_obj:
                    custom_emoji_images[doc_id] = img_obj.copy()
    except Exception:
        pass
    finally:
        if owned_ids:
            lock = _get_custom_emoji_inflight_lock()
            async with lock:
                for doc_id in owned_ids:
                    future = _custom_emoji_inflight.pop(doc_id, None)
                    if future and not future.done():
                        future.set_result(None)

    return custom_emoji_images


def _extract_user_badge_document_id(user: Any) -> Optional[int]:
    """Extracts the emoji-status document ID from a Telegram user-like object when available."""
    if not user or hasattr(user, 'title'):
        return None

    emoji_status = getattr(user, 'emoji_status', None)
    if not emoji_status:
        return None

    for attr_name in ('document_id', 'emoji_id'):
        value = getattr(emoji_status, attr_name, None)
        if value:
            try:
                return int(value)
            except Exception:
                return None
    return None


async def _load_user_badge_image(client, user: Any) -> Optional[Image.Image]:
    """Loads the user's emoji-status image or generates a premium fallback badge."""
    badge_document_id = _extract_user_badge_document_id(user)
    if badge_document_id:
        badge_map = await MediaService.load_custom_emoji_images_by_ids(client, {badge_document_id})
        badge_img = badge_map.get(badge_document_id)
        if badge_img:
            return badge_img

    if getattr(user, 'premium', False):
        scale = RenderConfig.BASE_SCALE_FACTOR * _read_font_scale() * RenderConfig.SUPERSAMPLE_FACTOR
        name_font_size = int(RenderConfig.BASE_NAME_FONT_SIZE * scale)
        return _draw_premium_badge_fallback(int(name_font_size * 0.9))
    return None


def _build_name_image(
    name: str,
    theme: str,
    user_id_for_color: int,
    badge_img: Optional[Image.Image] = None
) -> Optional[Image.Image]:
    """Builds the sender name image and appends premium/emoji-status badge when available."""
    if not name:
        return None

    theme_config = _get_theme_configuration(theme, user_id_for_color)
    name_color_rgb = theme_config.name_rgb
    accent_color = theme_config.accent_rgb

    profile = _build_render_scale_profile()
    max_content_width = profile.max_content_width
    name_font_size = int(RenderConfig.BASE_NAME_FONT_SIZE * profile.scale)
    name_font = FontService.font(PLUGIN_PATH, name_font_size, bold=True)
    name_entities = [{'type': 'bold', 'offset': 0, 'length': len(name.encode('utf-16-le')) // 2}]

    name_img = _render_rich_text(
        text=name,
        entities=name_entities,
        font_base=name_font,
        font_color=name_color_rgb,
        accent_color=accent_color,
        max_width=max_content_width,
        font_size=name_font_size
    )

    if not badge_img:
        return name_img

    badge_target = max(1, int(name_font_size * 0.95))
    badge_img = badge_img.copy()
    badge_img.thumbnail((badge_target, badge_target), BICUBIC)
    gap = max(4, int(name_font_size * 0.18))
    return _compose_inline_images([name_img, badge_img], gap=gap, align='center')


def _read_font_scale() -> float:
    """
    Reads the font scale setting from the database.
    """
    try:
        v = sqlite.get(DB_FONT_SCALE, '1.0').replace('%', '')
        try:
            scale = float(v)
            # Handle percentage values
            scale = scale / 100.0 if scale > 10 else scale
        except Exception:
            scale = 1.0
        # Clamp the value to a reasonable range
        return max(0.6, min(3.0, scale))
    except Exception:
        return 1.0


def _write_font_scale(value: float | str) -> None:
    """
    Writes the font scale setting to the database.
    """
    sqlite[DB_FONT_SCALE] = f"{float(value):.3f}" if isinstance(
        value, (float, int)) else str(value)


def _build_render_scale_profile() -> RenderScaleProfile:
    """Builds all size-related rendering constants from the current font scale."""
    supersample = RenderConfig.SUPERSAMPLE_FACTOR
    scale = RenderConfig.BASE_SCALE_FACTOR * _read_font_scale() * supersample
    indent = int(RenderConfig.BASE_INDENT_SIZE * scale)
    return RenderScaleProfile(
        supersample=supersample,
        scale=scale,
        avatar_size=int(RenderConfig.BASE_AVATAR_SIZE * scale),
        indent=indent,
        rect_round_radius=int(RenderConfig.BASE_BUBBLE_RADIUS * scale),
        gap=int(indent * RenderConfig.GAP_FACTOR_OF_INDENT),
        max_content_width=int(RenderConfig.BASE_MAX_CONTENT_WIDTH * scale),
        text_font_size=int(RenderConfig.BASE_TEXT_FONT_SIZE * scale),
        reply_name_font_size=int(RenderConfig.BASE_REPLY_NAME_FONT_SIZE * scale),
        reply_text_font_size=int(RenderConfig.BASE_REPLY_TEXT_FONT_SIZE * scale),
        forward_font_size=int(RenderConfig.BASE_FORWARD_FONT_SIZE * scale),
    )


def _parse_command(arg_str: str) -> ParsedCommand:
    """Parses theme and subcommand metadata from the raw command text."""
    theme = 'dark'
    cleaned_arg = arg_str.strip()
    parts = cleaned_arg.split()
    if '_day' in parts:
        theme = 'day'
        parts = [part for part in parts if part != '_day']
        cleaned_arg = ' '.join(parts)

    subcommand, _, subcommand_arg = cleaned_arg.partition(' ')
    return ParsedCommand(
        raw_arg=cleaned_arg.strip(),
        theme=theme,
        subcommand=subcommand.strip(),
        subcommand_arg=subcommand_arg.strip(),
    )


def _build_reply_preview(
    name: Optional[str] = None,
    text: Optional[str] = None,
    entities: Optional[List[Dict[str, Any]]] = None,
    chat_id: Optional[int] = None,
) -> ReplyPreview:
    return ReplyPreview(name=name, text=text, entities=entities, chat_id=chat_id)


def normalize_sticker_image(input_bytes: BytesIO) -> BytesIO:
    """
    Normalizes an image to meet Telegram's static sticker requirements.
    - Longest side is 512px.
    - Preserves transparency.
    - Outputs as WEBP.
    """
    input_bytes.seek(0)
    im = Image.open(input_bytes).convert("RGBA")

    max_side = 512
    w, h = im.size

    # Check if the image already meets the size requirements
    if (w == max_side and h <= max_side) or (h == max_side and w <= max_side):
        resized = im
    else:
        # Resize if necessary
        scale = max_side / max(w, h)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        resized = im.resize((new_w, new_h), BICUBIC)

    # Save as WEBP
    out = BytesIO()
    resized.save(out, format="WEBP", lossless=True, quality=100, method=2)
    out.seek(0)
    return out


async def _add_sticker_to_set_func(client, sticker_file: BytesIO, context):
    """
    Adds a sticker to the user-configured sticker pack.
    Creates the pack if it doesn't exist.
    """
    pack_name = Settings.get_sticker_pack_name()
    if not pack_name:
        await client.send_message(
            'me', "自动添加贴纸失败：请先设置贴纸包名称。", silent=True
        )
        return

    # 1. Normalize the image
    try:
        normalized = normalize_sticker_image(sticker_file)
    except Exception as e:
        await client.send_message(
            'me',
            f"自动添加贴纸失败（源对话ID: {context.chat_id}）\n原因：图片理失败: {e}",
            silent=True
        )
        return

    # 2. Upload the file to Telegram
    try:
        media_file = await client.upload_file(normalized, file_name="sticker.webp")
        result = await client(functions.messages.UploadMediaRequest(
            peer=InputPeerSelf(),
            media=types.InputMediaUploadedDocument(
                file=media_file,
                mime_type="image/webp",
                attributes=[],
            )
        ))
        if not (isinstance(result, types.MessageMediaDocument) and getattr(result, "document", None)):
            raise TypeError("上传结果不是有效的 Document")
        uploaded_document = result.document
    except Exception as e:
        await client.send_message(
            'me',
            f"自动添加贴纸失败（源对话ID: {context.chat_id}）\n原因：上传出错: {e}",
            silent=True
        )
        return

    # 3. Convert to InputDocument
    try:
        input_doc = utils.get_input_document(uploaded_document)
    except Exception as e:
        await client.send_message(
            'me',
            f"自动添加贴纸失败（源对话ID: {context.chat_id}）\n原因：文档转换失败: {e}",
            silent=True
        )
        return

    sticker_item = types.InputStickerSetItem(document=input_doc, emoji='💬')
    sticker_pack_link = f"[t.me/addstickers/{pack_name}](t.me/addstickers/{pack_name})"

    # 4. Try to add to an existing pack
    try:
        await client(functions.stickers.AddStickerToSetRequest(
            stickerset=InputStickerSetShortName(short_name=pack_name),
            sticker=sticker_item
        ))
        await client.send_message(
            'me',
            f"已成功添加贴纸到：{sticker_pack_link}",
            link_preview=False, silent=True
        )
        return
    except StickersetInvalidError:
        # Pack doesn't exist, proceed to creation
        pass
    except Exception as e:
        if "STICKERSET_INVALID" not in str(e):
            await client.send_message(
                'me',
                f"自动添加贴纸失败（源对话ID: {context.chat_id}）\n原因：{e}",
                silent=True
            )
            return

    # 5. Create a new pack if it doesn't exist
    try:
        await client(functions.stickers.CreateStickerSetRequest(
            user_id=types.InputUserSelf(),
            title=pack_name,
            short_name=pack_name,
            stickers=[sticker_item]
        ))
        await client.send_message(
            'me',
            f"已创建新的贴纸包并添加：{sticker_pack_link}",
            link_preview=False, silent=True
        )
        return
    except Exception as e:
        # 6. Handle race condition where pack was created by another process
        if "SHORT_NAME_OCCUPIED" in str(e) or "already exists" in str(e):
            try:
                await client(functions.stickers.AddStickerToSetRequest(
                    stickerset=InputStickerSetShortName(short_name=pack_name),
                    sticker=sticker_item
                ))
                await client.send_message(
                    'me',
                    f"已成功添加贴纸到：{sticker_pack_link}",
                    link_preview=False, silent=True
                )
                return
            except Exception as e2:
                await client.send_message(
                    'me',
                    f"自动添加贴纸失败（源对话ID: {context.chat_id}）\n原因：尝试加入已存在的包失败: {e2}",
                    silent=True
                )
        else:
            await client.send_message(
                'me',
                f"自动添加贴纸失败（源对话ID: {context.chat_id}）\n原因：创建包失败: {e}",
                silent=True
            )


async def yvlu_font_func(context, arg):
    """Handler for the '_font' subcommand."""
    if not arg:
        await context.edit(f'用法：`-{yvlu_cmd} _font <字体文件名>`\n例如：`-{yvlu_cmd} _font myfont.ttf`')
        return
    if arg.lower() in ('reset', 'default'):
        Settings.reset_custom_font_name()
        _clear_font_related_caches()
        await context.edit('已重置为默认字体。')
        return
    _ensure_dir(PLUGIN_PATH)
    font_path = f"{PLUGIN_PATH}{arg}"
    if not exists(font_path):
        await context.edit(f'错误：找不到字体文件 `{arg}`。\n请确保该文件已放置在 `data/yvlu/` 目录下。')
        return
    Settings.set_custom_font_name(arg)
    _clear_font_related_caches()
    await context.edit(f'已设置自定义字体为 `{arg}`。')


async def yvlu_size_func(context, arg):
    """Handler for the '_size' subcommand."""
    if not arg:
        await context.edit(f'用法：`-{yvlu_cmd} _size <倍率|百分比|reset>`，如 `-{yvlu_cmd} _size 1.2`')
        return
    if arg.lower() in ('reset', 'default'):
        _write_font_scale('1.0')
        await context.edit('已重置字体与布局倍率为 1.0。')
        return
    try:
        scale = float(arg.replace('%', ''))
        scale = scale / 100.0 if scale > 10 else scale
    except Exception:
        await context.edit('解析倍率失败，请输入如 1.2 或 120%')
        return
    scale = max(0.6, min(3.0, scale))
    _write_font_scale(scale)
    await context.edit(f'已设置字体与布局倍率为 {scale:.2f}。')


async def yvlu_pack_func(context, arg):
    """Handler for the '_pack' subcommand."""
    if not arg:
        current_pack = Settings.get_sticker_pack_name()
        if current_pack:
            await context.edit(f'当前设置的贴纸包为: `{current_pack}`\n你可以通 [t.me/addstickers/{current_pack}](t.me/addstickers/{current_pack}) 查看。', link_preview=False)
            return
        await context.edit(f'尚未设置贴纸包。使用 `-{yvlu_cmd} _pack <名称>` 来设置一个。')
        return
    # Validate sticker pack name
    if not _re.match("^[a-zA-Z][a-zA-Z0-9_]*$", arg):
        await context.edit('错误：贴纸包名称必须以字母开头，且只包含字母、数字和下划线。')
        return
    if len(arg) > 64:
        await context.edit('错误：贴纸包名称不能超过64个字。')
        return
    Settings.set_sticker_pack_name(arg)
    await context.edit(f'贴纸包名称已设置为: `{arg}`。\n如该包不存在，将会在下次添加自动创建。')


async def yvlu_packadd_func(context):
    """Handler for the '_packadd' toggle subcommand."""
    is_enabled = Settings.is_auto_add_sticker_enabled()
    if is_enabled:
        Settings.set_auto_add_sticker(False)
        await context.edit('已禁用自动添加到贴纸包。')
    else:
        Settings.set_auto_add_sticker(True)
        await context.edit('已启用自动添加到贴纸包。')

async def _download_file_async(url: str, path: str, timeout: int = 20):
    """Asynchronously downloads a file."""
    if not exists(path):
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
                    resp.raise_for_status()
                    with open(path, 'wb') as f:
                        async for chunk in resp.aiter_bytes():
                            await asyncio.to_thread(f.write, chunk)
            return True
        except Exception:
            if exists(path):
                remove(path)
            return False
    return True

async def _ensure_fallback_fonts_async() -> None:
    """Asynchronously and concurrently downloads fallback fonts."""
    _ensure_dir(PLUGIN_PATH)
    pairs = [
        (FontService.get_default_font_path(), FontConfig.DEFAULT_URL),
        (FontService.get_fallback_universal_path(), FontConfig.FALLBACK_UNIVERSAL_URL),
        (FontService.get_fallback_math_path(), FontConfig.FALLBACK_MATH_URL),
        (FontService.get_fallback_symbols2_path(), FontConfig.FALLBACK_SYMBOLS2_URL),
        (FontService.get_fallback_notosans_path(), FontConfig.FALLBACK_NOTOSANS_URL),
        (FontService.get_fallback_emoji_path(), FontConfig.FALLBACK_EMOJI_URL)
    ]
    tasks = [FontService.download_file(url, local_path) for local_path, url in pairs]
    await asyncio.gather(*tasks)


def _determine_primary_font_path() -> str | None:
    """Determines the path of the primary font to be used."""
    try:
        custom_name = Settings.get_custom_font_name()
        path = f'{PLUGIN_PATH}{custom_name}' if custom_name and exists(
            f'{PLUGIN_PATH}{custom_name}') else f'{PLUGIN_PATH}{FontConfig.DEFAULT_NAME}'
        return path if exists(path) else None
    except Exception:
        return None


def _make_fallback_resolver(primary_font_path: str | None, font_size: int):
    """
    Creates a function that resolves the correct font for a given non-emoji character.
    """
    # Use only Math and Symbols as specific fallbacks.
    fallback_paths = [
        FontService.get_fallback_math_path(),
        FontService.get_fallback_symbols2_path(),
        FontService.get_fallback_notosans_path(),
    ]

    primary_cmap = get_font_cmap(primary_font_path) if primary_font_path else set()
    fallback_cmaps = {path: get_font_cmap(path) for path in fallback_paths}

    @lru_cache(maxsize=2048)
    def resolver(ch: str, bold: bool = False, italic: bool = False) -> Optional[ImageFont.FreeTypeFont]:
        if len(ch) != 1:
            return None
        cp = ord(ch)

        # 1. Check primary font (Source Han Sans) first.
        if primary_font_path and cp in primary_cmap:
            # Pass all style info through.
            return get_font_object(primary_font_path, font_size, bold, italic)

        # 2. Check dedicated fallback fonts.
        for font_path, cmap in fallback_cmaps.items():
            if cp in cmap:
                # These are static fonts, so italic is irrelevant.
                return get_font_object(font_path, font_size, bold, False)

        # 3. If nothing is found, the last resort.
        universal_path = FontService.get_fallback_universal_path()
        if exists(universal_path):
            # Roboto Flex can handle bold and italic.
            return get_font_object(universal_path, font_size, bold, italic)
        return None
    return resolver


@lru_cache(maxsize=64)
def _get_char_font_resolver(primary_font_path: str | None, font_size: int):
    """Returns a cached resolver so text-measurement caches can reuse stable callables."""
    return _make_fallback_resolver(primary_font_path, font_size)


def measure_text_size(text_value, font_obj, stroke_width=0):
    """Measures the bounding box of a text string, optionally with a stroke."""
    try:
        # Pillow 8.0.0+ with stroke_width support in getbbox
        bbox = font_obj.getbbox(text_value, stroke_width=stroke_width)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except TypeError:
        # Pillow < 8.0.0 where getbbox exists but doesn't take stroke_width
        bbox = font_obj.getbbox(text_value)
        # Manually approximate the added width from the stroke.
        return (bbox[2] - bbox[0]) + (stroke_width * 2), (bbox[3] - bbox[1]) + (stroke_width * 2)
    except AttributeError:
        # Pillow < 6.2.0, fallback to getsize
        try:
            w, h = font_obj.getsize(text_value)
            return w + (stroke_width * 2), h + (stroke_width * 2)
        except Exception:
            # Final fallback, very rough estimation
            size = getattr(font_obj, 'size', 12)
            return len(text_value) * size, size


@lru_cache(maxsize=4096)
def _measure_text_size_with_fallback(text_value: str, base_font: ImageFont.FreeTypeFont,
                                     char_font_resolver: Optional[Callable[[str, bool, bool], Optional[ImageFont.FreeTypeFont]]],
                                     is_bold: bool,
                                     is_italic: bool,
                                     primary_font_path: Optional[str]) -> Tuple[int, int]:
    """
    Measures text size, considering character-by-character font fallbacks.
    Includes a fast path for text fully supported by the primary font.
    """
    if not char_font_resolver or not primary_font_path:
        return measure_text_size(text_value, base_font)

    try:
        primary_cmap = get_font_cmap(primary_font_path)
        if primary_cmap and all(ord(c) in primary_cmap for c in text_value):
            font_for_measure = get_font_object(primary_font_path, base_font.size, is_bold, is_italic) or base_font
            return measure_text_size(text_value, font_for_measure)
    except Exception:
        pass

    width = 0
    graphemes = regex.findall(r'\X', text_value)
    height = measure_text_size('A', base_font)[1]
    math_font_path = FontService.get_fallback_math_path()

    for g in graphemes:
        f = None
        if len(g) == 1:
            f = char_font_resolver(g, bold=is_bold, italic=is_italic)

        if f is None:
            f = base_font

        # Simulate bold for the math fallback font.
        stroke_w = 0
        if is_bold and f and getattr(f, 'path', '') == math_font_path:
            stroke_w = 1

        w, h = measure_text_size(g, f, stroke_width=stroke_w)
        width += w
        height = max(height, h)
    return width, height


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    """Converts a hex color string to an (R, G, B) tuple."""
    color = color.lstrip('#')
    color = ''.join([c * 2 for c in color]) if len(color) == 3 else color
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    
    
def _get_theme_configuration(theme: str, user_id_for_color: Optional[int]) -> ThemeConfigData:
    """
    Returns all computed theme colors for the current render.
    """
    is_light_theme = theme == 'day'
    
    background_hex = '#ffffff' if is_light_theme else '#19212d'
    text_rgb = (0, 0, 0) if is_light_theme else (255, 255, 255)
    accent_rgb = (0, 89, 153) if is_light_theme else (106, 183, 236)
    
    name_color_light = ['#d32f2f', '#f57c00', '#7b1fa2', '#00796b', '#0288d1', '#c2185b', '#5d4037']
    name_color_dark = ['#ff7961', '#ffb74d', '#ba68c8', '#4db6ac', '#64b5f6', '#f06292', '#ffd54f']
    
    palette = name_color_light if is_light_theme else name_color_dark
    
    safe_user_id = user_id_for_color or 0
    name_index = abs(int(safe_user_id)) % len(palette)
    name_hex = palette[name_index]
    
    return ThemeConfigData(
        background_rgb=_hex_to_rgb(background_hex),
        text_rgb=text_rgb,
        name_rgb=_hex_to_rgb(name_hex),
        palette=palette,
        accent_rgb=accent_rgb,
    )


def _luminance(rgb: Tuple[int, int, int]) -> float:
    """Calculates the perceived luminance of an RGB color."""
    r, g, b = rgb
    return (0.299 * r * r + 0.587 * g * g + 0.114 * b * b) ** 0.5


def _light_or_dark(hex_color: str) -> str:
    """Determines if a color is 'light' or 'dark' based on luminance."""
    return 'light' if _luminance(_hex_to_rgb(hex_color)) > 127.5 else 'dark'


def _rounded_mask(size: Tuple[int, int], radius: int) -> Image.Image:
    """Creates a grayscale mask for a rounded rectangle."""
    w, h = size
    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    return mask


def _circle_crop(image: Image.Image, size: int) -> Image.Image:
    """Crops an image into a circle."""
    w, h = image.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    image = image.crop((left, top, left + side, top + side)
                       ).resize((size, size), BICUBIC).convert('RGBA')
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    result = Image.new('RGBA', (size, size))
    result.paste(image, (0, 0), mask)
    return result

class _StyledWord:
    """Represents a word with associated styling information."""

    def __init__(self, word: str, style: List[str] | None = None, custom_emoji_id: int | None = None):
        self.word = word
        self.style = style or []
        self.custom_emoji_id = custom_emoji_id


@dataclass(frozen=True)
class RichTextMetrics:
    width: int
    height: int


@lru_cache(maxsize=1)
def get_noto_color_emoji_font():
    """
    Loads the Noto Color Emoji font at its native supported bitmap size (109).
    """
    try:
        font = ImageFont.truetype(FontService.get_fallback_emoji_path(
        ), size=109, layout_engine=ImageFont.Layout.RAQM)
        return font
    except Exception as e:
        return None


def _is_forbidden_line_start_punctuation_char(ch: str) -> bool:
    """
    Checks if a character is a punctuation mark that should not start a line.
    This includes Chinese and English closing punctuation, and most standalone punctuation.
    """
    if len(ch) != 1:
        return False
    category = _ud.category(ch)
    # Categories: Pe (Close Punctuation), Pf (Final Quote), Po (Other Punctuation)
    # Pd (Dash Punctuation) and Pc (Connector Punctuation)
    # Pi (Initial Quote) and Ps (Open Punctuation) are generally allowed at line start
    if category in ('Pe', 'Pf', 'Po', 'Pd', 'Pc'):
        return True
    # Also explicitly check common full-width Chinese punctuation
    # Removed opening ones: （【《“‘ as they are usually fine at line start
    if ch in '，。！？、：；）》】”’':
        return True
    return False


def is_cjk(ch: str) -> bool:
    """Checks if a character is a CJK (Chinese, Japanese, Korean) character."""
    # Handle multi-character graphemes by only checking the first character.
    if not ch:
        return False
    o = ord(ch[0])
    return (0x1100 <= o <= 0x11FF or 0x2E80 <= o <= 0x2EFF or 0x3000 <= o <= 0x303F or
            0x3040 <= o <= 0x30FF or 0x3100 <= o <= 0x312F or 0x3130 <= o <= 0x318F or
            0x3190 <= o <= 0x319F or 0x31A0 <= o <= 0x31BF or 0x31C0 <= o <= 0x31EF or
            0x31F0 <= o <= 0x31FF or 0x3200 <= o <= 0x32FF or 0x3300 <= o <= 0x33FF or
            0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or
            0xF900 <= o <= 0xFAFF)

def _build_utf16_index_map(text: str) -> Dict[int, int]:
    """Builds a UTF-16 offset to Python string index map."""
    utf16_to_str_idx: Dict[int, int] = {}
    current_utf16_offset = 0
    for str_idx, char_val in enumerate(text):
        utf16_to_str_idx[current_utf16_offset] = str_idx
        current_utf16_offset += len(char_val.encode('utf-16-le')) // 2
    utf16_to_str_idx[current_utf16_offset] = len(text)
    return utf16_to_str_idx


def _utf16_length(text: str) -> int:
    """Returns the length of a string in UTF-16 code units."""
    return len(text.encode('utf-16-le')) // 2


def _build_char_style_info(text: str, entities: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    """Builds per-character style/custom-emoji metadata from entities."""
    char_info: List[Dict[str, Any]] = [{'style': set(), 'custom_emoji_id': None} for _ in range(len(text))]
    utf16_to_str_idx = _build_utf16_index_map(text)

    for ent in sorted(entities or [], key=lambda e: e.get('offset', 0)):
        ent_type = ent.get('type') or ''
        ent_offset_utf16 = int(ent.get('offset', 0))
        ent_length_utf16 = int(ent.get('length', 0))
        start_str_idx = utf16_to_str_idx.get(ent_offset_utf16)
        end_str_idx = utf16_to_str_idx.get(ent_offset_utf16 + ent_length_utf16)
        if start_str_idx is None or end_str_idx is None or start_str_idx >= len(text) or end_str_idx > len(text):
            continue
        normalized_style = ENTITY_STYLE_MAPPING.get(ent_type, "plain")
        style_to_add = {normalized_style} if normalized_style not in ("plain", "custom_emoji") else set()
        for idx in range(start_str_idx, end_str_idx):
            if idx < len(char_info):
                char_info[idx]['style'].update(style_to_add)
                if ent_type == 'custom_emoji' and ent.get('custom_emoji_id') is not None:
                    char_info[idx]['custom_emoji_id'] = int(ent['custom_emoji_id'])
    return char_info


def _build_grapheme_index_map(text: str) -> Tuple[List[str], List[int]]:
    """Builds grapheme list and maps grapheme index back to string index."""
    graphemes = regex.findall(r'\X', text)
    grapheme_to_str_idx = [0] * len(graphemes)
    temp_str_idx = 0
    for idx, grapheme in enumerate(graphemes):
        grapheme_to_str_idx[idx] = temp_str_idx
        temp_str_idx += len(grapheme)
    return graphemes, grapheme_to_str_idx


def _consume_custom_emoji_token(
    text: str,
    char_info: List[Dict[str, Any]],
    grapheme_to_str_idx: List[int],
    start_grapheme_idx: int,
) -> Tuple[_StyledWord, int]:
    """Consumes a custom-emoji token from the grapheme stream."""
    start_str_idx = grapheme_to_str_idx[start_grapheme_idx]
    emoji_id = char_info[start_str_idx]['custom_emoji_id']
    end_str_idx = start_str_idx
    while end_str_idx < len(text) and char_info[end_str_idx].get('custom_emoji_id') == emoji_id:
        end_str_idx += 1
    next_grapheme_idx = start_grapheme_idx
    while next_grapheme_idx < len(grapheme_to_str_idx) and grapheme_to_str_idx[next_grapheme_idx] < end_str_idx:
        next_grapheme_idx += 1
    return _StyledWord(text[start_str_idx:end_str_idx], [], emoji_id), next_grapheme_idx


def _consume_plain_text_token(
    graphemes: List[str],
    grapheme_to_str_idx: List[int],
    char_info: List[Dict[str, Any]],
    start_grapheme_idx: int,
) -> Tuple[_StyledWord, int]:
    """Consumes a newline, single-char token, or western word token."""
    current_grapheme = graphemes[start_grapheme_idx]
    start_str_idx = grapheme_to_str_idx[start_grapheme_idx]
    current_style = frozenset(char_info[start_str_idx]['style'])

    if current_grapheme == '\n':
        return _StyledWord('\n', []), start_grapheme_idx + 1
    if emoji.is_emoji(current_grapheme):
        return _StyledWord(current_grapheme, list(current_style), None), start_grapheme_idx + 1
    if is_cjk(current_grapheme) or current_grapheme.isspace() or _is_forbidden_line_start_punctuation_char(current_grapheme):
        return _StyledWord(current_grapheme, list(current_style)), start_grapheme_idx + 1

    segment_end_idx = start_grapheme_idx + 1
    while segment_end_idx < len(graphemes):
        next_grapheme = graphemes[segment_end_idx]
        next_str_idx = grapheme_to_str_idx[segment_end_idx]
        next_info = char_info[next_str_idx]
        if is_cjk(next_grapheme) or next_grapheme.isspace() or emoji.is_emoji(next_grapheme) or next_grapheme == '\n':
            break
        if frozenset(next_info['style']) != current_style:
            break
        if next_info['custom_emoji_id'] is not None:
            break
        segment_end_idx += 1

    return _StyledWord("".join(graphemes[start_grapheme_idx:segment_end_idx]), list(current_style)), segment_end_idx


def _build_styled_words(text: str, entities: List[Dict[str, Any]] | None) -> List[_StyledWord]:
    """Tokenizes text into styled words/graphemes/custom-emoji units."""
    text = (text or '').replace('\r', '')
    if not text:
        return []

    char_info = _build_char_style_info(text, entities)
    graphemes, grapheme_to_str_idx = _build_grapheme_index_map(text)
    words: List[_StyledWord] = []
    current_grapheme_idx = 0

    while current_grapheme_idx < len(graphemes):
        start_str_idx = grapheme_to_str_idx[current_grapheme_idx]
        if char_info[start_str_idx]['custom_emoji_id'] is not None:
            word, current_grapheme_idx = _consume_custom_emoji_token(text, char_info, grapheme_to_str_idx, current_grapheme_idx)
        else:
            word, current_grapheme_idx = _consume_plain_text_token(graphemes, grapheme_to_str_idx, char_info, current_grapheme_idx)
        words.append(word)

    return words


@lru_cache(maxsize=256)
def get_font_metrics(font_obj: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    """Caches and returns the ascent and descent for a font object."""
    try:
        return font_obj.getmetrics()
    except Exception:
        # Fallback for older Pillow or other issues
        size = getattr(font_obj, 'size', 12)
        return int(size * 0.9), int(size * 0.3)


def _build_rich_text_runtime(
    font_base: ImageFont.FreeTypeFont,
    font_color: Tuple[int, int, int],
    accent_color: Tuple[int, int, int],
    max_width: int,
    font_size: int,
    custom_emoji_images: Optional[Dict[int, Image.Image]] = None,
) -> RichTextRuntime:
    """Precomputes runtime state shared across rich-text layout and drawing."""
    ascent, descent = get_font_metrics(font_base)
    primary_font_path = FontService.determine_primary_font_path()
    char_font_resolver = _get_char_font_resolver(primary_font_path, font_size) if _FT_AVAILABLE else None
    return RichTextRuntime(
        font_base=font_base,
        font_size=font_size,
        font_color=font_color,
        accent_color=accent_color,
        max_width=max_width,
        line_height=max(int(font_size * RenderConfig.LINE_HEIGHT_MULTIPLIER), ascent + descent),
        primary_ascent=ascent,
        primary_font_path=primary_font_path,
        char_font_resolver=char_font_resolver,
        noto_emoji_font_large=get_noto_color_emoji_font(),
        custom_emoji_images=custom_emoji_images or {},
    )


def _make_token_width_resolver(runtime: RichTextRuntime):
    """Creates a cached width resolver for rich-text tokens."""
    @lru_cache(maxsize=2048)
    def token_render_width(word_text: str, word_style: Tuple[str, ...], custom_emoji_id: int | None = None) -> int:
        temp_word = _StyledWord(word_text, list(word_style), custom_emoji_id)
        emo_size = runtime.font_size + max(RenderConfig.EMOJI_MIN_PADDING, runtime.font_size // RenderConfig.EMOJI_PADDING_DIVISOR)
        emoji_spacing = max(RenderConfig.EMOJI_MIN_SPACING, runtime.font_size // RenderConfig.EMOJI_SPACING_DIVISOR)
        if temp_word.custom_emoji_id or emoji.is_emoji(temp_word.word):
            return emo_size + emoji_spacing
        is_bold = 'bold' in temp_word.style
        is_italic = 'italic' in temp_word.style and not any(is_cjk(c) for c in temp_word.word)
        font_path = runtime.primary_font_path if not is_italic else FontService.get_fallback_universal_path()
        font_for_measure = FontService.get_font_object(font_path, runtime.font_size, is_bold, is_italic) or runtime.font_base
        return _measure_text_size_with_fallback(
            temp_word.word,
            font_for_measure,
            runtime.char_font_resolver,
            is_bold,
            is_italic,
            runtime.primary_font_path,
        )[0]

    return token_render_width


def _word_render_width(word: _StyledWord, token_render_width) -> int:
    return token_render_width(word.word, tuple(sorted(word.style)), word.custom_emoji_id)


def _measure_rich_text_canvas(lines: List[List[_StyledWord]], runtime: RichTextRuntime, token_render_width) -> RichTextMetrics:
    """Calculates the canvas size needed to render wrapped rich text."""
    quote_width = max(RenderConfig.QUOTE_LINE_MIN_WIDTH, int(runtime.font_size * RenderConfig.QUOTE_LINE_WIDTH_FACTOR))
    quote_padding = max(RenderConfig.QUOTE_MIN_PADDING, int(runtime.font_size * RenderConfig.QUOTE_PADDING_FACTOR))
    blockquote_extra_space = quote_width + quote_padding
    line_widths = [
        sum(_word_render_width(word, token_render_width) for word in line)
        + (blockquote_extra_space if any('blockquote' in word.style for word in line) else 0)
        for line in lines
    ]
    return RichTextMetrics(
        width=max(1, min(runtime.max_width, max(line_widths, default=1))),
        height=max(runtime.line_height, len(lines) * runtime.line_height),
    )


def _wrap_styled_words(words: List[_StyledWord], runtime: RichTextRuntime, token_render_width) -> List[List[_StyledWord]]:
    """Wraps styled words into lines based on measured token widths."""
    lines: List[List[_StyledWord]] = [[]]
    current_x = 0
    margin = max(2, int(runtime.font_size * RenderConfig.LINE_WRAP_MARGIN_FACTOR))
    available_width = runtime.max_width - margin

    for index, current_word in enumerate(words):
        if current_word.word == '\n':
            lines.append([])
            current_x = 0
            continue
        if not lines[-1] and current_word.word.isspace():
            continue

        current_width = _word_render_width(current_word, token_render_width)
        lookahead_width = 0
        if index + 1 < len(words) and _is_forbidden_line_start_punctuation_char(words[index + 1].word):
            lookahead_width = _word_render_width(words[index + 1], token_render_width)

        if current_x > 0 and current_x + current_width + lookahead_width > available_width:
            lines.append([])
            current_x = 0
            if current_word.word.isspace():
                continue

        if current_width > available_width and not (current_word.custom_emoji_id or emoji.is_emoji(current_word.word)):
            chunk = ""
            for grapheme in regex.findall(r'\X', current_word.word):
                chunk_width = token_render_width(chunk + grapheme, tuple(sorted(current_word.style)), current_word.custom_emoji_id)
                if current_x + chunk_width > available_width and chunk:
                    lines[-1].append(_StyledWord(chunk, current_word.style, current_word.custom_emoji_id))
                    lines.append([])
                    current_x = 0
                    chunk = grapheme
                else:
                    chunk += grapheme
            if chunk:
                chunk_word = _StyledWord(chunk, current_word.style, current_word.custom_emoji_id)
                lines[-1].append(chunk_word)
                current_x += _word_render_width(chunk_word, token_render_width)
            continue

        lines[-1].append(current_word)
        current_x += current_width

    return [line for line in lines if line]


def _draw_word_spoiler(draw: ImageDraw.ImageDraw, runtime: RichTextRuntime, word: _StyledWord, word_start_x: int, y: int, fill_color: Tuple[int, int, int]) -> None:
    """Draws spoiler background for a word when needed."""
    if 'spoiler' not in word.style:
        return
    is_bold = 'bold' in word.style
    is_italic = 'italic' in word.style and not any(is_cjk(c) for c in word.word)
    word_width, _ = _measure_text_size_with_fallback(
        word.word,
        runtime.font_base,
        runtime.char_font_resolver,
        is_bold,
        is_italic,
        runtime.primary_font_path,
    )
    r, g, b = fill_color
    draw.rectangle((word_start_x, y, word_start_x + word_width, y + runtime.line_height), fill=(r, g, b, 120))


def _draw_word_decorations(draw: ImageDraw.ImageDraw, runtime: RichTextRuntime, word: _StyledWord, word_start_x: int, word_end_x: int, y: int, fill_color: Tuple[int, int, int]) -> None:
    """Draws underline and strikethrough decorations for a word."""
    if 'strikethrough' in word.style:
        line_y = y + runtime.line_height // 2
        line_h = max(RenderConfig.DECORATION_LINE_MIN_HEIGHT, runtime.font_size // RenderConfig.STRIKE_LINE_HEIGHT_DIVISOR)
        draw.rectangle((word_start_x, line_y, word_end_x, line_y + line_h), fill=fill_color)
    if 'underline' in word.style:
        line_y = y + runtime.line_height - max(2, runtime.font_size // RenderConfig.UNDERLINE_HEIGHT_DIVISOR)
        line_h = max(RenderConfig.DECORATION_LINE_MIN_HEIGHT, runtime.font_size // RenderConfig.STRIKE_LINE_HEIGHT_DIVISOR)
        draw.rectangle((word_start_x, line_y, word_end_x, line_y + line_h), fill=fill_color)


def _draw_emoji_word(canvas: Image.Image, draw: ImageDraw.ImageDraw, runtime: RichTextRuntime, word: _StyledWord, x: int, y: int, token_render_width) -> int:
    """Draws a standard or custom emoji token and returns the new x position."""
    word_width = _word_render_width(word, token_render_width)
    if word.custom_emoji_id and (emoji_img := runtime.custom_emoji_images.get(word.custom_emoji_id)):
        emoji_size = runtime.font_size + max(2, runtime.font_size // 6)
        resized_emoji = emoji_img.resize((emoji_size, emoji_size), BICUBIC)
        canvas.paste(resized_emoji, (int(x), int(y + (runtime.line_height - resized_emoji.height) // 2)), resized_emoji)
        return x + word_width

    if runtime.noto_emoji_font_large:
        try:
            temp_img = Image.new("RGBA", (runtime.font_size * 2, runtime.font_size * 2), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            temp_draw.text((0, 0), word.word, font=runtime.noto_emoji_font_large, embedded_color=True)
            bbox = temp_img.getbbox()
            if bbox:
                crop = temp_img.crop(bbox)
                size = runtime.font_size + max(2, runtime.font_size // 6)
                crop.thumbnail((size, size), BICUBIC)
                canvas.paste(crop, (int(x), int(y + (runtime.line_height - crop.height) // 2)), crop)
                return x + word_width
        except Exception:
            pass

    draw.text((int(x), y), word.word, font=runtime.font_base, fill=runtime.font_color)
    return x + word_width


def _draw_text_word(draw: ImageDraw.ImageDraw, runtime: RichTextRuntime, word: _StyledWord, x: int, y: int, fill_color: Tuple[int, int, int]) -> int:
    """Draws a regular text token and returns the new x position."""
    is_bold = 'bold' in word.style
    is_italic = 'italic' in word.style and not any(is_cjk(c) for c in word.word)

    if is_italic:
        italic_font = FontService.get_font_object(FontService.get_fallback_universal_path(), runtime.font_size, is_bold, True) or runtime.font_base
        char_ascent, _ = get_font_metrics(italic_font)
        y_offset = runtime.primary_ascent - char_ascent
        draw.text((int(x), y + y_offset), word.word, font=italic_font, fill=fill_color)
        width, _ = measure_text_size(word.word, italic_font)
        return x + width

    math_font_path = FontService.get_fallback_math_path()
    for grapheme in regex.findall(r'\X', word.word):
        char_font = runtime.char_font_resolver(grapheme, bold=is_bold, italic=False) if runtime.char_font_resolver and len(grapheme) == 1 else runtime.font_base
        if not char_font:
            char_font = runtime.font_base
        stroke_width = 1 if is_bold and getattr(char_font, 'path', '') == math_font_path else 0
        char_ascent, _ = get_font_metrics(char_font)
        y_offset = runtime.primary_ascent - char_ascent
        draw.text((int(x), y + y_offset), grapheme, font=char_font, fill=fill_color, stroke_width=stroke_width)
        width, _ = measure_text_size(grapheme, char_font, stroke_width=stroke_width)
        x += width
    return x


def _draw_rich_text_lines(canvas: Image.Image, runtime: RichTextRuntime, lines: List[List[_StyledWord]], token_render_width) -> None:
    """Draws already-wrapped rich-text lines onto the canvas."""
    draw = ImageDraw.Draw(canvas)
    quote_width = max(RenderConfig.QUOTE_LINE_MIN_WIDTH, int(runtime.font_size * RenderConfig.QUOTE_LINE_WIDTH_FACTOR))
    quote_padding = max(RenderConfig.QUOTE_MIN_PADDING, int(runtime.font_size * RenderConfig.QUOTE_PADDING_FACTOR))
    blockquote_extra_space = quote_width + quote_padding

    current_y = 0
    for line in lines:
        current_x = 0
        if any('blockquote' in word.style for word in line):
            draw.rectangle((0, current_y, quote_width, current_y + runtime.line_height), fill=runtime.accent_color)
            current_x += blockquote_extra_space

        for word in line:
            fill_color = runtime.accent_color if ('monospace' in word.style or 'mention' in word.style) else runtime.font_color
            word_start_x = current_x
            _draw_word_spoiler(draw, runtime, word, word_start_x, current_y, fill_color)

            if word.custom_emoji_id or (runtime.noto_emoji_font_large and emoji.is_emoji(word.word)):
                current_x = _draw_emoji_word(canvas, draw, runtime, word, current_x, current_y, token_render_width)
            else:
                current_x = _draw_text_word(draw, runtime, word, current_x, current_y, fill_color)

            _draw_word_decorations(draw, runtime, word, word_start_x, current_x, current_y, fill_color)
        current_y += runtime.line_height


def _append_ellipsis_to_line(
    line: List[_StyledWord],
    runtime: RichTextRuntime,
    token_render_width,
) -> List[_StyledWord]:
    """Shrinks the last visible line so an ellipsis fits within the width limit."""
    ellipsis = _StyledWord('...', [])
    available_width = runtime.max_width - max(2, int(runtime.font_size * RenderConfig.LINE_WRAP_MARGIN_FACTOR))
    ellipsis_width = _word_render_width(ellipsis, token_render_width)
    blockquote_extra_space = 0
    if any('blockquote' in word.style for word in line):
        quote_width = max(RenderConfig.QUOTE_LINE_MIN_WIDTH, int(runtime.font_size * RenderConfig.QUOTE_LINE_WIDTH_FACTOR))
        quote_padding = max(RenderConfig.QUOTE_MIN_PADDING, int(runtime.font_size * RenderConfig.QUOTE_PADDING_FACTOR))
        blockquote_extra_space = quote_width + quote_padding
    allowed_line_width = max(ellipsis_width, available_width - blockquote_extra_space)

    trimmed_line = [word for word in line if word.word != '\n']
    while trimmed_line:
        current_width = sum(_word_render_width(word, token_render_width) for word in trimmed_line)
        if current_width + ellipsis_width <= allowed_line_width:
            break
        last_word = trimmed_line[-1]
        if len(regex.findall(r'\X', last_word.word)) <= 1:
            trimmed_line.pop()
            continue
        graphemes = regex.findall(r'\X', last_word.word)
        shortened_word = _StyledWord(''.join(graphemes[:-1]), last_word.style, last_word.custom_emoji_id)
        if shortened_word.word:
            trimmed_line[-1] = shortened_word
        else:
            trimmed_line.pop()

    trimmed_line.append(ellipsis)
    return trimmed_line


def _apply_max_height_to_lines(
    lines: List[List[_StyledWord]],
    runtime: RichTextRuntime,
    token_render_width,
    max_height: int | None,
) -> List[List[_StyledWord]]:
    """Limits wrapped lines to the requested height and adds an ellipsis when truncated."""
    if max_height is None:
        return lines
    max_lines = max(1, max_height // runtime.line_height)
    if len(lines) <= max_lines:
        return lines
    visible_lines = [list(line) for line in lines[:max_lines]]
    visible_lines[-1] = _append_ellipsis_to_line(visible_lines[-1], runtime, token_render_width)
    return visible_lines


def _render_rich_text(
    text: str,
    entities: List[Dict[str, any]] | None,
    font_base: ImageFont.FreeTypeFont,
    font_color: Tuple[int, int, int],
    accent_color: Tuple[int, int, int],
    max_width: int,
    font_size: int,
    max_height: int | None = None,
    custom_emoji_images: Dict[int, Image.Image] | None = None
) -> Image.Image:
    """Renders rich text into a transparent image."""
    runtime = _build_rich_text_runtime(
        font_base=font_base,
        font_color=font_color,
        accent_color=accent_color,
        max_width=max_width,
        font_size=font_size,
        custom_emoji_images=custom_emoji_images,
    )
    words = _build_styled_words(text, entities)
    token_render_width = _make_token_width_resolver(runtime)
    final_lines = _wrap_styled_words(words, runtime, token_render_width)
    final_lines = _apply_max_height_to_lines(final_lines, runtime, token_render_width, max_height)
    metrics = _measure_rich_text_canvas(final_lines, runtime, token_render_width)
    img = Image.new('RGBA', (int(metrics.width + max(2, font_size // 8)), int(metrics.height)), (0, 0, 0, 0))
    _draw_rich_text_lines(img, runtime, final_lines, token_render_width)
    return img


def _draw_premium_badge_fallback(size: int) -> Image.Image:
    """Draws a simple premium-style badge when no emoji-status document is available."""
    badge = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    outer_margin = max(1, size // 12)
    inner_margin = max(2, size // 5)
    draw.ellipse((outer_margin, outer_margin, size - outer_margin, size - outer_margin), fill=(96, 167, 255, 255))
    draw.ellipse((inner_margin, inner_margin, size - inner_margin, size - inner_margin), fill=(129, 193, 255, 255))

    cx = cy = size / 2
    outer_r = size * 0.26
    inner_r = outer_r * 0.45
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = outer_r if i % 2 == 0 else inner_r
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=(255, 255, 255, 255))
    return badge


def _compose_inline_images(images: List[Image.Image], gap: int = 0, align: str = 'center') -> Optional[Image.Image]:
    """Combines multiple images into a single horizontal strip."""
    valid_images = [img for img in images if img]
    if not valid_images:
        return None
    if len(valid_images) == 1:
        return valid_images[0]

    total_width = sum(img.width for img in valid_images) + gap * (len(valid_images) - 1)
    max_height = max(img.height for img in valid_images)
    canvas = Image.new('RGBA', (total_width, max_height), (0, 0, 0, 0))

    x = 0
    for img in valid_images:
        if align == 'bottom':
            y = max_height - img.height
        elif align == 'top':
            y = 0
        else:
            y = max(0, (max_height - img.height) // 2)
        canvas.paste(img, (x, y), img)
        x += img.width + gap
    return canvas


def _draw_reply_line(line_width: int, height: int, color: Tuple[int, int, int]) -> Image.Image:
    """Draws the vertical line for a reply quote."""
    img = Image.new('RGBA', (max(2, line_width), height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, max(1, line_width // 2), height), fill=color)
    return img


def _build_avatar_canvas(
    avatar_bytes: Optional[BytesIO],
    sender_name: Optional[str],
    theme_config: ThemeConfigData,
    avatar_size: int,
) -> Optional[Image.Image]:
    """Builds the sender avatar canvas, including fallback initials avatar."""
    try:
        loaded_avatar = Image.open(avatar_bytes).convert('RGBA') if avatar_bytes else None
    except Exception:
        loaded_avatar = None

    if loaded_avatar:
        return _circle_crop(loaded_avatar, avatar_size)

    base = Image.new('RGB', (500, 500), color=theme_config.name_rgb)
    draw = ImageDraw.Draw(base)
    init_font_size = int(RenderConfig.AVATAR_INITIAL_FONT_SIZE * _read_font_scale())
    initials_char = '?'
    if sender_name and sender_name.strip():
        first_grapheme = regex.match(r'\X', sender_name.strip())
        if first_grapheme:
            initials_char = first_grapheme.group(0).upper()

    char_font_resolver = FontService.make_fallback_resolver(FontService.determine_primary_font_path(), init_font_size) if _FT_AVAILABLE else None
    init_font = char_font_resolver(initials_char, bold=True) if char_font_resolver and len(initials_char) == 1 else None
    if not init_font:
        init_font = FontService.font(PLUGIN_PATH, init_font_size, bold=True)
    draw.text((250, 250), initials_char, fill=(250, 250, 250), font=init_font, anchor='mm')
    return _circle_crop(base, avatar_size)


def _resize_standalone_emoji_image(media_img: Image.Image, target_size: int) -> Image.Image:
    """Resizes a standalone emoji image while preserving aspect ratio."""
    width, height = media_img.size
    if width <= 0 or height <= 0:
        return media_img
    aspect = width / height
    new_width, new_height = (
        (target_size, int(target_size / aspect)) if width > height else (int(target_size * aspect), target_size)
    )
    return media_img.resize((max(1, new_width), max(1, new_height)), BICUBIC)


def _resolve_media_content(
    text: str,
    entities: List[Dict[str, Any]],
    media_bytes: Optional[BytesIO],
    custom_emoji_images: Optional[Dict[int, Image.Image]],
    text_font_size: int,
) -> Tuple[str, List[Dict[str, Any]], Optional[Image.Image], bool]:
    """Resolves media content and handles standalone emoji promotion into media."""
    text_value = text or ''
    entity_list = entities or []
    media_img = None
    if media_bytes:
        try:
            media_img = Image.open(media_bytes).convert('RGBA')
        except Exception:
            media_img = None

    standalone_custom = (
        emoji.emoji_count(text_value, unique=False) == 1
        and entity_list
        and len(entity_list) == 1
        and entity_list[0].get('type') == 'custom_emoji'
    )
    standalone_standard = (
        not standalone_custom
        and emoji.emoji_count(text_value, unique=False) == 1
        and emoji.replace_emoji(text_value, replace='').strip() == ''
    )

    if media_img and (standalone_custom or standalone_standard):
        standalone_custom = False
        standalone_standard = False

    target_size = int(text_font_size * RenderConfig.STANDALONE_EMOJI_SCALE_FACTOR)
    if standalone_custom:
        emoji_id = entity_list[0].get('custom_emoji_id')
        if emoji_id and custom_emoji_images and emoji_id in custom_emoji_images:
            media_img = _resize_standalone_emoji_image(custom_emoji_images[emoji_id], target_size)
            return "", [], media_img, True

    if standalone_standard:
        noto_emoji_font_large = get_noto_color_emoji_font()
        if noto_emoji_font_large:
            try:
                temp_img = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                temp_draw.text((0, 0), text_value.strip(), font=noto_emoji_font_large, embedded_color=True)
                bbox = temp_img.getbbox()
                if bbox:
                    media_img = _resize_standalone_emoji_image(temp_img.crop(bbox), target_size)
                    return "", [], media_img, True
            except Exception:
                media_img = None

    return text_value, entity_list, media_img, False


def _resize_media_for_bubble(media_img: Optional[Image.Image], max_content_width: int, scale: float) -> Optional[Image.Image]:
    """Resizes media preview so it fits the bubble layout constraints."""
    if not media_img:
        return None
    original_width, original_height = media_img.size
    if original_width <= 0 or original_height <= 0:
        return media_img

    max_media_width = int(max_content_width * RenderConfig.MEDIA_WIDTH_SCALE_FACTOR)
    aspect_ratio = original_height / original_width
    new_width = max_media_width
    new_height = int(new_width * aspect_ratio)
    max_height = int(RenderConfig.MAX_MEDIA_HEIGHT * scale)
    if new_height > max_height:
        new_height = max_height
        new_width = int(new_height / aspect_ratio)
    if (new_width, new_height) == (original_width, original_height):
        return media_img
    try:
        return media_img.resize((new_width, new_height), BICUBIC)
    except Exception:
        return media_img


def _render_reply_block(
    reply_name: Optional[str],
    reply_text: Optional[str],
    reply_entities: Optional[List[Dict[str, Any]]],
    reply_chat_id: Optional[int],
    theme_config: ThemeConfigData,
    accent_color: Tuple[int, int, int],
    text_color: Tuple[int, int, int],
    reply_name_font: ImageFont.FreeTypeFont,
    reply_text_font: ImageFont.FreeTypeFont,
    max_content_width: int,
    reply_name_font_size: int,
    reply_text_font_size: int,
) -> Optional[ReplyRenderBlock]:
    """Renders the reply preview block when reply metadata exists."""
    if not (reply_name and reply_text):
        return None
    reply_name_color_hex = theme_config.palette[abs(int(reply_chat_id or 0)) % len(theme_config.palette)]
    reply_name_color = _hex_to_rgb(reply_name_color_hex)
    reply_name_entities = [{'type': 'bold', 'offset': 0, 'length': len(reply_name.encode('utf-16-le')) // 2}]
    name_img = _render_rich_text(
        reply_name,
        reply_name_entities,
        reply_name_font,
        reply_name_color,
        accent_color,
        int(max_content_width * 0.9),
        reply_name_font_size,
    )
    text_img = _render_rich_text(
        reply_text,
        reply_entities or [],
        reply_text_font,
        text_color,
        accent_color,
        int(max_content_width * 0.9),
        reply_text_font_size,
    )
    return ReplyRenderBlock(name_img=name_img, text_img=text_img, color=reply_name_color)


def _build_bubble_elements(
    name_img: Optional[Image.Image],
    forward_info_img: Optional[Image.Image],
    reply_block: Optional[ReplyRenderBlock],
    media_img: Optional[Image.Image],
    text_img: Optional[Image.Image],
) -> List[BubbleElement]:
    """Builds ordered bubble elements used for layout and painting."""
    elements: List[BubbleElement] = []
    if name_img:
        elements.append(BubbleElement(kind='name', img=name_img))
    if forward_info_img:
        elements.append(BubbleElement(kind='forward', img=forward_info_img))
    if reply_block:
        elements.append(BubbleElement(kind='reply', reply_block=reply_block))
    if media_img:
        elements.append(BubbleElement(kind='media', img=media_img))
    if text_img:
        elements.append(BubbleElement(kind='text', img=text_img))
    return elements


def _measure_bubble_layout(
    elements: List[BubbleElement],
    profile: RenderScaleProfile,
    force_avatar_space: bool,
    suppress_sender: bool,
    content_width: Optional[int],
) -> BubbleLayoutMetrics:
    """Calculates bubble layout dimensions from prepared visual elements."""
    should_reserve_avatar_space = force_avatar_space or not suppress_sender
    layout_avatar_space = profile.avatar_size if should_reserve_avatar_space else 0
    layout_gap = int(RenderConfig.AVATAR_TO_BUBBLE_GAP * profile.scale) if should_reserve_avatar_space else 0

    image_widths = [element.img.width for element in elements if element.img]
    reply_widths = [
        max(element.reply_block.name_img.width, element.reply_block.text_img.width) + profile.indent
        for element in elements if element.reply_block
    ]
    calculated_max_width = max(image_widths + reply_widths + [0])
    max_width = content_width if content_width is not None else calculated_max_width

    content_heights = []
    for element in elements:
        if element.reply_block:
            content_heights.append(element.reply_block.name_img.height + element.reply_block.text_img.height)
        elif element.img:
            content_heights.append(element.img.height)
    content_height = (
        sum(content_heights) + profile.gap * (len(elements) - 1 if elements else -1)
        if elements else int(profile.text_font_size * 0.5)
    )
    final_height = max(profile.indent * 2 + content_height, profile.avatar_size if should_reserve_avatar_space else 0)
    final_width = layout_avatar_space + layout_gap + max_width + profile.indent * 2
    bubble_x = layout_avatar_space + layout_gap
    bubble_width = max_width + profile.indent * 2
    return BubbleLayoutMetrics(
        should_reserve_avatar_space=should_reserve_avatar_space,
        layout_avatar_space=layout_avatar_space,
        layout_gap=layout_gap,
        calculated_max_width=calculated_max_width,
        max_width=max_width,
        content_height=content_height,
        final_height=final_height,
        final_width=final_width,
        bubble_x=bubble_x,
        bubble_width=bubble_width,
    )


def _paint_bubble_elements(
    canvas: Image.Image,
    elements: List[BubbleElement],
    layout: BubbleLayoutMetrics,
    profile: RenderScaleProfile,
) -> None:
    """Paints prepared bubble elements onto the final bubble canvas."""
    current_y = profile.indent
    paste_x = layout.bubble_x + profile.indent
    reply_line_width = int(RenderConfig.BASE_REPLY_LINE_WIDTH * profile.scale)
    reply_content_offset = reply_line_width + int(profile.indent * 0.5)

    for index, element in enumerate(elements):
        if element.reply_block:
            reply_height = element.reply_block.name_img.height + element.reply_block.text_img.height
            line = _draw_reply_line(reply_line_width, reply_height, element.reply_block.color)
            canvas.paste(line, (int(paste_x), int(current_y)), line)
            canvas.paste(element.reply_block.name_img, (int(paste_x + reply_content_offset), int(current_y)), element.reply_block.name_img)
            canvas.paste(
                element.reply_block.text_img,
                (int(paste_x + reply_content_offset), int(current_y + element.reply_block.name_img.height)),
                element.reply_block.text_img,
            )
            current_y += reply_height
        elif element.img:
            canvas.paste(element.img, (int(paste_x), int(current_y)), element.img)
            current_y += element.img.height
        if index < len(elements) - 1:
            current_y += profile.gap


def _compose_bubble_canvas(
    avatar_canvas: Optional[Image.Image],
    elements: List[BubbleElement],
    theme_config: ThemeConfigData,
    profile: RenderScaleProfile,
    layout: BubbleLayoutMetrics,
) -> Image.Image:
    """Builds the supersampled bubble canvas before final downscaling."""
    canvas = Image.new('RGBA', (int(layout.final_width), int(layout.final_height)), (0, 0, 0, 0))
    rect = Image.new('RGBA', (int(layout.bubble_width), int(layout.final_height)), theme_config.background_rgb + (255,))
    rect_mask = _rounded_mask((int(layout.bubble_width), int(layout.final_height)), profile.rect_round_radius)
    canvas.paste(rect, (int(layout.bubble_x), 0), rect_mask)
    if avatar_canvas:
        canvas.paste(avatar_canvas, (0, 0), avatar_canvas)
    _paint_bubble_elements(canvas, elements, layout, profile)
    return canvas


def yv_lu_generate(text: str, avatar_bytes: BytesIO | None,
                   sender_name: str | None = None,
                   media_bytes: BytesIO | None = None,
                   entities: List[Dict[str, Any]] | None = None, reply_name: str | None = None,
                   reply_text: str | None = None, reply_entities: List[Dict[str, Any]] | None = None,
                   reply_chat_id: int | None = None, user_id_for_color: int | None = None,
                   custom_emoji_images: Dict[int, Image.Image] | None = None,
                   suppress_sender: bool = False,
                   theme: str = 'dark',
                   content_width: Optional[int] = None,
                   force_avatar_space: bool = False,
                   name_img: Optional[Image.Image] = None,
                   forward_info_img: Optional[Image.Image] = None
                   ) -> Tuple[BytesIO, int]:
    """
    The main image generation function.
    It orchestrates all the pieces: avatar, name, text, replies, media, etc.
    Accepts and returns BytesIO objects for images.
    """
    profile = _build_render_scale_profile()

    theme_config = _get_theme_configuration(theme, user_id_for_color)
    text_color = theme_config.text_rgb
    accent_color = theme_config.accent_rgb

    text_font_size = profile.text_font_size
    reply_name_font_size = profile.reply_name_font_size
    reply_text_font_size = profile.reply_text_font_size
    text_font = FontService.font(PLUGIN_PATH, text_font_size)
    reply_name_font = FontService.font(PLUGIN_PATH, reply_name_font_size, bold=True)
    reply_text_font = FontService.font(PLUGIN_PATH, reply_text_font_size)

    avatar_canvas = _build_avatar_canvas(avatar_bytes, sender_name, theme_config, profile.avatar_size) if not suppress_sender else None

    text_value, entities, media_img, promoted_standalone_emoji = _resolve_media_content(
        text=text,
        entities=entities or [],
        media_bytes=media_bytes,
        custom_emoji_images=custom_emoji_images,
        text_font_size=text_font_size,
    )
    has_text, has_media = bool(text_value), bool(media_img)
    max_content_width = profile.max_content_width
    if media_img and not promoted_standalone_emoji:
        media_img = _resize_media_for_bubble(media_img, max_content_width, profile.scale)

    reply_block = _render_reply_block(
        reply_name=reply_name,
        reply_text=reply_text,
        reply_entities=reply_entities,
        reply_chat_id=reply_chat_id,
        theme_config=theme_config,
        accent_color=accent_color,
        text_color=text_color,
        reply_name_font=reply_name_font,
        reply_text_font=reply_text_font,
        max_content_width=max_content_width,
        reply_name_font_size=reply_name_font_size,
        reply_text_font_size=reply_text_font_size,
    )
    text_img = _render_rich_text(
        text_value,
        entities,
        text_font,
        text_color,
        accent_color,
        max_content_width,
        text_font_size,
        max_height=int(RenderConfig.MAX_TEXT_BLOCK_HEIGHT * profile.scale),
        custom_emoji_images=custom_emoji_images,
    ) if has_text else None

    elements = _build_bubble_elements(name_img, forward_info_img, reply_block, media_img, text_img)
    layout = _measure_bubble_layout(elements, profile, force_avatar_space, suppress_sender, content_width)
    canvas = _compose_bubble_canvas(avatar_canvas, elements, theme_config, profile, layout)
    final_width, final_height = int(canvas.width / profile.supersample), int(canvas.height / profile.supersample)
    final_image = canvas.resize((final_width, final_height), BICUBIC) if final_width > 0 and final_height > 0 else Image.new('RGBA', (1, 1), (0,0,0,0))
    final_output = BytesIO(); final_output.name = 'result.png'
    final_image.save(final_output, 'PNG'); final_output.seek(0)
    return final_output, layout.calculated_max_width


async def _download_static_media_preview(client, media_obj) -> BytesIO | None:
    """
    Downloads a static preview (PNG) of a media object into a BytesIO object.
    """
    if not media_obj:
        return None

    # Use BytesIO as the download target
    tmp_full_bytes = BytesIO()
    try:
        # Handle photos directly
        if isinstance(media_obj, MessageMediaPhoto) and hasattr(media_obj, 'photo'):
            await client.download_media(media_obj.photo, file=tmp_full_bytes)
            tmp_full_bytes.seek(0)
            if tmp_full_bytes.getbuffer().nbytes > 0:
                with Image.open(tmp_full_bytes) as img:
                    output_bytes = BytesIO()
                    img.convert('RGBA').save(output_bytes, 'PNG')
                    output_bytes.seek(0)
                    return output_bytes
            return None

        doc = getattr(media_obj, 'document', None)
        if doc is None:
            return None

        # For documents (stickers, videos), try thumbnail first, then full file
        thumb = max(getattr(doc, 'thumbs', []) or [], key=lambda t: getattr(
            t, 'w', 0) * getattr(t, 'h', 0), default=None)
        
        await client.download_media(doc, file=tmp_full_bytes, thumb=thumb)
            
        tmp_full_bytes.seek(0)
        if tmp_full_bytes.getbuffer().nbytes > 0:
            with Image.open(tmp_full_bytes) as img:
                # If it's an animated format that PIL can read, seek to the first frame
                if getattr(img, 'is_animated', False):
                    img.seek(0)
                output_bytes = BytesIO()
                img.convert('RGBA').save(output_bytes, 'PNG')
                output_bytes.seek(0)
                return output_bytes

    except Exception:
        # If any step fails, return None
        return None
    return None

    
def _telethon_entities_to_dicts(entities_obj) -> List[Dict[str, Any]]:
    """Converts Telethon entity objects to a simpler dictionary format."""
    result: List[Dict[str, Any]] = []
    if not entities_obj:
        return result
    for ent in entities_obj:
        if isinstance(ent, MessageEntityCustomEmoji):
            raw_type = "custom_emoji"
        else:
            raw_type = ent.__class__.__name__.replace('MessageEntity', '').lower()

        try:
            item = {'type': raw_type, 'offset': int(
                getattr(ent, 'offset', 0)), 'length': int(getattr(ent, 'length', 0))}
            
            if raw_type == 'custom_emoji':
                item['custom_emoji_id'] = int(getattr(ent, 'document_id'))
            result.append(item)
        except Exception:
            continue
    return result


@listener(command=yvlu_cmd,
          description=f"生成语录\n\n回复消息: `-{yvlu_cmd}`\n合并多条: `-{yvlu_cmd} <数字>`\n直接生成: `-{yvlu_cmd} <文本>`\n伪造消息: `-{yvlu_cmd} _fake <文本>`\n设置字体: `-{yvlu_cmd} _font <文件名|reset>`\n设置倍率: `-{yvlu_cmd} _size <倍率|reset>`\n使用浅色主题: `-{yvlu_cmd} _day`\n设置贴纸包: `-{yvlu_cmd} _pack <名称>`\n开关自动添加: `-{yvlu_cmd} _packadd`\n强制添加贴纸: 回复消息并使用 `-{yvlu_cmd} _packadd`",
          parameters="[text/reply or n | _fake text | _font <filename|reset> | _size scale | _day | _pack <name> | _packadd]")
async def yv_lu(context):
    """The main command handler."""
    _ensure_dir(PLUGIN_PATH)
    parsed = _parse_command(_get_command_arg(context, yvlu_cmd, 'yvlu'))

    if parsed.subcommand == '_font':
        await yvlu_font_func(context, parsed.subcommand_arg)
        return
    if parsed.subcommand == '_size':
        await yvlu_size_func(context, parsed.subcommand_arg)
        return
    if parsed.subcommand == '_pack':
        await yvlu_pack_func(context, parsed.subcommand_arg)
        return

    if parsed.subcommand == '_debug_font':
        await yvlu_debug_font_func(context, parsed.subcommand_arg)
        return

    try:
        await FontService.ensure_fallback_fonts()
    except Exception:
        await context.edit('错误：初始化字体文件失败，请检查网络或稍后再试。')
        return

    handling = await _get_reply_message_if_needed(context, parsed)
    if await _handle_packadd_toggle_if_needed(context, handling):
        return

    await context.edit('处理中。。。')
    succeeded = await _dispatch_render_request(context, handling)
    if succeeded:
        await context.delete()


# --- Image helpers ---
def _thumbnail_for_sticker(image: Image.Image, size: int = RenderConfig.STICKER_THUMBNAIL_SIZE) -> Image.Image:
    """Resizes a PIL image to fit within a square of `size`, preserving aspect ratio."""
    image.thumbnail((size, size))
    return image


def _get_entity_display_name(entity: Any) -> str:
    """Returns the human-readable display name for a user/chat entity."""
    if not entity:
        return ""
    if hasattr(entity, 'title'):
        return (entity.title or '').strip()
    first_name = getattr(entity, 'first_name', '') or ''
    last_name = getattr(entity, 'last_name', '') or ''
    return f"{first_name.strip()} {last_name.strip()}".strip()


async def _resolve_forward_source_name(client, forward_info: Any, default: str = "Deleted Account") -> str:
    """Resolves the display name for a forwarded message source."""
    if not forward_info:
        return default

    from_id = getattr(forward_info, 'from_id', None)
    if from_id:
        try:
            source_entity = await client.get_entity(from_id)
            resolved_name = _get_entity_display_name(source_entity)
            if resolved_name:
                return resolved_name
        except Exception:
            pass

    return getattr(forward_info, 'from_name', None) or default


async def _resolve_message_identity(client, message: Any) -> MessageIdentity:
    """Resolves the effective display name and color identity for a message sender/forward source."""
    if not message:
        return MessageIdentity(display_name="", color_id=None)

    try:
        forward_info = getattr(message, 'forward', None)
        if forward_info and (getattr(forward_info, 'from_id', None) or getattr(forward_info, 'from_name', None)):
            from_id = getattr(forward_info, 'from_id', None)
            if from_id:
                try:
                    sender_entity = await client.get_entity(from_id)
                    return MessageIdentity(
                        display_name=_get_entity_display_name(sender_entity),
                        color_id=int(sender_entity.id),
                    )
                except Exception:
                    pass
            forward_name = getattr(forward_info, 'from_name', '') or ''
            return MessageIdentity(display_name=forward_name, color_id=hash(forward_name) if forward_name else None)

        sender_entity = await message.get_sender()
        if not sender_entity:
            return MessageIdentity(display_name="", color_id=None)
        return MessageIdentity(
            display_name=_get_entity_display_name(sender_entity),
            color_id=int(sender_entity.id),
        )
    except Exception:
        return MessageIdentity(display_name="", color_id=None)


def _build_reply_preview_from_message(message: Any, identity: MessageIdentity) -> ReplyPreview:
    """Builds reply preview data from a message object and resolved identity."""
    return _build_reply_preview(
        name=identity.display_name or None,
        text=getattr(message, 'message', None) or '',
        entities=_telethon_entities_to_dicts(getattr(message, 'entities', None)),
        chat_id=identity.color_id,
    )


def _image_to_sticker_file(image: Image.Image, lossless: bool = True, method: int = 2) -> BytesIO:
    """Converts a PIL image into a Telegram sticker file."""
    sticker_image = _thumbnail_for_sticker(image.copy())
    file = BytesIO()
    file.name = "sticker.webp"
    sticker_image.save(file, "WEBP", lossless=lossless, method=method)
    file.seek(0)
    return file


async def _run_generator(**kwargs) -> Tuple[BytesIO, int]:
    """Runs the synchronous rendering pipeline in the executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(yv_lu_generate, **kwargs))


def _extract_message_entities_after_prefix(message: Any) -> Tuple[List[Dict[str, Any]], int]:
    """Extracts message entities and shifts offsets to exclude the command prefix."""
    raw_text = getattr(message, 'raw_text', '') or ''
    prefix_text, sep, _ = raw_text.partition(' ')
    prefix_units = _utf16_length(prefix_text + sep)
    full_entities = _telethon_entities_to_dicts(getattr(message, 'entities', None))
    adjusted_entities = [
        {**entity, 'offset': max(0, entity['offset'] - prefix_units)}
        for entity in full_entities
        if entity['offset'] >= prefix_units
    ]
    return adjusted_entities, prefix_units


def _parse_reply_command_options(arg: str) -> ReplyCommandOptions:
    """Parses reply-mode options such as count and force-add flag."""
    parts = arg.split()
    count = 1
    for part in parts:
        if part.isdigit():
            count = int(part)
            break
    return ReplyCommandOptions(count=count, force_add='_packadd' in parts)


async def _render_and_send_generated_sticker(context, reply_to: Optional[int], force_add: bool = False, **generator_kwargs) -> None:
    """Runs the renderer, converts the result into a sticker, and sends it."""
    image_bytes, _ = await _run_generator(**generator_kwargs)
    file = _image_to_sticker_file(Image.open(image_bytes), lossless=True, method=2)
    await _send_sticker_file(context, file, reply_to, force_add=force_add)


async def _get_reply_message_if_needed(context, parsed: ParsedCommand) -> MessageHandlingContext:
    """Loads the replied message once for downstream routing."""
    reply_message = await context.get_reply_message()
    return MessageHandlingContext(parsed=parsed, reply_message=reply_message)


async def _handle_packadd_toggle_if_needed(context, handling: MessageHandlingContext) -> bool:
    """Handles `_packadd` as a toggle when there is no replied message."""
    if handling.parsed.subcommand == '_packadd' and not handling.reply_message:
        await yvlu_packadd_func(context)
        return True
    return False


async def _dispatch_render_request(context, handling: MessageHandlingContext) -> bool:
    """Dispatches the main render request after command preprocessing."""
    parsed = handling.parsed
    if parsed.subcommand == '_fake':
        return await _handle_fake_message(context, parsed.subcommand_arg, parsed.theme)
    if handling.reply_message:
        return await _handle_reply_message(context, handling.reply_message, parsed.raw_arg, parsed.theme)
    return await _handle_direct_message(context, parsed.raw_arg, parsed.theme)


async def _build_forward_info_image(client, message: Any, theme: str, user_id_for_color: int) -> Optional[Image.Image]:
    """Builds the forwarded-from info image for a message when applicable."""
    if not getattr(message, 'forward', None):
        return None
    profile = _build_render_scale_profile()
    theme_config = _get_theme_configuration(theme, user_id_for_color)
    forward_source_name = await _resolve_forward_source_name(client, message.forward)
    forward_font_size = profile.forward_font_size
    forward_font = FontService.font(PLUGIN_PATH, forward_font_size, bold=False)
    return _render_rich_text(
        text=f"Forwarded from {forward_source_name}",
        entities=[],
        font_base=forward_font,
        font_color=theme_config.name_rgb,
        accent_color=theme_config.accent_rgb,
        max_width=profile.max_content_width,
        font_size=forward_font_size,
    )


async def _resolve_reply_preview_for_message(client, message: Any) -> ReplyPreview:
    """Loads and resolves reply preview data for a message if it is replying to another message."""
    if not getattr(message, 'reply_to_msg_id', None):
        return _build_reply_preview()
    reply_msg = await message.get_reply_message()
    if not reply_msg:
        return _build_reply_preview()
    return _build_reply_preview_from_message(reply_msg, await _resolve_message_identity(client, reply_msg))


async def _prepare_render_sequence(client, messages: List[Any], theme: str) -> List[MessageRenderData]:
    """Prepares render-data sequence with compact sender suppression for consecutive messages."""
    prepare_tasks = []
    previous_author_id = None
    for message in messages:
        current_author_id = await _get_effective_author_id(client, message)
        suppress_sender = bool(
            current_author_id is not None
            and previous_author_id is not None
            and current_author_id == previous_author_id
        )
        prepare_tasks.append(_prepare_message_render_data(client, message, theme, suppress_sender=suppress_sender))
        previous_author_id = current_author_id
    return [
        result
        for result in await asyncio.gather(*prepare_tasks, return_exceptions=True)
        if isinstance(result, MessageRenderData)
    ]


async def _render_sequence_images(render_data_list: List[MessageRenderData], force_avatar_space: bool, content_width: Optional[int] = None) -> List[Tuple[Image.Image, int]]:
    """Renders a sequence of prepared messages with optional uniform content width."""
    render_tasks = [
        _render_message_image(render_data, content_width=content_width, force_avatar_space=force_avatar_space)
        for render_data in render_data_list
    ]
    return [
        result
        for result in await asyncio.gather(*render_tasks, return_exceptions=True)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], Image.Image)
    ]


async def _collect_message_batch(context, start_message, count: int) -> List[Any]:
    """Collects a contiguous batch of messages starting from the replied message."""
    messages = [start_message]
    if count <= 1:
        return messages
    more_messages = await context.client.get_messages(context.chat_id, min_id=start_message.id, limit=count - 1, reverse=True)
    if more_messages:
        messages.extend(list(more_messages))
    return messages


async def _send_sticker_file(context, file: BytesIO, reply_to: Optional[int], force_add: bool = False) -> None:
    """Sends a sticker file and optionally adds it to the configured sticker pack."""
    await context.client.send_file(context.chat_id, file, force_document=False, reply_to=reply_to)
    if force_add or Settings.is_auto_add_sticker_enabled():
        file.seek(0)
        await _add_sticker_to_set_func(context.client, file, context)


async def _build_sender_visuals(client, entity: Any, theme: str, user_id_for_color: int, include_avatar: bool = True) -> SenderAssets:
    """Fetches sender assets and derives the rendered name label."""
    if not entity:
        return SenderAssets(display_name="", avatar_bytes=None, badge_img=None, name_img=None)

    display_name = _get_entity_display_name(entity)
    avatar_bytes = BytesIO() if include_avatar else None
    avatar_task = client.download_profile_photo(entity.id, file=avatar_bytes, download_big=True) if include_avatar and avatar_bytes else _placeholder_task()
    badge_task = MediaService.load_user_badge_image(client, entity)
    avatar_result, badge_img = await asyncio.gather(avatar_task, badge_task, return_exceptions=True)

    if isinstance(avatar_result, Exception) or not avatar_bytes or avatar_bytes.getbuffer().nbytes <= 0:
        avatar_bytes = None
    if isinstance(badge_img, Exception):
        badge_img = None
    return SenderAssets(
        display_name=display_name,
        avatar_bytes=avatar_bytes,
        badge_img=badge_img,
        name_img=_build_name_image(display_name, theme, user_id_for_color, badge_img=badge_img) if display_name else None,
    )


async def _render_sender_message(
    context,
    *,
    text: str,
    theme: str,
    user_id_for_color: int,
    sender_name: str,
    avatar_bytes: Optional[BytesIO],
    name_img: Optional[Image.Image],
    reply_to: Optional[int],
    entities: Optional[List[Dict[str, Any]]] = None,
    custom_emoji_images: Optional[Dict[int, Image.Image]] = None,
    force_add: bool = False,
) -> None:
    """Renders a sender-scoped message and sends it as a sticker."""
    await _render_and_send_generated_sticker(
        context,
        reply_to,
        force_add=force_add,
        text=text,
        avatar_bytes=avatar_bytes,
        sender_name=sender_name,
        entities=entities or [],
        user_id_for_color=user_id_for_color,
        custom_emoji_images=custom_emoji_images or {},
        theme=theme,
        name_img=name_img,
    )


async def _handle_fake_message(context, content: str, theme: str) -> bool:
    """Handles the '_fake' message generation."""
    reply_message = await context.get_reply_message()
    if not reply_message:
        await context.edit('请回复一条消息来使用 `_fake` 功能。')
        return False
    if not content:
        await context.edit(f'用法: `-{yvlu_cmd} _fake <要伪造的消息内容>`')
        return False
    # Determine the target user from the replied message (including forwards)
    target_user, name = None, None
    forward_from_id = getattr(
        getattr(reply_message, 'forward', None), 'from_id', None)
    forward_from_name = getattr(
        getattr(reply_message, 'forward', None), 'from_name', None)
    if forward_from_id:
        try:
            target_user = await context.client.get_entity(forward_from_id)
        except Exception:
            target_user = None
            if forward_from_name:
                name = forward_from_name
    elif forward_from_name:
        name = forward_from_name
    else:
        target_user = await reply_message.get_sender()
    if target_user:
        user_id_for_color = int(target_user.id)
        sender_assets = await _build_sender_visuals(context.client, target_user, theme, user_id_for_color)
        name = sender_assets.display_name
        avatar_bytes = sender_assets.avatar_bytes
        name_img = sender_assets.name_img
    else:
        user_id_for_color = hash(name or '')
        avatar_bytes = None
        name_img = None

    await _render_sender_message(
        context,
        text=content,
        theme=theme,
        user_id_for_color=user_id_for_color,
        sender_name=name or "",
        avatar_bytes=avatar_bytes,
        name_img=name_img,
        reply_to=context.message.reply_to_msg_id,
    )
    return True


async def _handle_direct_message(context, content: str, theme: str) -> bool:
    """Handles generating a quote from the user's own message."""
    if not content:
        await context.edit('你需要回复一条消息，或在命令后提供文本。')
        return False
    self_id = getattr(context, 'sender_id', None) or getattr(
        context.message, 'sender_id', None)
    target_user = await context.client.get_entity(self_id)
    
    user_id_for_color = int(target_user.id)
    sender_assets = await _build_sender_visuals(context.client, target_user, theme, user_id_for_color)

    adjusted_entities, prefix_units = _extract_message_entities_after_prefix(context.message)
    custom_emoji_images = await MediaService.fetch_and_cache_custom_emojis(context.client, context.message, prefix_units)

    await _render_sender_message(
        context,
        text=content,
        theme=theme,
        user_id_for_color=int(target_user.id),
        sender_name=sender_assets.display_name,
        avatar_bytes=sender_assets.avatar_bytes,
        name_img=sender_assets.name_img,
        reply_to=getattr(context.message, 'reply_to_msg_id', None),
        entities=adjusted_entities,
        custom_emoji_images=custom_emoji_images,
    )
    return True


async def _handle_reply_message(context, reply_message, arg: str, theme: str) -> bool:
    """Handles generating a quote by replying to a message."""
    options = _parse_reply_command_options(arg)
    return await _process_message_sequence(context, reply_message, options.count, theme, force_add=options.force_add)
        

    
async def _placeholder_task():
    """No-op coroutine used in conditional `asyncio.gather` calls."""
    return None
    
    
    
async def _prepare_message_render_data(client, message, theme: str, suppress_sender: bool = False) -> MessageRenderData:
    """Collects all assets needed to render one message."""
    main_user = await message.get_sender()

    media_task = MediaService.download_static_media_preview(client, message.media)
    emoji_task = MediaService.fetch_and_cache_custom_emojis(client, message)

    user_id_for_color = int(main_user.id) if main_user else 0
    name_text_to_render = ""
    if not suppress_sender:
        name_text_to_render = _get_entity_display_name(main_user)
    forward_info_img = await _build_forward_info_image(client, message, theme, user_id_for_color) if not suppress_sender else None

    sender_task = MediaService.fetch_sender_assets(client, main_user, theme, user_id_for_color, include_avatar=not suppress_sender) if main_user and not suppress_sender else _placeholder_task()
    results = await asyncio.gather(sender_task, media_task, emoji_task, return_exceptions=True)
    sender_assets = results[0] if isinstance(results[0], SenderAssets) else SenderAssets("", None, None, None)
    avatar_bytes = sender_assets.avatar_bytes
    media_bytes = results[1] if isinstance(results[1], BytesIO) else None
    custom_emoji_images = results[2] if not isinstance(results[2], Exception) else {}
    name_img = sender_assets.name_img if name_text_to_render else None

    msg_text = message.message or ''
    entities = _telethon_entities_to_dicts(getattr(message, 'entities', None))
    reply_preview = await _resolve_reply_preview_for_message(client, message)

    return MessageRenderData(
        text=msg_text,
        sender_name=name_text_to_render,
        avatar_bytes=avatar_bytes,
        media_bytes=media_bytes,
        entities=entities,
        reply_preview=reply_preview,
        user_id_for_color=user_id_for_color,
        custom_emoji_images=custom_emoji_images,
        suppress_sender=suppress_sender,
        theme=theme,
        name_img=name_img,
        forward_info_img=forward_info_img,
    )


async def _render_message_image(render_data: MessageRenderData, content_width: Optional[int] = None, force_avatar_space: bool = False) -> Tuple[Optional[Image.Image], int]:
    """Renders one message from prepared render data."""

    image_bytes, calculated_width = await _run_generator(
        text=render_data.text,
        sender_name=render_data.sender_name,
        avatar_bytes=render_data.avatar_bytes,
        media_bytes=render_data.media_bytes,
        entities=render_data.entities,
        reply_name=render_data.reply_preview.name,
        reply_text=render_data.reply_preview.text,
        reply_entities=render_data.reply_preview.entities,
        reply_chat_id=render_data.reply_preview.chat_id,
        user_id_for_color=render_data.user_id_for_color,
        custom_emoji_images=render_data.custom_emoji_images,
        suppress_sender=render_data.suppress_sender,
        theme=render_data.theme,
        content_width=content_width,
        force_avatar_space=force_avatar_space,
        name_img=render_data.name_img,
        forward_info_img=render_data.forward_info_img,
    )
    
    if image_bytes:
        return Image.open(image_bytes), calculated_width
    return None, 0
  

async def _get_effective_author_id(client, message):
    """
    Returns a stable author identity for compact multi-message layout.
    """
    # Prefer the original forward source when available.
    if message.forward and getattr(message.forward, 'from_id', None):
        try:
            return utils.get_peer_id(message.forward.from_id)
        except Exception:
            pass
            
    # Fall back to a stable hash for named forward sources.
    if message.forward and getattr(message.forward, 'from_name', None):
        return hash(message.forward.from_name)

    # Otherwise use the direct sender id.
    return getattr(message, 'sender_id', None)


async def _render_message_sequence(client, messages: List[Any], theme: str) -> List[Image.Image]:
    """Renders a message sequence into one or more PIL images with unified width."""
    if not messages:
        return []
    should_force_avatar_space = len(messages) > 1
    render_data_list = await _prepare_render_sequence(client, messages, theme)
    if not render_data_list:
        return []

    measured_images = await _render_sequence_images(render_data_list, force_avatar_space=should_force_avatar_space)
    if not measured_images:
        return []

    uniform_content_width = max(width for _, width in measured_images)
    return [
        image for image, _ in await _render_sequence_images(
            render_data_list,
            force_avatar_space=should_force_avatar_space,
            content_width=uniform_content_width,
        )
    ]


async def _process_message_sequence(context, start_message, count: int, theme: str, force_add: bool = False) -> bool:
    """Collects, renders, and sends a single-message or multi-message sequence."""
    messages = await _collect_message_batch(context, start_message, count)
    rendered_images = await _render_message_sequence(context.client, messages, theme)
    if not rendered_images:
        await context.edit('生成失败：没有可输出的图像。')
        return False
    final_image = rendered_images[0] if len(rendered_images) == 1 else _combine_images(rendered_images)
    file = _image_to_sticker_file(final_image, lossless=True, method=2)
    await _send_sticker_file(context, file, context.message.reply_to_msg_id, force_add=force_add)
    return True


def _combine_images(images: List[Image.Image]) -> Image.Image:
    """Combines multiple images vertically."""
    if not images:
        return Image.new('RGBA', (1, 1), (0, 0, 0, 0))
    gap = RenderConfig.COMBINED_IMAGE_GAP
    max_width = max(img.width for img in images)
    total_height = sum(img.height for img in images) + gap * (len(images) - 1)
    combined_image = Image.new('RGBA', (max_width, total_height), (0, 0, 0, 0))
    y_offset = 0
    for img in images:
        x_offset = (max_width - img.width) // 2
        combined_image.paste(img, (x_offset, y_offset),
                             img if img.mode == 'RGBA' else None)
        y_offset += img.height + gap
    return combined_image


async def yvlu_debug_font_func(context, arg):
    """Handler for the '_debug_font' subcommand for diagnostics."""
    if not arg:
        await context.edit(f'用法：`-{yvlu_cmd} _debug_font <字体文件名>`\n例如：`-{yvlu_cmd} _debug_font RobotoFlex[...].ttf`')
        return

    from fontTools.ttLib import TTFont

    font_path = f"{PLUGIN_PATH}{arg}"
    if not exists(font_path):
        await context.edit(f'错误：在 `data/yvlu/` 目录下找不到字体文件 `{arg}`。')
        return

    try:
        font = TTFont(font_path)
        output = f"🔍 **字体文件诊断报告:**\n`{arg}`\n\n"

        if 'fvar' in font:
            output += "**这是一个可变字体 (Variable Font)。**\n\n"
            output += "**支持的调节轴:**\n"
            for axis in font['fvar'].axes:
                output += (
                    f"  - **轴名称:** `{axis.axisTag}`\n"
                    f"    - 最小值: `{axis.minValue}`\n"
                    f"    - 默认值: `{axis.defaultValue}`\n"
                    f"    - 最大值: `{axis.maxValue}`\n"
                )
        else:
            output += "**这是一个静态字体 (Static Font)，不支持轴调节。**"

        await context.edit(output)

    except Exception as e:
        await context.edit(f"诊断时发生错误: {e}")
