# PagerMaid_Plugins

这个 repo 用于存储 PagerMaid-Modify 的插件。

## 如何上传插件？

欢迎加入 [讨论群](https://t.me/joinchat/FLV4ZFXq9nUFLLe0HDxfQQ) 探讨你的疑问。

> 开始编写 PagerMaid 插件前请确认 repo 没有对应功能的插件。

### pypi 包引入须知

额外不在 PagerMaid-Modify `requirements.txt` 下的包，请通过 `try` 来进行引入，在用户运行命令时判断包是否引入，如未引入则编辑消息提醒用户安装相应的 pypi 包。

代码参照：https://github.com/xtaodada/PagerMaid_Plugins/blob/master/sendat.py

### 调试

使用 `-apt install` 回复你的插件即可进行本地安装，请在本地测试基本无报错后进行下一步。

### 添加文件

您可以使用的文件目录为：
 - `/` 根目录放置 插件的 python 源文件
 - `/插件名/` 子目录放置 插件的资源文件（可选）

### 添加插件到库

您需要参照 `list.json` 的相关格式，在 `list` (`object`) 下 创建一个 `list`

下面是对应参数的介绍：
 - `name` : 插件名
 - `version` : 版本号
 - `section` : 分类
 - `maintainer` : 作者
 - `size` : 插件大小
 - `supported` : 插件是否允许 issue
 - `des-short` : 短介绍（用于 `-apt search`）
 - `des` : 详细介绍（用于 `-apt show`）

## Plugins 文件结构介绍

- 插件名
    - `*.*` : 插件对应的资源文件
- `插件名.py` : 插件源文件
- `version.json` : 通过 `-apt install` 命令安装的插件版本记录文件

## 目前已有的插件

- admin （管理类）
    - `aban` : 封禁管理插件。
- chat （聊天类）
    - `1A2B` : Play a game of 1A2B.
    - `abstract` : 能够将你的语句变得抽象起来。
    - `aff` : 光速发Aff信息。
    - `atadmins` : 一键 AT 本群管理员。
    - `autodel` : 定时删除消息。
    - `base64` : Base64 编码/解码。
    - `bingwall` : 获取 Bing 每日壁纸，带任意参数时以原图文件形式发送。
    - `bf` : 高级备份恢复管理插件。
    - `calculator` : 小型计算器。
    - `clean_member` : 群成员清理插件。
    - `crazy4` : 随机输出 KFC 疯狂星期四文案。
    - `cron` : 定时发送用户自定义消息/命令插件。
    - `da` : 删除所有信息。
    - `dc` : 获取指定用户或当前群组/频道的 DC 服务器。
    - `dictionary` : 查询英语单词的意思。
    - `dme` : 反 TG desktop 防撤回插件。
    - `everyday_en` : 每日英语。
    - `everyday_greet` : 每日问候。
    - `gdl` : 基于 gallery-dl，从各种网站下载图片或视频。
    - `gemini` : Google Gemini AI 插件。
    - `his` : 群成员历史消息记录查询。
    - `jupai` : 举牌小人。
    - `moyu` : 摸鱼日历。
    - `paolu` : 一键跑路（删所有消息并禁言）。
    - `print_official_notifications` : 将收到的官方私聊打印到控制台。
    - `qr` : 二维码相关操作。
    - `repeat` : 消息复读插件。
    - `shift` : 智能消息转发与备份插件。
    - `strx` : 回复贴纸/图片添加到自己的贴纸包。
    - `xjj` : 生成小姐姐视频。
    - `ytdl` : 基于 yt-dlp，从各种网站下载视频或音频。
    - `yvlu` : 将消息渲染为 Telegram 风格的语录贴纸。
- profile （资料类）
    - `autochangename` : 自动更新 last_name 为时间等。
- daily （便民类）
    - `bin` : 查询信用卡 bin 信息。
    - `diss` : 儒雅随和版祖安语录。
    - `ip` : 查询 IP 地址信息。
    - `listusernames` : 列出所有属于自己的公开群组/频道。
    - `news` : 每日新闻、历史上的今天、天天成语、慧语香风、诗歌天地。
    - `resou` : 微博、知乎、抖音实时热搜。
    - `speedtest` : 测试当前服务器网络速度。
    - `tel` : 查询手机号码归属地等信息。
    - `weather` : 查询天气。
    - `whois` : 查询域名whois信息。
