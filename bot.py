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
    create_reminder,
    delete_reminder,
    get_all_active_reminders,
    get_bot_state,
    get_reminder_by_id,
    get_user_reminders,
    init_db,
    set_bot_state,
    update_reminder_content,
    update_reminder_time_by_user,
)
from llm_parser import parse_reminder_input
from scheduler import ReminderScheduler

logger = logging.getLogger(__name__)

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

REPEAT_TYPE_MAP = {
    "daily": "毎日",
    "weekly": "毎週",
    "monthly": "毎月",
    "biweekly": "隔週",
    "weekdays": "平日",
}


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

        # 永続Viewを登録（Bot再起動後もボタンが機能するように）
        self.add_view(PersistentListView())

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

        # 起動時に常設リストを更新
        await self.update_persistent_list()

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
            weekday = WEEKDAY_JA[remind_at.weekday()]
            time_str = f"{remind_at.strftime('%m/%d')} ({weekday}) {remind_at.strftime('%H:%M')}"

            value = f"🕐 {time_str}"
            if r.get("repeat_type") and r["repeat_type"] != "none":
                value += f" (🔁 {r['repeat_type']})"

            embed.add_field(
                name=r["content"][:30],
                value=value,
                inline=False,
            )

        if len(reminders) > 10:
            embed.set_footer(text=f"他 {len(reminders) - 10} 件")

        view = ReminderListView(reminders[:25], str(message.author.id))
        await message.reply(embed=embed, view=view)

    async def update_persistent_list(self):
        """常設リマインダーリストを更新（既存メッセージをeditで位置固定）"""
        if not self.reminder_channel_id:
            return

        channel = self.get_channel(self.reminder_channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.reminder_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"常設リスト用チャンネル取得失敗: {e}")
                return

        reminders = await get_all_active_reminders()

        # Embedを構築
        embed = discord.Embed(
            title=f"📋 リマインダー一覧（{len(reminders)}件）",
            color=discord.Color.blue(),
        )

        if reminders:
            lines = []
            for r in reminders[:25]:
                remind_at = datetime.fromisoformat(r["remind_at"])
                weekday = WEEKDAY_JA[remind_at.weekday()]
                time_str = f"{remind_at.strftime('%m/%d')} ({weekday}) {remind_at.strftime('%H:%M')}"

                line = f"**{r['content'][:50]}**\n🕐 {time_str}"
                if r.get("repeat_type") and r["repeat_type"] != "none":
                    repeat_label = REPEAT_TYPE_MAP.get(r["repeat_type"], r["repeat_type"])
                    line += f"  🔁 {repeat_label}"
                line += f"  👤 <@{r['user_id']}>"
                lines.append(line)

            embed.description = "\n\n".join(lines)

            if len(reminders) > 25:
                embed.set_footer(text=f"他 {len(reminders) - 25} 件")
        else:
            embed.description = "リマインダーはありません"

        # Viewを構築（リマインダーがある場合のみセレクトメニュー付き）
        if reminders:
            view = PersistentListView(reminders[:25])
        else:
            view = discord.ui.View()

        logger.info(f"常設リスト更新: {len(reminders)}件, view_children={len(view.children) if view else 0}")

        # 保存済みメッセージIDを取得
        saved_message_id = await get_bot_state("list_message_id")

        if saved_message_id:
            try:
                msg = await channel.fetch_message(int(saved_message_id))
                # 最新メッセージか判定: 最下部ならedit、埋もれていたら削除→再作成
                last_message = channel.last_message or await channel.fetch_message(channel.last_message_id)
                if last_message and last_message.id == msg.id:
                    await msg.edit(embed=embed, view=view)
                    logger.info(f"常設リスト編集完了（最下部）: message_id={msg.id}")
                    return
                else:
                    await msg.delete()
                    logger.info("常設メッセージが埋もれているため削除→再作成")
            except discord.NotFound:
                logger.info("常設メッセージが見つからないため新規作成")
            except discord.HTTPException as e:
                logger.warning(f"常設メッセージ更新失敗: {e}")

        # 新規作成
        msg = await channel.send(embed=embed, view=view)
        await set_bot_state("list_message_id", str(msg.id))
        logger.info(f"常設リスト作成完了: message_id={msg.id}")

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
        bot: ReminderBot,
        user_id: str,
        guild_id: str | None,
        channel_id: str,
        content: str,
        remind_at: datetime,
        repeat_type: str | None = None,
        repeat_value: str | None = None,
    ):
        super().__init__(timeout=300)
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

        embed = discord.Embed(
            title="📝 リマインダー確認",
            color=discord.Color.yellow(),
        )
        embed.add_field(name="内容", value=self.content, inline=False)
        embed.add_field(
            name="日時",
            value=f"{self.remind_at.strftime('%Y/%m/%d')} ({weekday}) {self.remind_at.strftime('%H:%M')}",
            inline=True,
        )

        if self.repeat_type and self.repeat_type != "none":
            repeat_text = self.repeat_type
            if self.repeat_value:
                repeat_text += f" ({self.repeat_value})"
            embed.add_field(name="繰り返し", value=repeat_text, inline=True)

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

        embed = discord.Embed(
            title="✅ 登録完了",
            description=self.content,
            color=discord.Color.green(),
        )
        embed.add_field(
            name="通知日時",
            value=f"{self.remind_at.strftime('%Y/%m/%d')} ({weekday}) {self.remind_at.strftime('%H:%M')}",
            inline=True,
        )
        embed.set_footer(text="登録しました")

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        # 常設リストを更新
        await self.bot_instance.update_persistent_list()

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

        await interaction.message.delete()


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
        # 現在の値をデフォルトに設定
        self.date_input.default = parent_view.remind_at.strftime("%Y/%m/%d")
        self.time_input.default = parent_view.remind_at.strftime("%H:%M")

    async def on_submit(self, interaction: discord.Interaction):
        from llm_parser import parse_reminder_input

        # 入力を解析
        date_str = self.date_input.value
        time_str = self.time_input.value

        # まず直接パースを試みる
        tz = ZoneInfo(TIMEZONE)
        try:
            # 標準形式でパース
            if "/" in date_str:
                parts = date_str.split("/")
                if len(parts) == 3:
                    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                elif len(parts) == 2:
                    year = datetime.now(tz).year
                    month, day = int(parts[0]), int(parts[1])
                else:
                    raise ValueError("Invalid date format")
            else:
                raise ValueError("Use LLM")

            time_parts = time_str.split(":")
            hour, minute = int(time_parts[0]), int(time_parts[1]) if len(time_parts) > 1 else 0

            new_datetime = datetime(year, month, day, hour, minute, tzinfo=tz)
        except (ValueError, IndexError):
            # LLMで解析
            result = await parse_reminder_input(f"{date_str} {time_str}に予定")
            if result:
                new_datetime = result["datetime"]
            else:
                await interaction.response.send_message(
                    "日時を解析できませんでした。「2026/01/29 18:00」形式で入力してください。",
                    ephemeral=True,
                )
                return

        # 親Viewを更新
        self.parent_view.remind_at = new_datetime
        embed = self.parent_view.create_confirm_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


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

        await interaction.response.defer()

        reminder_id = int(values[0])
        deleted = await delete_reminder(reminder_id, self.user_id)

        if deleted:
            await interaction.followup.send("削除しました。")
            # 常設リストも更新
            await bot.update_persistent_list()
        else:
            await interaction.followup.send("削除に失敗しました。", ephemeral=True)


