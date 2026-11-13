import re
import asyncio
import difflib
import urllib.parse
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import log
from utils.speech import MAX_AUDIO_SECONDS, SpeechError, SpeechTranscriber

# ==========================================
# TRANSLATION SERVICE
# ==========================================

# Google Translate's keyless web endpoint. It needs no API key or account, but it is
# not an officially documented API, so every failure mode is reported to the user.
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# The text travels in the query string, so long messages are sent in several requests.
# Measured after URL-encoding, where one non-Latin character can take up to 9 bytes.
MAX_ENCODED_CHUNK = 6000

EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_LIMIT = 1024
HISTORY_SCAN_LIMIT = 20

# Voice messages and audio attachments are transcribed locally, then translated like text.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_EXTENSIONS = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".wma", ".webm")


class TranslationError(Exception):
    """Raised with a message that is safe to show to the user."""


# ==========================================
# LANGUAGE TABLE
# ==========================================

LANGUAGES: Dict[str, str] = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "hy": "Armenian",
    "az": "Azerbaijani", "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bs": "Bosnian",
    "bg": "Bulgarian", "ca": "Catalan", "ceb": "Cebuano", "ny": "Chichewa",
    "zh-CN": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)", "co": "Corsican",
    "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch", "en": "English",
    "eo": "Esperanto", "et": "Estonian", "tl": "Filipino", "fi": "Finnish", "fr": "French",
    "fy": "Frisian", "gl": "Galician", "ka": "Georgian", "de": "German", "el": "Greek",
    "gu": "Gujarati", "ht": "Haitian Creole", "ha": "Hausa", "haw": "Hawaiian", "iw": "Hebrew",
    "hi": "Hindi", "hmn": "Hmong", "hu": "Hungarian", "is": "Icelandic", "ig": "Igbo",
    "id": "Indonesian", "ga": "Irish", "it": "Italian", "ja": "Japanese", "jw": "Javanese",
    "kn": "Kannada", "kk": "Kazakh", "km": "Khmer", "rw": "Kinyarwanda", "ko": "Korean",
    "ku": "Kurdish", "ky": "Kyrgyz", "lo": "Lao", "la": "Latin", "lv": "Latvian",
    "lt": "Lithuanian", "lb": "Luxembourgish", "mk": "Macedonian", "mg": "Malagasy",
    "ms": "Malay", "ml": "Malayalam", "mt": "Maltese", "mi": "Maori", "mr": "Marathi",
    "mn": "Mongolian", "my": "Burmese", "ne": "Nepali", "no": "Norwegian", "or": "Odia",
    "ps": "Pashto", "fa": "Persian", "pl": "Polish", "pt": "Portuguese", "pa": "Punjabi",
    "ro": "Romanian", "ru": "Russian", "sm": "Samoan", "gd": "Scots Gaelic", "sr": "Serbian",
    "st": "Sesotho", "sn": "Shona", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak",
    "sl": "Slovenian", "so": "Somali", "es": "Spanish", "su": "Sundanese", "sw": "Swahili",
    "sv": "Swedish", "tg": "Tajik", "ta": "Tamil", "tt": "Tatar", "te": "Telugu", "th": "Thai",
    "tr": "Turkish", "tk": "Turkmen", "uk": "Ukrainian", "ur": "Urdu", "ug": "Uyghur",
    "uz": "Uzbek", "vi": "Vietnamese", "cy": "Welsh", "xh": "Xhosa", "yi": "Yiddish",
    "yo": "Yoruba", "zu": "Zulu",
}

# Extra names and codes people actually type, mapped onto the table above.
LANGUAGE_ALIASES: Dict[str, str] = {
    "chinese": "zh-CN", "mandarin": "zh-CN", "simplified chinese": "zh-CN", "zh": "zh-CN",
    "zh-hans": "zh-CN", "traditional chinese": "zh-TW", "zh-hant": "zh-TW", "taiwanese": "zh-TW",
    "tagalog": "tl", "fil": "tl", "farsi": "fa", "he": "iw", "jv": "jw", "myanmar": "my",
    "nb": "no", "bokmal": "no", "brazilian": "pt", "brazilian portuguese": "pt",
    "castilian": "es", "flemish": "nl", "gaelic": "gd", "scottish gaelic": "gd",
    "creole": "ht", "haitian": "ht", "oriya": "or", "sinhalese": "si", "slovene": "sl",
    "bangla": "bn", "panjabi": "pa", "pushto": "ps", "kirghiz": "ky", "nyanja": "ny",
    "uighur": "ug", "sotho": "st", "khmer cambodian": "km", "cambodian": "km",
}

