"""
Pagermaid_Telethon group message history query plugin. Plugin by @tom-snow (@caiji_shiwo)
"""
from pagermaid.enums import Client, Message
from pagermaid.listener import listener
from pagermaid.utils import alias_command
from pagermaid.config import Config
from pagermaid.utils.bot_utils import log
from telethon.tl.types import (
    MessageService, MessageMediaPhoto, MessageMediaDocument, MessageMediaContact,
    MessageMediaGeo, MessageMediaVenue, MessageMediaPoll, MessageMediaWebPage,
    MessageMediaDice, MessageMediaGame, DocumentAttributeSticker, DocumentAttributeVideo,
    DocumentAttributeAudio, DocumentAttributeAnimated
)


class HisMsg:
    LANGUAGES = {
        "en": {
            "name": "",
            "arg": "&lt;entity> [-n &lt;num>]",
            "help": "Query the message history of a specified user or channel in the group.\n"
            f"Usage: \n`,{alias_command('his')} &lt;entity> [-n &lt;num>]`"
            "\n&nbsp;&nbsp; <i>entity</i>: Can be a username, user ID, or channel ID (@username or -100...).\n"
            "You can also reply to a message without the <i>entity</i> argument.",
            "processing": f",{alias_command('his')}: Querying...",
            "media": {
                "AUDIO": "[AUDIO]:", "DOCUMENT": "[DOCUMENT]:", "PHOTO": "[PHOTO]:",
                "STICKER": "[STICKER]:", "VIDEO": "[VIDEO]:", "ANIMATION": "[ANIMATION]:",
                "VOICE": "[VOICE]:", "VIDEO_NOTE": "[VIDEO_NOTE]:", "CONTACT": "[CONTACT]:",
                "LOCATION": "[LOCATION]:", "VENUE": "[VENUE]:", "POLL": "[POLL]:",
                "WEB_PAGE": "[WEB_PAGE]:", "DICE": "[DICE]:", "GAME": "[GAME]:",
            },
            "service": {
                "service": "[Service_Message]: ", "PINNED_MESSAGE": "Pinned: ", "NEW_CHAT_TITLE": "New chat title: ",
            },
            "query_success": "Queryed history message. chat_id: {chat_id} entity: {entity}",
        },
        "zh-cn": {
            "help": "查询指定用户或频道身份在群内的发言历史。\n"
            f"使用方法: \n`,{alias_command('his')} &lt;目标> [-n &lt;num>]`"
            "\n&nbsp;&nbsp; <i>目标</i>: 可以是用户名、用户ID、频道用户名或频道ID (-100...)\n"
            "你也可以直接回复一条消息，不带 <i>目标</i> 参数。",
            "processing": f",{alias_command('his')}: 正在查询...",
            "media": {
                "AUDIO": "[音频]:", "DOCUMENT": "[文档]:", "PHOTO": "[图片]:",
                "STICKER": "[贴纸]:", "VIDEO": "[视频]:", "ANIMATION": "[动画表情]:",
                "VOICE": "[语音]:", "VIDEO_NOTE": "[视频备注]:", "CONTACT": "[联系人]:",
                "LOCATION": "[位置]:", "VENUE": "[场地]:", "POLL": "[投票]:",
                "WEB_PAGE": "[网页]:", "DICE": "[骰子]:", "GAME": "[游戏]:",
            },
            "service": {
                "service": "[服务消息]: ", "PINNED_MESSAGE": "置顶了: ", "NEW_CHAT_TITLE": "新的群组名字: ",
            },
            "query_success": "查询历史消息完成. 群组id: {chat_id} 目标: {entity}",
        },
    }
    MAX_COUNT = 30

    def __init__(self):
        try:
            self.lang_dict = self.LANGUAGES[Config.LANGUAGE]
        except:
            self.lang_dict = self.LANGUAGES["en"]

    def lang(self, text: str, default: str = "") -> str:
        res = self.lang_dict.get(text, default)
        if res == "":
            res = text
        return res


his_msg = HisMsg()


