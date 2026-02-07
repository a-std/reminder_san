"""Discord Bot本体"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from config import (
    DISCORD_BOT_TOKEN,
    REMINDER_CHANNEL_ID,
    SPECIAL_COMMANDS,
    TIMEZONE,
)
from database import (
    close_db,
    create_reminder,
    delete_reminder,
    get_all_active_reminders,
    get_reminder_by_id,
    get_user_reminders,
    init_db,
    update_reminder_content,
    update_reminder_time_by_user,
)
from llm_parser import parse_reminder_input
from scheduler import ReminderScheduler
from utils import WEEKDAY_JA, format_remaining, format_repeat_label, parse_datetime_input

logger = logging.getLogger(__name__)


class ReminderBot(commands.Bot):
    """リマインダーBot"""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.scheduler: ReminderScheduler | None = None
        self.tz = ZoneInfo(TIMEZONE)
        # 専用チャンネルID
        self.reminder_channel_id: int | None = int(REMINDER_CHANNEL_ID) if REMINDER_CHANNEL_ID else None

    async def setup_hook(self):
        """Bot起動時の初期化"""
        await init_db()

        # アクティブなリマインダーのスヌーズViewを復元
        from scheduler import SnoozeView
        active_reminders = await get_all_active_reminders()
        for r in active_reminders:
            is_recurring = bool(r.get("repeat_type") and r["repeat_type"] != "none")
            self.add_view(SnoozeView(r["id"], bot=self, is_recurring=is_recurring))

        self.scheduler = ReminderScheduler(self)
        await self.scheduler.start()

        await self.tree.sync()
        logger.info("スラッシュコマンド同期完了")

    async def on_ready(self):
        """Bot準備完了時"""
        logger.info(f"Botログイン完了: {self.user.name} ({self.user.id})")

        if self.reminder_channel_id:
            channel = self.get_channel(self.reminder_channel_id)
            if channel:
                logger.info(f"専用チャンネル: #{channel.name} ({self.reminder_channel_id})")
            else:
                logger.warning(f"専用チャンネルが見つかりません: {self.reminder_channel_id}")

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="リマインダー",
            )
        )

    async def on_message(self, message: discord.Message):
        """メッセージ受信時"""
        if message.author.bot:
            return

        # 専用チャンネルのメッセージを処理
        if self.reminder_channel_id and message.channel.id == self.reminder_channel_id:
            await self.handle_reminder_message(message)
            return

        await self.process_commands(message)

    async def handle_reminder_message(self, message: discord.Message):
        """専用チャンネルのメッセージを処理"""
        import time
        start_time = time.time()
        logger.info(f"メッセージ受信: {message.content[:50]}")

        content = message.content.strip()

        if not content:
            return

        # 特殊コマンドチェック
        if content in SPECIAL_COMMANDS:
            command = SPECIAL_COMMANDS[content]
            if command == "list":
                await self.show_reminder_list(message)
            return

        # LLMで解析
        try:
            result = await parse_reminder_input(content)
        except Exception as e:
            logger.error(f"解析中にエラー: {e}", exc_info=True)
            await message.reply(
                "解析中にエラーが発生しました。しばらく待ってからお試しください。",
            )
            return

        if not result:
            await message.reply(
                "解析できませんでした。「明日18時に歯医者」のような形式でお試しください。",
            )
            return

        logger.info(f"解析完了: content={result['content']}, datetime={result['datetime']}, 処理時間={time.time()-start_time:.2f}秒")

        # 確認画面を表示
        view = ConfirmReminderView(
            bot=self,
            user_id=str(message.author.id),
            guild_id=str(message.guild.id) if message.guild else None,
            channel_id=str(message.channel.id),
            content=result["content"],
            remind_at=result["datetime"],
            repeat_type=result.get("repeat_type"),
            repeat_value=result.get("repeat_value"),
        )

        embed = view.create_confirm_embed()
        sent = await message.reply(embed=embed, view=view)
        view.message = sent

    async def show_reminder_list(self, message: discord.Message):
        """リマインダー一覧を表示"""
        reminders = await get_user_reminders(str(message.author.id))

        if not reminders:
            await message.reply("登録済みのリマインダーはありません。")
            return

        embed = discord.Embed(
            title="リマインダーリスト",
            color=discord.Color.blue(),
        )

        for r in reminders[:10]:
            remind_at = datetime.fromisoformat(r["remind_at"])
            weekday = WEEKDAY_JA[remind_at.weekday()]
            remaining = format_remaining(remind_at)
            time_str = f"{remind_at.strftime('%m/%d')} ({weekday}) {remind_at.strftime('%H:%M')} - {remaining}"

            if r.get("repeat_type") and r["repeat_type"] != "none":
                time_str += f" ({format_repeat_label(r['repeat_type'], r.get('repeat_value'))})"

            embed.add_field(
                name=r["content"][:50],
                value=time_str,
                inline=False,
            )

        if len(reminders) > 10:
            embed.set_footer(text=f"他 {len(reminders) - 10} 件")

        view = ReminderListView(reminders[:25], str(message.author.id), bot_instance=self)
        sent = await message.reply(embed=embed, view=view)
        view.message = sent

    async def close(self):
        """Bot終了時"""
        try:
            if self.scheduler:
                await self.scheduler.stop()
        except Exception as e:
            logger.error(f"スケジューラ停止エラー: {e}")
        try:
            await close_db()
        except Exception as e:
            logger.error(f"DB クローズエラー: {e}")
        await super().close()


# Botインスタンス
bot = ReminderBot()


class ConfirmReminderView(discord.ui.View):
    """リマインダー確認用View"""

    def __init__(
        self,
        bot: ReminderBot,
        user_id: str,
        guild_id: str | None,
        channel_id: str,
        content: str,
        remind_at: datetime,
        repeat_type: str | None = None,
        repeat_value: str | None = None,
    ):
        super().__init__(timeout=180)
        self.message = None
        self.bot_instance = bot
        self.user_id = user_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.content = content
        self.remind_at = remind_at
        self.repeat_type = repeat_type
        self.repeat_value = repeat_value

    def create_confirm_embed(self) -> discord.Embed:
        """確認用Embedを作成"""
        weekday = WEEKDAY_JA[self.remind_at.weekday()]
        display_content = self.content[:200] + "..." if len(self.content) > 200 else self.content
        remaining = format_remaining(self.remind_at)

        embed = discord.Embed(
            title="リマインダー確認",
            description=display_content,
            color=discord.Color.yellow(),
        )
        embed.add_field(
            name="日時",
            value=f"{self.remind_at.strftime('%Y/%m/%d')} ({weekday}) {self.remind_at.strftime('%H:%M')}（{remaining}）",
            inline=True,
        )

        if self.repeat_type and self.repeat_type != "none":
            embed.add_field(name="繰り返し", value=format_repeat_label(self.repeat_type, self.repeat_value), inline=True)

        embed.set_footer(text="3分以内にボタンを押してください")
        return embed

    @discord.ui.button(label="登録", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        reminder_id = await create_reminder(
            user_id=self.user_id,
            guild_id=self.guild_id,
            channel_id=self.channel_id,
            content=self.content,
            remind_at=self.remind_at,
            repeat_type=self.repeat_type,
            repeat_value=self.repeat_value,
        )

        weekday = WEEKDAY_JA[self.remind_at.weekday()]
        remaining = format_remaining(self.remind_at)

        embed = discord.Embed(
            title="登録完了",
            description=self.content,
            color=discord.Color.green(),
        )
        embed.add_field(
            name="通知日時",
            value=f"{self.remind_at.strftime('%Y/%m/%d')} ({weekday}) {self.remind_at.strftime('%H:%M')}",
            inline=True,
        )
        embed.add_field(name="通知まで", value=remaining, inline=True)

        if self.repeat_type and self.repeat_type != "none":
            embed.add_field(name="繰り返し", value=format_repeat_label(self.repeat_type, self.repeat_value), inline=True)

        embed.set_footer(text="登録しました")

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="日時変更", style=discord.ButtonStyle.primary)
    async def change_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        modal = DateTimeModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        embed = discord.Embed(
            title="キャンセル",
            description=self.content,
            color=discord.Color.light_grey(),
        )

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        """タイムアウト時にボタンを無効化"""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


async def _resolve_datetime(date_str: str, time_str: str) -> datetime | None:
    """日付・時刻文字列からdatetimeを解決する共通処理。
    まず直接パースを試み、失敗したらLLMフォールバック。"""
    result = parse_datetime_input(date_str, time_str)
    if result:
        return result
    # LLMフォールバック
    llm_result = await parse_reminder_input(f"{date_str} {time_str}に予定")
    return llm_result["datetime"] if llm_result else None


class DateTimeModal(discord.ui.Modal, title="日時変更"):
    """日時変更用モーダル"""

    date_input = discord.ui.TextInput(
        label="日付 (例: 2026/01/29 または 明日)",
        placeholder="2026/01/29",
        required=True,
        max_length=20,
    )
    time_input = discord.ui.TextInput(
        label="時刻 (例: 18:00)",
        placeholder="18:00",
        required=True,
        max_length=10,
    )

    def __init__(self, parent_view: ConfirmReminderView):
        super().__init__()
        self.parent_view = parent_view
        self.date_input.default = parent_view.remind_at.strftime("%Y/%m/%d")
        self.time_input.default = parent_view.remind_at.strftime("%H:%M")

    async def on_submit(self, interaction: discord.Interaction):
        new_datetime = await _resolve_datetime(self.date_input.value, self.time_input.value)
        if not new_datetime:
            await interaction.response.send_message(
                "日時を解析できませんでした。「2026/01/29 18:00」形式で入力してください。",
                ephemeral=True,
            )
            return

        self.parent_view.remind_at = new_datetime
        embed = self.parent_view.create_confirm_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class ReminderListView(discord.ui.View):
    """リマインダー一覧用View"""

    def __init__(self, reminders: list[dict], user_id: str, bot_instance: "ReminderBot" = None):
        super().__init__(timeout=180)
        self.message = None
        self.user_id = user_id
        self.bot_instance = bot_instance

        if reminders:
            options = []
            for r in reminders[:25]:
                ra = datetime.fromisoformat(r["remind_at"])
                wd = WEEKDAY_JA[ra.weekday()]
                desc = f"{ra.strftime('%m/%d')} ({wd}) {ra.strftime('%H:%M')} - {format_remaining(ra)}"
                options.append(discord.SelectOption(
                    label=f"{r['content'][:50]}",
                    description=desc[:100],
                    value=str(r["id"]),
                ))
            select = discord.ui.Select(
                placeholder="操作するリマインダーを選択...",
                options=options,
            )
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        """選択後にReminderActionViewで詳細操作を表示"""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        values = interaction.data.get("values", []) if interaction.data else []
        if not values:
            await interaction.response.send_message("選択されていません。", ephemeral=True)
            return

        try:
            reminder_id = int(values[0])
        except (ValueError, IndexError):
            await interaction.response.send_message("無効な選択です。", ephemeral=True)
            return

        reminder = await get_reminder_by_id(reminder_id)
        if not reminder:
            await interaction.response.send_message("リマインダーが見つかりません。", ephemeral=True)
            return

        action_view = ReminderActionView(reminder_id, reminder, self.bot_instance)
        embed = action_view.create_embed()
        await interaction.response.send_message(embed=embed, view=action_view, ephemeral=True)

    async def on_timeout(self):
        """タイムアウト時にセレクトメニューを無効化"""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


class ReminderActionView(discord.ui.View):
    """リマインダー操作用View（ephemeral、セレクト選択後に表示）"""

    def __init__(self, reminder_id: int, reminder: dict, bot_instance: ReminderBot):
        super().__init__(timeout=180)
        self.message = None
        self.reminder_id = reminder_id
        self.reminder = reminder
        self.bot_instance = bot_instance

    def create_embed(self) -> discord.Embed:
        """リマインダー詳細Embedを作成"""
        remind_at = datetime.fromisoformat(self.reminder["remind_at"])
        weekday = WEEKDAY_JA[remind_at.weekday()]
        remaining = format_remaining(remind_at)
        time_str = f"{remind_at.strftime('%m/%d')} ({weekday}) {remind_at.strftime('%H:%M')} - {remaining}"

        embed = discord.Embed(
            title=self.reminder["content"],
            color=discord.Color.blue(),
        )
        embed.add_field(name="日時", value=time_str, inline=True)

        if self.reminder.get("repeat_type") and self.reminder["repeat_type"] != "none":
            embed.add_field(name="繰り返し", value=format_repeat_label(self.reminder['repeat_type'], self.reminder.get('repeat_value')), inline=True)

        return embed

    @discord.ui.button(label="削除", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """リマインダーを削除"""
        if self.reminder["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        deleted = await delete_reminder(self.reminder_id, str(interaction.user.id))
        if deleted:
            embed = discord.Embed(
                title="削除完了",
                description=f"**{self.reminder['content']}** を削除しました。",
                color=discord.Color.red(),
            )
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message("削除に失敗しました。", ephemeral=True)

    @discord.ui.button(label="タイトル変更", style=discord.ButtonStyle.primary)
    async def edit_content_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """タイトル変更モーダルを開く"""
        if self.reminder["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        modal = EditContentModal(self.reminder_id, self.reminder["content"], self.bot_instance)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="時刻変更", style=discord.ButtonStyle.primary)
    async def edit_time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """時刻変更モーダルを開く"""
        if self.reminder["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        modal = EditTimeModal(self.reminder_id, self.reminder["remind_at"], self.bot_instance)
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        """タイムアウト時にボタンを無効化"""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


class EditContentModal(discord.ui.Modal, title="タイトル変更"):
    """リマインダーの内容編集モーダル"""

    content_input = discord.ui.TextInput(
        label="リマインダーの内容",
        placeholder="新しい内容を入力...",
        required=True,
        max_length=200,
    )

    def __init__(self, reminder_id: int, current_content: str, bot_instance: "ReminderBot" = None):
        super().__init__()
        self.reminder_id = reminder_id
        self.bot_instance = bot_instance
        self.content_input.default = current_content

    async def on_submit(self, interaction: discord.Interaction):
        new_content = self.content_input.value.strip()
        if not new_content:
            await interaction.response.send_message("内容を入力してください。", ephemeral=True)
            return

        success = await update_reminder_content(
            self.reminder_id, str(interaction.user.id), new_content,
        )

        if success:
            await interaction.response.send_message(
                f"内容を **{new_content}** に変更しました。",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("変更に失敗しました。", ephemeral=True)


class EditTimeModal(discord.ui.Modal, title="時刻変更"):
    """リマインダーの時刻編集モーダル"""

    date_input = discord.ui.TextInput(
        label="日付 (例: 2026/01/29 または 明日)",
        placeholder="2026/01/29",
        required=True,
        max_length=20,
    )
    time_input = discord.ui.TextInput(
        label="時刻 (例: 18:00)",
        placeholder="18:00",
        required=True,
        max_length=10,
    )

    def __init__(self, reminder_id: int, current_remind_at: str, bot_instance: "ReminderBot" = None):
        super().__init__()
        self.reminder_id = reminder_id
        self.bot_instance = bot_instance
        remind_at = datetime.fromisoformat(current_remind_at)
        self.date_input.default = remind_at.strftime("%Y/%m/%d")
        self.time_input.default = remind_at.strftime("%H:%M")

    async def on_submit(self, interaction: discord.Interaction):
        new_datetime = await _resolve_datetime(self.date_input.value, self.time_input.value)
        if not new_datetime:
            await interaction.response.send_message(
                "日時を解析できませんでした。「2026/01/29 18:00」形式で入力してください。",
                ephemeral=True,
            )
            return

        success = await update_reminder_time_by_user(
            self.reminder_id, str(interaction.user.id), new_datetime,
        )

        if success:
            weekday = WEEKDAY_JA[new_datetime.weekday()]
            await interaction.response.send_message(
                f"時刻を **{new_datetime.strftime('%Y/%m/%d')} ({weekday}) {new_datetime.strftime('%H:%M')}** に変更しました。",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("変更に失敗しました。", ephemeral=True)


def run_bot():
    """Botを起動"""
    bot.run(DISCORD_BOT_TOKEN)
