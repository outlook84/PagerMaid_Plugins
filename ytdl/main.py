import contextlib
import html
import os
import pathlib
import re
import shutil
import time
import traceback
import asyncio
import httpx
import importlib

from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

from pagermaid.enums import Message, AsyncClient
from pagermaid.listener import listener
from pagermaid.services import bot, sqlite as db
from pagermaid.utils import pip_install

dependencies = {
    "yt_dlp": "yt-dlp[curl-cffi]",
    "FastTelethonhelper": "git+https://github.com/outlook84/FastTelethonhelper.git@main",
}

for alias, pip_name in dependencies.items():
    try:
        importlib.import_module(alias)
    except ModuleNotFoundError:
        if "git+" in pip_name:
            pip_install(alias, version=" @ " + pip_name, alias=alias)
        else:
            pip_install(pip_name, alias=alias)

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

try:
    from FastTelethonhelper import fast_upload
except ImportError:
    fast_upload = None

ytdl_is_downloading = False

# Common yt-dlp options
base_opts = {
    "default_search": "ytsearch",
    "geo_bypass": True,
    "nocheckcertificate": True,
    "addmetadata": True,
    "noplaylist": True,
}

SEARCH_CACHE_KEY = "custom.ytdl_search_cache"
SEARCH_RESULT_LIMIT = 5
SEARCH_CACHE_TTL_SECONDS = 10 * 60
MUSIC_POSITIVE_TERMS = ("official", "官方")
MUSIC_NEGATIVE_TERMS = ("live", "cover", "remix", "dj", "舞蹈", "dance", "伴奏")
MUSIC_MAX_DURATION_SECONDS = 10 * 60


def _looks_like_url(value: str) -> bool:
    value = value.strip()
    if re.match(r"^(https?://|www\.)", value, re.I):
        return True
    return bool(re.match(r"^[A-Za-z0-9-]+\.[A-Za-z]{2,}([/:?].*)?$", value))


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return "--:--"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _music_result_score(item: dict) -> int:
    raw_title = item.get("title", "")
    raw_channel = item.get("channel", "")
    title = raw_title.lower()
    channel = raw_channel.lower()
    duration = item.get("duration") or 0
    score = 0
    if any(term in title or term in channel for term in MUSIC_POSITIVE_TERMS if term.isascii()) or any(
        term in raw_title or term in raw_channel for term in MUSIC_POSITIVE_TERMS if not term.isascii()
    ):
        score += 6
    for term in MUSIC_NEGATIVE_TERMS:
        if term in title or term in channel:
            score -= 4
    if duration and duration > MUSIC_MAX_DURATION_SECONDS:
        score -= 8
    if duration and duration > 15 * 60:
        score -= 16
    return score


def _ytdl_search(
    keyword: str,
    limit: int = SEARCH_RESULT_LIMIT,
    music_only: bool = False,
) -> list[dict]:
    opts = {
        **base_opts,
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit * 3}:{keyword}", download=False)

    results = []
    for entry in info.get("entries", []) or []:
        url = entry.get("webpage_url") or entry.get("url")
        if url and not _looks_like_url(url):
            url = f"https://www.youtube.com/watch?v={url}"
        results.append(
            {
                "title": entry.get("title") or "N/A",
                "url": url,
                "duration": entry.get("duration"),
                "channel": entry.get("channel") or entry.get("uploader") or "Unknown",
            }
        )
    results = [item for item in results if item.get("url")]
    if music_only:
        results.sort(
            key=lambda item: (
                -_music_result_score(item),
                (item.get("duration") or 0) > MUSIC_MAX_DURATION_SECONDS,
                item.get("duration") is None,
                abs((item.get("duration") or 0) - 240) if item.get("duration") else 9999,
            )
        )
    return results[:limit]


def _format_search_results(keyword: str, results: list[dict], is_audio: bool) -> str:
    lines = [
        f"搜索结果: <code>{html.escape(keyword)}</code>",
        "",
    ]
    for idx, item in enumerate(results, start=1):
        lines.append(
            f"{idx}. <a href=\"{html.escape(item['url'], quote=True)}\">{html.escape(item['title'])}</a>"
        )
        lines.append(
            f"   时长: <code>{_format_duration(item.get('duration'))}</code> | 频道: <code>{html.escape(item['channel'])}</code>"
        )
    lines.extend(
        [
            "",
            "发送 <code>ytdl 序号</code> 下载视频",
            "发送 <code>ytdl m 序号</code> 下载音频",
        ]
    )
    return "\n".join(lines)


