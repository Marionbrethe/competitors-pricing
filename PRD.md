# Competitor Pricing Scraper — PRD & How-To Guide

**Product:** Bounce Competitor Pricing Scraper
**Target site:** [usebounce.com](https://www.usebounce.com)
**Last updated:** March 2026

---

## Table of Contents

1. [What this tool does](#1-what-this-tool-does)
2. [What you get out of it](#2-what-you-get-out-of-it)
3. [How it works under the hood](#3-how-it-works-under-the-hood)
4. [Prerequisites — what you need before starting](#4-prerequisites--what-you-need-before-starting)
5. [Step-by-step setup guide](#5-step-by-step-setup-guide)
6. [Running the scraper](#6-running-the-scraper)
7. [Understanding the output](#7-understanding-the-output)
8. [Configuring cities and settings](#8-configuring-cities-and-settings)
9. [Troubleshooting](#9-troubleshooting)
10. [Limitations and known constraints](#10-limitations-and-known-constraints)

---

## 1. What this tool does

This tool automatically visits the Bounce luggage storage website, searches for storage locations in cities you choose, and collects the prices they charge for different bag sizes — saving everything into a spreadsheet (CSV file) you can open in Excel or Google Sheets.

Instead of manually visiting the website, clicking through dozens of locations, and copying prices into a spreadsheet yourself, this tool does it all automatically in minutes.

**Example of what it collects for one location:**

| City   | Location Name        | Address             | Bag Size  | Price | Currency |
|--------|----------------------|---------------------|-----------|-------|----------|
| London | King's Cross Station | Euston Rd, N1 9AL   | small     | 15.00 | GBP      |
| London | King's Cross Station | Euston Rd, N1 9AL   | regular   | 15.00 | GBP      |
| London | King's Cross Station | Euston Rd, N1 9AL   | odd-sized | 15.00 | GBP      |

---

## 2. What you get out of it

- A `.csv` file saved in the `output/` folder, named by city and date (e.g. `london_20260319.csv`)
- One row per bag size per location — so if a city has 10 locations with 3 bag sizes each, you get 30 rows
- Columns: `city`, `location_name`, `address`, `size`, `price`, `currency`, `price_unit`, `scraped_at`
- The file opens directly in Excel or Google Sheets — no conversion needed

---

## 3. How it works under the hood

The tool has two modes. You don't need to understand the technical details, but knowing which mode to use helps:

### Mode 1: `playwright` (default)
A standard automated browser visits the Bounce website. It acts like a regular web browser — it loads pages, clicks buttons, and reads the text. This is fast and quiet, but some websites try to block automated browsers.

### Mode 2: `computer-use` (AI-powered fallback)
If the standard mode is blocked by the website, this mode uses Claude (Anthropic's AI) to look at screenshots of the website and control the browser the same way you would — by looking at the screen and clicking what it sees. This requires an Anthropic API key (see setup below).

**Which should I use?**
- Try `playwright` first. If it returns zero results or errors, switch to `computer-use`.

---

## 4. Prerequisites — what you need before starting

You need three things installed on your computer:

| What | Why | Free? |
|------|-----|-------|
| **Python 3.11 or newer** | The tool is written in Python | Yes |
| **Git** | To download the code | Yes |
| **An Anthropic API key** | Only needed for `computer-use` mode | Paid (pay-as-you-go) |

### How to check if Python is already installed

Open your **Terminal** (Mac/Linux) or **Command Prompt** (Windows) and type:

```
python --version
```

If you see something like `Python 3.11.4`, you're good. If you see an error or a version below 3.11, download Python from [python.org/downloads](https://www.python.org/downloads/).

### How to check if Git is already installed

In the same terminal, type:

```
git --version
```

If you see something like `git version 2.39.0`, you're good. If not, download Git from [git-scm.com](https://git-scm.com/downloads).

---

## 5. Step-by-step setup guide

> **What is a terminal?**
> It's a text-based window where you type commands. On a Mac, press `Cmd + Space`, type `Terminal`, and press Enter. On Windows, press the Windows key, type `cmd`, and press Enter.

---

### Step 1 — Download the code

In your terminal, navigate to a folder where you want to store the project. For example, your Desktop:

**On Mac/Linux:**
```
cd ~/Desktop
```

**On Windows:**
```
cd %USERPROFILE%\Desktop
```

Then download (clone) the code:

```
git clone <repository-url>
cd competitors-pricing
```

> Replace `<repository-url>` with the actual URL of this repository (ask whoever shared this with you if you're unsure).

---

### Step 2 — Install the required libraries

Still in your terminal, inside the `competitors-pricing` folder, run:

```
pip install -r requirements.txt
```

This downloads all the Python libraries the tool needs. It may take 1–2 minutes.

Then install the browser the tool uses (Chromium):

```
playwright install chromium
```

---

### Step 3 — Set up your Anthropic API key (only needed for `computer-use` mode)

> Skip this step if you only plan to use the default `playwright` mode.

1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account
2. Go to **API Keys** and create a new key — it will look like `sk-ant-api03-...`
3. In the `competitors-pricing` folder, create a file called `.env` (note the dot at the start)

**On Mac/Linux**, in your terminal:
```
echo "ANTHROPIC_API_KEY=sk-ant-api03-YOUR-KEY-HERE" > .env
```

**On Windows**, in your terminal:
```
echo ANTHROPIC_API_KEY=sk-ant-api03-YOUR-KEY-HERE > .env
```

> Replace `sk-ant-api03-YOUR-KEY-HERE` with your actual key. Keep this file private — do not share it or commit it to Git.

---

## 6. Running the scraper

All commands below are typed in your terminal, from inside the `competitors-pricing` folder.

---

### Scrape one city

```
python main.py --city "London"
```

### Scrape multiple cities

```
python main.py --city "London" --city "Paris"
```

### Scrape all cities from the config file

```
python main.py --all-cities
```

### Limit how many locations to collect (useful for quick tests)

```
python main.py --city "London" --max-locations 3
```

This collects pricing from only the first 3 locations — good for checking that everything is working before running a full scrape.

### Use AI-powered mode (if the default mode is blocked)

```
python main.py --city "London" --mode computer-use
```

> This requires your Anthropic API key to be set up (Step 3 above). It is slower than the default mode — expect 5–15 minutes per city.

### See the browser window while scraping (useful for debugging)

```
python main.py --city "London" --headless false
```

By default the browser runs invisibly in the background. Adding `--headless false` makes it visible so you can watch what it's doing.

---

### Full example combining options

```
python main.py --city "London" --city "Paris" --max-locations 5 --headless false
```

This scrapes London and Paris, collects up to 5 locations each, and shows the browser window.

---

## 7. Understanding the output

After running, you'll find a file in the `output/` folder. The filename includes the city name and today's date, for example:

```
output/london_20260319.csv
```

If you scraped multiple cities at once, there will be one file with all results combined.

### Opening the file

- **Excel:** Double-click the `.csv` file. It should open automatically.
- **Google Sheets:** Go to File → Import → Upload the `.csv` file.

### Column descriptions

| Column | What it means | Example |
|--------|---------------|---------|
| `city` | The city you searched for | London |
| `location_name` | Name of the storage location | King's Cross Station |
| `address` | Street address of the location | Euston Rd, London N1 9AL |
| `size` | Bag size category | small / regular / odd-sized |
| `price` | Price as a number | 15.00 |
| `currency` | Currency code | GBP / USD / EUR |
| `price_unit` | How the price is charged | day |
| `scraped_at` | Date and time the data was collected | 2026-03-19T10:00:00Z |

---

## 8. Configuring cities and settings

All settings live in the file `config/targets.yaml`. You can open it with any text editor (Notepad on Windows, TextEdit on Mac, or any code editor).

### Adding or removing cities

Find the `cities:` section and add or remove city names:

```yaml
cities:
  - London
  - Paris
  - New York
  - Barcelona
```

Each city name must start with two spaces and a dash. Use the exact city name as you would type it into the Bounce website search bar.

### Changing the delay between locations

The `delay_between_locations` setting controls how many seconds the tool waits between visiting each location. Increasing this makes the scraper slower but less likely to be detected as a bot:

```yaml
scraper:
  delay_between_locations: 2   # change this number
```

### Setting a default limit on locations per city

```yaml
scraper:
  max_locations_per_city: 0   # 0 means unlimited; set to e.g. 10 to cap each city
```

---

## 9. Troubleshooting

### "python: command not found" or "python3: command not found"

Python is not installed or not on your PATH. Download it from [python.org/downloads](https://www.python.org/downloads/) and make sure to tick **"Add Python to PATH"** during installation (Windows).

On a Mac, try typing `python3` instead of `python`.

### "No module named playwright" or similar import error

You haven't installed the dependencies yet. Run:
```
pip install -r requirements.txt
playwright install chromium
```

### The scraper runs but collects 0 results

The website may be blocking the automated browser. Try:
1. Run with `--headless false` to see what the browser is doing
2. Switch to AI mode: `--mode computer-use`

### "ANTHROPIC_API_KEY is not set"

You're running `--mode computer-use` but haven't created the `.env` file. Follow Step 3 in the setup guide above.

### The output file is empty or missing

Check the terminal for any error messages in red. If the scraper finishes with `0 price record(s) collected`, the website was not scraped successfully — see "0 results" above.

### "Permission denied" when creating the .env file (Windows)

Try opening your Command Prompt as Administrator: right-click the Command Prompt icon and choose "Run as administrator".

---

## 10. Limitations and known constraints

| Limitation | Details |
|---|---|
| **Website changes** | If Bounce redesigns their website, the scraper may break and need to be updated |
| **Bot detection** | Bounce uses Cloudflare bot protection. The `playwright` mode may be blocked; `computer-use` mode is harder to detect but not guaranteed |
| **Speed** | The default mode scrapes roughly 1 location per 5–10 seconds. A city with 20 locations takes 2–4 minutes. `computer-use` mode is much slower (5–15 min per city) |
| **API costs** | `computer-use` mode calls the Anthropic API, which is billed per use. A full city scrape typically costs $0.50–$2.00 depending on how many locations and iterations are needed |
| **Terms of Service** | Automated scraping may conflict with Bounce's Terms of Service. This tool collects publicly visible pricing for competitive analysis — consult your legal team if unsure |
| **Geolocation** | Prices shown may vary based on your IP address location. For consistent results, always run from the same location |
| **Login-gated prices** | If Bounce moves pricing behind a login wall, this tool will not work without credentials |
