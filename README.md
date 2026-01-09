# Discord Sports Betting Bot

A Discord bot for betting on sports with fictional money. Everything you need for friendly competition with your crew.

## Features
- 💰 Everyone starts with $1,000 fictional money
- 🎮 Games posted as embeds with odds
- 🔒 Auto-locks betting at game time
- 📊 Leaderboard and stats tracking
- ⚡ Simple commands

## Setup

### 1. Create a Discord Bot
1. Go to https://discord.com/developers/applications
2. Click "New Application" and give it a name
3. Go to the "Bot" tab on the left
4. Click "Reset Token" and copy the token (you'll need this!)
5. Scroll down and enable these under "Privileged Gateway Intents":
   - MESSAGE CONTENT INTENT
   - SERVER MEMBERS INTENT

### 2. Invite Bot to Your Server
1. Go to the "OAuth2" → "URL Generator" tab
2. Select scopes: `bot`
3. Select bot permissions: 
   - Send Messages
   - Embed Links
   - Read Message History
   - Use Slash Commands
4. Copy the generated URL and open it in your browser to invite the bot

### 3. Install and Run

```bash
# Install dependencies
pip install -r requirements.txt

# Edit .env and add your bot token:
# DISCORD_TOKEN=your_actual_token_here

# Run the bot
python bot.py
```

## Commands

### Player Commands
- `!balance` - Check your cash and W/L record
- `!bet <game_id> <home/away> <amount>` - Place a bet
- `!mybets` - See your active bets
- `!games` - List all open games
- `!leaderboard` - See who's winning big
- `!help` - Show all commands

### Admin Commands (requires Manage Messages permission)
- `!creategame <home_team> <away_team> <home_odds> <away_odds> <start_time>`
  - Example: `!creategame Lakers Warriors -110 +150 2026-01-15 19:00`
  - Time is in UTC (YYYY-MM-DD HH:MM format)
  - Odds: negative = favorite, positive = underdog

- `!result <game_id> <home/away>` - Declare winner and distribute payouts

## How Odds Work
- **Negative odds (e.g., -110)**: Favorite. Bet $110 to win $100
- **Positive odds (e.g., +150)**: Underdog. Bet $100 to win $150

## Example Workflow

1. Admin creates a game:
   ```
   !creategame Lakers Celtics -120 +100 2026-01-15 20:00
   ```

2. Players place bets:
   ```
   !bet Lakers_Celtics_1736971200 home 200
   ```

3. Bot auto-locks betting at game time

4. Admin sets result after the game:
   ```
   !result Lakers_Celtics_1736971200 home
   ```

5. Winners get paid automatically!

## Notes
- Minimum bet: $10
- One bet per person per game
- All data saved in `betting_data.json`
- Bot checks every minute for games that need to be locked
- Everyone starts fresh with $1,000

## Troubleshooting
- If bot doesn't respond: Check that MESSAGE CONTENT INTENT is enabled
- If embeds don't show: Bot needs "Embed Links" permission
- If time conversion is off: All times are in UTC by default

Have fun and may the odds be ever in your favor!
