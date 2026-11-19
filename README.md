# groups-help

Telegram control bot that drives several userbot accounts (Telethon). On `/post`, every logged-in account sends one random text ad to a group you own, `GAP_SECONDS` (default 60) apart, then the round stops.

## Setup

```bash
cd /root && git clone https://github.com/amitt07-pip/groups-help && cd groups-help
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS
cp groupshelp.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now groupshelp
journalctl -u groupshelp -f
```

## Use (bot DM, admins only, silent for everyone else)

1. `/addaccount` – phone → code (send it with spaces, `1 2 3 4 5`) → 2FA password if set.
2. `/addad` – send the ad text (formatting kept).
3. Add the bot to your group, then send `/addgroup` inside it. Only groups where you are the owner are accepted. Make the bot admin so it can create an invite link; accounts that aren't members join with it.
4. `/post` – pick the group; progress is shown live.

Also: `/accounts`, `/delaccount <id>`, `/ads`, `/delad <id>`, `/groups`, `/delgroup <id>`, `/cancel`.

## Keeping accounts healthy

- Use real, aged accounts with 2FA; log each in once and keep the session.
- The bot obeys Telegram's flood waits; an account that gets `PeerFlood` is skipped – rest it.
- Session strings are stored in `groupshelp.db` (file mode 600). Keep it private; anyone with it controls those accounts.
