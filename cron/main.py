"""PagerMaid 插件：定时发送用户自定义消息/命令"""

import json
import contextlib
from dataclasses import dataclass, asdict

from apscheduler.triggers.cron import CronTrigger
from telethon import events

from pagermaid.enums import Message
from pagermaid.hook import Hook
from pagermaid.listener import listener
from pagermaid.services import bot, sqlite, scheduler
from pagermaid.utils import alias_command

SQLITE_KEY = "cron.tasks"


# ── 数据模型 ──────────────────────────────────────────────────────────────

@dataclass
class CronTask:
    task_id: int
    cron_expr: str   # 标准 5 段 cron 表达式 (min hour dom mon dow)
    chat_id: str     # 目标聊天 ID，支持 "me"
    content: str     # 消息内容
    max_runs: int = 0   # 最大执行次数，0 = 无限
    run_count: int = 0  # 已执行次数


# ── 持久化 ────────────────────────────────────────────────────────────────

def load_tasks() -> list[CronTask]:
    raw = sqlite.get(SQLITE_KEY)
    if not raw:
        return []
    try:
        return [CronTask(**item) for item in json.loads(raw)]
    except Exception:
        return []


def save_tasks(tasks: list[CronTask]):
    sqlite[SQLITE_KEY] = json.dumps([asdict(t) for t in tasks], ensure_ascii=False)


def next_task_id(tasks: list[CronTask]) -> int:
    return max((t.task_id for t in tasks), default=0) + 1


# ── 任务执行 ──────────────────────────────────────────────────────────────

def _resolve_chat_id(raw: str):
    """将用户输入的 chat_id 转换为 bot 可用的值"""
    if raw.lower() in ("me", "self", "saved"):
        return "me"
    with contextlib.suppress(ValueError):
        return int(raw)
    return raw


async def _dispatch_to_handlers(msg):
    """手动将消息分发给 PagerMaid 注册的事件处理器
    """
    for callback, event_builder in bot.list_event_handlers():
        if not isinstance(event_builder, events.NewMessage):
            continue
        event = events.NewMessage.Event(msg)
        event._set_client(bot)
        if event_builder.filter(event):
            try:
                await callback(event)
            except events.StopPropagation:
                break
            except Exception:
                pass


async def execute_cron_task(task: CronTask):
    """执行一个定时任务：发送消息并尝试分发给命令处理器"""
    chat = _resolve_chat_id(task.chat_id)
    try:
        msg = await bot.send_message(chat, task.content)
        await _dispatch_to_handlers(msg)
    except Exception as e:
        print(f"[cron] 任务 #{task.task_id} 执行失败: {e}")

    # 更新执行计数，达到上限则自毁
    tasks = load_tasks()
    for t in tasks:
        if t.task_id == task.task_id:
            t.run_count += 1
            if t.max_runs > 0 and t.run_count >= t.max_runs:
                tasks = [x for x in tasks if x.task_id != task.task_id]
                unschedule_task(task.task_id)
            break
    save_tasks(tasks)


# ── Scheduler 管理 ────────────────────────────────────────────────────────

def _job_id(task_id: int) -> str:
    return f"cron_plugin.{task_id}"


