import discord
from discord.ext import commands, tasks
import json
import aiohttp
from datetime import datetime, timezone, timedelta
import asyncio
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

class BettingSystem:
    def __init__(self):
        self.users = {}
        self.games = {}
        self.bets = {}
        self.config = {'betting_channel_id': None, 'auto_fetch_enabled': False, 'bettor_role_id': None}
        self.load_data()
    
    def load_data(self):
        try:
            with open('betting_data.json', 'r') as f:
                data = json.load(f)
                self.users = data.get('users', {})
                self.games = data.get('games', {})
                self.bets = data.get('bets', {})
                self.config = data.get('config', {'betting_channel_id': None, 'auto_fetch_enabled': False, 'bettor_role_id': None})
        except FileNotFoundError:
            pass
    
    def save_data(self):
        with open('betting_data.json', 'w') as f:
            json.dump({
                'users': self.users, 
                'games': self.games, 
                'bets': self.bets,
                'config': self.config
            }, f, indent=2)
    
    def get_balance(self, user_id: str) -> int:
        if user_id not in self.users:
            self.users[user_id] = {
                'balance': 1000, 
                'total_wagered': 0, 
                'wins': 0, 
                'losses': 0,
                'inventory': {},
                'last_daily': None,
                'loan_amount': 0
            }
            self.save_data()
        # Ensure all users have loan_amount field (for existing users)
        if 'loan_amount' not in self.users[user_id]:
            self.users[user_id]['loan_amount'] = 0
        return self.users[user_id]['balance']
    
    def update_balance(self, user_id: str, amount: int):
        self.get_balance(user_id)
        self.users[user_id]['balance'] += amount
        self.save_data()

betting = BettingSystem()

@bot.event
async def on_ready():
    print(f'{bot.user} is online and ready to take bets!')
    
    # Register persistent views
    bot.add_view(BettingView(None, {}))
    
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} slash commands')
    except Exception as e:
        print(f'Failed to sync commands: {e}')
    check_game_locks.start()
    auto_fetch_games.start()

@tasks.loop(minutes=1)
async def check_game_locks():
    now = datetime.now(timezone.utc)
    for game_id, game in list(betting.games.items()):
        if not game['locked']:
            # Check lock_time first, fall back to start_time
            lock_time_str = game.get('lock_time') or game['start_time']
            lock_time = datetime.fromisoformat(lock_time_str)
            
            if lock_time <= now:
                game['locked'] = True
                betting.save_data()
                channel = bot.get_channel(game['channel_id'])
                if channel:
                    await channel.send(f"🔒 **Betting closed** for {game['home_team']} vs {game['away_team']}!")