def _get_search_cache() -> dict:
    data = db.get(SEARCH_CACHE_KEY, {})
    if not isinstance(data, dict):
        return {}

    now = int(time.time())
    cleaned = {
        key: value
        for key, value in data.items()
        if isinstance(value, dict)
        and now - int(value.get("updated_at", 0)) <= SEARCH_CACHE_TTL_SECONDS
    }
    if cleaned != data:
        db[SEARCH_CACHE_KEY] = cleaned
    return cleaned


def _save_search_cache(data: dict) -> None:
    db[SEARCH_CACHE_KEY] = data


async def _resolve_cached_result(message: Message, target: str) -> str | None:
    if not target.isdigit():
        return None

    cache = _get_search_cache()
    results = cache.get(str(message.chat_id), {}).get("results", [])

    index = int(target) - 1
    if not results:
        await message.edit("当前会话没有可用的搜索缓存，请先使用关键词搜索。")
        return ""
    if index < 0 or index >= len(results):
        await message.edit(f"序号超出范围，请输入 1 到 {len(results)}。")
        return ""
    return results[index]["url"]


def _should_use_cached_index(target: str, cache: dict, chat_id: int) -> bool:
    return target.isdigit() and bool(cache.get(str(chat_id), {}).get("results"))


def ydv_opts(url: str) -> dict:
    """Get video download options based on URL."""
    opts = {
        **base_opts,
        "merge_output_format": "mp4",
        "outtmpl": "data/ytdl/videos/%(title)s.%(ext)s",
        "postprocessor_args": ["-movflags", "+faststart"],
    }
    if "youtube.com" in url or "youtu.be" in url:
        codec = db.get("custom.ytdl_codec", "avc1")
        opts["format"] = (
            f"bestvideo[vcodec^={codec}]+bestaudio/"
            "bestvideo[vcodec!=av01]+bestaudio/"
            "best[vcodec!=av01]"
        )
    else:
        opts["format"] = "bestvideo+bestaudio/best"
    return opts


ydm_opts = {
    **base_opts,
    "format": "bestaudio[vcodec=none]/best",
    "outtmpl": "data/ytdl/audios/%(title)s.%(ext)s",
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "best",
        }
    ],
}