class PersistentListView(discord.ui.View):
    """常設リマインダーリスト用View（永続化対応・セレクトメニューのみ）"""

    def __init__(self, reminders: list[dict] | None = None):
        super().__init__(timeout=None)
        self.reminders = reminders or []

        # セレクトメニューを動的に構築
        if self.reminders:
            options = [
                discord.SelectOption(
                    label=r["content"][:50],
                    description=datetime.fromisoformat(r["remind_at"]).strftime("%m/%d %H:%M"),
                    value=str(r["id"]),
                )
                for r in self.reminders[:25]
            ]
        else:
            options = [
                discord.SelectOption(label="読み込み中...", value="0"),
            ]

        select = discord.ui.Select(
            custom_id="persistent_list:select",
            placeholder="操作するリマインダーを選択...",
            options=options,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        """セレクトメニュー選択時: ephemeralでアクションパネルを返す"""
        values = interaction.data.get("values", []) if interaction.data else []
        if not values or values[0] == "0":
            await interaction.response.defer()
            return

        reminder_id = int(values[0])
        reminder = await get_reminder_by_id(reminder_id)

        if not reminder or not reminder.get("is_active"):
            await interaction.response.send_message(
                "このリマインダーは既に無効です。リストが更新されるまでお待ちください。",
                ephemeral=True,
            )
            await bot.update_persistent_list()
            return

        # アクションパネルをephemeralで表示
        view = ReminderActionView(reminder_id, reminder, bot)
        embed = view.create_embed()
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True,
        )