def schedule_task(task: CronTask):
    """将任务注册到 APScheduler"""
    job_id = _job_id(task.task_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    try:
        parts = task.cron_expr.split()
        if len(parts) != 5:
            print(f"[cron] 任务 #{task.task_id} cron 表达式无效: {task.cron_expr}")
            return
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
        scheduler.add_job(
            execute_cron_task,
            trigger,
            id=job_id,
            name=f"cron_task_{task.task_id}",
            args=[task],
            replace_existing=True,
        )
    except Exception as e:
        print(f"[cron] 注册任务 #{task.task_id} 失败: {e}")


def unschedule_task(task_id: int):
    """从 APScheduler 移除任务"""
    job_id = _job_id(task_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


# ── 启动恢复 ──────────────────────────────────────────────────────────────

@Hook.load_success()
async def restore_cron_tasks():
    """插件加载完成后，自动恢复所有持久化的定时任务"""
    for task in load_tasks():
        schedule_task(task)


# ── 帮助文本 ──────────────────────────────────────────────────────────────

HELP_TEXT = f"""⏰ **定时任务插件 (cron)**

**添加任务**
`-{alias_command("cron")} add <cron表达式> <chat_id> [n<次数>] <内容>`

cron 表达式为标准 5 段格式：`分 时 日 月 周`
chat\\_id 可以是数字 ID 或 `me`（收藏夹）
内容以 `-` 开头时将作为 PagerMaid 命令执行
可选 `n<次数>` 指定执行次数，达到后任务自动删除

**示例**
`{alias_command("cron")} add 0 8 * * * me 早上好！`
  → 每天 8:00 向收藏夹发送"早上好！"（永久）
`{alias_command("cron")} add */30 * * * * -100123456 -help`
  → 每 30 分钟在指定群组执行 help 命令
`{alias_command("cron")} add 0 9 * * 1-5 me n5 -help`
  → 每个工作日 9:00 执行 help 命令，执行 5 次后任务自毁

**列出任务**
`{alias_command("cron")} list`

**删除任务**
`{alias_command("cron")} del <任务ID>`

**清空任务**
`{alias_command("cron")} clear`
"""


# ── 主命令 ─────────────────────────────────────────────────────────────────

@listener(
    command="cron",
    need_admin=True,
    description=f"定时发送消息/命令\n请使用 {alias_command('cron')} help 查看帮助",
)
async def cron_handler(message: Message):
    params = message.parameter
    if not params or params[0] in ("help", "h"):
        return await message.edit(HELP_TEXT)

    sub_cmd = params[0].lower()

    # ── add ────────────────────────────────────────────────────────────
    if sub_cmd == "add":
        # cron add <m> <h> <dom> <mon> <dow> <chat_id> <content...>
        if len(params) < 8:
            return await message.edit(
                "❌ 参数不足\n"
                f"用法：`{alias_command('cron')} add <分> <时> <日> <月> <周> <chat_id> <内容>`"
            )
        cron_expr = " ".join(params[1:6])
        chat_id = params[6]
        rest = params[7:]

        # 解析可选的 n<次数> 参数
        max_runs = 0
        if rest and rest[0].startswith("n") and rest[0][1:].isdigit():
            max_runs = int(rest[0][1:])
            rest = rest[1:]

        if not rest:
            return await message.edit("❌ 缺少消息内容")
        content = " ".join(rest)

        # 验证 cron 表达式
        try:
            CronTrigger(
                minute=params[1],
                hour=params[2],
                day=params[3],
                month=params[4],
                day_of_week=params[5],
            )
        except Exception as e:
            return await message.edit(f"❌ cron 表达式无效：`{e}`")

        tasks = load_tasks()
        tid = next_task_id(tasks)
        task = CronTask(
            task_id=tid,
            cron_expr=cron_expr,
            chat_id=chat_id,
            content=content,
            max_runs=max_runs,
        )
        tasks.append(task)
        save_tasks(tasks)
        schedule_task(task)

        runs_text = f"{max_runs} 次" if max_runs > 0 else "无限"
        await message.edit(
            f"✅ 定时任务 **#{tid}** 已添加\n\n"
            f"• Cron：`{cron_expr}`\n"
            f"• 目标：`{chat_id}`\n"
            f"• 次数：{runs_text}\n"
            f"• 内容：`{content}`"
        )

    # ── list ───────────────────────────────────────────────────────────
    elif sub_cmd in ("list", "ls", "l"):
        tasks = load_tasks()
        if not tasks:
            return await message.edit("📭 当前没有定时任务")
        lines = ["⏰ **定时任务列表**\n"]
        for t in tasks:
            runs_info = f"{t.run_count}/{t.max_runs}" if t.max_runs > 0 else f"{t.run_count}/∞"
            lines.append(
                f"**#{t.task_id}** | `{t.cron_expr}` → `{t.chat_id}` | 已执行 {runs_info}\n"
                f"  内容：`{t.content}`"
            )
        await message.edit("\n".join(lines))

    # ── del ────────────────────────────────────────────────────────────
    elif sub_cmd in ("del", "rm", "delete", "remove"):
        if len(params) < 2:
            return await message.edit(
                f"❌ 请指定任务 ID\n用法：`-{alias_command('cron')} del <任务ID>`"
            )
        try:
            target_id = int(params[1])
        except ValueError:
            return await message.edit("❌ 任务 ID 必须是数字")

        tasks = load_tasks()
        new_tasks = [t for t in tasks if t.task_id != target_id]
        if len(new_tasks) == len(tasks):
            return await message.edit(f"❌ 未找到任务 **#{target_id}**")

        save_tasks(new_tasks)
        unschedule_task(target_id)
        await message.edit(f"🗑️ 定时任务 **#{target_id}** 已删除")

    # ── clear ──────────────────────────────────────────────────────────
    elif sub_cmd == "clear":
        tasks = load_tasks()
        if not tasks:
            return await message.edit("📭 当前没有定时任务")
        for t in tasks:
            unschedule_task(t.task_id)
        save_tasks([])
        await message.edit(f"🗑️ 已清空全部 **{len(tasks)}** 个定时任务")

    else:
        await message.edit(
            f"❌ 未知子命令：`{sub_cmd}`\n"
            f"使用 `-{alias_command('cron')} help` 查看帮助"
        )
