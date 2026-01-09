import discord
from discord.ext import commands, tasks
import json
import aiohttp
from datetime import datetime, timezone
import asyncio
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

class BettingSystem:
    def __init__(self):
        self.users = {}
        self.games = {}
        self.bets = {}
        self.load_data()
    
    def load_data(self):
        try:
            with open('betting_data.json', 'r') as f:
                data = json.load(f)
                self.users = data.get('users', {})
                self.games = data.get('games', {})
                self.bets = data.get('bets', {})
        except FileNotFoundError:
            pass
    
    def save_data(self):
        with open('betting_data.json', 'w') as f:
            json.dump({'users': self.users, 'games': self.games, 'bets': self.bets}, f, indent=2)
    
    def get_balance(self, user_id: str) -> int:
        if user_id not in self.users:
            self.users[user_id] = {'balance': 1000, 'total_wagered': 0, 'wins': 0, 'losses': 0}
            self.save_data()
        return self.users[user_id]['balance']
    
    def update_balance(self, user_id: str, amount: int):
        self.get_balance(user_id)
        self.users[user_id]['balance'] += amount
        self.save_data()

betting = BettingSystem()

@bot.event
async def on_ready():
    print(f'{bot.user} is online and ready to take bets!')
    check_game_locks.start()

@tasks.loop(minutes=1)
async def check_game_locks():
    now = datetime.now(timezone.utc)
    for game_id, game in list(betting.games.items()):
        if not game['locked'] and datetime.fromisoformat(game['start_time']) <= now:
            game['locked'] = True
            betting.save_data()
            channel = bot.get_channel(game['channel_id'])
            if channel:
                await channel.send(f"🔒 **Betting closed** for {game['home_team']} vs {game['away_team']}!")

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

@bot.command(name='creategame')
@commands.has_permissions(manage_messages=True)
async def creategame(ctx, home_team: str, away_team: str, home_odds: float, away_odds: float, start_time: str):
    """Create a game (YYYY-MM-DD HH:MM format, UTC)"""
    try:
        game_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
        game_id = f"{home_team}_{away_team}_{int(game_time.timestamp())}"
        
        betting.games[game_id] = {
            'home_team': home_team,
            'away_team': away_team,
            'home_odds': home_odds,
            'away_odds': away_odds,
            'start_time': game_time.isoformat(),
            'locked': False,
            'result': None,
            'channel_id': ctx.channel.id
        }
        betting.bets[game_id] = []
        betting.save_data()
        
        embed = discord.Embed(title="🏈 New Betting Opportunity", color=0x3498db)
        embed.add_field(name="Matchup", value=f"**{home_team}** vs **{away_team}**", inline=False)
        embed.add_field(name=f"{home_team} Odds", value=f"{home_odds:+.2f}", inline=True)
        embed.add_field(name=f"{away_team} Odds", value=f"{away_odds:+.2f}", inline=True)
        embed.add_field(name="Game Time", value=f"<t:{int(game_time.timestamp())}:F>", inline=False)
        embed.set_footer(text=f"Game ID: {game_id}\nUse !bet {game_id} [team] [amount]")
        
        await ctx.send(embed=embed)
    except ValueError:
        await ctx.send("❌ Invalid time format! Use: YYYY-MM-DD HH:MM (UTC)")

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
    embed = discord.Embed(title="✅ Bet Placed!", color=0x2ecc71)
    embed.add_field(name="Game", value=f"{game['home_team']} vs {game['away_team']}", inline=False)
    embed.add_field(name="Your Pick", value=team_name, inline=True)
    embed.add_field(name="Wagered", value=f"${amount:,}", inline=True)
    embed.add_field(name="Potential Win", value=f"${potential_win:,.2f}", inline=True)
    await ctx.send(embed=embed)

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
    `!balance` - Check your balance and stats
    `!bet <game_id> <home/away> <amount>` - Place a bet
    `!mybets` - View your active bets
    `!games` - List all active games
    `!leaderboard` - Top 10 richest bettors
    """
    
    admin_cmds = """
    `!creategame <home> <away> <home_odds> <away_odds> <time>`
    Example: `!creategame Lakers Warriors -110 +150 2026-01-15 19:00`
    
    `!result <game_id> <home/away>` - Set winner and pay out
    """
    
    embed.add_field(name="Player Commands", value=player_cmds, inline=False)
    embed.add_field(name="Admin Commands (Manage Messages)", value=admin_cmds, inline=False)
    embed.set_footer(text="Everyone starts with $1,000 | Minimum bet: $10")
    await ctx.send(embed=embed)

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env file")

bot.run(TOKEN)