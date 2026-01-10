# Discord Sports Betting Bot

A Discord bot for betting on sports with fictional money. Everything you need for friendly competition with your crew.

## Features
- 💰 Everyone starts with $1,000 fictional money
- 🎮 Interactive betting with button-based UI
- 🔒 Auto-locks betting at game time
- 📊 Leaderboard and stats tracking
- ⚡ Simple slash and prefix commands
- 🤖 Auto-fetch upcoming NFL & College Football games with live odds
- 📺 Dedicated betting channel setup
- ⏰ Fetches games every 15 minutes (6 hour window, 5 min minimum)

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

# Copy the example .env file
cp .env.example .env

# Edit .env and add your bot token:
# DISCORD_TOKEN=your_actual_token_here

# Run the bot
python bot.py
```

## How to Bet

When games are posted, you'll see embeds with three buttons:
- 🏠 **Bet Home** - Click to bet on the home team
- ✈️ **Bet Away** - Click to bet on the away team  
- 👥 **View Bets** - See all bets placed on this game

A modal will pop up asking for your bet amount. Enter it and confirm!

## Commands

### Player Commands (Use ! or /)
- `balance` - Check your cash and W/L record
- `mybets` - See your active bets
- `games` - List all open games
- `leaderboard` - See who's winning big
- `help` - Show all commands (prefix only)

**Note:** Use the buttons on game embeds to place bets!

### Admin Commands (requires Manage Messages permission)
- `creategame <home_team> <away_team> <home_odds> <away_odds> <start_time>`
  - Example: `!creategame Lakers Warriors -110 +150 2026-01-15 19:00`
  - Time is in UTC (YYYY-MM-DD HH:MM format)
  - Odds: negative = favorite, positive = underdog

- `result <game_id> <home/away>` - Declare winner and distribute payouts

Both prefix commands (!) and slash commands (/) are supported for all main features!

## Initial Setup in Discord

After the bot is online, you can use either prefix or slash commands:

**Using Slash Commands (Recommended):**
1. Type `/setup` in any channel
2. Use the interactive selectors:
   - **📺 Channel Selector** - Pick which channel gets game posts
   - **👥 Role Selector** - Optionally set a "bettor" role
   - **⚙️ Settings Dropdown** - Toggle auto-fetch or fetch games manually

**Using Prefix Commands:**
1. **Set the betting channel** (in the channel where you want bets posted):
   ```
   !setup setchannel
   ```

2. **Enable auto game fetching** (optional but recommended):
   ```
   !setup autofetch on
   ```

3. **Manually fetch games** (to test it out):
   ```
   !setup fetch
   ```

The bot will automatically fetch NFL and College Football games every 15 minutes and post them as embeds in your betting channel with live odds from ESPN!

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