@listener(
    command="his",
    groups_only=True,
    need_admin=True,
    description=his_msg.lang("help"),
    parameters=his_msg.lang("arg", "&lt;entity> [-n &lt;num>]"),
)
async def his(bot: Client, message: Message):
    target_entity = ""
    num = 9999999
    chat_id = message.chat_id

    try:
        def parse_entity(arg_str):
            """Tries to convert the argument to an integer (for user/channel IDs), otherwise returns it as a string (for usernames)."""
            try:
                return int(arg_str)
            except ValueError:
                return arg_str

        if len(message.parameter) == 3 and message.parameter[1] == "-n":
            target_entity = parse_entity(message.parameter[0])
            num = int(message.parameter[2])
        elif len(message.parameter) == 1:
            target_entity = parse_entity(message.parameter[0])
        elif len(message.parameter) == 2 and message.is_reply and message.parameter[0] == "-n":
            reply_msg = await message.get_reply_message()
            target_entity = reply_msg.sender_id
            num = int(message.parameter[1])
        elif message.is_reply:
            reply_msg = await message.get_reply_message()
            target_entity = reply_msg.sender_id
        else:
            return await message.edit(his_msg.lang("help"))
    except Exception:
        return await message.edit(his_msg.lang("help"))

    await message.edit(his_msg.lang("processing"))

    base_link_url = ""
    try:
        chat = await bot.get_entity(chat_id)
        if getattr(chat, 'username', None):
            base_link_url = f"https://t.me/{chat.username}/"
        elif getattr(chat, 'megagroup', False):
            base_link_url = f"https://t.me/c/{chat.id}/"
    except Exception as e:
        await log(f"[HIS_ERROR] Could not get chat entity for linking: {e}")

    count = 0
    results = ""
    try:
        async for msg in bot.iter_messages(
            chat_id, limit=min(num, his_msg.MAX_COUNT), from_user=target_entity
        ):
            count += 1
            message_text = msg.text or ""

            if msg.media:
                media_caption = msg.text or ""
                # Media handling logic remains the same
                if isinstance(msg.media, MessageMediaPhoto):
                    message_text = his_msg.lang(
                        "media")["PHOTO"] + media_caption
                elif isinstance(msg.media, MessageMediaDocument):
                    doc = msg.media.document
                    is_video = any(isinstance(attr, DocumentAttributeVideo)
                                   for attr in doc.attributes)
                    is_voice = any(isinstance(attr, DocumentAttributeAudio)
                                   and attr.voice for attr in doc.attributes)
                    is_audio = any(isinstance(attr, DocumentAttributeAudio)
                                   for attr in doc.attributes)
                    is_sticker = any(isinstance(attr, DocumentAttributeSticker)
                                     for attr in doc.attributes)
                    is_animation = any(isinstance(
                        attr, DocumentAttributeAnimated) for attr in doc.attributes)

                    if is_sticker:
                        message_text = his_msg.lang(
                            "media")["STICKER"] + media_caption
                    elif is_animation:
                        message_text = his_msg.lang(
                            "media")["ANIMATION"] + media_caption
                    elif is_video:
                        message_text = his_msg.lang(
                            "media")["VIDEO"] + media_caption
                    elif is_voice:
                        message_text = his_msg.lang(
                            "media")["VOICE"] + media_caption
                    elif is_audio:
                        message_text = his_msg.lang(
                            "media")["AUDIO"] + media_caption
                    else:
                        message_text = his_msg.lang(
                            "media")["DOCUMENT"] + media_caption
                elif isinstance(msg.media, MessageMediaContact):
                    message_text = his_msg.lang(
                        "media")["CONTACT"] + media_caption
                elif isinstance(msg.media, (MessageMediaGeo, MessageMediaVenue)):
                    message_text = his_msg.lang(
                        "media")["LOCATION"] + media_caption
                elif isinstance(msg.media, MessageMediaPoll):
                    message_text = his_msg.lang(
                        "media")["POLL"] + media_caption
                elif isinstance(msg.media, MessageMediaWebPage):
                    message_text = his_msg.lang(
                        "media")["WEB_PAGE"] + media_caption
                elif isinstance(msg.media, MessageMediaDice):
                    message_text = his_msg.lang(
                        "media")["DICE"] + media_caption
                elif isinstance(msg.media, MessageMediaGame):
                    message_text = his_msg.lang(
                        "media")["GAME"] + media_caption

            if isinstance(msg, MessageService):
                action = msg.action
                service_text = action.__class__.__name__.replace(
                    "MessageAction", "")
                message_text = his_msg.lang(
                    "service")["service"] + service_text

            if not message_text:
                message_text = "[Unsupported Message]"

            message_text_display = f"{count}.  {message_text[:20]}..." if len(
                message_text) > 20 else f"{count}. {message_text}"

            if base_link_url:
                message_link = f"{base_link_url}{msg.id}"
                results += f'\n<a href="{message_link}">{message_text_display}</a> \n'
            else:
                results += f'\n{message_text_display}\n'

        if not results:
            return await message.edit("No messages found for this entity.")

        await message.edit(
            f"<b>Message History</b> | <code>{target_entity}</code> | 🔍 \n{results}",
            link_preview=False,
        )
        await log(his_msg.lang("query_success").format(chat_id=chat_id, entity=target_entity))
    except Exception as e:
        await message.edit(f"[HIS_ERROR]: {e}")
        await log(f"[HIS_ERROR]: {e}")
