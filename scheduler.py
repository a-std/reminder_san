"""APSchedulerでリマインド通知を管理するモジュール"""

import asyncio
import logging
import re
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
from utils import format_repeat_label

logger = logging.getLogger(__name__)

# 同時通知の並行数制限
MAX_CONCURRENT_SENDS = 3


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

        # 起動時に即座にチェック（Bot停止中に期限を迎えたリマインダーを拾う）
        await self.check_and_send_reminders()

    async def stop(self):
        """スケジューラを停止"""
        self.scheduler.shutdown(wait=True)
        logger.info("スケジューラ停止")

    async def check_and_send_reminders(self):
        """期限が来たリマインダーをチェックして通知"""
        try:
            now = datetime.now(self.tz)
            due_reminders = await get_due_reminders(now)
        except Exception as e:
            logger.error(f"期限チェックエラー: {e}", exc_info=True)
            return

        if not due_reminders:
            return

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SENDS)

        async def _process_one(reminder: dict):
            async with semaphore:
                try:
                    await self.send_reminder(reminder)
                    await self.handle_after_send(reminder)
                except Exception as e:
                    logger.error(f"リマインダー送信エラー (ID={reminder['id']}): {e}", exc_info=True)
                    # 送信後処理で失敗した場合もデータ不整合を防ぐ
                    try:
                        await deactivate_reminder(reminder["id"])
                    except Exception:
                        logger.error(f"非アクティブ化にも失敗 (ID={reminder['id']})")

        try:
            async with asyncio.timeout(60):
                tasks = [_process_one(r) for r in due_reminders]
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            logger.warning(f"リマインダー送信処理がタイムアウト（60秒、{len(due_reminders)}件）")

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
        embed.set_footer(text="リマインダー通知")

        # 繰り返し情報を追加
        if reminder.get("repeat_type"):
            repeat_text = self._format_repeat(reminder["repeat_type"], reminder.get("repeat_value"))
            embed.add_field(name="繰り返し", value=repeat_text, inline=True)

        # スヌーズボタンを作成
        is_recurring = bool(reminder.get("repeat_type") and reminder["repeat_type"] != "none")
        view = SnoozeView(reminder["id"], bot=self.bot, is_recurring=is_recurring)

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
        else:
            # 繰り返しあり → 次回日時を計算
            current_time = datetime.fromisoformat(reminder["remind_at"])
            if current_time.tzinfo is None:
                # naive datetimeをタイムゾーン付きに正しく変換
                current_time = current_time.replace(tzinfo=self.tz)
            else:
                # タイムゾーン付きの場合は正しく変換
                current_time = current_time.astimezone(self.tz)

            next_time = self._calculate_next_time(current_time, repeat_type, reminder.get("repeat_value"))

            if next_time:
                success = await update_reminder_time(reminder["id"], next_time)
                if success:
                    logger.info(f"次回リマインダー更新: ID={reminder['id']}, next={next_time}")
                else:
                    logger.error(f"次回リマインダー更新失敗: ID={reminder['id']}")
                    await deactivate_reminder(reminder["id"])
            else:
                logger.warning(f"次回日時計算不能: ID={reminder['id']}, type={repeat_type}")
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
            next_month = current.month + 1
            next_year = current.year
            if next_month > 12:
                next_month = 1
                next_year += 1

            # 第N(,N) X曜日 / 第N(,N) X曜日の前日（複数対応）
            nth_match = re.match(r'第([\d,]+)([月火水木金土日])(の前日)?', repeat_value or "")
            if nth_match:
                weekday_map = {'月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6}
                nths = [int(n) for n in nth_match.group(1).split(',') if n.strip()]
                wd = weekday_map[nth_match.group(2)]
                is_prev_day = nth_match.group(3) is not None
                offset = -1 if is_prev_day else 0

                # current以降で最も近い候補を探す（今月残り → 来月 → 再来月）
                candidates = []
                search_months = [
                    (current.year, current.month),
                    (next_year, next_month),
                ]
                # 再来月も念のため
                rny, rnm = next_year, next_month + 1
                if rnm > 12:
                    rnm = 1
                    rny += 1
                search_months.append((rny, rnm))

                for sy, sm in search_months:
                    for n in nths:
                        target = self._nth_weekday_of_month(sy, sm, n, wd)
                        if target is not None:
                            result = target + timedelta(days=offset)
                            result = result.replace(
                                hour=current.hour, minute=current.minute,
                                second=current.second, microsecond=current.microsecond,
                            )
                            if result > current:
                                candidates.append(result)

                if candidates:
                    return min(candidates)
                return None

            # 翌月の同日
            # 日付オーバーフロー対応（31日→翌月に存在しない場合）
            try:
                return current.replace(year=next_year, month=next_month)
            except ValueError:
                # 翌月末に調整（翌々月1日の前日を求める）
                if next_month == 12:
                    next_next_month = 1
                    next_next_year = next_year + 1
                else:
                    next_next_month = next_month + 1
                    next_next_year = next_year
                last_day = (datetime(next_next_year, next_next_month, 1, tzinfo=self.tz)
                            - timedelta(days=1))
                return last_day.replace(
                    hour=current.hour, minute=current.minute,
                    second=current.second, microsecond=current.microsecond,
                )

        elif repeat_type == "biweekly":
            return current + timedelta(weeks=2)

        elif repeat_type == "weekdays":
            # 次の平日
            next_day = current + timedelta(days=1)
            while next_day.weekday() >= 5:  # 土日スキップ
                next_day += timedelta(days=1)
            return next_day

        return None

    def _nth_weekday_of_month(self, year: int, month: int, nth: int, weekday: int) -> datetime | None:
        """指定月の第N X曜日を計算。存在しなければNone"""
        first = datetime(year, month, 1, tzinfo=self.tz)
        days_ahead = weekday - first.weekday()
        if days_ahead < 0:
            days_ahead += 7
        first_target = first + timedelta(days=days_ahead)
        result = first_target + timedelta(weeks=nth - 1)
        if result.month != month:
            return None
        return result

    def _format_repeat(self, repeat_type: str, repeat_value: str | None) -> str:
        """繰り返し設定を表示用にフォーマット"""
        return format_repeat_label(repeat_type, repeat_value)


class SnoozeView(discord.ui.View):
    """スヌーズボタンのView（永続化対応）"""

    def __init__(self, reminder_id: int, bot: discord.Client | None = None, is_recurring: bool = False):
        super().__init__(timeout=None)
        self.reminder_id = reminder_id
        self.bot = bot

        # 動的にcustom_idを設定（永続化のため）
        self.snooze_5min.custom_id = f"snooze:5:{reminder_id}"
        self.snooze_30min.custom_id = f"snooze:30:{reminder_id}"
        self.snooze_1hour.custom_id = f"snooze:60:{reminder_id}"
        self.snooze_tomorrow.custom_id = f"snooze:1440:{reminder_id}"
        self.mark_done.custom_id = f"snooze:done:{reminder_id}"

        # 単発リマインダーは通知時点で無効化済みなので完了ボタン不要
        if not is_recurring:
            self.remove_item(self.mark_done)

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
        await self._snooze(interaction, 1440)

    @discord.ui.button(label="完了", style=discord.ButtonStyle.success)
    async def mark_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        from database import deactivate_reminder

        await deactivate_reminder(self.reminder_id)
        await interaction.response.send_message(
            "リマインダーを完了にしました。",
            ephemeral=True,
        )
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