class ReminderActionView(discord.ui.View):
    """リマインダー操作用View（ephemeral、セレクト選択後に表示）"""

    def __init__(self, reminder_id: int, reminder: dict, bot_instance: ReminderBot):
        super().__init__(timeout=180)
        self.reminder_id = reminder_id
        self.reminder = reminder
        self.bot_instance = bot_instance

    def create_embed(self) -> discord.Embed:
        """リマインダー詳細Embedを作成"""
        remind_at = datetime.fromisoformat(self.reminder["remind_at"])
        weekday = WEEKDAY_JA[remind_at.weekday()]
        time_str = f"{remind_at.strftime('%m/%d')} ({weekday}) {remind_at.strftime('%H:%M')}"

        embed = discord.Embed(
            title=f"📝 {self.reminder['content']}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="日時", value=f"🕐 {time_str}", inline=True)

        if self.reminder.get("repeat_type") and self.reminder["repeat_type"] != "none":
            repeat_label = REPEAT_TYPE_MAP.get(self.reminder["repeat_type"], self.reminder["repeat_type"])
            embed.add_field(name="繰り返し", value=f"🔁 {repeat_label}", inline=True)

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
                title="🗑️ 削除完了",
                description=f"**{self.reminder['content']}** を削除しました。",
                color=discord.Color.red(),
            )
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)
            await self.bot_instance.update_persistent_list()
        else:
            await interaction.response.send_message("削除に失敗しました。", ephemeral=True)

    @discord.ui.button(label="タイトル変更", style=discord.ButtonStyle.primary)
    async def edit_content_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """タイトル変更モーダルを開く"""
        if self.reminder["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        modal = EditContentModal(self.reminder_id, self.reminder["content"])
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="時刻変更", style=discord.ButtonStyle.primary)
    async def edit_time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """時刻変更モーダルを開く"""
        if self.reminder["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("他のユーザーのリマインダーは操作できません。", ephemeral=True)
            return

        modal = EditTimeModal(self.reminder_id, self.reminder["remind_at"])
        await interaction.response.send_modal(modal)


class EditContentModal(discord.ui.Modal, title="タイトル変更"):
    """リマインダーの内容編集モーダル"""

    content_input = discord.ui.TextInput(
        label="リマインダーの内容",
        placeholder="新しい内容を入力...",
        required=True,
        max_length=200,
    )

    def __init__(self, reminder_id: int, current_content: str):
        super().__init__()
        self.reminder_id = reminder_id
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
            await bot.update_persistent_list()
        else:
            await interaction.response.send_message("変更に失敗しました。", ephemeral=True)


class EditTimeModal(discord.ui.Modal, title="時刻変更"):
    """リマインダーの時刻編集モーダル"""

    date_input = discord.ui.TextInput(
        label="日付 (例: 2026/01/29)",
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

    def __init__(self, reminder_id: int, current_remind_at: str):
        super().__init__()
        self.reminder_id = reminder_id
        remind_at = datetime.fromisoformat(current_remind_at)
        self.date_input.default = remind_at.strftime("%Y/%m/%d")
        self.time_input.default = remind_at.strftime("%H:%M")

    async def on_submit(self, interaction: discord.Interaction):
        date_str = self.date_input.value
        time_str = self.time_input.value

        tz = ZoneInfo(TIMEZONE)
        try:
            if "/" in date_str:
                parts = date_str.split("/")
                if len(parts) == 3:
                    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                elif len(parts) == 2:
                    year = datetime.now(tz).year
                    month, day = int(parts[0]), int(parts[1])
                else:
                    raise ValueError("Invalid date format")
            else:
                raise ValueError("Use LLM")

            time_parts = time_str.split(":")
            hour, minute = int(time_parts[0]), int(time_parts[1]) if len(time_parts) > 1 else 0

            new_datetime = datetime(year, month, day, hour, minute, tzinfo=tz)
        except (ValueError, IndexError):
            result = await parse_reminder_input(f"{date_str} {time_str}に予定")
            if result:
                new_datetime = result["datetime"]
            else:
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
            await bot.update_persistent_list()
        else:
            await interaction.response.send_message("変更に失敗しました。", ephemeral=True)


def run_bot():
    """Botを起動"""
    bot.run(DISCORD_BOT_TOKEN)
