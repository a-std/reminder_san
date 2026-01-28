"""Discord Bot本体"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    DISCORD_BOT_TOKEN,
    REMINDER_CHANNEL_ID,
    SPECIAL_COMMANDS,
    TIMEZONE,
)
from database import (
    create_reminder,
    delete_reminder,
    get_user_reminders,
    init_db,
)
from llm_parser import parse_reminder_input
from scheduler import ReminderScheduler

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
        content = message.content.strip()

        # 特殊コマンドチェック
        if content in SPECIAL_COMMANDS:
            command = SPECIAL_COMMANDS[content]
            if command == "list":
                await self.show_reminder_list(message)
            return

        # LLMで解析
        result = await parse_reminder_input(content)

        if not result:
            await message.reply(
                "解析できませんでした。「明日18時に歯医者」のような形式でお試しください。",
                delete_after=10,
            )
            return

        # 確認画面を表示
        view = ConfirmReminderView(
            user_id=str(message.author.id),
            guild_id=str(message.guild.id) if message.guild else None,
            channel_id=str(message.channel.id),
            content=result["content"],
            remind_at=result["datetime"],
            repeat_type=result.get("repeat_type"),
            repeat_value=result.get("repeat_value"),
        )

        embed = view.create_confirm_embed()
        await message.reply(embed=embed, view=view)

    async def show_reminder_list(self, message: discord.Message):
        """リマインダー一覧を表示"""
        reminders = await get_user_reminders(str(message.author.id))

        if not reminders:
            await message.reply("登録済みのリマインダーはありません。")
            return

        embed = discord.Embed(
            title="📋 リマインダー一覧",
            color=discord.Color.blue(),
        )

        for r in reminders[:10]:
            remind_at = datetime.fromisoformat(r["remind_at"])
            time_str = remind_at.strftime("%m/%d %H:%M")

            value = f"🕐 {time_str}"
            if r.get("repeat_type") and r["repeat_type"] != "none":
                value += f" (🔁 {r['repeat_type']})"

            embed.add_field(
                name=f"ID:{r['id']} {r['content'][:30]}",
                value=value,
                inline=False,
            )

        if len(reminders) > 10:
            embed.set_footer(text=f"他 {len(reminders) - 10} 件")

        view = ReminderListView(reminders[:25], str(message.author.id))
        await message.reply(embed=embed, view=view)

    async def close(self):
        """Bot終了時"""
        if self.scheduler:
            await self.scheduler.stop()
        await super().close()


# Botインスタンス
bot = ReminderBot()


class ConfirmReminderView(discord.ui.View):
    """リマインダー確認用View"""

    def __init__(
        self,
        user_id: str,
        guild_id: str | None,
        channel_id: str,
        content: str,
        remind_at: datetime,
        repeat_type: str | None = None,
        repeat_value: str | None = None,
    ):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.content = content
        self.remind_at = remind_at
        self.repeat_type = repeat_type
        self.repeat_value = repeat_value

    def create_confirm_embed(self) -> discord.Embed:
        """確認用Embedを作成"""
        embed = discord.Embed(
            title="📝 リマインダー確認",
            color=discord.Color.yellow(),
        )
        embed.add_field(name="内容", value=self.content, inline=False)
        embed.add_field(
            name="日時",
            value=self.remind_at.strftime("%Y/%m/%d %H:%M"),
            inline=True,
        )

        if self.repeat_type and self.repeat_type != "none":
            repeat_text = self.repeat_type
            if self.repeat_value:
                repeat_text += f" ({self.repeat_value})"
            embed.add_field(name="繰り返し", value=repeat_text, inline=True)

        return embed

    @discord.ui.button(label="登録", style=discord.ButtonStyle.success, emoji="✅")
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

        embed = discord.Embed(
            title="✅ 登録完了",
            description=self.content,
            color=discord.Color.green(),
        )
        embed.add_field(
            name="通知日時",
            value=self.remind_at.strftime("%Y/%m/%d %H:%M"),
            inline=True,
        )
        embed.set_footer(text=f"ID: {reminder_id}")

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        await interaction.message.delete()


class ReminderListView(discord.ui.View):
    """リマインダー一覧用View"""

    def __init__(self, reminders: list[dict], user_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id

        if reminders:
            options = [
                discord.SelectOption(
                    label=f"{r['content'][:50]}",
                    description=datetime.fromisoformat(r["remind_at"]).strftime("%m/%d %H:%M"),
                    value=str(r["id"]),
                )
                for r in reminders[:25]
            ]
            select = discord.ui.Select(
                placeholder="削除するリマインダーを選択...",
                options=options,
            )
            select.callback = self.delete_callback
            self.add_item(select)

    async def delete_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        values = interaction.data.get("values", []) if interaction.data else []
        if not values:
            await interaction.response.send_message("選択されていません。", ephemeral=True)
            return

        reminder_id = int(values[0])
        deleted = await delete_reminder(reminder_id, self.user_id)

        if deleted:
            await interaction.response.send_message(f"ID: {reminder_id} を削除しました。")
        else:
            await interaction.response.send_message("削除に失敗しました。", ephemeral=True)


def run_bot():
    """Botを起動"""
    bot.run(DISCORD_BOT_TOKEN)
