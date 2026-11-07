@echo off
echo Starting Fortnite Grind & Combat Efficiency Suite...
echo.
echo This will:
echo   1. Read your Fortnite replay files
echo   2. Count chests you opened (not squadmates')
echo   3. Show you live stats and recommendations
echo.
echo The HUD will appear on your second monitor.
echo Press Ctrl+C to stop.
echo.

start "" "%~dp0dist\FortniteGrindSuite.exe"
ENDOFFILE && echo "Created Start Chest Counter.bat" && echo "" && echo "=== Making sure .gitignore covers personal files ===" && cat > .gitignore << 'ENDOFFILE'
# Personal data - never commit
data.json
data.json.tmp
stats.csv
report.txt
report.err.txt

# Build output
dist/
build/
__pycache__/
*.pyc
*.pyo
*.spec

# Reader (large binary, not needed in repo)
reader/

# Python cache
.pytest_cache/
.mypy_cache/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Freebuff
.freebuff/

# Environment files (may contain secrets)
.env
.env.local
.env.production
ENDOFFILE && echo "Updated .gitignore" && echo "" && echo "=== Checking what files exist now ===" && ls -la && echo "" && echo "=== What the user sees ===" && echo "When you run 'Start Chest Counter.bat' or 'FortniteGrindSuite.exe':" && echo "" && echo "PAGE 1 - TODAY:" && echo "  - Big chest count number" && echo "  - Progress to daily goal (e.g., 287 of 400 = 72%)" && echo "  - Streak days" && echo "  - CPM (chests per minute)" && echo "  - Live match clock (if in a match)" && echo "  - Quit recommendation window" && echo "  - FLASHING RED when it's time to quit" && echo "" && echo "PAGE 2 - STRATEGY:" && echo "  - Best exit time (e.g., Quit at 10:00)" && echo "  - Chests per hour at that time (e.g., 88/hr vs 66/hr full match)" && echo "  - Chests per match target (e.g., ~13 chests)" && echo "  - Match length recommendation (e.g., 10 minutes)" && echo "  - How much better quitting early is vs playing all matches" && echo "" && echo "PAGE 3 - LOADOUT:" && echo "  - Top 3 recommended weapons" && echo "  - Each with K/D and how many matches you've looted it in" && echo "  - Note: weapons rated by matches looted, not kills with that gun" && echo "" && echo "PAGE 4 - DROP ZONES:" && echo "  - Top 3 best drop zones" && echo "  - Each with: chests found, per-visit average, quiet %" && echo "  - Note: zones are coordinate clusters, not named POIs" && echo "" && echo "Navigation:" && echo "  - Click to go to next page" && echo "  - Left/Right arrow keys to switch pages" && echo "  - Auto-cycles every 8 seconds" && echo "  - Right-click for menu"
