# groups-help

Telegram control bot that drives several userbot accounts (Telethon). On `/post`, every logged-in account sends one random text ad to a group you own, `GAP_SECONDS` (default 60) apart, then the round stops.

## Setup

```bash
cd /root && git clone https://github.com/amitt07-pip/groups-help && cd groups-help
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS, GROUP_OWNER_ID
cp groupshelp.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now groupshelp
journalctl -u groupshelp -f
```

## Use (only in the control chat `CONTROL_CHAT_ID`, admins only, silent everywhere else)

Make the bot **admin in the control chat** (so it sees replies and can delete login codes/passwords).

1. `/addaccount` – reply with phone → code (with spaces, `1 2 3 4 5`) → 2FA password if set. The bot deletes the code/password messages.
2. `/addad` – reply with the ad text (formatting kept).
3. Add the bot as admin to the target group, then `/addgroup <chat id>` in the control chat. Only groups owned by `GROUP_OWNER_ID` are accepted (the owner doesn't get bot commands unless also in `ADMIN_IDS`). Accounts that aren't members join via the bot's invite link.
4. `/post` – tap the group button (topic groups then show General + each open topic); progress is shown live.

Also: `/accounts`, `/delaccount <id>`, `/ads`, `/delad <id>`, `/groups`, `/delgroup <id>`, `/cancel`.

## Paid rounds (`/premium on|off`)

With premium on, any user in the control chat can `/post`: paid notice → Agree → network (BEP20 / Polygon / ERC20) → token (USDT / USDC) → address + **Payment done** → reply with the tx hash → the bot checks the receipt on-chain (≥ `PRICE` to `PAY_ADDRESS`, sent after the request, hash not used before) → group/topic buttons for one round. If nothing gets posted the round is kept for later. Buttons only work for the user they were sent to.

## Keeping accounts healthy

- Use real, aged accounts with 2FA; log each in once and keep the session.
- The bot obeys Telegram's flood waits; an account that gets `PeerFlood` is skipped – rest it.
- Session strings are stored in `groupshelp.db` (file mode 600). Keep it private; anyone with it controls those accounts.
