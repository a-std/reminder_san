"""APSchedulerでリマインド通知を管理するモジュール"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import SCHEDULER_CHECK_INTERVAL_SEC, TIMEZONE
from database import (
    deactivate_reminder,
    get_due_reminders,
    update_reminder_time,
)

logger = logging.getLogger(__name__)


class ReminderScheduler:
    """リマインダー通知スケジューラ"""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=TIMEZONE)
        self.tz = ZoneInfo(TIMEZONE)

    async def start(self):
        """スケジューラを開始"""
        # 定期チェックジョブを追加
        self.scheduler.add_job(
            self.check_and_send_reminders,
            trigger=IntervalTrigger(seconds=SCHEDULER_CHECK_INTERVAL_SEC),
            id="reminder_checker",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(f"スケジューラ開始（チェック間隔: {SCHEDULER_CHECK_INTERVAL_SEC}秒）")

    async def stop(self):
        """スケジューラを停止"""
        self.scheduler.shutdown(wait=False)
        logger.info("スケジューラ停止")

    async def check_and_send_reminders(self):
        """期限が来たリマインダーをチェックして通知"""
        now = datetime.now(self.tz)
        due_reminders = await get_due_reminders(now)

        for reminder in due_reminders:
            try:
                await self.send_reminder(reminder)
                await self.handle_after_send(reminder)
            except Exception as e:
                logger.error(f"リマインダー送信エラー (ID={reminder['id']}): {e}")

    async def send_reminder(self, reminder: dict):
        """リマインダーを送信"""
        channel_id = int(reminder["channel_id"])
        user_id = int(reminder["user_id"])

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"チャンネル取得失敗: {channel_id}, error={e}")
                await deactivate_reminder(reminder["id"])
                return

        # リマインド通知用Embed
        embed = discord.Embed(
            title="リマインダー",
            description=reminder["content"],
            color=discord.Color.blue(),
            timestamp=datetime.now(self.tz),
        )
        embed.set_footer(text=f"ID: {reminder['id']}")

        # 繰り返し情報を追加
        if reminder.get("repeat_type"):
            repeat_text = self._format_repeat(reminder["repeat_type"], reminder.get("repeat_value"))
            embed.add_field(name="繰り返し", value=repeat_text, inline=True)

        # スヌーズボタンを作成
        view = SnoozeView(reminder["id"])

        try:
            await channel.send(
                content=f"<@{user_id}>",
                embed=embed,
                view=view,
            )
            logger.info(f"リマインダー送信: ID={reminder['id']}, user={user_id}")
        except discord.Forbidden:
            logger.warning(f"メッセージ送信権限がありません: channel={channel_id}")
            await deactivate_reminder(reminder["id"])

    async def handle_after_send(self, reminder: dict):
        """送信後の処理（繰り返し更新 or 非アクティブ化）"""
        repeat_type = reminder.get("repeat_type")

        if not repeat_type or repeat_type == "none":
            # 繰り返しなし → 非アクティブ化
            await deactivate_reminder(reminder["id"])
            return

        # 繰り返しあり → 次回日時を計算
        current_time = datetime.fromisoformat(reminder["remind_at"])
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=self.tz)

        next_time = self._calculate_next_time(current_time, repeat_type, reminder.get("repeat_value"))

        if next_time:
            await update_reminder_time(reminder["id"], next_time)
            logger.info(f"次回リマインダー更新: ID={reminder['id']}, next={next_time}")
        else:
            await deactivate_reminder(reminder["id"])

    def _calculate_next_time(
        self, current: datetime, repeat_type: str, repeat_value: str | None
    ) -> datetime | None:
        """次回の通知日時を計算"""
        if repeat_type == "daily":
            return current + timedelta(days=1)

        elif repeat_type == "weekly":
            return current + timedelta(weeks=1)

        elif repeat_type == "monthly":
            # 翌月の同日
            next_month = current.month + 1
            next_year = current.year
            if next_month > 12:
                next_month = 1
                next_year += 1

            # 日付オーバーフロー対応（31日→翌月に存在しない場合）
            try:
                return current.replace(year=next_year, month=next_month)
            except ValueError:
                # 翌月末に調整
                if next_month == 12:
                    next_next_month = 1
                    next_next_year = next_year + 1
                else:
                    next_next_month = next_month + 1
                    next_next_year = next_year
                return current.replace(
                    year=next_next_year, month=next_next_month, day=1
                ) - timedelta(days=1)

        elif repeat_type == "biweekly":
            return current + timedelta(weeks=2)

        elif repeat_type == "weekdays":
            # 次の平日
            next_day = current + timedelta(days=1)
            while next_day.weekday() >= 5:  # 土日スキップ
                next_day += timedelta(days=1)
            return next_day

        return None

    def _format_repeat(self, repeat_type: str, repeat_value: str | None) -> str:
        """繰り返し設定を表示用にフォーマット"""
        type_map = {
            "daily": "毎日",
            "weekly": "毎週",
            "monthly": "毎月",
            "biweekly": "隔週",
            "weekdays": "平日",
        }
        base = type_map.get(repeat_type, repeat_type)
        if repeat_value:
            return f"{base} ({repeat_value})"
        return base


class SnoozeView(discord.ui.View):
    """スヌーズボタンのView"""

    def __init__(self, reminder_id: int):
        super().__init__(timeout=86400)  # 24時間有効
        self.reminder_id = reminder_id

    @discord.ui.button(label="5分後", style=discord.ButtonStyle.secondary)
    async def snooze_5min(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._snooze(interaction, 5)

    @discord.ui.button(label="30分後", style=discord.ButtonStyle.secondary)
    async def snooze_30min(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._snooze(interaction, 30)

    @discord.ui.button(label="1時間後", style=discord.ButtonStyle.secondary)
    async def snooze_1hour(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._snooze(interaction, 60)

    @discord.ui.button(label="明日", style=discord.ButtonStyle.secondary)
    async def snooze_tomorrow(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._snooze(interaction, 1440)  # 24時間

    @discord.ui.button(label="完了", style=discord.ButtonStyle.success)
    async def mark_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        from database import deactivate_reminder

        await deactivate_reminder(self.reminder_id)
        await interaction.response.send_message(
            "リマインダーを完了にしました。",
            ephemeral=True,
        )
        # ボタンを無効化
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

    async def _snooze(self, interaction: discord.Interaction, minutes: int):
        from database import snooze_reminder

        tz = ZoneInfo(TIMEZONE)
        new_time = datetime.now(tz) + timedelta(minutes=minutes)
        success = await snooze_reminder(self.reminder_id, new_time)

        if success:
            await interaction.response.send_message(
                f"リマインダーを {new_time.strftime('%m/%d %H:%M')} に再通知します。",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "スヌーズに失敗しました。",
                ephemeral=True,
            )
