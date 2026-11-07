from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
from discord import ui

TRADE_STYLES: tuple[tuple[tuple[str, ...], tuple[str, int]], ...] = (
    (("gcash", "cash", "php", "peso", "₱"), ("💵", 0x2ECC71)),
    (("robux", "rbx"), ("🟩", 0xE74C3C)),
    (("crypto", "btc", "eth", "usdt", "ltc"), ("₿", 0xF1C40F)),
    (("paypal",), ("💳", 0x3498DB)),
    (("in-game", "ingame", "game", "items", "mm2", "pet"), ("🎮", 0x9B59B6)),
)

SOURCE_LABELS = {
    "manual": "verified trade",
    "queue": "verified trade",
    "auto": "verified trade",
    "ticket": "ticket closed",
}


def trade_style(trade: str) -> tuple[str, int]:
    lowered = trade.lower()
    for keywords, style in TRADE_STYLES:
        if any(keyword in lowered for keyword in keywords):
            return style
    return "🤝", 0x5865F2


def stars(rating: int) -> str:
    rating = max(0, min(5, int(rating)))
    return "★" * rating + "☆" * (5 - rating)


def number(value: int) -> str:
    return f"{int(value):,}"


def clean_name(value: str) -> str:
    return discord.utils.escape_markdown((value or "Trader").replace("`", "'"))[:64]


def clean_stat_name(value: str) -> str:
    return (value or "Trader").replace("`", "'")[:32]


def rating_text(stats: dict[str, Any]) -> str:
    return f"{stats.get('avg', 0.0):.2f}★" if stats.get("count") else "—"


def proof_items(record: dict[str, Any]) -> list[discord.MediaGalleryItem]:
    items: list[discord.MediaGalleryItem] = []
    for path in record.get("proof_files", [])[:10]:
        name = Path(str(path)).name
        if name:
            items.append(
                discord.MediaGalleryItem(
                    f"attachment://{name}", description=f"Proof #{len(items) + 1}"
                )
            )
    for url in record.get("proof_urls", []):
        if len(items) >= 10:
            break
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            items.append(
                discord.MediaGalleryItem(url, description=f"Proof #{len(items) + 1}")
            )
    return items


def build_vouch_view(
    record: dict[str, Any],
    *,
    trader_name: str,
    avatar_url: str | None,
    mm_id: int,
    mm_name: str,
    trader_stats: dict[str, Any],
    mm_stats: dict[str, Any],
    ticket_url: str = "",
    vouches_url: str = "",
    verified_trader: bool = False,
) -> ui.LayoutView:
    trade = record.get("trade") or "Trade"
    emoji, accent = trade_style(trade)
    rating = int(record.get("rating", 5))
    review = (record.get("review") or "").strip()[:400]
    created_at = int(record.get("created_at") or 0)
    vouch_number = int(record.get("number", 0))

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=accent)

    header = ui.TextDisplay(
        f"## ✅ New Vouch `#{vouch_number:04d}`\n"
        f"-# <t:{created_at}:f> · <t:{created_at}:R>"
    )
    trade_line = ui.TextDisplay(f"### {emoji} {trade}")
    if avatar_url and avatar_url.startswith(("http://", "https://")):
        container.add_item(
            ui.Section(
                header,
                trade_line,
                accessory=ui.Thumbnail(media=avatar_url),
            )
        )
    else:
        container.add_item(header)
        container.add_item(trade_line)

    trader_line = (
        f"<@{int(record['trader_id'])}>"
        if record.get("trader_id")
        else f"**{clean_name(trader_name)}**"
    )
    if verified_trader:
        trader_line += "  ✅"
    mm_line = f"<@{int(mm_id)}>" if mm_id else f"**{mm_name}**"

    container.add_item(ui.Separator())
    container.add_item(
        ui.TextDisplay(f"**Trader**  {trader_line}\n**Middleman**  {mm_line}")
    )

    container.add_item(ui.Separator())
    container.add_item(
        ui.TextDisplay(
            f"**Trader Review**  {stars(rating)}  `{rating}.0 / 5`\n"
            f">>> {review or 'Smooth trade, no issues.'}"
        )
    )

    container.add_item(ui.Separator())
    container.add_item(
        ui.TextDisplay(
            "**📊 Vouch Stats**\n"
            f"`{clean_stat_name(trader_name)}`  {number(trader_stats['count'])} vouches · {rating_text(trader_stats)} avg\n"
            f"`{clean_stat_name(mm_name)}`  {number(mm_stats['count'])} vouches · {rating_text(mm_stats)} avg"
        )
    )

    gallery = proof_items(record)
    if gallery:
        container.add_item(ui.Separator())
        container.add_item(ui.MediaGallery(*gallery))

    buttons = []
    if ticket_url:
        buttons.append(
            ui.Button(
                style=discord.ButtonStyle.link,
                label="Open a ticket",
                url=ticket_url,
                emoji="🎫",
            )
        )
    if vouches_url:
        buttons.append(
            ui.Button(
                style=discord.ButtonStyle.link,
                label="All vouches",
                url=vouches_url,
                emoji="📚",
            )
        )
    if buttons:
        container.add_item(ui.Separator())
        container.add_item(ui.ActionRow(*buttons[:5]))

    container.add_item(
        ui.TextDisplay(
            f"-# {SOURCE_LABELS.get(record.get('source', ''), 'verified trade')} · "
            f"<t:{created_at}:R>"
        )
    )

    view.add_item(container)
    return view