# Offered first in autocomplete and in the right-click language picker (max 25).
COMMON_LANGUAGES: Tuple[str, ...] = (
    "en", "es", "fr", "de", "it", "pt", "nl", "ru", "uk", "pl", "sv", "no", "da", "fi",
    "tr", "el", "ar", "iw", "hi", "ja", "ko", "zh-CN", "zh-TW", "vi", "id",
)


def _normalize(value: str) -> str:
    cleaned = re.sub(r"[()_]", " ", value.lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


_CODE_LOOKUP: Dict[str, str] = {code.lower(): code for code in LANGUAGES}
_NAME_LOOKUP: Dict[str, str] = {_normalize(name): code for code, name in LANGUAGES.items()}
_NAME_LOOKUP.update(LANGUAGE_ALIASES)


def language_name(code: str) -> str:
    """Human-readable name for a language code, including codes the service reports back."""
    key = (code or "").strip().lower()
    known = _CODE_LOOKUP.get(key) or _NAME_LOOKUP.get(key)
    if known:
        return LANGUAGES[known]
    return code or "Unknown"


def resolve_language(query: str, fuzzy: bool = True) -> Optional[str]:
    """Turn user input ('es', 'Spanish', 'zh_tw', 'spansh') into a supported language code."""
    key = _normalize(query or "")
    if not key:
        return None

    code = _CODE_LOOKUP.get(key.replace(" ", "-")) or _NAME_LOOKUP.get(key)
    if code or not fuzzy or len(key) < 4:
        return code

    close = difflib.get_close_matches(key, list(_NAME_LOOKUP), n=1, cutoff=0.8)
    return _NAME_LOOKUP[close[0]] if close else None


def suggest_languages(query: str, limit: int = 3) -> List[str]:
    """Names of supported languages that look similar to a query that did not resolve."""
    close = difflib.get_close_matches(_normalize(query or ""), list(_NAME_LOOKUP), n=limit * 2, cutoff=0.6)
    names: List[str] = []
    for match in close:
        name = LANGUAGES[_NAME_LOOKUP[match]]
        if name not in names:
            names.append(name)
    return names[:limit]


def split_language_and_text(language: str, text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Separate the language from the text in a prefix invocation.

    discord.py hands over the first word as the language and everything after it as text,
    which breaks two-word languages (`!translate haitian creole`). The longest leading run
    of words that names a language exactly wins; otherwise the first word is matched loosely.
    """
    rest = (text or "").strip()
    if rest:
        words = rest.split(None, 2)
        for count in (2, 1):
            if len(words) < count:
                continue
            code = resolve_language(" ".join([language] + words[:count]), fuzzy=False)
            if code:
                remaining = rest.split(None, count)
                return code, (remaining[count] if len(remaining) > count else None)

    return resolve_language(language), (rest or None)


def locale_to_language(locale: object) -> str:
    """Map a Discord client locale (e.g. `en-US`, `es-419`, `zh-TW`) to a language code."""
    value = str(getattr(locale, "value", locale) or "")
    return resolve_language(value, fuzzy=False) or resolve_language(value.split("-")[0], fuzzy=False) or "en"


# ==========================================
# TEXT HELPERS
# ==========================================

def _encoded_len(text: str) -> int:
    return len(urllib.parse.quote(text, safe=""))


# Preferred places to cut a long message, best first: line breaks, sentence ends, spaces.
_SPLITTERS = (
    re.compile(r"(?<=\n)"),
    re.compile(r"(?<=[.!?。！？…])"),
    re.compile(r"(?<=\s)"),
)


def split_text(text: str, limit: int = MAX_ENCODED_CHUNK, _level: int = 0) -> List[str]:
    """Split text into pieces that each fit in one request. Joining them restores the input."""
    if _encoded_len(text) <= limit:
        return [text]

    if _level >= len(_SPLITTERS):
        pieces: List[str] = list(text)
    else:
        pieces = [piece for piece in _SPLITTERS[_level].split(text) if piece]

    chunks: List[str] = []
    current = ""
    for piece in pieces:
        if _encoded_len(current + piece) <= limit:
            current += piece
            continue
        if current:
            chunks.append(current)
            current = ""
        if _encoded_len(piece) <= limit:
            current = piece
        else:
            nested = split_text(piece, limit, _level + 1)
            chunks.extend(nested[:-1])
            current = nested[-1]
    if current:
        chunks.append(current)
    return chunks


def extract_text(message: discord.Message) -> str:
    """Pull the translatable text out of a message: its content, a forward, or its embeds."""
    content = (message.clean_content or "").strip()
    if content:
        return content

    for snapshot in getattr(message, "message_snapshots", None) or []:
        forwarded = (getattr(snapshot, "content", "") or "").strip()
        if forwarded:
            return forwarded

    parts: List[str] = []
    for embed in message.embeds:
        for value in (embed.title, embed.description):
            if value and value.strip():
                parts.append(value.strip())
    return "\n\n".join(parts)


def find_audio(message: discord.Message, voice_only: bool = False) -> Optional[discord.Attachment]:
    """The voice message on a message (or on a forward), or failing that an audio attachment."""
    attachments: List[discord.Attachment] = list(message.attachments)
    for snapshot in getattr(message, "message_snapshots", None) or []:
        attachments.extend(getattr(snapshot, "attachments", None) or [])

    for attachment in attachments:
        if attachment.is_voice_message():
            return attachment
    if voice_only:
        return None

    for attachment in attachments:
        content_type = (attachment.content_type or "").lower()
        if content_type.startswith("audio/"):
            return attachment
        if not content_type and attachment.filename.lower().endswith(AUDIO_EXTENSIONS):
            return attachment
    return None


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build_translation_embed(
    translated: str,
    source_code: str,
    target_code: str,
    source_message: Optional[discord.Message] = None,
    requester: Optional[discord.abc.User] = None,
    heard: Optional[str] = None,
) -> discord.Embed:
    embed = discord.Embed(description=_truncate(translated, EMBED_DESCRIPTION_LIMIT), color=discord.Color.blue())
    if source_message is not None:
        author = source_message.author
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    else:
        embed.title = "🌐 Translation"

    # For audio, show what was heard so a wrong transcription is easy to spot.
    if heard and heard.strip() != translated.strip():
        embed.add_field(name="🎙️ Heard", value=_truncate(heard, EMBED_FIELD_LIMIT), inline=False)

    footer = f"{language_name(source_code)} → {language_name(target_code)}"
    if requester is not None:
        footer += f" • Requested by {requester.display_name}"
    embed.set_footer(text=footer)
    return embed


# ==========================================
# UI COMPONENTS
# ==========================================

class JumpView(discord.ui.View):
    """A single link button back to the message that was translated."""

    def __init__(self, source_message: discord.Message):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Original message", url=source_message.jump_url))


class LanguageSelect(discord.ui.Select):
    def __init__(self, current: str):
        options = [
            discord.SelectOption(label=LANGUAGES[code], value=code, default=(code == current))
            for code in COMMON_LANGUAGES
        ]
        super().__init__(placeholder="🌐 Translate into another language...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "LanguagePickerView" = self.view
        target = self.values[0]
        await interaction.response.defer()

        try:
            translated, source = await view.cog.translate_text(view.text, target)
        except TranslationError as e:
            return await interaction.followup.send(f"❌ {e}", ephemeral=True)

        for option in self.options:
            option.default = option.value == target
        embed = build_translation_embed(translated, source, target, view.source_message, heard=view.heard)
        await interaction.edit_original_response(embed=embed, view=view)


class LanguagePickerView(discord.ui.View):
    """Shown under a right-click translation so the user can switch the target language."""

    def __init__(
        self,
        cog: "Translate",
        text: str,
        source_message: discord.Message,
        current: str,
        heard: Optional[str] = None,
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.text = text
        self.heard = heard
        self.source_message = source_message
        self.message: Optional[discord.Message] = None
        self.add_item(LanguageSelect(current))
        self.add_item(discord.ui.Button(label="Original message", url=source_message.jump_url))

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            await self.message.edit(view=JumpView(self.source_message))
        except discord.HTTPException:
            pass


# ==========================================
# TRANSLATE COG
# ==========================================

class Translate(commands.Cog):
    """Text and voice message translation by reply, slash command, or the right-click Apps menu."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.transcriber = SpeechTranscriber()
        self._prefetch_task: Optional[asyncio.Task] = None

        # Slash commands cannot be sent as a reply, so the right-click menu is the
        # slash-style way to point at one exact message.
        self.context_menu = app_commands.ContextMenu(name="Translate", callback=self.translate_message_menu)
        self.bot.tree.add_command(self.context_menu)

    async def cog_load(self) -> None:
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        # Fetch the speech model in the background so the first voice message is not slow.
        self._prefetch_task = asyncio.create_task(self.transcriber.prefetch())

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.context_menu.name, type=self.context_menu.type)
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

    # ------------------------------------------------------------------
    # Translation Service
    # ------------------------------------------------------------------
    async def _request_translation(self, text: str, target: str) -> Tuple[str, str]:
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

        params = {"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text}
        try:
            async with self.http_session.get(TRANSLATE_URL, params=params, headers=REQUEST_HEADERS) as resp:
                if resp.status == 429:
                    raise TranslationError("The translation service is rate limiting the bot. Try again in a few minutes.")
                if resp.status != 200:
                    log.warning(f"Translation request failed with HTTP {resp.status}")
                    raise TranslationError(f"The translation service returned an error (HTTP {resp.status}).")
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            raise TranslationError("The translation service took too long to respond. Try again shortly.")
        except aiohttp.ClientError as e:
            log.warning(f"Translation request failed: {e!r}")
            raise TranslationError("Could not reach the translation service. Try again shortly.")
        except ValueError:
            data = None  # Not JSON (for example an HTML block page); reported below.

        # Response shape: [[[translated, original, ...], ...], null, detected_source, ...]
        try:
            translated = "".join(segment[0] for segment in data[0] if segment and segment[0])
            source = data[2] if isinstance(data[2], str) else ""
        except (TypeError, IndexError, KeyError):
            log.warning("Translation service returned an unexpected response shape.")
            raise TranslationError("The translation service sent back a response I could not read.")

        if not translated:
            raise TranslationError("The translation service returned an empty result.")
        return translated, source

    async def translate_text(self, text: str, target: str) -> Tuple[str, str]:
        """Translate text into a language code. Returns (translation, detected source code)."""
        parts: List[str] = []
        detected = ""
        for chunk in split_text(text):
            core = chunk.strip()
            if not core:
                parts.append(chunk)
                continue
            # The service trims edge whitespace, so it is set aside and put back afterwards.
            leading = chunk[:len(chunk) - len(chunk.lstrip())]
            trailing = chunk[len(chunk.rstrip()):]
            translated, source = await self._request_translation(core, target)
            parts.append(leading + translated + trailing)
            detected = detected or source
        return "".join(parts), detected

    # ------------------------------------------------------------------
    # Voice Messages
    # ------------------------------------------------------------------
    async def transcribe_attachment(self, attachment: discord.Attachment) -> str:
        """Speech-to-text for a voice message or audio attachment."""
        limit = f"{MAX_AUDIO_SECONDS // 60} minutes"
        duration = getattr(attachment, "duration", None)
        if duration and duration > MAX_AUDIO_SECONDS:
            raise TranslationError(f"That voice message is too long. I can translate up to {limit} of audio.")
        if attachment.size > MAX_AUDIO_BYTES:
            raise TranslationError(f"That audio file is too large. I can translate up to {limit} of audio.")

        try:
            data = await attachment.read()
        except discord.HTTPException:
            raise TranslationError("I could not download that audio from Discord. Try again shortly.")

        try:
            transcript = await self.transcriber.transcribe(data)
        except SpeechError as e:
            raise TranslationError(str(e))
        return transcript.text

    # ------------------------------------------------------------------
    # Source Message Lookup
    # ------------------------------------------------------------------
    async def _replied_message(self, ctx: commands.Context) -> Optional[discord.Message]:
        reference = ctx.message.reference if ctx.interaction is None else None
        if reference is None or reference.message_id is None:
            return None
        if isinstance(reference.resolved, discord.Message):
            return reference.resolved
        if isinstance(reference.resolved, discord.DeletedReferencedMessage):
            raise TranslationError("The message you replied to has been deleted.")
        try:
            return await ctx.channel.fetch_message(reference.message_id)
        except discord.NotFound:
            raise TranslationError("The message you replied to has been deleted.")
        except discord.HTTPException:
            raise TranslationError("I could not read the message you replied to. I need **Read Message History** here.")

    async def _latest_message(self, ctx: commands.Context) -> Optional[discord.Message]:
        before = ctx.message if ctx.interaction is None else None
        try:
            async for candidate in ctx.channel.history(limit=HISTORY_SCAN_LIMIT, before=before):
                # Skipping the bot's own posts keeps repeated use from re-translating a translation.
                if candidate.author.id == self.bot.user.id:
                    continue
                if extract_text(candidate) or find_audio(candidate, voice_only=True):
                    return candidate
        except discord.HTTPException:
            raise TranslationError("I could not read this channel's recent messages. I need **Read Message History** here.")
        return None

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="translate",
        aliases=["tr"],
        description="Translate the message or voice message you replied to, the latest message, or your own text."
    )
    @app_commands.describe(
        language="Language to translate into (name or code, e.g. Spanish or es)",
        text="Text to translate (leave blank to translate the latest message in this channel)"
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def translate(self, ctx: commands.Context, language: str, *, text: Optional[str] = None):
        ephemeral = ctx.interaction is not None

        if ctx.interaction is not None:
            requested = language
            target, text = resolve_language(language), (text or "").strip() or None
        else:
            requested = " ".join(filter(None, [language, text]))
            target, text = split_language_and_text(language, text)

        if target is None:
            hint = suggest_languages(requested)
            suggestion = f" Did you mean {', '.join(f'**{name}**' for name in hint)}?" if hint else ""
            return await ctx.send(
                f"❌ I don't know the language `{requested[:60]}`.{suggestion}\n"
                "Use a name or code such as `spanish`, `japanese`, `fr`, or `zh-TW`.",
                ephemeral=ephemeral
            )

        source_message: Optional[discord.Message] = None
        audio: Optional[discord.Attachment] = None
        heard: Optional[str] = None
        try:
            if text is None:
                source_message = await self._replied_message(ctx) or await self._latest_message(ctx)
                if source_message is None:
                    return await ctx.send(
                        "❌ I couldn't find a message to translate. Reply to one with `!translate <language>`, "
                        "or right-click it and choose **Apps → Translate**.",
                        ephemeral=ephemeral
                    )
                text = extract_text(source_message)
                if not text:
                    audio = find_audio(source_message)
                    if audio is None:
                        return await ctx.send("❌ That message has no text or voice message to translate.", ephemeral=ephemeral)

            # Shows "typing" for prefix use and defers the response for slash use.
            async with ctx.typing():
                if audio is not None:
                    text = heard = await self.transcribe_attachment(audio)
                translated, source = await self.translate_text(text, target)
        except TranslationError as e:
            return await ctx.send(f"❌ {e}", ephemeral=ephemeral)

        embed = build_translation_embed(translated, source, target, source_message, ctx.author, heard=heard)
        if source_message is None:
            return await ctx.send(embed=embed)

        view = JumpView(source_message)
        if ctx.interaction is None and source_message.channel.id == ctx.channel.id:
            # Attach the translation to the original message so it reads in context.
            try:
                return await ctx.send(
                    embed=embed,
                    view=view,
                    reference=source_message.to_reference(fail_if_not_exists=False),
                    mention_author=False
                )
            except discord.HTTPException:
                pass
        await ctx.send(embed=embed, view=view)

    @translate.autocomplete("language")
    async def language_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        query = _normalize(current)
        if not query:
            codes = list(COMMON_LANGUAGES)
        else:
            matches = [
                (not name.lower().startswith(query), name, code)
                for code, name in LANGUAGES.items()
                if query in _normalize(name) or code.lower().startswith(query)
            ]
            codes = [code for _, _, code in sorted(matches)]
            alias = resolve_language(current, fuzzy=False)
            if alias and alias not in codes:
                codes.insert(0, alias)
        return [app_commands.Choice(name=f"{LANGUAGES[code]} ({code})", value=code) for code in codes[:25]]

    async def translate_message_menu(self, interaction: discord.Interaction, message: discord.Message):
        text = extract_text(message)
        audio = None if text else find_audio(message)
        if not text and audio is None:
            return await interaction.response.send_message(
                "❌ That message has no text or voice message to translate.", ephemeral=True
            )

        # Context menus take no arguments, so start with the user's own Discord language
        # and offer a dropdown to switch.
        target = locale_to_language(interaction.locale)
        await interaction.response.defer(ephemeral=True, thinking=True)

        heard: Optional[str] = None
        try:
            if audio is not None:
                text = heard = await self.transcribe_attachment(audio)
            translated, source = await self.translate_text(text, target)
        except TranslationError as e:
            return await interaction.followup.send(f"❌ {e}", ephemeral=True)

        view = LanguagePickerView(self, text, message, target, heard=heard)
        embed = build_translation_embed(translated, source, target, message, heard=heard)
        view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Translate(bot))
