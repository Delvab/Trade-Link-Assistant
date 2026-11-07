from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from cards import (
    build_history_view,
    build_queue_view,
    build_stats_view,
    build_vouch_view,
)
from config import CONFIG, DATA_DIR, PROOF_DIR, TOKEN
from modals import VouchModal, VouchPromptView
from panel import ConfigPanel
from store import Store

log = logging.getLogger("vouchbot")

PROOF_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov"}
MAX_PROOF_BYTES = 8 * 1024 * 1024
MAX_TOTAL_PROOF_BYTES = 20 * 1024 * 1024


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:48] or "trader"


def parse_trader(value: str) -> tuple[int | None, str]:
    text = (value or "").strip()
    match = re.fullmatch(r"<@!?(\d{15,25})>", text) or re.fullmatch(r"(\d{15,25})", text)
    if match:
        return int(match.group(1)), ""
    name = text.lstrip("@").strip()
    return None, name[:64]


def parse_rating(value: str) -> int | None:
    text = (value or "").strip()
    if text.isdigit() and 1 <= int(text) <= 5:
        return int(text)
    filled = text.count("★") + text.count("*")
    if 1 <= filled <= 5:
        return filled
    match = re.search(r"\b([1-5])\b", text)
    if match:
        return int(match.group(1))
    return None