def build_stats_view(
    *,
    display_name: str,
    user_id: int,
    avatar_url: str | None,
    stats: dict[str, Any],
    is_mm: bool,
    accent: int = 0x5865F2,
) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=accent)

    title = "## 📊 Middleman Stats" if is_mm else "## 📊 Trader Stats"
    mention = f"<@{user_id}>" if user_id else ""
    header = ui.TextDisplay(f"{title}\n{mention} `{clean_name(display_name)}`")
    if avatar_url and avatar_url.startswith(("http://", "https://")):
        container.add_item(
            ui.Section(header, accessory=ui.Thumbnail(media=avatar_url))
        )
    else:
        container.add_item(header)

    container.add_item(ui.Separator())
    if stats["count"]:
        container.add_item(
            ui.TextDisplay(
                f"**All-time**  `{number(stats['count'])}` vouches · "
                f"`{stats['avg']:.2f}★` average\n"
                f"**Recorded here**  `{number(stats['recorded_count'])}` vouches · "
                f"`{stats['recorded_avg']:.2f}★` average\n"
                f"**Last vouch**  <t:{int(stats['last_at'])}:R>"
            )
        )
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(f"{stars(round(stats['avg']))}  `{stats['avg']:.2f} / 5`")
        )
    else:
        container.add_item(
            ui.TextDisplay("No vouches recorded for this user yet.")
        )

    container.add_item(
        ui.TextDisplay(
            "-# Counts include recorded history plus this bot's vouches."
        )
    )
    view.add_item(container)
    return view


def build_history_view(
    entries: list[dict[str, Any]],
    *,
    accent: int = 0x5865F2,
) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=accent)
    container.add_item(ui.TextDisplay(f"## 🧾 Recent Vouches\n-# {len(entries)} shown"))

    if not entries:
        container.add_item(ui.TextDisplay("No vouches yet."))
    else:
        lines = []
        for record in entries:
            trader = (
                f"<@{int(record['trader_id'])}>"
                if record.get("trader_id")
                else f"**{clean_name(record.get('trader_name', 'Trader'))}**"
            )
            status = (
                f"<t:{int(record['created_at'])}:R>"
                if record.get("posted")
                else "⏳ pending"
            )
            jump = ""
            if record.get("guild_id") and record.get("channel_id") and record.get("message_id"):
                jump = (
                    f" · [jump](https://discord.com/channels/"
                    f"{int(record['guild_id'])}/{int(record['channel_id'])}/"
                    f"{int(record['message_id'])})"
                )
            lines.append(
                f"`#{int(record['number']):04d}` · {trader} · "
                f"{stars(int(record.get('rating', 0)))} · {status}{jump}"
            )
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay("\n".join(lines)))

    view.add_item(container)
    return view


def build_queue_view(
    entries: list[dict[str, Any]],
    *,
    total: int,
    accent: int = 0xF1C40F,
) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=accent)
    container.add_item(
        ui.TextDisplay(f"## ⏳ Vouch Queue\n-# {number(total)} pending")
    )

    if not entries:
        container.add_item(ui.TextDisplay("Queue is empty."))
    else:
        lines = []
        for record in entries:
            trader = (
                f"<@{int(record['trader_id'])}>"
                if record.get("trader_id")
                else clean_name(record.get("trader_name", "Trader"))
            )
            due = int(record.get("not_before", 0))
            when = f"<t:{due}:R>" if due else "next tick"
            attempts = int(record.get("attempts", 0))
            retry = f" · {attempts} failed attempt(s)" if attempts else ""
            lines.append(
                f"`#{int(record['number']):04d}` · {trader} · "
                f"{record.get('source', 'manual')} · due {when}{retry}"
            )
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay("\n".join(lines)))

    container.add_item(
        ui.TextDisplay("-# Pending vouches are posted automatically when due.")
    )
    view.add_item(container)
    return view