def _ytdl_download(url: str, message: Message, loop, opts: dict, file_type_zh: str):
    """Download media using yt-dlp."""
    thumb_path = None
    last_edit_time = time.time()

    def progress_hook(d):
        nonlocal last_edit_time
        if d["status"] == "downloading":
            if time.time() - last_edit_time > 10:
                last_edit_time = time.time()
                total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total_bytes:
                    downloaded_bytes = d.get("downloaded_bytes")
                    percentage = downloaded_bytes / total_bytes * 100
                    text = f"📥 正在下载{file_type_zh}... {percentage:.1f}%"
                    asyncio.run_coroutine_threadsafe(message.edit(text), loop)

    opts_local = opts.copy()
    opts_local["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts_local) as ydl:
            info = ydl.extract_info(url, download=True)
            entry_info = info
            if "entries" in info and info["entries"]:
                entry_info = info["entries"][0]

            file_path = entry_info.get("filepath")
            if not file_path or not os.path.exists(file_path):
                # Fallback to scanning the directory
                outtmpl = opts_local["outtmpl"]
                if isinstance(outtmpl, dict):
                    outtmpl = outtmpl.get("default")
                download_dir = pathlib.Path(outtmpl).parent
                downloaded_files = list(download_dir.glob("*.*"))
                if not downloaded_files:
                    raise DownloadError(
                        "Could not determine the path of the downloaded file."
                    )
                # Get the most recently modified file
                file_path = str(max(downloaded_files, key=os.path.getmtime))

            if os.stat(file_path).st_size > 2 * 1024 * 1024 * 1024 * 0.99:
                raise DownloadError("文件太大(超过 2GB),无法发送。")

            title = entry_info.get("title", "N/A")
            duration = entry_info.get("duration")
            width = entry_info.get("width")
            height = entry_info.get("height")
            thumb_url = entry_info.get("thumbnail")
            webpage_url = entry_info.get("webpage_url")

            if thumb_url:
                thumb_path = "data/ytdl/thumb.jpg"
                with contextlib.suppress(Exception):
                    resp = httpx.get(thumb_url)
                    resp.raise_for_status()
                    with open(thumb_path, "wb") as f:
                        f.write(resp.content)
            return file_path, title, thumb_path, duration, width, height, webpage_url
    except (DownloadError, ExtractorError) as e:
        raise e


async def ytdl_common(message: Message, file_type: str, proxy: str = None, url: str = None):
    if not shutil.which("ffmpeg"):
        return await message.edit(
            "本插件需要 `ffmpeg` 才能正常工作，请先安装 `ffmpeg`。", parse_mode="md",
        )
    global ytdl_is_downloading
    if ytdl_is_downloading:
        return await message.edit("有一个下载任务正在运行中，请不要重复使用命令。")
    ytdl_is_downloading = True

    # Create temporary directory for download
    download_path = pathlib.Path("data/ytdl")
    with contextlib.suppress(Exception):
        shutil.rmtree(download_path)
    download_path.mkdir(parents=True, exist_ok=True)

    url = url or message.arguments
    if file_type == "audio":
        opts = ydm_opts.copy()
        file_type_zh = "音频"
    else:
        opts = ydv_opts(url)
        file_type_zh = "视频"
    if proxy:
        opts["proxy"] = proxy
    message: Message = await message.edit(f"📥 正在请求{file_type_zh}...")

    try:
        (
            file_path,
            title,
            thumb_path,
            duration,
            width,
            height,
            webpage_url,
        ) = await bot.loop.run_in_executor(
            None, _ytdl_download, url, message, bot.loop, opts, file_type_zh
        )

        caption = f"<code>{title}</code>"
        if webpage_url:
            caption += f"\n<a href='{webpage_url}'>Original URL</a>"

        attributes = []
        if duration:
            if file_type == "video":
                attributes.append(
                    DocumentAttributeVideo(
                        duration=duration, w=width or 0, h=height or 0
                    )
                )
            else:
                attributes.append(
                    DocumentAttributeAudio(duration=duration, title=title)
                )

        if fast_upload:
            file = await fast_upload(
                bot, file_path, message, os.path.basename(file_path)
            )
            await bot.send_file(
                message.chat_id,
                file,
                thumb=thumb_path,
                caption=caption,
                force_document=False,
                attributes=attributes,
                workers=4,
                parse_mode="html",
                supports_streaming=True,
            )
            await message.delete()
        else:
            await message.edit(f"📤 正在上传{file_type_zh}...")
            last_edit_time = time.time()

            async def progress(current, total):
                nonlocal last_edit_time
                if time.time() - last_edit_time > 10:
                    last_edit_time = time.time()
                    with contextlib.suppress(Exception):
                        await message.edit(
                            f"📤 正在上传{file_type_zh}... {current / total:.2%}"
                        )

            await bot.send_file(
                message.chat_id,
                file_path,
                thumb=thumb_path,
                caption=caption,
                force_document=False,
                attributes=attributes,
                progress_callback=progress,
                workers=4,
                parse_mode="html",
                supports_streaming=True,
            )
            await message.delete()
    except DownloadError as e:
        if "Unsupported URL" in str(e):
            await message.edit("下载失败：不支持的 URL 或该网站暂时无法下载。")
        else:
            await message.edit(
                f"下载/发送文件失败，发生错误：\n<code>{traceback.format_exc()}</code>",
                parse_mode="html",
            )
    except Exception as e:
        await message.edit(
            f"下载/发送文件失败，发生错误：\n<code>{traceback.format_exc()}</code>",
            parse_mode="html",
        )
    finally:
        ytdl_is_downloading = False
        with contextlib.suppress(Exception):
            shutil.rmtree(download_path)


ytdl_help = (
    "**Youtube-dl**\n\n"
    "使用方法: `ytdl [m] <链接/关键词/序号> | _proxy [<url>] | _codec [<codec>] | update`\n\n"
    " - `ytdl <链接>`: 下载视频 (默认)\n"
    " - `ytdl <关键词>`: 搜索 youtube 并返回候选列表（缓存 10 分钟）\n"
    " - `ytdl <序号>`: 下载上一次搜索结果中的对应视频\n"
    " - `ytdl m <链接>`: 下载音频\n"
    " - `ytdl m <关键词>`: 对搜索结果按音乐特征重排后返回候选列表（缓存 10 分钟）\n"
    " - `ytdl m <序号>`: 下载上一次搜索结果中的对应音频\n"
    " - `ytdl _proxy <url>`: 设置 HTTP/SOCKS 代理\n"
    " - `ytdl _proxy`: 删除代理\n"
    " - `ytdl _codec <codec>`: 设置优先选择的 Youtube 视频编码 (默认 avc1, 可选 vp9/av01)\n"
    " - `ytdl _codec`: 删除优先选择的 Youtube 视频编码\n"
    " - `ytdl update`: 更新 yt-dlp"
)


@listener(
    command="ytdl",
    description="从各种网站下载视频或音频。\n\n" + ytdl_help,
    parameters="[m] <链接/关键词/序号> | _proxy [<url>] | _codec [<codec>] | update",
)
async def ytdl(message: Message, client: AsyncClient):
    """
    Downloads videos or audio from various sites.
    - `ytdl <url>`: download video
    - `ytdl <keyword>`: search youtube and list candidates
    - `ytdl <index>`: download from cached search results
    - `ytdl m <url>`: download audio
    - `ytdl m <keyword>`: rank candidates with music-oriented heuristics
    - `ytdl m <index>`: download audio from cached search results
    - `ytdl _proxy <url>`: set HTTP/SOCKS proxy
    - `ytdl _proxy`: delete proxy
    - `ytdl _codec <codec>`: set preferred video codec
    - `ytdl _codec`: reset preferred video codec
    - `ytdl update`: update yt-dlp
    """
    arguments = message.arguments
    if arguments.startswith("_proxy"):
        parts = arguments.split(" ", 1)
        if len(parts) > 1 and parts[1]:
            db["custom.ytdl_proxy"] = parts[1]
            return await message.edit(f"代理已设置为: `{parts[1]}`")
        else:
            proxy = db.get("custom.ytdl_proxy")
            if proxy:
                del db["custom.ytdl_proxy"]
                return await message.edit(f"代理 `{proxy}` 已删除。")
            else:
                return await message.edit("未设置代理。")

    if arguments.startswith("_codec"):
        parts = arguments.split(" ", 1)
        if len(parts) > 1 and parts[1]:
            db["custom.ytdl_codec"] = parts[1]
            return await message.edit(f"Youtube 优先视频编码已设置为: `{parts[1]}`")
        else:
            codec = db.get("custom.ytdl_codec")
            if codec:
                del db["custom.ytdl_codec"]
                return await message.edit(f"Youtube 优先视频编码 `{codec}` 已删除。")
            else:
                return await message.edit("Youtube 未设置优先视频编码。")

    if arguments == "update":
        await ytdl_update(message, client)
        return
    if not arguments:
        return await message.edit(ytdl_help, parse_mode="markdown")

    parts = arguments.split(" ", 1)
    is_audio = parts[0] == "m"

    if is_audio:
        if len(parts) < 2 or not parts[1].strip():
            return await message.edit(ytdl_help, parse_mode="markdown")
        target = parts[1].strip()
        file_type = "audio"
    else:
        target = arguments.strip()
        file_type = "video"

    cache = _get_search_cache()
    cache_key = str(message.chat_id)

    if _should_use_cached_index(target, cache, message.chat_id):
        resolved_url = await _resolve_cached_result(message, target)
        if resolved_url == "":
            return
        if resolved_url is None:
            return await message.edit("请输入链接、关键词，或使用 `ytdl 序号` 选择结果。", parse_mode="markdown")
        message.arguments = resolved_url
    elif _looks_like_url(target):
        message.arguments = target
    else:
        try:
            await message.edit("🔎 正在搜索候选结果...")
            results = await bot.loop.run_in_executor(
                None, _ytdl_search, target, SEARCH_RESULT_LIMIT, is_audio
            )
        except Exception:
            return await message.edit(
                f"搜索失败，发生错误：\n<code>{traceback.format_exc()}</code>",
                parse_mode="html",
            )
        if not results:
            return await message.edit("没有找到可用结果。")
        cache[cache_key] = {
            "keyword": target,
            "results": results,
            "updated_at": int(time.time()),
        }
        _save_search_cache(cache)
        await message.edit(
            _format_search_results(target, results, is_audio),
            parse_mode="html",
            link_preview=False,
        )
        return

    proxy = db.get("custom.ytdl_proxy")
    await ytdl_common(message, file_type, proxy)


async def ytdl_update(message: Message, client: AsyncClient):
    """强制更新 yt-dlp 到最新版本。"""
    await message.edit("正在更新 yt-dlp...")
    try:
        req = await client.get("https://pypi.org/pypi/yt-dlp/json")
        data = req.json()
        latest_version = data["info"]["version"]
    except Exception:
        await message.edit("获取最新版本信息失败，请稍后再试。")
        return
    pip_install("yt-dlp[curl-cffi]", version=f">={latest_version}", alias="a")
    await message.edit(f"yt-dlp 已更新到最新版本：{latest_version}。重启 PagerMaid 后生效。")
