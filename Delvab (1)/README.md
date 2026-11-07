# Vouch Bot (Components V2)

Standalone Python vouch bot. Every card is native Components V2 (container, sections, media gallery, buttons) with a trader + middleman stats bar. Commands are prefix commands (default `.`). Requires Python 3.10+ and discord.py 2.7+.

## Setup

1. Edit `config.json`:
   - `prefix` — command prefix (default `.`)
   - `guild_id` — your server ID
   - `vouch_channel_id` — channel where vouch cards post
   - `staff_role_ids` — middleman roles (`.vouch`, `.close`, `.vouchqueue`, `.vouchauto`)
   - `trader_role_ids` — trader roles, get a ✅ on their vouch cards
   - `whitelist_role_ids` / `whitelist_user_ids` — extra users allowed to run `.vouch`
   - `owner_ids` — your user ID
   - `middleman` — MM name, user ID (0 = the bot account), baseline vouch count/rating for the stats bar
   - `ticket_category_ids` — categories where `.close` is allowed
   - `links.ticket` — optional "Open a ticket" button URL
2. Copy `.env.example` to `.env` and paste the bot token.
3. `pip install -r requirements.txt`
4. `python bot.py`

Bot permissions in the vouch channel: View Channel, Send Messages, Attach Files, Read Message History. For `.close` with `delete_channel`, also Manage Channels in ticket categories.

The bot needs the **Message Content Intent** enabled in the Developer Portal (Bot tab) for prefix commands.

## Commands

| Command | What it does |
| --- | --- |
| `.vouch [@trader] [trade]` | Posts a button that opens the vouch form, then posts the card immediately |
| `.close [@trader]` | Inside a configured ticket category: posts the ticket's vouch after a delay, then deletes the channel |
| `.vouchstats [@user]` | Stats card (all-time + recorded here) |
| `.vouchhistory [count]` | List of recent vouches with jump links |
| `.vouchqueue` | List of pending/scheduled vouches |
| `.vouchauto on\|off` | Toggle automatic vouches |
| `.vouchremove <id> confirm` | Delete a vouch and its Discord message |
| `.config` | Owner-only panel: middleman roles, trader roles, whitelist, vouch channel, ticket categories |

Anything set in `.config` is written to `config.json` immediately and applies without a restart.

## Automatic vouches

On startup the bot waits 2-5 minutes, then enqueues an automatic vouch and keeps doing so every 45-120 minutes (max 10/day). Edit `config.auto` to change timings or set `enabled` to false.

Automatic entries come from `data/auto_templates.json` (names, trades, reviews). Add trader IDs/avatar URLs to a template if you want real mentions and avatars. Restart the bot after changing `config.json`.

`.close` vouches are queued separately and post even when `.vouchauto` is off.

## Files

- `data/store.json` — vouches, queue, stats baselines (created on first run)
- `data/proofs/` — uploaded proof images (re-uploaded with each card)
- `data/auto_templates.json` — automatic vouch content
