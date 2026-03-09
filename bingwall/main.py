import secrets

from os import sep

from pagermaid.dependence import client
from pagermaid.enums import Message
from pagermaid.listener import listener
from pagermaid.utils import safe_remove


async def get_wallpaper_url(num):
    json_url = f"https://www.bing.com/HPImageArchive.aspx?format=js&mkt=zh-CN&n=1&idx={str(num)}"
    req = await client.get(json_url)
    urls = []
    copy_right = ""
    if req.status_code == 200:
        data = req.json()
        image = data["images"][0]
        url_base = image.get("urlbase", "")
        if url_base:
            for resolution in ("UHD", "1920x1080"):
                urls.append(f"https://www.bing.com{url_base}_{resolution}.jpg")
        if image.get("url"):
            urls.append(f"https://www.bing.com{image['url']}")
        copy_right = image["copyright"]
    return urls, copy_right


@listener(command="bingwall", description="获取Bing每日壁纸（带任意参数以原图文件形式发送）")
async def bingwall(message: Message):
    status = False
    filename = f"data{sep}wallpaper.jpg"
    for _ in range(3):
        num = secrets.choice(range(7))
        image_urls, copy_right = await get_wallpaper_url(num)
        try:
            if not image_urls:
                continue
            for image_url in image_urls:
                img = await client.get(image_url)
                if img.status_code != 200:
                    continue
                with open(filename, "wb") as f:
                    f.write(img.content)
                await message.client.send_file(
                    message.chat_id,
                    filename,
                    caption=f"#bing wallpaper\n{copy_right}",
                    force_document=bool(message.arguments),
                )
                status = True
                break  # 成功了就赶紧结束啦！
            if status:
                break
        except Exception:
            continue
    safe_remove(filename)
    if not status:
        return await message.edit("出错了呜呜呜 ~ 试了好多好多次都无法访问到服务器 。")
    await message.safe_delete()