@tasks.loop(minutes=15)
async def auto_fetch_games():
    if not betting.config.get('auto_fetch_enabled') or not betting.config.get('betting_channel_id'):
        return
    
    try:
        async with aiohttp.ClientSession() as session:
            # ESPN API for NFL games
            async with session.get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await process_games(data.get('events', []), 'NFL')
            
            # ESPN API for College Football games
            async with session.get('https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await process_games(data.get('events', []), 'CFB')
    except Exception as e:
        print(f"Error fetching games: {e}")

async def process_games(events, sport):
    channel_id = betting.config.get('betting_channel_id')
    if not channel_id:
        return
    
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    
    for event in events:
        try:
            if event['status']['type']['state'] != 'pre':
                continue
            
            game_time = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            time_until_game = (game_time - datetime.now(timezone.utc)).total_seconds()
            
            # Only post games starting within 6 hours, but at least 5 minutes out
            if time_until_game < 300 or time_until_game > 21600:
                continue
            
            home_team = event['competitions'][0]['competitors'][0]['team']['abbreviation']
            away_team = event['competitions'][0]['competitors'][1]['team']['abbreviation']
            game_id = f"{home_team}_{away_team}_{int(game_time.timestamp())}"
            
            if game_id in betting.games:
                continue
            
            # Try to get odds from ESPN
            home_odds = -110.0
            away_odds = -110.0
            
            try:
                comp = event['competitions'][0]
                odds_data = comp.get('odds', [])
                
                if odds_data and len(odds_data) > 0:
                    first_odds = odds_data[0]
                    
                    # Method 1: Try homeTeamOdds/awayTeamOdds structure
                    home_ml = first_odds.get('homeTeamOdds', {}).get('moneyLine')
                    away_ml = first_odds.get('awayTeamOdds', {}).get('moneyLine')
                    
                    if home_ml:
                        home_odds = float(home_ml)
                    if away_ml:
                        away_odds = float(away_ml)
                    
                    # Method 2: Try direct moneyline fields
                    if not home_ml and 'homeMoneyLine' in first_odds:
                        home_odds = float(first_odds['homeMoneyLine'])
                    if not away_ml and 'awayMoneyLine' in first_odds:
                        away_odds = float(first_odds['awayMoneyLine'])
                    
                    # Method 3: Try spread as fallback indicator
                    if home_odds == -110.0 and away_odds == -110.0:
                        spread = first_odds.get('spread')
                        if spread and spread != 0:
                            # Team with negative spread is favored
                            if spread < 0:
                                home_odds = -150.0
                                away_odds = 130.0
                            else:
                                home_odds = 130.0
                                away_odds = -150.0
            except Exception as e:
                print(f"Error parsing odds: {e}")
            
            betting.games[game_id] = {
                'home_team': home_team,
                'away_team': away_team,
                'home_odds': home_odds,
                'away_odds': away_odds,
                'start_time': game_time.isoformat(),
                'locked': False,
                'result': None,
                'channel_id': channel_id,
                'sport': sport
            }
            betting.bets[game_id] = []
            betting.save_data()
            
            emoji = "🏈" if sport == "NFL" else "🏟️"
            embed = discord.Embed(
                title=f"{emoji} {home_team} vs {away_team}",
                description=f"**{sport}** • <t:{int(game_time.timestamp())}:R>",
                color=0x00ff88
            )
            
            # Odds section with better formatting
            embed.add_field(
                name="━━━━━━━━━━━━━━━━━━━━━━━",
                value="\u200b",
                inline=False
            )
            
            # Color-code favorite (green) vs underdog (red)
            home_syntax = "diff\n+" if home_odds < 0 else "diff\n-"
            away_syntax = "diff\n+" if away_odds < 0 else "diff\n-"
            
            embed.add_field(
                name=f"{home_team}",
                value=f"```{home_syntax}{home_odds:+.0f}```",
                inline=True
            )
            embed.add_field(
                name="\u200b",
                value="**VS**",
                inline=True
            )
            embed.add_field(
                name=f"{away_team}",
                value=f"```{away_syntax}{away_odds:+.0f}```",
                inline=True
            )
            
            embed.add_field(
                name="━━━━━━━━━━━━━━━━━━━━━━━",
                value="\u200b",
                inline=False
            )
            
            embed.add_field(name="🕐 Kickoff", value=f"<t:{int(game_time.timestamp())}:F>", inline=False)
            embed.set_footer(text=f"Game ID: {game_id}")
            embed.timestamp = game_time
            
            view = BettingView(game_id, betting.games[game_id])
            role_id = betting.config.get('bettor_role_id')
            content = f"<@&{role_id}>" if role_id else None
            await channel.send(content=content, embed=embed, view=view)
        except Exception as e:
            print(f"Error processing game: {e}")

@bot.command(name='balance')
async def balance(ctx):
    """Check your balance"""
    bal = betting.get_balance(str(ctx.author.id))
    embed = discord.Embed(title="💰 Your Balance", color=0x2ecc71)
    embed.add_field(name="Cash", value=f"${bal:,}", inline=False)
    stats = betting.users[str(ctx.author.id)]
    embed.add_field(name="Record", value=f"{stats['wins']}W - {stats['losses']}L", inline=True)
    embed.add_field(name="Total Wagered", value=f"${stats['total_wagered']:,}", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='setup')
@commands.has_permissions(administrator=True)
async def setup(ctx, action: str = None):
    """Configure betting channel and auto-fetch (Admin only)"""
    if not action:
        current_channel = betting.config.get('betting_channel_id')
        auto_enabled = betting.config.get('auto_fetch_enabled', False)
        bettor_role = betting.config.get('bettor_role_id')
        
        embed = discord.Embed(title="⚙️ Bot Configuration", color=0x95a5a6)
        embed.add_field(
            name="📺 Betting Channel", 
            value=f"<#{current_channel}>" if current_channel else "Not set", 
            inline=False
        )
        embed.add_field(
            name="👥 Bettor Role",
            value=f"<@&{bettor_role}>" if bettor_role else "Not set (optional)",
            inline=False
        )
        embed.add_field(name="🔄 Auto-Fetch Games", value="✅ Enabled" if auto_enabled else "❌ Disabled", inline=False)
        embed.add_field(
            name="Commands", 
            value="**!setup setchannel** - Set current channel as betting channel\n"
                  "**!setup autofetch on/off** - Enable/disable auto game fetching\n"
                  "**!setup fetch** - Manually fetch games now\n\n"
                  "💡 Use `/setup` for interactive selectors (channel/role)", 
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    action = action.lower()
    
    if action == 'setchannel':
        betting.config['betting_channel_id'] = ctx.channel.id
        betting.save_data()
        await ctx.send(f"✅ Betting channel set to {ctx.channel.mention}!")
    
    elif action == 'autofetch':
        await ctx.send("❌ Usage: `!setup autofetch on` or `!setup autofetch off`")
    
    elif action == 'fetch':
        if not betting.config.get('betting_channel_id'):
            await ctx.send("❌ Set a betting channel first with `!setup setchannel`")
            return
        await ctx.send("🔄 Fetching upcoming games...")
        await auto_fetch_games()
        await ctx.send("✅ Games fetched!")
    
    else:
        await ctx.send("❌ Invalid action! Use: setchannel, autofetch, or fetch")

@bot.command(name='autofetch')
@commands.has_permissions(administrator=True)
async def autofetch_toggle(ctx, status: str):
    """Enable or disable auto game fetching"""
    status = status.lower()
    if status not in ['on', 'off']:
        await ctx.send("❌ Use: `!setup autofetch on` or `!setup autofetch off`")
        return
    
    betting.config['auto_fetch_enabled'] = (status == 'on')
    betting.save_data()
    
    if status == 'on':
        await ctx.send("✅ Auto-fetch enabled! Games will be fetched every 12 hours.")
    else:
        await ctx.send("❌ Auto-fetch disabled.")

# Setup View with Channel and Role Selectors
class SetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        
        # Add channel selector
        channel_select = discord.ui.ChannelSelect(
            placeholder="📺 Select Betting Channel",
            channel_types=[discord.ChannelType.text],
            max_values=1
        )
        channel_select.callback = self.channel_callback
        self.add_item(channel_select)
        
        # Add role selector
        role_select = discord.ui.RoleSelect(
            placeholder="👥 Select Bettor Role (Optional)",
            max_values=1,
            min_values=0
        )
        role_select.callback = self.role_callback
        self.add_item(role_select)
    
    async def channel_callback(self, interaction: discord.Interaction):
        channel = interaction.data['values'][0]
        betting.config['betting_channel_id'] = int(channel)
        betting.save_data()
        await interaction.response.send_message(f"✅ Betting channel set to <#{channel}>!", ephemeral=True)
    
    async def role_callback(self, interaction: discord.Interaction):
        values = interaction.data.get('values', [])
        if values:
            role = values[0]
            betting.config['bettor_role_id'] = int(role)
            betting.save_data()
            await interaction.response.send_message(f"✅ Bettor role set to <@&{role}>!", ephemeral=True)
        else:
            betting.config['bettor_role_id'] = None
            betting.save_data()
            await interaction.response.send_message("✅ Bettor role removed!", ephemeral=True)
    
    @discord.ui.select(
        placeholder="⚙️ Other Settings",
        options=[
            discord.SelectOption(label="Enable Auto-Fetch", value="autofetch_on", emoji="✅", description="Auto-fetch games every 15 minutes"),
            discord.SelectOption(label="Disable Auto-Fetch", value="autofetch_off", emoji="❌", description="Turn off automatic game fetching"),
            discord.SelectOption(label="Fetch Games Now", value="fetch", emoji="🔄", description="Manually fetch games right now"),
        ]
    )
    async def settings_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        action = select.values[0]
        
        if action == "autofetch_on":
            betting.config['auto_fetch_enabled'] = True
            betting.save_data()
            await interaction.response.send_message("✅ Auto-fetch enabled! Games will be fetched every 15 minutes.", ephemeral=True)
        
        elif action == "autofetch_off":
            betting.config['auto_fetch_enabled'] = False
            betting.save_data()
            await interaction.response.send_message("❌ Auto-fetch disabled.", ephemeral=True)
        
        elif action == "fetch":
            if not betting.config.get('betting_channel_id'):
                await interaction.response.send_message("❌ Set a betting channel first!", ephemeral=True)
                return
            await interaction.response.send_message("🔄 Fetching upcoming games...", ephemeral=True)
            await auto_fetch_games()

# Betting View with Buttons
class BetModal(discord.ui.Modal, title="Place Your Bet"):
    def __init__(self, game_id: str, team: str, game_data: dict):
        super().__init__()
        self.game_id = game_id
        self.team = team
        self.game_data = game_data
        
    amount = discord.ui.TextInput(
        label="Bet Amount",
        placeholder="Enter amount (minimum $10)",
        required=True,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        try:
            bet_amount = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("❌ Invalid amount!", ephemeral=True)
            return
        
        if bet_amount < 10:
            await interaction.response.send_message("❌ Minimum bet is $10!", ephemeral=True)
            return
        
        game = betting.games.get(self.game_id)
        if not game:
            await interaction.response.send_message("❌ Game not found!", ephemeral=True)
            return
        
        if game['locked']:
            await interaction.response.send_message("🔒 Betting is closed for this game!", ephemeral=True)
            return
        
        balance = betting.get_balance(user_id)
        if bet_amount > balance:
            await interaction.response.send_message(f"❌ You only have ${balance:,}!", ephemeral=True)
            return
        
        existing_bet = next((b for b in betting.bets[self.game_id] if b['user_id'] == user_id), None)
        if existing_bet:
            await interaction.response.send_message("❌ You already have a bet on this game!", ephemeral=True)
            return
        
        betting.update_balance(user_id, -bet_amount)
        betting.users[user_id]['total_wagered'] += bet_amount
        
        odds = game['home_odds'] if self.team == 'home' else game['away_odds']
        potential_win = bet_amount * (1 + abs(odds) / 100) if odds > 0 else bet_amount * (1 + 100 / abs(odds))
        
        # Check for power-ups
        has_2x = betting.users[user_id].get('inventory', {}).get('2x_multiplier', 0) > 0
        has_insurance = betting.users[user_id].get('inventory', {}).get('insurance', 0) > 0
        
        used_items = []
        if has_2x:
            potential_win *= 2
            betting.users[user_id]['inventory']['2x_multiplier'] -= 1
            used_items.append('2x_multiplier')
        
        if has_insurance:
            betting.users[user_id]['inventory']['insurance'] -= 1
            used_items.append('insurance')
        
        betting.bets[self.game_id].append({
            'user_id': user_id,
            'team': self.team,
            'amount': bet_amount,
            'odds': odds,
            'potential_win': potential_win,
            'used_items': used_items
        })
        betting.save_data()
        
        team_name = game['home_team'] if self.team == 'home' else game['away_team']
        
        embed = discord.Embed(title="✅ Bet Confirmed!", color=0x2ecc71)
        embed.add_field(name="🎯 Your Pick", value=f"**{team_name}**", inline=False)
        embed.add_field(name="💵 Wagered", value=f"${bet_amount:,}", inline=True)
        embed.add_field(name="💰 Potential Win", value=f"${potential_win:,.2f}", inline=True)
        embed.add_field(name="📊 Odds", value=f"{odds:+.0f}", inline=True)
        
        if used_items:
            items_text = ""
            if '2x_multiplier' in used_items:
                items_text += "💎 2x Multiplier activated!\n"
            if 'insurance' in used_items:
                items_text += "🛡️ Insurance activated!\n"
            embed.add_field(name="🎁 Items Used", value=items_text, inline=False)
        embed.add_field(name="💵 Wagered", value=f"${bet_amount:,}", inline=True)
        embed.add_field(name="💰 Potential Win", value=f"${potential_win:,.2f}", inline=True)
        embed.add_field(name="📊 Odds", value=f"{odds:+.0f}", inline=True)
        embed.set_footer(text=f"New Balance: ${betting.users[user_id]['balance']:,}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class BettingView(discord.ui.View):
    def __init__(self, game_id: str, game_data: dict):
        super().__init__(timeout=None)
        self.game_id = game_id
        self.game_data = game_data
        
        # Create dynamic buttons with team names
        home_team = game_data.get('home_team', 'Home')
        away_team = game_data.get('away_team', 'Away')
        
        # Home team button
        home_button = discord.ui.Button(
            label=home_team,
            style=discord.ButtonStyle.primary,
            custom_id="bet_home"
        )
        home_button.callback = self.bet_home_callback
        self.add_item(home_button)
        
        # Away team button
        away_button = discord.ui.Button(
            label=away_team,
            style=discord.ButtonStyle.danger,
            custom_id="bet_away"
        )
        away_button.callback = self.bet_away_callback
        self.add_item(away_button)
        
        # View bets button
        view_button = discord.ui.Button(
            label="View Bets",
            style=discord.ButtonStyle.secondary,
            emoji="👥",
            custom_id="view_bets"
        )
        view_button.callback = self.view_bets_callback
        self.add_item(view_button)
        
    async def bet_home_callback(self, interaction: discord.Interaction):
        game_id = interaction.message.embeds[0].footer.text.replace("Game ID: ", "")
        modal = BetModal(game_id, 'home', betting.games.get(game_id, {}))
        await interaction.response.send_modal(modal)
    
    async def bet_away_callback(self, interaction: discord.Interaction):
        game_id = interaction.message.embeds[0].footer.text.replace("Game ID: ", "")
        modal = BetModal(game_id, 'away', betting.games.get(game_id, {}))
        await interaction.response.send_modal(modal)
    
    async def view_bets_callback(self, interaction: discord.Interaction):
        modal = BetModal(game_id, 'home', betting.games.get(game_id, {}))
        await interaction.response.send_modal(modal)
    
    async def view_bets_callback(self, interaction: discord.Interaction):
        game_id = interaction.message.embeds[0].footer.text.replace("Game ID: ", "")
        game = betting.games.get(game_id)
        if not game:
            await interaction.response.send_message("❌ Game not found!", ephemeral=True)
            return
        
        bets_list = betting.bets.get(game_id, [])
        if not bets_list:
            await interaction.response.send_message("📭 No bets placed yet!", ephemeral=True)
            return
        
        home_bets = [b for b in bets_list if b['team'] == 'home']
        away_bets = [b for b in bets_list if b['team'] == 'away']
        
        embed = discord.Embed(title="📊 Current Bets", color=0x9b59b6)
        embed.add_field(name=f"{game['home_team']}", value=f"{len(home_bets)} bet(s)", inline=True)
        embed.add_field(name=f"{game['away_team']}", value=f"{len(away_bets)} bet(s)", inline=True)
        embed.add_field(name="💰 Total Action", value=f"${sum(b['amount'] for b in bets_list):,}", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name='leaderboard')
async def leaderboard(ctx):
    """Show the richest bettors"""
    sorted_users = sorted(betting.users.items(), key=lambda x: x[1]['balance'], reverse=True)[:10]
    embed = discord.Embed(title="🏆 Leaderboard", color=0xf1c40f)
    desc = ""
    for i, (user_id, data) in enumerate(sorted_users, 1):
        user = await bot.fetch_user(int(user_id))
        desc += f"{i}. **{user.name}** - ${data['balance']:,}\n"
    embed.description = desc or "No users yet!"
    await ctx.send(embed=embed)

@bot.command(name='shop')
async def shop(ctx):
    """View the shop"""
    embed = discord.Embed(title="🏪 Shop", color=0xf1c40f)
    embed.add_field(
        name="💎 2x Multiplier",
        value="**$500** - One-time use 2x payout on your next winning bet",
        inline=False
    )
    embed.add_field(
        name="🛡️ Insurance",
        value="**$300** - Get 50% of your bet back on your next loss",
        inline=False
    )
    embed.add_field(
        name="🎰 Slot Spin",
        value="**Use:** `/slots <amount>` - Try your luck!",
        inline=False
    )
    embed.set_footer(text="Use /buy <item> to purchase • Use /inventory to view your items")
    await ctx.send(embed=embed)

@bot.command(name='buy')
async def buy(ctx, *, item: str):
    """Buy an item from the shop"""
    user_id = str(ctx.author.id)
    balance = betting.get_balance(user_id)
    item = item.lower()
    
    shop_items = {
        '2x': {'price': 500, 'name': '2x_multiplier', 'display': '💎 2x Multiplier'},
        'multiplier': {'price': 500, 'name': '2x_multiplier', 'display': '💎 2x Multiplier'},
        '2x multiplier': {'price': 500, 'name': '2x_multiplier', 'display': '💎 2x Multiplier'},
        'insurance': {'price': 300, 'name': 'insurance', 'display': '🛡️ Insurance'}
    }
    
    if item not in shop_items:
        await ctx.send("❌ Item not found! Use `!shop` to see available items.")
        return
    
    item_data = shop_items[item]
    price = item_data['price']
    
    if balance < price:
        await ctx.send(f"❌ You need ${price:,} but only have ${balance:,}!")
        return
    
    betting.update_balance(user_id, -price)
    
    # Add to inventory
    if 'inventory' not in betting.users[user_id]:
        betting.users[user_id]['inventory'] = {}
    
    inv_name = item_data['name']
    betting.users[user_id]['inventory'][inv_name] = betting.users[user_id]['inventory'].get(inv_name, 0) + 1
    betting.save_data()
    
    await ctx.send(f"✅ Purchased {item_data['display']} for ${price:,}!")

@bot.command(name='inventory')
async def inventory(ctx):
    """View your inventory"""
    user_id = str(ctx.author.id)
    betting.get_balance(user_id)
    inv = betting.users[user_id].get('inventory', {})
    
    if not inv or all(v == 0 for v in inv.values()):
        await ctx.send("📦 Your inventory is empty! Visit `!shop` to buy items.")
        return
    
    embed = discord.Embed(title="📦 Your Inventory", color=0x9b59b6)
    
    item_names = {
        '2x_multiplier': '💎 2x Multiplier',
        'insurance': '🛡️ Insurance'
    }
    
    for item, count in inv.items():
        if count > 0:
            display = item_names.get(item, item)
            embed.add_field(name=display, value=f"Quantity: **{count}**", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='daily')
async def daily(ctx):
    """Claim your daily bonus"""
    user_id = str(ctx.author.id)
    betting.get_balance(user_id)
    
    last_daily = betting.users[user_id].get('last_daily')
    now = datetime.now(timezone.utc)
    
    if last_daily:
        last_claim = datetime.fromisoformat(last_daily)
        time_diff = (now - last_claim).total_seconds()
        
        if time_diff < 86400:  # 24 hours
            hours_left = (86400 - time_diff) / 3600
            await ctx.send(f"⏰ Daily already claimed! Come back in {hours_left:.1f} hours.")
            return
    
    daily_amount = 250
    betting.update_balance(user_id, daily_amount)
    betting.users[user_id]['last_daily'] = now.isoformat()
    betting.save_data()
    
    await ctx.send(f"💰 Claimed your daily bonus of ${daily_amount}! New balance: ${betting.users[user_id]['balance']:,}")

@bot.command(name='slots')
async def slots(ctx, amount: int):
    """Spin the slot machine"""
    user_id = str(ctx.author.id)
    balance = betting.get_balance(user_id)
    
    if amount < 10:
        await ctx.send("❌ Minimum bet is $10!")
        return
    
    if amount > balance:
        await ctx.send(f"❌ You only have ${balance:,}!")
        return
    
    betting.update_balance(user_id, -amount)
    
    import random
    emojis = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']
    weights = [30, 25, 20, 15, 7, 3]  # 💎 and 7️⃣ are rare
    
    slots = random.choices(emojis, weights=weights, k=3)
    
    # Calculate winnings
    if slots[0] == slots[1] == slots[2]:
        if slots[0] == '7️⃣':
            multiplier = 10  # Jackpot!
        elif slots[0] == '💎':
            multiplier = 5
        else:
            multiplier = 3
        winnings = amount * multiplier
    elif slots[0] == slots[1] or slots[1] == slots[2]:
        multiplier = 1.5
        winnings = int(amount * multiplier)
    else:
        winnings = 0
    
    if winnings > 0:
        betting.update_balance(user_id, winnings)
        result = f"**WIN!** You won ${winnings:,}! 💰"
    else:
        result = "**LOST!** Better luck next time! 😢"
    
    embed = discord.Embed(title="🎰 Slot Machine", color=0xf1c40f if winnings > 0 else 0xe74c3c)
    embed.add_field(name="Result", value=" | ".join(slots), inline=False)
    embed.add_field(name="Outcome", value=result, inline=False)
    embed.set_footer(text=f"New Balance: ${betting.users[user_id]['balance']:,}")
    
    await ctx.send(embed=embed)

@bot.command(name='send')
async def send(ctx, member: discord.Member, amount: int):
    """Send money to another user"""
    sender_id = str(ctx.author.id)
    receiver_id = str(member.id)
    
    if sender_id == receiver_id:
        await ctx.send("❌ You can't send money to yourself!")
        return
    
    if amount < 1:
        await ctx.send("❌ Amount must be at least $1!")
        return
    
    balance = betting.get_balance(sender_id)
    if amount > balance:
        await ctx.send(f"❌ You only have ${balance:,}!")
        return
    
    betting.update_balance(sender_id, -amount)
    betting.get_balance(receiver_id)
    betting.update_balance(receiver_id, amount)
    
    await ctx.send(f"✅ Sent ${amount:,} to {member.mention}!")

@bot.command(name='creategame')
@commands.has_permissions(manage_messages=True)
async def creategame(ctx):
    """Create a game from live/upcoming matchups"""
    await ctx.send("⚠️ Use `/creategame` slash command for interactive game selection!", ephemeral=True if hasattr(ctx, 'ephemeral') else False)

@bot.command(name='bet')
async def bet(ctx, game_id: str, team_choice: str, amount: int):
    """Place a bet on a game"""
    user_id = str(ctx.author.id)
    
    if game_id not in betting.games:
        await ctx.send("❌ Game not found!")
        return
    
    game = betting.games[game_id]
    
    if game['locked']:
        await ctx.send("🔒 Betting is closed for this game!")
        return
    
    if game['result']:
        await ctx.send("❌ This game is already finished!")
        return
    
    team_choice = team_choice.lower()
    if team_choice not in ['home', 'away']:
        await ctx.send("❌ Choose 'home' or 'away'!")
        return
    
    if amount < 10:
        await ctx.send("❌ Minimum bet is $10!")
        return
    
    balance = betting.get_balance(user_id)
    if amount > balance:
        await ctx.send(f"❌ You only have ${balance:,}!")
        return
    
    existing_bet = next((b for b in betting.bets[game_id] if b['user_id'] == user_id), None)
    if existing_bet:
        await ctx.send("❌ You already have a bet on this game!")
        return
    
    betting.update_balance(user_id, -amount)
    betting.users[user_id]['total_wagered'] += amount
    
    odds = game['home_odds'] if team_choice == 'home' else game['away_odds']
    potential_win = amount * (1 + abs(odds) / 100) if odds > 0 else amount * (1 + 100 / abs(odds))
    
    betting.bets[game_id].append({
        'user_id': user_id,
        'team': team_choice,
        'amount': amount,
        'odds': odds,
        'potential_win': potential_win
    })
    betting.save_data()
    
    team_name = game['home_team'] if team_choice == 'home' else game['away_team']
    
    # Game info embed
    game_embed = discord.Embed(title="🎮 Game Info", color=0x3498db)
    game_embed.add_field(name="Matchup", value=f"**{game['home_team']}** vs **{game['away_team']}**", inline=False)
    game_embed.add_field(name=f"{game['home_team']} Odds", value=f"{game['home_odds']:+.2f}", inline=True)
    game_embed.add_field(name=f"{game['away_team']} Odds", value=f"{game['away_odds']:+.2f}", inline=True)
    game_time = datetime.fromisoformat(game['start_time'])
    game_embed.add_field(name="Game Time", value=f"<t:{int(game_time.timestamp())}:F>\n<t:{int(game_time.timestamp())}:R>", inline=False)
    
    # Bet confirmation embed
    bet_embed = discord.Embed(title="✅ Bet Placed!", color=0x2ecc71)
    bet_embed.add_field(name="Your Pick", value=team_name, inline=True)
    bet_embed.add_field(name="Wagered", value=f"${amount:,}", inline=True)
    bet_embed.add_field(name="Potential Win", value=f"${potential_win:,.2f}", inline=True)
    
    await ctx.send(embeds=[game_embed, bet_embed])

@bot.command(name='mybets')
async def mybets(ctx):
    """View your active bets"""
    user_id = str(ctx.author.id)
    active_bets = []
    
    for game_id, bets in betting.bets.items():
        user_bet = next((b for b in bets if b['user_id'] == user_id), None)
        if user_bet and not betting.games[game_id].get('result'):
            game = betting.games[game_id]
            team_name = game['home_team'] if user_bet['team'] == 'home' else game['away_team']
            active_bets.append(f"**{game['home_team']} vs {game['away_team']}**\n└ {team_name} - ${user_bet['amount']:,} → ${user_bet['potential_win']:,.2f}")
    
    embed = discord.Embed(title="🎲 Your Active Bets", color=0x9b59b6)
    embed.description = "\n\n".join(active_bets) if active_bets else "No active bets!"
    await ctx.send(embed=embed)

@bot.command(name='games')
async def games(ctx):
    """List all active games"""
    active_games = [(gid, g) for gid, g in betting.games.items() if not g.get('result')]
    
    if not active_games:
        await ctx.send("No active games right now!")
        return
    
    embed = discord.Embed(title="🏟️ Active Games", color=0xe74c3c)
    for game_id, game in active_games:
        status = "🔒 Locked" if game['locked'] else "✅ Open"
        embed.add_field(
            name=f"{game['home_team']} vs {game['away_team']}",
            value=f"{status} | {game['home_odds']:+.1f} / {game['away_odds']:+.1f}\nID: `{game_id}`",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name='result')
@commands.has_permissions(manage_messages=True)
async def result(ctx, game_id: str, winner: str):
    """Set game result (home/away)"""
    if game_id not in betting.games:
        await ctx.send("❌ Game not found!")
        return
    
    game = betting.games[game_id]
    winner = winner.lower()
    
    if winner not in ['home', 'away']:
        await ctx.send("❌ Winner must be 'home' or 'away'!")
        return
    
    game['result'] = winner
    game['locked'] = True
    
    payouts = []
    for bet in betting.bets[game_id]:
        user_id = bet['user_id']
        if bet['team'] == winner:
            payout = bet['potential_win']
            betting.update_balance(user_id, int(payout))
            betting.users[user_id]['wins'] += 1
            payouts.append((user_id, payout, True))
        else:
            betting.users[user_id]['losses'] += 1
            payouts.append((user_id, 0, False))
    
    betting.save_data()
    
    winner_team = game['home_team'] if winner == 'home' else game['away_team']
    embed = discord.Embed(title="🎉 Game Result", color=0x2ecc71)
    embed.add_field(name="Game", value=f"{game['home_team']} vs {game['away_team']}", inline=False)
    embed.add_field(name="Winner", value=winner_team, inline=False)
    
    winners_text = ""
    losers_text = ""
    for user_id, payout, won in payouts:
        user = await bot.fetch_user(int(user_id))
        if won:
            winners_text += f"✅ {user.name}: +${payout:,.2f}\n"
        else:
            losers_text += f"❌ {user.name}\n"
    
    if winners_text:
        embed.add_field(name="Winners", value=winners_text, inline=True)
    if losers_text:
        embed.add_field(name="Losers", value=losers_text, inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='help')
async def help_cmd(ctx):
    """Show all commands"""
    embed = discord.Embed(title="🎰 Sports Betting Bot Commands", color=0x3498db)
    
    player_cmds = """
    **Main Commands**
    `/balance` or `!balance` - Check your balance and stats
    `/mybets` or `!mybets` - View your active bets
    `/games` or `!games` - List all active games
    `/leaderboard` or `!leaderboard` - Top 10 richest bettors
    
    **Placing Bets**
    Click the buttons on game embeds to place bets!
    🏠 Bet Home | ✈️ Bet Away | 👥 View Bets
    """
    
    admin_cmds = """
    **Setup Commands (Administrator)**
    `!setup` - View configuration (prefix)
    `/setup` - Interactive setup with dropdown menu (slash)
    `!setup setchannel` - Set betting channel
    `!setup autofetch on/off` - Toggle auto-fetch
    `!setup fetch` - Manually fetch games
    
    **Game Management (Manage Messages)**
    `!creategame <home> <away> <home_odds> <away_odds> <time>`
    `!result <game_id> <home/away>` - Set winner and pay out
    
    `/creategame` and `/result` also available as slash commands
    
    Example: `!creategame Lakers Warriors -110 +150 2026-01-15 19:00`
    """
    
    embed.add_field(name="Player Commands", value=player_cmds, inline=False)
    embed.add_field(name="Admin Commands", value=admin_cmds, inline=False)
    embed.set_footer(text="Everyone starts with $1,000 | Minimum bet: $10 | Auto-fetch: NFL & CFB")
    await ctx.send(embed=embed)

# Slash Commands
@bot.tree.command(name="setup", description="Configure bot settings (Admin only)")
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_setup(interaction: discord.Interaction):
    current_channel = betting.config.get('betting_channel_id')
    auto_enabled = betting.config.get('auto_fetch_enabled', False)
    bettor_role = betting.config.get('bettor_role_id')
    
    embed = discord.Embed(title="⚙️ Bot Configuration", color=0x95a5a6)
    embed.add_field(
        name="📺 Betting Channel", 
        value=f"<#{current_channel}>" if current_channel else "Not set", 
        inline=False
    )
    embed.add_field(
        name="👥 Bettor Role",
        value=f"<@&{bettor_role}>" if bettor_role else "Not set (optional)",
        inline=False
    )
    embed.add_field(name="🔄 Auto-Fetch Games", value="✅ Enabled" if auto_enabled else "❌ Disabled", inline=False)
    embed.add_field(
        name="Use the selectors below to configure:",
        value="📺 Select betting channel\n👥 Select bettor role (optional)\n⚙️ Other settings",
        inline=False
    )
    
    view = SetupView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="balance", description="Check your balance and stats")
async def slash_balance(interaction: discord.Interaction):
    bal = betting.get_balance(str(interaction.user.id))
    embed = discord.Embed(title="💰 Your Balance", color=0x2ecc71)
    embed.add_field(name="Cash", value=f"${bal:,}", inline=False)
    stats = betting.users[str(interaction.user.id)]
    embed.add_field(name="Record", value=f"{stats['wins']}W - {stats['losses']}L", inline=True)
    embed.add_field(name="Total Wagered", value=f"${stats['total_wagered']:,}", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Show the richest bettors")
async def slash_leaderboard(interaction: discord.Interaction):
    sorted_users = sorted(betting.users.items(), key=lambda x: x[1]['balance'], reverse=True)[:10]
    embed = discord.Embed(title="🏆 Leaderboard", color=0xf1c40f)
    desc = ""
    for i, (user_id, data) in enumerate(sorted_users, 1):
        user = await bot.fetch_user(int(user_id))
        desc += f"{i}. **{user.name}** - ${data['balance']:,}\n"
    embed.description = desc or "No users yet!"
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="bet", description="Place a bet on a game")
async def slash_bet(interaction: discord.Interaction, game_id: str, team: str, amount: int):
    user_id = str(interaction.user.id)
    
    if game_id not in betting.games:
        await interaction.response.send_message("❌ Game not found!", ephemeral=True)
        return
    
    game = betting.games[game_id]
    
    if game['locked']:
        await interaction.response.send_message("🔒 Betting is closed for this game!", ephemeral=True)
        return
    
    if game['result']:
        await interaction.response.send_message("❌ This game is already finished!", ephemeral=True)
        return
    
    team_choice = team.lower()
    if team_choice not in ['home', 'away']:
        await interaction.response.send_message("❌ Choose 'home' or 'away'!", ephemeral=True)
        return
    
    if amount < 10:
        await interaction.response.send_message("❌ Minimum bet is $10!", ephemeral=True)
        return
    
    balance = betting.get_balance(user_id)
    if amount > balance:
        await interaction.response.send_message(f"❌ You only have ${balance:,}!", ephemeral=True)
        return
    
    existing_bet = next((b for b in betting.bets[game_id] if b['user_id'] == user_id), None)
    if existing_bet:
        await interaction.response.send_message("❌ You already have a bet on this game!", ephemeral=True)
        return
    
    betting.update_balance(user_id, -amount)
    betting.users[user_id]['total_wagered'] += amount
    
    odds = game['home_odds'] if team_choice == 'home' else game['away_odds']
    potential_win = amount * (1 + abs(odds) / 100) if odds > 0 else amount * (1 + 100 / abs(odds))
    
    betting.bets[game_id].append({
        'user_id': user_id,
        'team': team_choice,
        'amount': amount,
        'odds': odds,
        'potential_win': potential_win
    })
    betting.save_data()
    
    team_name = game['home_team'] if team_choice == 'home' else game['away_team']
    
    # Game info embed
    game_embed = discord.Embed(title="🎮 Game Info", color=0x3498db)
    game_embed.add_field(name="Matchup", value=f"**{game['home_team']}** vs **{game['away_team']}**", inline=False)
    game_embed.add_field(name=f"{game['home_team']} Odds", value=f"{game['home_odds']:+.2f}", inline=True)
    game_embed.add_field(name=f"{game['away_team']} Odds", value=f"{game['away_odds']:+.2f}", inline=True)
    game_time = datetime.fromisoformat(game['start_time'])
    game_embed.add_field(name="Game Time", value=f"<t:{int(game_time.timestamp())}:F>\n<t:{int(game_time.timestamp())}:R>", inline=False)
    
    # Bet confirmation embed
    bet_embed = discord.Embed(title="✅ Bet Placed!", color=0x2ecc71)
    bet_embed.add_field(name="Your Pick", value=team_name, inline=True)
    bet_embed.add_field(name="Wagered", value=f"${amount:,}", inline=True)
    bet_embed.add_field(name="Potential Win", value=f"${potential_win:,.2f}", inline=True)
    
    await interaction.response.send_message(embeds=[game_embed, bet_embed])

@bot.tree.command(name="mybets", description="View your active bets")
async def slash_mybets(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    active_bets = []
    
    for game_id, bets in betting.bets.items():
        user_bet = next((b for b in bets if b['user_id'] == user_id), None)
        if user_bet and not betting.games[game_id].get('result'):
            game = betting.games[game_id]
            team_name = game['home_team'] if user_bet['team'] == 'home' else game['away_team']
            active_bets.append(f"**{game['home_team']} vs {game['away_team']}**\n└ {team_name} - ${user_bet['amount']:,} → ${user_bet['potential_win']:,.2f}")
    
    embed = discord.Embed(title="🎲 Your Active Bets", color=0x9b59b6)
    embed.description = "\n\n".join(active_bets) if active_bets else "No active bets!"
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="games", description="List all active games")
async def slash_games(interaction: discord.Interaction):
    active_games = [(gid, g) for gid, g in betting.games.items() if not g.get('result')]
    
    if not active_games:
        await interaction.response.send_message("No active games right now!")
        return
    
    embed = discord.Embed(title="🏟️ Active Games", color=0xe74c3c)
    for game_id, game in active_games:
        status = "🔒 Locked" if game['locked'] else "✅ Open"
        embed.add_field(
            name=f"{game['home_team']} vs {game['away_team']}",
            value=f"{status} | {game['home_odds']:+.1f} / {game['away_odds']:+.1f}\nID: `{game_id}`",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="refresh", description="Refresh a game embed with new styling (Admin only)")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def slash_refresh(interaction: discord.Interaction, game_id: str, message_id: str):
    """Refresh an existing game embed"""
    if game_id not in betting.games:
        await interaction.response.send_message("❌ Game not found!", ephemeral=True)
        return
    
    game = betting.games[game_id]
    channel_id = game['channel_id']
    channel = bot.get_channel(channel_id)
    
    if not channel:
        await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
        return
    
    try:
        message = await channel.fetch_message(int(message_id))
    except:
        await interaction.response.send_message("❌ Message not found!", ephemeral=True)
        return
    
    # Rebuild embed with new styling
    home_team = game['home_team']
    away_team = game['away_team']
    home_odds = game['home_odds']
    away_odds = game['away_odds']
    sport = game.get('sport', 'NFL')
    game_time = datetime.fromisoformat(game['start_time'])
    
    emoji = "🏈" if sport == "NFL" else "🏟️"
    embed = discord.Embed(
        title=f"{emoji} {home_team} vs {away_team}",
        description=f"**{sport}** • <t:{int(game_time.timestamp())}:R>",
        color=0x00ff88
    )
    
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━━━━━━",
        value="\u200b",
        inline=False
    )
    
    # Color-code favorite (green) vs underdog (red)
    home_syntax = "diff\n+" if home_odds < 0 else "diff\n-"
    away_syntax = "diff\n+" if away_odds < 0 else "diff\n-"
    
    embed.add_field(
        name=f"{home_team}",
        value=f"```{home_syntax}{home_odds:+.0f}```",
        inline=True
    )
    embed.add_field(
        name="\u200b",
        value="**VS**",
        inline=True
    )
    embed.add_field(
        name=f"{away_team}",
        value=f"```{away_syntax}{away_odds:+.0f}```",
        inline=True
    )
    
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━━━━━━",
        value="\u200b",
        inline=False
    )
    
    if game.get('lock_time'):
        lock_timestamp = int(datetime.fromisoformat(game['lock_time']).timestamp())
        embed.add_field(name="🔒 Betting Closes", value=f"<t:{lock_timestamp}:R>", inline=True)
    
    embed.add_field(name="🕐 Kickoff", value=f"<t:{int(game_time.timestamp())}:F>", inline=False)
    embed.set_footer(text=f"Game ID: {game_id}")
    embed.timestamp = game_time
    
    view = BettingView(game_id, game)
    await message.edit(embed=embed, view=view)
    await interaction.response.send_message("✅ Game embed refreshed!", ephemeral=True)


@bot.tree.command(name="creategame", description="Add a game from live/upcoming matchups (Admin only)")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def slash_creategame(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    # Fetch live and upcoming games
    available_games = []
    
    try:
        async with aiohttp.ClientSession() as session:
            # Get NFL games
            async with session.get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for event in data.get('events', []):
                        try:
                            status = event['status']['type']['state']
                            if status in ['pre', 'in']:  # Include pre-game and in-progress
                                game_time = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                                home_team = event['competitions'][0]['competitors'][0]['team']['abbreviation']
                                away_team = event['competitions'][0]['competitors'][1]['team']['abbreviation']
                                
                                # Get scores if live
                                home_score = None
                                away_score = None
                                if status == 'in':
                                    try:
                                        home_score = event['competitions'][0]['competitors'][0].get('score', '0')
                                        away_score = event['competitions'][0]['competitors'][1].get('score', '0')
                                    except:
                                        pass
                                
                                # Get odds
                                home_odds = -110.0
                                away_odds = -110.0
                                try:
                                    odds_data = event['competitions'][0].get('odds', [])
                                    if odds_data:
                                        first_odds = odds_data[0]
                                        
                                        # Try multiple fields for moneyline
                                        home_ml = first_odds.get('homeTeamOdds', {}).get('moneyLine')
                                        away_ml = first_odds.get('awayTeamOdds', {}).get('moneyLine')
                                        
                                        if not home_ml:
                                            home_ml = first_odds.get('homeMoneyLine')
                                        if not away_ml:
                                            away_ml = first_odds.get('awayMoneyLine')
                                        
                                        if home_ml:
                                            home_odds = float(home_ml)
                                        if away_ml:
                                            away_odds = float(away_ml)
                                        
                                        # If still no moneyline, use spread to estimate
                                        if home_odds == -110.0 and away_odds == -110.0:
                                            spread = first_odds.get('spread')
                                            if spread is not None:
                                                spread = float(spread)
                                                if spread < -7:
                                                    home_odds = -300.0
                                                    away_odds = 250.0
                                                elif spread < -3:
                                                    home_odds = -180.0
                                                    away_odds = 155.0
                                                elif spread < -0.5:
                                                    home_odds = -130.0
                                                    away_odds = 110.0
                                                elif spread > 7:
                                                    home_odds = 250.0
                                                    away_odds = -300.0
                                                elif spread > 3:
                                                    home_odds = 155.0
                                                    away_odds = -180.0
                                                elif spread > 0.5:
                                                    home_odds = 110.0
                                                    away_odds = -130.0
                                except:
                                    pass
                                
                                available_games.append({
                                    'home': home_team,
                                    'away': away_team,
                                    'time': game_time,
                                    'home_odds': home_odds,
                                    'away_odds': away_odds,
                                    'sport': 'NFL',
                                    'status': status,
                                    'home_score': home_score,
                                    'away_score': away_score
                                })
                        except:
                            continue
            
            # Get CFB games
            async with session.get('https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for event in data.get('events', []):
                        try:
                            status = event['status']['type']['state']
                            if status in ['pre', 'in']:
                                game_time = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                                home_team = event['competitions'][0]['competitors'][0]['team']['abbreviation']
                                away_team = event['competitions'][0]['competitors'][1]['team']['abbreviation']
                                
                                # Get scores if live
                                home_score = None
                                away_score = None
                                if status == 'in':
                                    try:
                                        home_score = event['competitions'][0]['competitors'][0].get('score', '0')
                                        away_score = event['competitions'][0]['competitors'][1].get('score', '0')
                                    except:
                                        pass
                                
                                home_odds = -110.0
                                away_odds = -110.0
                                try:
                                    odds_data = event['competitions'][0].get('odds', [])
                                    if odds_data:
                                        first_odds = odds_data[0]
                                        
                                        # Try multiple fields for moneyline
                                        home_ml = first_odds.get('homeTeamOdds', {}).get('moneyLine')
                                        away_ml = first_odds.get('awayTeamOdds', {}).get('moneyLine')
                                        
                                        if not home_ml:
                                            home_ml = first_odds.get('homeMoneyLine')
                                        if not away_ml:
                                            away_ml = first_odds.get('awayMoneyLine')
                                        
                                        if home_ml:
                                            home_odds = float(home_ml)
                                        if away_ml:
                                            away_odds = float(away_ml)
                                        
                                        # If still no moneyline, use spread to estimate
                                        if home_odds == -110.0 and away_odds == -110.0:
                                            spread = first_odds.get('spread')
                                            if spread is not None:
                                                spread = float(spread)
                                                if spread < -7:
                                                    home_odds = -300.0
                                                    away_odds = 250.0
                                                elif spread < -3:
                                                    home_odds = -180.0
                                                    away_odds = 155.0
                                                elif spread < -0.5:
                                                    home_odds = -130.0
                                                    away_odds = 110.0
                                                elif spread > 7:
                                                    home_odds = 250.0
                                                    away_odds = -300.0
                                                elif spread > 3:
                                                    home_odds = 155.0
                                                    away_odds = -180.0
                                                elif spread > 0.5:
                                                    home_odds = 110.0
                                                    away_odds = -130.0
                                except:
                                    pass
                                
                                available_games.append({
                                    'home': home_team,
                                    'away': away_team,
                                    'time': game_time,
                                    'home_odds': home_odds,
                                    'away_odds': away_odds,
                                    'sport': 'CFB',
                                    'status': status,
                                    'home_score': home_score,
                                    'away_score': away_score
                                })
                        except:
                            continue
    except Exception as e:
        await interaction.followup.send(f"❌ Error fetching games: {e}", ephemeral=True)
        return
    
    if not available_games:
        await interaction.followup.send("❌ No live or upcoming games found!", ephemeral=True)
        return
    
    # Create dropdown with games
    class GameSelectView(discord.ui.View):
        def __init__(self, games_list):
            super().__init__(timeout=180)
            
            options = []
            for i, game in enumerate(games_list[:25]):  # Discord limit
                status_emoji = "🔴" if game['status'] == 'in' else "⏰"
                label = f"{game['home']} vs {game['away']}"
                description = f"{game['sport']} • {status_emoji} {game['status'].upper()}"
                options.append(discord.SelectOption(
                    label=label[:100],
                    description=description[:100],
                    value=str(i)
                ))
            
            select = discord.ui.Select(
                placeholder="Select a game to add...",
                options=options
            )
            select.callback = self.select_callback
            self.add_item(select)
            self.games_list = games_list
        
        async def select_callback(self, interaction: discord.Interaction):
            selected_game = self.games_list[int(interaction.data['values'][0])]
            
            # Show modal to set betting duration
            class BettingDurationModal(discord.ui.Modal, title="Set Betting Duration"):
                duration = discord.ui.TextInput(
                    label="Minutes until betting closes",
                    placeholder="Enter minutes (e.g., 30) or leave blank for game start",
                    required=False,
                    max_length=3
                )
                
                def __init__(self, game_data):
                    super().__init__()
                    self.game_data = game_data
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    game_time = self.game_data['time']
                    home_team = self.game_data['home']
                    away_team = self.game_data['away']
                    home_odds = self.game_data['home_odds']
                    away_odds = self.game_data['away_odds']
                    sport = self.game_data['sport']
                    
                    game_id = f"{home_team}_{away_team}_{int(game_time.timestamp())}"
                    
                    # Check if already exists
                    if game_id in betting.games:
                        await modal_interaction.response.send_message("❌ This game is already in the betting channel!", ephemeral=True)
                        return
                    
                    # Calculate lock time
                    lock_time = None
                    if self.duration.value.strip():
                        try:
                            minutes = int(self.duration.value)
                            lock_time = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
                        except ValueError:
                            await modal_interaction.response.send_message("❌ Invalid duration! Using game start time.", ephemeral=True)
                    
                    betting.games[game_id] = {
                        'home_team': home_team,
                        'away_team': away_team,
                        'home_odds': home_odds,
                        'away_odds': away_odds,
                        'start_time': game_time.isoformat(),
                        'lock_time': lock_time,
                        'locked': False,
                        'result': None,
                        'channel_id': betting.config.get('betting_channel_id', modal_interaction.channel_id),
                        'sport': sport
                    }
                    betting.bets[game_id] = []
                    betting.save_data()
                    
                    # Post to betting channel
                    channel_id = betting.config.get('betting_channel_id', modal_interaction.channel_id)
                    channel = bot.get_channel(channel_id)
                    
                    emoji = "🏈" if sport == "NFL" else "🏟️"
                    
                    # Build description with live score if available
                    description_parts = [f"**{sport}**"]
                    if self.game_data.get('home_score') is not None and self.game_data.get('away_score') is not None:
                        description_parts.append(f"🔴 **LIVE** • {self.game_data['home_score']}-{self.game_data['away_score']}")
                    else:
                        description_parts.append(f"<t:{int(game_time.timestamp())}:R>")
                    
                    embed = discord.Embed(
                        title=f"{emoji} {home_team} vs {away_team}",
                        description=" • ".join(description_parts),
                        color=0xff4444 if self.game_data.get('home_score') is not None else 0x00ff88
                    )
                    
                    embed.add_field(
                        name="━━━━━━━━━━━━━━━━━━━━━━━",
                        value="\u200b",
                        inline=False
                    )
                    
                    # Determine favorite (negative odds) vs underdog (positive odds)
                    home_syntax = "diff\n+" if home_odds < 0 else "diff\n-"
                    away_syntax = "diff\n+" if away_odds < 0 else "diff\n-"
                    
                    embed.add_field(
                        name=f"{home_team}",
                        value=f"```{home_syntax}{home_odds:+.0f}```",
                        inline=True
                    )
                    embed.add_field(
                        name="\u200b",
                        value="**VS**",
                        inline=True
                    )
                    embed.add_field(
                        name=f"{away_team}",
                        value=f"```{away_syntax}{away_odds:+.0f}```",
                        inline=True
                    )
                    
                    embed.add_field(
                        name="━━━━━━━━━━━━━━━━━━━━━━━",
                        value="\u200b",
                        inline=False
                    )
                    
                    if lock_time:
                        lock_timestamp = int(datetime.fromisoformat(lock_time).timestamp())
                        embed.add_field(name="🔒 Betting Closes", value=f"<t:{lock_timestamp}:R>", inline=True)
                    
                    embed.add_field(name="🕐 Kickoff", value=f"<t:{int(game_time.timestamp())}:F>", inline=False)
                    embed.set_footer(text=f"Game ID: {game_id}")
                    embed.timestamp = game_time
                    
                    view = BettingView(game_id, betting.games[game_id])
                    if channel:
                        # Ping role if configured
                        role_id = betting.config.get('bettor_role_id')
                        content = f"<@&{role_id}>" if role_id else None
                        await channel.send(content=content, embed=embed, view=view)
                    
                    duration_text = f" (closes in {self.duration.value} min)" if self.duration.value.strip() else ""
                    await modal_interaction.response.send_message(f"✅ Game added: {home_team} vs {away_team}{duration_text}", ephemeral=True)
            
            modal = BettingDurationModal(selected_game)
            await interaction.response.send_modal(modal)
    
    view = GameSelectView(available_games)
    await interaction.followup.send(f"📋 Found {len(available_games)} available games. Select one:", view=view, ephemeral=True)

@bot.tree.command(name="result", description="Set game result and pay winners (Admin only)")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def slash_result(interaction: discord.Interaction, game_id: str, winner: str):
    if game_id not in betting.games:
        await interaction.response.send_message("❌ Game not found!", ephemeral=True)
        return
    
    game = betting.games[game_id]
    winner = winner.lower()
    
    if winner not in ['home', 'away']:
        await interaction.response.send_message("❌ Winner must be 'home' or 'away'!", ephemeral=True)
        return
    
    game['result'] = winner
    game['locked'] = True
    
    payouts = []
    for bet in betting.bets[game_id]:
        user_id = bet['user_id']
        if bet['team'] == winner:
            payout = bet['potential_win']
            betting.update_balance(user_id, int(payout))
            betting.users[user_id]['wins'] += 1
            payouts.append((user_id, payout, True, []))
        else:
            betting.users[user_id]['losses'] += 1
            # Check for insurance
            used_items = bet.get('used_items', [])
            if 'insurance' in used_items:
                refund = int(bet['amount'] * 0.5)
                betting.update_balance(user_id, refund)
                payouts.append((user_id, refund, False, ['insurance']))
            elif '2x_multiplier' in used_items:
                # Double loss penalty - lose additional bet amount (if they can afford it)
                current_balance = betting.users[user_id]['balance']
                penalty = min(bet['amount'], current_balance)  # Can't go negative
                if penalty > 0:
                    betting.update_balance(user_id, -penalty)
                    payouts.append((user_id, -penalty, False, ['2x_penalty']))
                else:
                    payouts.append((user_id, 0, False, ['2x_nofunds']))
            else:
                payouts.append((user_id, 0, False, []))
    
    betting.save_data()
    
    winner_team = game['home_team'] if winner == 'home' else game['away_team']
    embed = discord.Embed(title="🎉 Game Result", color=0x2ecc71)
    embed.add_field(name="Game", value=f"{game['home_team']} vs {game['away_team']}", inline=False)
    embed.add_field(name="Winner", value=winner_team, inline=False)
    
    winners_text = ""
    losers_text = ""
    for user_id, payout, won, items in payouts:
        user = await bot.fetch_user(int(user_id))
        if won:
            bonus_text = " (2x!)" if '2x_multiplier' in items else ""
            winners_text += f"✅ {user.name}: +${payout:,.2f}{bonus_text}\n"
        else:
            if '2x_penalty' in items:
                losers_text += f"❌ {user.name}: -${abs(payout):,.0f} (2x penalty!)\n"
            elif 'insurance' in items:
                losers_text += f"❌ {user.name}: +${payout:,.0f} (insurance)\n"
            else:
                losers_text += f"❌ {user.name}\n"
    
    if winners_text:
        embed.add_field(name="Winners", value=winners_text, inline=True)
    if losers_text:
        embed.add_field(name="Losers", value=losers_text, inline=True)
    
    await interaction.response.send_message(embed=embed)

# Shop & Economy Commands
@bot.tree.command(name="shop", description="Buy power-ups and items")
async def slash_shop(interaction: discord.Interaction):
    embed = discord.Embed(title="🛒 Shop", description="Buy items with your winnings!", color=0xf1c40f)
    
    embed.add_field(
        name="💎 2x Multiplier - $500",
        value="Double your winnings! (Also doubles losses)\n`/buy 2x`",
        inline=False
    )
    embed.add_field(
        name="🛡️ Insurance - $300",
        value="Get 50% back if you lose\n`/buy insurance`",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="buy", description="Purchase an item from the shop")
async def slash_buy(interaction: discord.Interaction, item: str):
    user_id = str(interaction.user.id)
    balance = betting.get_balance(user_id)
    
    items_for_sale = {
        '2x': {'name': '2x Multiplier', 'price': 500, 'key': '2x_multiplier'},
        'insurance': {'name': 'Insurance', 'price': 300, 'key': 'insurance'}
    }
    
    item = item.lower()
    if item not in items_for_sale:
        await interaction.response.send_message("❌ Item not found! Use `/shop` to see available items.", ephemeral=True)
        return
    
    item_data = items_for_sale[item]
    price = item_data['price']
    
    if balance < price:
        await interaction.response.send_message(f"❌ You need ${price:,} but only have ${balance:,}!", ephemeral=True)
        return
    
    betting.update_balance(user_id, -price)
    
    if 'inventory' not in betting.users[user_id]:
        betting.users[user_id]['inventory'] = {}
    
    if item_data['key'] not in betting.users[user_id]['inventory']:
        betting.users[user_id]['inventory'][item_data['key']] = 0
    
    betting.users[user_id]['inventory'][item_data['key']] += 1
    betting.save_data()
    
    await interaction.response.send_message(f"✅ Purchased **{item_data['name']}** for ${price:,}!\nNew balance: ${betting.users[user_id]['balance']:,}", ephemeral=True)

@bot.tree.command(name="inventory", description="View your items")
async def slash_inventory(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    betting.get_balance(user_id)
    inventory = betting.users[user_id].get('inventory', {})
    
    embed = discord.Embed(title="🎒 Your Inventory", color=0x9b59b6)
    
    if not inventory or all(v == 0 for v in inventory.values()):
        embed.description = "Empty! Buy items from `/shop`"
    else:
        items_text = ""
        if inventory.get('2x_multiplier', 0) > 0:
            items_text += f"💎 2x Multiplier: {inventory['2x_multiplier']}x\n"
        if inventory.get('insurance', 0) > 0:
            items_text += f"🛡️ Insurance: {inventory['insurance']}x\n"
        embed.description = items_text or "Empty!"
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="daily", description="Claim your daily bonus")
async def slash_daily(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    betting.get_balance(user_id)
    
    last_daily = betting.users[user_id].get('last_daily')
    now = datetime.now(timezone.utc)
    
    if last_daily:
        last_daily_dt = datetime.fromisoformat(last_daily)
        time_diff = (now - last_daily_dt).total_seconds()
        
        if time_diff < 86400:  # 24 hours
            hours_left = (86400 - time_diff) / 3600
            await interaction.response.send_message(f"⏰ Daily already claimed! Come back in {hours_left:.1f} hours.", ephemeral=True)
            return
    
    bonus = 200
    betting.update_balance(user_id, bonus)
    betting.users[user_id]['last_daily'] = now.isoformat()
    betting.save_data()
    
    await interaction.response.send_message(f"💰 Claimed ${bonus} daily bonus!\nNew balance: ${betting.users[user_id]['balance']:,}")

@bot.tree.command(name="loan", description="Borrow money (max $100, 20% interest)")
async def slash_loan(interaction: discord.Interaction, amount: int):
    user_id = str(interaction.user.id)
    betting.get_balance(user_id)
    
    current_loan = betting.users[user_id].get('loan_amount', 0)
    
    if current_loan > 0:
        await interaction.response.send_message(f"❌ You already have a loan of ${current_loan}! Pay it back with `/repay` first.", ephemeral=True)
        return
    
    if amount > 100:
        await interaction.response.send_message("❌ Maximum loan is $100!", ephemeral=True)
        return
    
    if amount < 10:
        await interaction.response.send_message("❌ Minimum loan is $10!", ephemeral=True)
        return
    
    interest = int(amount * 1.2)  # 20% interest
    
    betting.update_balance(user_id, amount)
    betting.users[user_id]['loan_amount'] = interest
    betting.save_data()
    
    await interaction.response.send_message(f"💸 Borrowed ${amount}! You owe ${interest} (20% interest)\nUse `/repay` to pay it back.\nNew balance: ${betting.users[user_id]['balance']:,}")

@bot.tree.command(name="repay", description="Pay back your loan")
async def slash_repay(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    balance = betting.get_balance(user_id)
    loan = betting.users[user_id].get('loan_amount', 0)
    
    if loan == 0:
        await interaction.response.send_message("✅ You don't have any loans!", ephemeral=True)
        return
    
    if balance < loan:
        await interaction.response.send_message(f"❌ You need ${loan} but only have ${balance:,}!", ephemeral=True)
        return
    
    betting.update_balance(user_id, -loan)
    betting.users[user_id]['loan_amount'] = 0
    betting.save_data()
    
    await interaction.response.send_message(f"✅ Loan paid off! Paid ${loan}.\nNew balance: ${betting.users[user_id]['balance']:,}")

@bot.tree.command(name="send", description="Send money to another user")
async def slash_send(interaction: discord.Interaction, user: discord.User, amount: int):
    sender_id = str(interaction.user.id)
    receiver_id = str(user.id)
    
    if sender_id == receiver_id:
        await interaction.response.send_message("❌ You can't send money to yourself!", ephemeral=True)
        return
    
    balance = betting.get_balance(sender_id)
    
    if amount < 1:
        await interaction.response.send_message("❌ Amount must be at least $1!", ephemeral=True)
        return
    
    if amount > balance:
        await interaction.response.send_message(f"❌ You only have ${balance:,}!", ephemeral=True)
        return
    
    betting.update_balance(sender_id, -amount)
    betting.get_balance(receiver_id)  # Ensure receiver exists
    betting.update_balance(receiver_id, amount)
    betting.save_data()
    
    await interaction.response.send_message(f"✅ Sent ${amount:,} to {user.mention}!\nYour new balance: ${betting.users[sender_id]['balance']:,}")

@bot.tree.command(name="slots", description="Play the slot machine")
async def slash_slots(interaction: discord.Interaction, amount: int):
    user_id = str(interaction.user.id)
    balance = betting.get_balance(user_id)
    
    if amount < 10:
        await interaction.response.send_message("❌ Minimum bet is $10!", ephemeral=True)
        return
    
    if amount > balance:
        await interaction.response.send_message(f"❌ You only have ${balance:,}!", ephemeral=True)
        return
    
    betting.update_balance(user_id, -amount)
    
    # Slot symbols
    symbols = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']
    weights = [30, 25, 20, 15, 8, 2]  # Rarity weights
    
    import random
    slot1 = random.choices(symbols, weights=weights)[0]
    slot2 = random.choices(symbols, weights=weights)[0]
    slot3 = random.choices(symbols, weights=weights)[0]
    
    # Calculate winnings
    winnings = 0
    if slot1 == slot2 == slot3:
        if slot1 == '7️⃣':
            winnings = amount * 10  # Jackpot!
        elif slot1 == '💎':
            winnings = amount * 5
        else:
            winnings = amount * 3
    elif slot1 == slot2 or slot2 == slot3:
        winnings = amount * 2
    
    if winnings > 0:
        betting.update_balance(user_id, winnings)
        result_text = f"🎰 **[ {slot1} {slot2} {slot3} ]**\n\n🎉 YOU WIN ${winnings:,}!"
        color = 0x2ecc71
    else:
        result_text = f"🎰 **[ {slot1} {slot2} {slot3} ]**\n\n❌ Better luck next time!"
        color = 0xe74c3c
    
    betting.save_data()
    
    embed = discord.Embed(title="🎰 Slot Machine", description=result_text, color=color)
    embed.set_footer(text=f"New Balance: ${betting.users[user_id]['balance']:,}")
    
    await interaction.response.send_message(embed=embed)

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env file")

bot.run(TOKEN)