def topic_trader(topic: str | None) -> str:
    match = re.search(
        r"trader(?:\s*[:=]\s*|\s+)(?:<@!?)?(\d{15,25})",
        topic or "",
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


class VouchBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(str(CONFIG["prefix"])),
            intents=intents,
            help_command=None,
        )
        self.cfg = CONFIG
        self.prefix = str(CONFIG["prefix"])
        self.store = Store(DATA_DIR / "store.json")
        self.templates: dict[str, Any] = {}
        self.mm_id = int(self.cfg["middleman"]["id"])
        self.mm_name = str(self.cfg["middleman"]["name"])
        self._tasks: list[asyncio.Task[Any]] = []
        self._background: set[asyncio.Task[Any]] = set()
        self._ready_logged = False

    async def setup_hook(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        await self.store.load()
        await self.store.initialize_auto(bool(self.cfg["auto"]["enabled"]))
        self.templates = self._load_templates()
        await self._seed_baselines(self.mm_id)

        guild_id = int(self.cfg["guild_id"])
        if guild_id:
            await self.tree.sync(guild=discord.Object(id=guild_id))
        else:
            await self.tree.sync()
        log.info("Prefix commands ready (prefix=%s), slash commands cleared", self.prefix)

        self._tasks.append(asyncio.create_task(self.scheduler_loop()))
        self._tasks.append(asyncio.create_task(self.generator_loop()))

    async def on_ready(self) -> None:
        if self.user is None:
            return
        resolved_mm = int(self.cfg["middleman"]["id"]) or self.user.id
        if resolved_mm != self.mm_id:
            self.mm_id = resolved_mm
            await self._seed_baselines(resolved_mm)

        if not self._ready_logged:
            self._ready_logged = True
            channel_id = int(self.cfg["vouch_channel_id"])
            channel = self.get_channel(channel_id) if channel_id else None
            log.info(
                "Logged in as %s (%s) | guilds=%d | vouch channel=%s | auto=%s",
                self.user,
                self.user.id,
                len(self.guilds),
                f"#{channel.name}" if channel else "NOT SET",
                "on" if self.store.auto_enabled else "off",
            )
            if not channel_id:
                log.warning("vouch_channel_id is empty in config.json - vouches cannot post")
            if not self.cfg["staff_role_ids"]:
                log.warning(
                    "staff_role_ids is empty - only Manage Server / owner IDs can post"
                )

    async def close(self) -> None:
        for task in [*self._tasks, *self._background]:
            task.cancel()
        await super().close()

    def _load_templates(self) -> dict[str, Any]:
        path = DATA_DIR / "auto_templates.json"
        if not path.exists():
            log.warning("Missing %s - automatic vouch generation is disabled", path)
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not load auto templates")
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    @staticmethod
    def _trader_key(trader_id: int | None, name: str) -> str:
        if trader_id:
            return f"trader:id:{int(trader_id)}"
        return f"trader:name:{slugify(name)}"

    async def _seed_baselines(self, mm_id: int) -> None:
        mm = self.cfg["middleman"]
        if mm_id:
            await self.store.ensure_baseline(
                f"mm:{mm_id}",
                int(mm["baseline_vouches"]),
                float(mm["baseline_rating"]),
            )
        for trader in self.templates.get("traders", []):
            trader_id = trader.get("id")
            name = str(trader.get("name") or "Trader")
            key = self._trader_key(int(trader_id) if trader_id else None, name)
            await self.store.ensure_baseline(
                key,
                int(trader.get("vouches", 0) or 0),
                float(trader.get("rating", 5.0) or 5.0),
            )

    async def _save_uploads(
        self, attachments: list[discord.Attachment]
    ) -> list[str]:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        total = 0
        for index, attachment in enumerate(attachments[:5]):
            content_type = (attachment.content_type or "").lower()
            if not content_type.startswith(("image/", "video/")):
                continue
            if attachment.size > MAX_PROOF_BYTES:
                log.warning("Skipping oversized proof %s", attachment.filename)
                continue
            try:
                data = await attachment.read()
            except discord.HTTPException:
                log.warning("Could not download proof %s", attachment.filename, exc_info=True)
                continue
            if total + len(data) > MAX_TOTAL_PROOF_BYTES:
                break
            extension = Path(attachment.filename).suffix.lower()
            if extension not in PROOF_EXTENSIONS:
                extension = ".png"
            name = f"{secrets.token_hex(6)}_{index}{extension}"
            path = PROOF_DIR / name
            path.write_bytes(data)
            saved.append(path.relative_to(DATA_DIR).as_posix())
            total += len(data)
        return saved

    async def _member_name(
        self, guild: discord.Guild | None, user_id: int
    ) -> str:
        if guild is None:
            return ""
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException):
                return ""
        return member.display_name

    async def handle_modal_submit(
        self, interaction: discord.Interaction, modal: VouchModal
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        trader_id, raw_name = parse_trader(modal.trader.value)
        trade = (modal.trade.value or "").strip()[:80]
        review = (modal.review.value or "").strip()[:400]
        rating = parse_rating(modal.rating.value)

        if rating is None:
            await interaction.followup.send(
                "❌ Rating must be a number from 1 to 5.", ephemeral=True
            )
            return
        if not trade:
            await interaction.followup.send(
                "❌ Trade type cannot be empty.", ephemeral=True
            )
            return
        if not review:
            await interaction.followup.send(
                "❌ Review cannot be empty.", ephemeral=True
            )
            return
        if not trader_id and not raw_name:
            await interaction.followup.send(
                "❌ Trader must be a mention, user ID, or username.", ephemeral=True
            )
            return
        if not int(self.cfg["vouch_channel_id"]):
            await interaction.followup.send(
                "❌ `vouch_channel_id` is not set in `config.json`.", ephemeral=True
            )
            return

        if not self.mm_id and self.user is not None:
            self.mm_id = self.user.id

        trader_name = raw_name
        if trader_id and not trader_name:
            trader_name = await self._member_name(interaction.guild, trader_id)

        saved_proofs = await self._save_uploads(modal.proof.values)
        fields: dict[str, Any] = {
            "trader_id": int(trader_id) if trader_id else None,
            "trader_name": trader_name,
            "trader_key": self._trader_key(trader_id, trader_name or str(trader_id)),
            "trade": trade,
            "rating": rating,
            "review": review,
            "proof_files": saved_proofs,
            "proof_urls": [],
            "avatar_url": "",
            "mm_id": self.mm_id,
            "mm_name": self.mm_name,
            "mm_key": f"mm:{self.mm_id}",
            "guild_id": interaction.guild_id,
        }

        if modal.mode == "close":
            close_cfg = self.cfg["close"]
            low = max(0, int(close_cfg["vouch_delay_min_seconds"]))
            high = max(low, int(close_cfg["vouch_delay_max_seconds"]))
            delay = random.randint(low, high)
            record = await self.store.add_vouch(
                fields,
                source="ticket",
                not_before=time.time() + delay,
            )
            channel = modal.ticket_channel or interaction.channel
            minutes, seconds = divmod(delay, 60)
            when = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
            if isinstance(channel, discord.abc.Messageable):
                try:
                    await channel.send(
                        f"🔒 **Ticket closed** — vouch scheduled in {when}."
                    )
                except discord.HTTPException:
                    log.warning("Could not post close notice", exc_info=True)
            await interaction.followup.send(
                f"✅ Vouch `#{record['number']:04d}` will post in {when}.",
                ephemeral=True,
            )
            if (
                close_cfg["delete_channel"]
                and int(close_cfg["delete_after_seconds"]) > 0
                and isinstance(channel, discord.TextChannel)
            ):
                task = asyncio.create_task(
                    self._delete_later(
                        channel, int(close_cfg["delete_after_seconds"])
                    )
                )
                self._background.add(task)
                task.add_done_callback(self._background.discard)
            return

        record = await self.store.add_vouch(
            fields,
            source="manual",
            not_before=time.time(),
        )
        try:
            message = await self.post_vouch(record)
        except Exception as exc:
            log.exception("Could not post vouch #%s", record["number"])
            await self.store.record_failure(record["number"], exc)
            await interaction.followup.send(
                "❌ Could not post the vouch. It is still queued and will retry.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Vouch `#{record['number']:04d}` posted in {message.channel.mention}.",
            ephemeral=True,
        )

    @staticmethod
    async def _delete_later(channel: discord.TextChannel, seconds: int) -> None:
        await asyncio.sleep(max(1, seconds))
        try:
            await channel.delete(reason="Ticket closed")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.warning("Could not delete ticket channel %s", channel.id)

    async def post_vouch(self, record: dict[str, Any]) -> discord.Message:
        channel_id = int(self.cfg["vouch_channel_id"])
        if not channel_id:
            raise RuntimeError("vouch_channel_id is not set")

        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("vouch_channel_id must point to a text channel")

        guild = channel.guild
        trader_id = record.get("trader_id")
        trader_name = record.get("trader_name") or "Trader"
        avatar_url = record.get("avatar_url") or ""
        verified_trader = False

        if trader_id:
            member = guild.get_member(int(trader_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(trader_id))
                except (discord.NotFound, discord.HTTPException):
                    member = None
            if member is not None:
                trader_name = member.display_name
                avatar_url = member.display_avatar.url
                verified_trader = bool(
                    {role.id for role in member.roles}
                    & set(CONFIG["trader_role_ids"])
                )

        if not avatar_url:
            avatar_url = (self.cfg["links"].get("fallback_thumbnail") or "").strip()
        if not avatar_url and guild.icon:
            avatar_url = guild.icon.url

        if not self.mm_id and self.user is not None:
            self.mm_id = self.user.id
        mm_id = self.mm_id
        mm = self.cfg["middleman"]
        await self.store.ensure_baseline(
            f"mm:{mm_id}",
            int(mm["baseline_vouches"]),
            float(mm["baseline_rating"]),
        )

        trader_stats = self.store.stats_for(record["trader_key"])
        mm_stats = self.store.stats_for(f"mm:{mm_id}")

        ticket_url = (self.cfg["links"].get("ticket") or "").strip()
        vouches_url = f"https://discord.com/channels/{guild.id}/{channel.id}"

        proof_paths: list[Path] = []
        for relative in record.get("proof_files", []):
            path = Path(str(relative))
            if not path.is_absolute():
                path = DATA_DIR / path
            if path.exists():
                proof_paths.append(path)
            if len(proof_paths) >= 10:
                break

        card_record = {**record, "proof_files": [path.name for path in proof_paths]}
        view = build_vouch_view(
            card_record,
            trader_name=trader_name,
            avatar_url=avatar_url,
            mm_id=mm_id,
            mm_name=self.mm_name,
            trader_stats=trader_stats,
            mm_stats=mm_stats,
            ticket_url=ticket_url,
            vouches_url=vouches_url,
            verified_trader=verified_trader,
        )

        files = [discord.File(path, filename=path.name) for path in proof_paths]

        message = await channel.send(
            view=view,
            files=files or None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.store.mark_posted(record["number"], channel.id, message.id)
        return message

    async def scheduler_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                if not int(self.cfg["vouch_channel_id"]):
                    await asyncio.sleep(30)
                    continue
                for _ in range(3):
                    record = self.store.next_due(time.time())
                    if record is None:
                        break
                    try:
                        await self.post_vouch(record)
                        log.info("Posted queued vouch #%s", record["number"])
                    except Exception as exc:
                        log.exception("Failed to post vouch #%s", record["number"])
                        await self.store.record_failure(record["number"], exc)
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scheduler loop error")
            await asyncio.sleep(15)

    async def generator_loop(self) -> None:
        await self.wait_until_ready()
        auto = self.cfg["auto"]
        first_low = max(0, int(auto["first_delay_min_seconds"]))
        first_high = max(first_low, int(auto["first_delay_max_seconds"]))
        await asyncio.sleep(random.uniform(first_low, first_high))

        while not self.is_closed():
            try:
                if not int(self.cfg["vouch_channel_id"]):
                    await asyncio.sleep(30)
                    continue
                if not self.store.auto_enabled or not self.templates.get("traders"):
                    await asyncio.sleep(60)
                    continue

                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if await self.store.reserve_generated(day, int(auto["daily_cap"])):
                    record = await self._queue_generated()
                    if record:
                        log.info("Queued auto vouch #%s", record["number"])

                low = max(30, int(auto["interval_min_seconds"]))
                high = max(low, int(auto["interval_max_seconds"]))
                await asyncio.sleep(random.uniform(low, high))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Generator loop error")
                await asyncio.sleep(120)

    async def _queue_generated(self) -> dict[str, Any] | None:
        traders = self.templates.get("traders") or []
        trades = self.templates.get("trades") or []
        reviews = self.templates.get("reviews") or []
        if not traders or not trades or not reviews:
            return None

        trader = random.choice(traders)
        trade = random.choice(trades)
        review = random.choice(reviews)
        trader_id = trader.get("id") or None
        trader_name = str(trader.get("name") or "Trader")
        key = self._trader_key(int(trader_id) if trader_id else None, trader_name)

        await self.store.ensure_baseline(
            key,
            int(trader.get("vouches", 0) or 0),
            float(trader.get("rating", 5.0) or 5.0),
        )

        mm_id = self.mm_id
        fields = {
            "trader_id": int(trader_id) if trader_id else None,
            "trader_name": trader_name,
            "trader_key": key,
            "trade": str(trade)[:80],
            "rating": int(review.get("rating", 5)),
            "review": str(review.get("text", ""))[:400],
            "proof_files": [],
            "proof_urls": list(self.templates.get("proof_urls") or [])[:10],
            "avatar_url": str(trader.get("avatar") or ""),
            "mm_id": mm_id,
            "mm_name": self.mm_name,
            "mm_key": f"mm:{mm_id}",
            "guild_id": int(self.cfg["guild_id"]),
        }
        return await self.store.add_vouch(
            fields,
            source="auto",
            not_before=time.time(),
        )


bot = VouchBot()


def staff_only():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in CONFIG["owner_ids"]:
            return True
        if isinstance(ctx.author, discord.Member):
            if ctx.author.guild_permissions.manage_guild:
                return True
            role_ids = {role.id for role in ctx.author.roles}
            if role_ids & set(CONFIG["staff_role_ids"]):
                return True
        raise commands.CheckFailure("staff_only")

    return commands.check(predicate)


def owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in CONFIG["owner_ids"]:
            return True
        if isinstance(ctx.author, discord.Member) and (
            ctx.author.guild_permissions.administrator
        ):
            return True
        raise commands.CheckFailure("owner_only")

    return commands.check(predicate)


def vouch_access():
    async def predicate(ctx: commands.Context) -> bool:
        author = ctx.author
        if author.id in CONFIG["owner_ids"] or author.id in CONFIG["whitelist_user_ids"]:
            return True
        if isinstance(author, discord.Member):
            if author.guild_permissions.manage_guild:
                return True
            role_ids = {role.id for role in author.roles}
            allowed = set(CONFIG["staff_role_ids"]) | set(CONFIG["whitelist_role_ids"])
            if role_ids & allowed:
                return True
        raise commands.CheckFailure("vouch_access")

    return commands.check(predicate)


@bot.command(name="vouch", usage="[trader] [trade]")
@commands.guild_only()
@vouch_access()
async def vouch_cmd(
    ctx: commands.Context,
    trader: str | None = None,
    *,
    trade: str = "",
) -> None:
    if not int(CONFIG["vouch_channel_id"]):
        await ctx.reply(
            "❌ Set `vouch_channel_id` in `config.json` first.", mention_author=False
        )
        return
    view = VouchPromptView(
        bot,
        mode="now",
        prefill_trader=(trader or "").strip(),
        prefill_trade=(trade or "").strip()[:80],
    )
    view.message = await ctx.reply(
        "📝 Click **Open vouch form** to post a vouch now.",
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="close", usage="[trader]")
@commands.guild_only()
@staff_only()
async def close_cmd(
    ctx: commands.Context,
    trader: str | None = None,
) -> None:
    channel = ctx.channel
    categories = set(CONFIG["ticket_category_ids"])
    if not categories:
        await ctx.reply(
            "❌ Set `ticket_category_ids` in `config.json` first.", mention_author=False
        )
        return
    if (
        not isinstance(channel, discord.TextChannel)
        or channel.category_id not in categories
    ):
        await ctx.reply(
            "❌ This command only works inside a configured ticket channel.",
            mention_author=False,
        )
        return
    if not int(CONFIG["vouch_channel_id"]):
        await ctx.reply(
            "❌ Set `vouch_channel_id` in `config.json` first.", mention_author=False
        )
        return
    prefill = (trader or "").strip() or topic_trader(channel.topic)
    view = VouchPromptView(
        bot,
        mode="close",
        prefill_trader=prefill,
        channel=channel,
    )
    view.message = await ctx.reply(
        "🔒 Click **Open vouch form** to close this ticket and schedule its vouch.",
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="vouchstats", usage="[member]")
@commands.guild_only()
async def vouchstats_cmd(
    ctx: commands.Context,
    member: discord.Member = None,
) -> None:
    target = member or ctx.author
    is_mm = target.id == bot.mm_id
    key = f"mm:{target.id}" if is_mm else f"trader:id:{target.id}"
    stats = bot.store.stats_for(key)
    view = build_stats_view(
        display_name=target.display_name,
        user_id=target.id,
        avatar_url=target.display_avatar.url if target.display_avatar else None,
        stats=stats,
        is_mm=is_mm,
    )
    await ctx.reply(
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="vouchhistory", usage="[count]")
@commands.guild_only()
async def vouchhistory_cmd(ctx: commands.Context, count: int = 10) -> None:
    entries = bot.store.recent(max(1, min(15, count)))
    view = build_history_view(entries)
    await ctx.reply(
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="vouchqueue")
@commands.guild_only()
@staff_only()
async def vouchqueue_cmd(ctx: commands.Context) -> None:
    pending = bot.store.pending(10)
    view = build_queue_view(pending, total=bot.store.pending_count())
    await ctx.reply(
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="vouchauto", usage="<on|off>")
@commands.guild_only()
@staff_only()
async def vouchauto_cmd(ctx: commands.Context, value: str) -> None:
    token = value.strip().lower()
    if token in {"on", "true", "yes", "1", "enable", "enabled"}:
        enabled = True
    elif token in {"off", "false", "no", "0", "disable", "disabled"}:
        enabled = False
    else:
        await ctx.reply(
            f"❌ Usage: `{bot.prefix}vouchauto on|off`", mention_author=False
        )
        return
    await bot.store.set_auto(enabled)
    state = "on" if enabled else "off"
    pending = bot.store.pending_count()
    cap = int(CONFIG["auto"]["daily_cap"])
    cap_text = f"{cap}/day" if cap > 0 else "unlimited"
    await ctx.reply(
        f"✅ Automatic vouches are now **{state}**.\n"
        f"Cap: `{cap_text}` · Pending: `{pending}`",
        mention_author=False,
    )


@bot.command(name="config")
@commands.guild_only()
@owner_only()
async def config_cmd(ctx: commands.Context) -> None:
    if ctx.guild is None:
        return
    await ctx.reply(
        view=ConfigPanel(ctx.guild),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="vouchremove", usage="<vouch_id> [confirm]")
@commands.guild_only()
@owner_only()
async def vouchremove_cmd(
    ctx: commands.Context,
    vouch_id: str,
    confirm: str = "",
) -> None:
    digits = re.sub(r"[^0-9]", "", vouch_id or "")
    if not digits:
        await ctx.reply("❌ Give me a vouch number, e.g. `42`.", mention_author=False)
        return
    record = bot.store.get(int(digits))
    if record is None:
        await ctx.reply(
            f"❌ Vouch `#{int(digits):04d}` was not found.", mention_author=False
        )
        return
    if confirm.strip().lower() not in {"confirm", "true", "yes"}:
        await ctx.reply(
            f"⚠️ This will delete vouch `#{int(digits):04d}`. "
            f"Run `{bot.prefix}vouchremove {int(digits)} confirm` to do it.",
            mention_author=False,
        )
        return

    if record.get("channel_id") and record.get("message_id"):
        channel = bot.get_channel(int(record["channel_id"]))
        if channel is None:
            try:
                channel = await bot.fetch_channel(int(record["channel_id"]))
            except discord.HTTPException:
                channel = None
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(int(record["message_id"]))
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning("Could not delete vouch message %s", record["message_id"])

    await bot.store.remove(int(digits))
    await ctx.reply(
        f"🗑️ Removed vouch `#{int(digits):04d}`.", mention_author=False
    )


@bot.event
async def on_command_error(
    ctx: commands.Context, error: commands.CommandError
) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        message = "You don't have permission to use this command."
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        usage = ctx.command.usage if ctx.command and ctx.command.usage else ""
        message = f"❌ Wrong usage. Try `{bot.prefix}{ctx.command} {usage}`."
    else:
        log.exception("Command failed", exc_info=error)
        message = "Something went wrong. Check the bot logs."
    try:
        await ctx.reply(message, mention_author=False)
    except discord.HTTPException:
        pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not TOKEN:
        raise SystemExit(
            "No DISCORD_TOKEN found. Copy .env.example to .env and add your bot token."
        )
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
